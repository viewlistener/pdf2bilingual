#!/usr/bin/env python3
"""Compare auto and manually corrected source_reset files.

This tool focuses on paragraph structure and ordering, not full-text diff.
It generates:
- reset_diff_report.md
- paragraph_map.json
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path


IMAGE_RE = re.compile(r"^<!--\s*PDF_IMAGE\b.+?-->$")
CAPTION_RE = re.compile(r"^(Figure|Fig\.|Table|Scheme|Eq\.)\s*\d+", re.IGNORECASE)
STRICT_CAPTION_RE = re.compile(
    r"^(Figure|Fig\.|Table|Scheme|Eq\.)\s*(S?\d+[A-Za-z]?)(?:\s*[\.:|]|(?:\s*\([a-z]\))+)",
    re.IGNORECASE,
)
BODY_FIGURE_REF_RE = re.compile(
    r"^(Figure|Fig\.|Table|Scheme|Eq\.)\s*(S?\d+[A-Za-z]?)\s+"
    r"(shows?|illustrates?|demonstrates?|presents?|reveals?|indicates?|summarizes?|compares?|describes?)\b",
    re.IGNORECASE,
)
HEADING_RE = re.compile(r"^#+\s+")
MAJOR_HEADING_RE = re.compile(r"^##\s+")
MINOR_HEADING_RE = re.compile(r"^###\s+")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
FRAGMENT_START_RE = re.compile(
    r"^(and|or|but|which|that|those|these|such|however|therefore|thus|then|while|whereas|because|though|although|if|when|with|without|by|for|from|into|onto|upon|ones,|embryos|growth solution,|reaction time to|structural information)\b",
    re.IGNORECASE,
)


@dataclass
class Part:
    index: int
    major_section: str
    minor_section: str
    section_index: int
    block_type: str
    text: str
    normalized: str
    token_count: int


@dataclass
class ParagraphMapping:
    manual_index: int
    manual_block_type: str
    manual_section: str
    manual_preview: str
    relation: str
    best_auto_indices: list[int]
    best_auto_sections: list[str]
    best_score: float
    order_delta: int | None
    notes: list[str]


def normalize_text(text: str) -> str:
    compact = text.replace("\n", " ")
    compact = re.sub(r"\s+", " ", compact).strip()
    return compact


def tokenize(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9{}()/<>\-]+", text.lower())


def preview(text: str, limit: int = 180) -> str:
    compact = normalize_text(text)
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def classify_block_type(text: str) -> str:
    stripped = text.strip()
    if IMAGE_RE.match(stripped):
        return "image_anchor"
    if MAJOR_HEADING_RE.match(stripped):
        return "major_heading"
    if MINOR_HEADING_RE.match(stripped):
        return "minor_heading"
    if HEADING_RE.match(stripped):
        return "heading"
    if CAPTION_RE.match(stripped) and not BODY_FIGURE_REF_RE.match(stripped) and STRICT_CAPTION_RE.match(stripped):
        return "figure_caption"
    if stripped.startswith("* "):
        return "frontmatter_note"
    return "paragraph"


def heading_key(text: str) -> str:
    return text.strip().splitlines()[0].strip()


def split_parts(text: str) -> list[Part]:
    raw_parts = [part.strip() for part in text.split("\n\n") if part.strip()]
    major = "(front)"
    minor = "(front)"
    section_counts: dict[str, int] = {}
    parts: list[Part] = []
    for index, raw in enumerate(raw_parts, start=1):
        block_type = classify_block_type(raw)
        if block_type == "major_heading":
            major = heading_key(raw)
            minor = major
        elif block_type == "minor_heading":
            minor = heading_key(raw)
        section_name = minor if minor != "(front)" else major
        section_index = 0
        if block_type not in {"major_heading", "minor_heading", "heading"}:
            section_index = section_counts.get(section_name, 0) + 1
            section_counts[section_name] = section_index
        parts.append(
            Part(
                index=index,
                major_section=major,
                minor_section=minor,
                section_index=section_index,
                block_type=block_type,
                text=raw,
                normalized=normalize_text(raw),
                token_count=len(tokenize(raw)),
            )
        )
    return parts


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    a_tokens = set(tokenize(a))
    b_tokens = set(tokenize(b))
    if not a_tokens or not b_tokens:
        return ratio
    overlap = len(a_tokens & b_tokens) / max(1, len(a_tokens | b_tokens))
    return max(ratio, (ratio * 0.7) + (overlap * 0.3))


def best_auto_matches(manual: Part, auto_parts: list[Part], top_n: int = 5) -> list[tuple[float, int]]:
    scored_matches: list[tuple[tuple[float, ...], float, int]] = []
    for auto in auto_parts:
        if manual.block_type != auto.block_type:
            if not {manual.block_type, auto.block_type} <= {"figure_caption", "paragraph"}:
                continue
        score = similarity(manual.normalized, auto.normalized)
        if score >= 0.2:
            same_section = float(section_label(manual) == section_label(auto))
            exact_match = float(manual.normalized == auto.normalized)
            containment = float(
                manual.normalized in auto.normalized or auto.normalized in manual.normalized
            )
            proximity_bonus = 0.0
            if same_section:
                proximity_bonus = max(0.0, 1.0 - (abs(auto.section_index - manual.section_index) * 0.15))
            ranking = (
                exact_match,
                same_section,
                containment,
                round(score, 6),
                proximity_bonus,
                -float(auto.index),
            )
            scored_matches.append((ranking, score, auto.index))
    scored_matches.sort(key=lambda item: item[0], reverse=True)
    return [(score, index) for _, score, index in scored_matches[:top_n]]


def section_label(part: Part) -> str:
    return part.minor_section if part.minor_section != "(front)" else part.major_section


def combine_parts(parts: list[Part]) -> str:
    return normalize_text(" ".join(part.text for part in parts))


def detect_relation(manual: Part, auto_parts: list[Part], matches: list[tuple[float, int]]) -> tuple[str, list[int], list[str], float, int | None, list[str]]:
    notes: list[str] = []
    if not matches:
        return "unmatched", [], [], 0.0, None, ["no auto paragraph cleared baseline similarity"]

    best_score, best_index = matches[0]
    auto_by_index = {part.index: part for part in auto_parts}
    auto_position_by_index = {part.index: pos for pos, part in enumerate(auto_parts)}
    best_auto = auto_by_index[best_index]
    best_pos = auto_position_by_index[best_index]
    matched_indices = [best_index]
    matched_sections = [section_label(best_auto)]
    same_section = section_label(manual) == section_label(best_auto)
    order_delta = best_auto.section_index - manual.section_index if same_section else None
    relation = "aligned"

    if manual.normalized == best_auto.normalized:
        relation = "exact"
    elif manual.normalized in best_auto.normalized and len(best_auto.normalized) > len(manual.normalized) * 1.2:
        relation = "merged_into_auto"
        notes.append("manual paragraph appears inside a larger auto paragraph")
    elif best_auto.normalized in manual.normalized and len(manual.normalized) > len(best_auto.normalized) * 1.2:
        relation = "split_from_auto"
        notes.append("auto paragraph appears to cover only part of the manual paragraph")
    elif best_score < 0.75:
        relation = "weak_match"
        notes.append("best textual match is weak")

    for span in (2, 3):
        start = max(0, best_pos - span)
        end = min(len(auto_parts), best_pos + span + 1)
        for left in range(start, end):
            window = auto_parts[left : left + span]
            if len(window) != span:
                continue
            if any(part.block_type != manual.block_type for part in window):
                continue
            combo = combine_parts(window)
            combo_score = similarity(manual.normalized, combo)
            if combo_score > max(best_score + 0.06, 0.88):
                relation = "split_across_auto_parts"
                matched_indices = [part.index for part in window]
                matched_sections = [section_label(part) for part in window]
                best_score = combo_score
                notes.append(f"manual paragraph matches concatenation of auto parts {matched_indices}")
                break
        if relation == "split_across_auto_parts":
            break

    if section_label(manual) not in matched_sections:
        notes.append("best match lands in a different section")
        if relation in {"exact", "aligned"}:
            relation = "section_mismatch"

    if order_delta is not None and abs(order_delta) >= 3:
        notes.append(f"order displacement within section: local delta {order_delta}")
        if relation in {"exact", "aligned"}:
            relation = "moved"

    return relation, matched_indices, matched_sections, best_score, order_delta, notes


def count_parts_by_section(parts: list[Part]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for part in parts:
        if part.block_type in {"major_heading", "minor_heading"}:
            continue
        counts[section_label(part)] = counts.get(section_label(part), 0) + 1
    return counts


def fragment_candidates(parts: list[Part]) -> list[Part]:
    output: list[Part] = []
    for part in parts:
        if part.block_type != "paragraph":
            continue
        text = part.normalized
        if FRAGMENT_START_RE.match(text):
            output.append(part)
            continue
        if text and text[0].islower():
            output.append(part)
    return output


def build_report(auto_parts: list[Part], manual_parts: list[Part], mappings: list[ParagraphMapping]) -> str:
    auto_counts = count_parts_by_section(auto_parts)
    manual_counts = count_parts_by_section(manual_parts)
    relation_counts: dict[str, int] = {}
    for mapping in mappings:
        relation_counts[mapping.relation] = relation_counts.get(mapping.relation, 0) + 1

    lines = [
        "# Reset Diff Report",
        "",
        "## Summary",
        f"- auto parts: {len(auto_parts)}",
        f"- manual parts: {len(manual_parts)}",
        f"- auto fragment-like paragraphs: {len(fragment_candidates(auto_parts))}",
        f"- manual fragment-like paragraphs: {len(fragment_candidates(manual_parts))}",
        "",
        "## Heading Anomalies",
    ]
    auto_bad_headings = [part for part in auto_parts if part.block_type in {"major_heading", "minor_heading"} and "\n" in part.text.strip()]
    manual_bad_headings = [part for part in manual_parts if part.block_type in {"major_heading", "minor_heading"} and "\n" in part.text.strip()]
    lines.append(f"- auto contaminated headings: {len(auto_bad_headings)}")
    lines.append(f"- manual contaminated headings: {len(manual_bad_headings)}")
    for part in auto_bad_headings[:10]:
        lines.append(f"- auto heading {part.index}: {preview(part.text)}")

    lines.extend([
        "",
        "## Mapping Summary",
    ])
    for relation, count in sorted(relation_counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- {relation}: {count}")

    lines.extend(["", "## Section Part Counts"])
    section_names = sorted(set(auto_counts) | set(manual_counts))
    for name in section_names:
        lines.append(f"- {name}: auto={auto_counts.get(name, 0)}, manual={manual_counts.get(name, 0)}")

    lines.extend(["", "## Auto Fragment Candidates"])
    auto_fragments = fragment_candidates(auto_parts)
    if auto_fragments:
        for part in auto_fragments[:20]:
            lines.append(f"- auto {part.index} [{section_label(part)}]: {preview(part.text)}")
    else:
        lines.append("- none")

    lines.extend(["", "## High-Risk Mappings"])
    risky = [m for m in mappings if m.relation not in {"exact", "aligned"}]
    if risky:
        for mapping in risky[:40]:
            match_text = ", ".join(str(idx) for idx in mapping.best_auto_indices) or "none"
            lines.append(
                f"- manual {mapping.manual_index} [{mapping.manual_section}] -> auto {match_text}; "
                f"relation={mapping.relation}; score={mapping.best_score:.3f}; notes={'; '.join(mapping.notes) if mapping.notes else 'none'}"
            )
            lines.append(f"  manual: {mapping.manual_preview}")
    else:
        lines.append("- none")

    lines.extend(["", "## Section Mismatch Examples"])
    mismatches = [m for m in mappings if "different section" in " ".join(m.notes)]
    if mismatches:
        for mapping in mismatches[:20]:
            lines.append(
                f"- manual {mapping.manual_index} [{mapping.manual_section}] matched auto sections {', '.join(mapping.best_auto_sections) or 'none'}"
            )
            lines.append(f"  manual: {mapping.manual_preview}")
    else:
        lines.append("- none")

    lines.extend(["", "## Merge/Split Candidates"])
    merge_split = [m for m in mappings if m.relation in {"merged_into_auto", "split_from_auto", "split_across_auto_parts"}]
    if merge_split:
        for mapping in merge_split[:30]:
            lines.append(
                f"- manual {mapping.manual_index} relation={mapping.relation} auto={mapping.best_auto_indices} score={mapping.best_score:.3f}"
            )
            lines.append(f"  manual: {mapping.manual_preview}")
    else:
        lines.append("- none")

    lines.extend(["", "## Figure/Table Order Check"])
    auto_figs = [part.normalized for part in auto_parts if part.block_type == "figure_caption"]
    manual_figs = [part.normalized for part in manual_parts if part.block_type == "figure_caption"]
    lines.append(f"- auto figure-like blocks: {len(auto_figs)}")
    lines.append(f"- manual figure-like blocks: {len(manual_figs)}")
    for idx, text in enumerate(manual_figs[:12], start=1):
        auto_text = auto_figs[idx - 1] if idx - 1 < len(auto_figs) else "(missing)"
        state = "same" if text == auto_text else "different"
        lines.append(f"- figure slot {idx}: {state}")
        if state == "different":
            lines.append(f"  manual: {preview(text)}")
            lines.append(f"  auto: {preview(auto_text)}")

    return "\n".join(lines).rstrip() + "\n"


def infer_manual_path(folder: Path, auto_path: Path) -> Path:
    candidates = [path for path in folder.iterdir() if path.name.startswith("source_reset - ") and path.suffix == ".md"]
    if not candidates:
        raise FileNotFoundError("Could not find a manual comparison file like 'source_reset - 副本.md'")
    if len(candidates) == 1:
        return candidates[0]
    for candidate in candidates:
        if candidate != auto_path:
            return candidate
    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare auto and manually corrected source_reset files")
    parser.add_argument("paper_dir", help="Paper folder containing source_reset.md and the manual copy")
    parser.add_argument("--auto", default="source_reset.md", help="Auto source_reset filename")
    parser.add_argument("--manual", help="Manual corrected source_reset filename")
    parser.add_argument("--report", default="reset_diff_report.md", help="Output markdown report filename")
    parser.add_argument("--map", dest="map_name", default="paragraph_map.json", help="Output mapping JSON filename")
    args = parser.parse_args()

    paper_dir = Path(args.paper_dir).expanduser().resolve()
    auto_path = paper_dir / args.auto
    if not auto_path.exists():
        raise FileNotFoundError(f"Auto source_reset file not found: {auto_path}")
    manual_path = (paper_dir / args.manual).resolve() if args.manual else infer_manual_path(paper_dir, auto_path)
    if not manual_path.exists():
        raise FileNotFoundError(f"Manual source_reset file not found: {manual_path}")

    auto_parts = split_parts(auto_path.read_text(encoding="utf-8"))
    manual_parts = split_parts(manual_path.read_text(encoding="utf-8"))

    mappings: list[ParagraphMapping] = []
    auto_content_parts = [part for part in auto_parts if part.block_type not in {"major_heading", "minor_heading", "heading"}]
    for manual in manual_parts:
        if manual.block_type in {"major_heading", "minor_heading", "heading"}:
            continue
        matches = best_auto_matches(manual, auto_content_parts)
        relation, auto_indices, auto_sections, score, delta, notes = detect_relation(manual, auto_content_parts, matches)
        mappings.append(
            ParagraphMapping(
                manual_index=manual.index,
                manual_block_type=manual.block_type,
                manual_section=section_label(manual),
                manual_preview=preview(manual.text),
                relation=relation,
                best_auto_indices=auto_indices,
                best_auto_sections=auto_sections,
                best_score=score,
                order_delta=delta,
                notes=notes,
            )
        )

    report_path = paper_dir / args.report
    map_path = paper_dir / args.map_name
    report_path.write_text(build_report(auto_parts, manual_parts, mappings), encoding="utf-8")
    map_path.write_text(json.dumps([asdict(mapping) for mapping in mappings], ensure_ascii=False, indent=2), encoding="utf-8")

    print(report_path)
    print(map_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
