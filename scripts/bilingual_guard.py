#!/usr/bin/env python3
"""Helpers for source-aware bilingual markdown generation and validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


WORKFLOW_VERSION = "pdf2bilingual-block-v1"
PROVENANCE_RE = re.compile(
    r'^<!--\s*PDF2BILINGUAL_PROVENANCE\s+source_sha256="(?P<source>[0-9a-f]{64})"\s+'
    r'workflow_version="(?P<version>[^"]+)"\s+generated_at="(?P<generated>[^"]+)"\s*-->$'
)
BLOCK_MARKER_RE = re.compile(
    r'^<!--\s*PDF2BILINGUAL_BLOCK\s+id="(?P<id>\d{4})"\s+role="(?P<role>source|translation)"\s+'
    r'type="(?P<type>[a-z_]+)"\s+block_sha256="(?P<hash>[0-9a-f]{64})"\s*-->$'
)
IMAGE_PLACEHOLDER_RE = re.compile(r"^<!--\s*PDF_IMAGE\s+page=\d+\s+bbox=[0-9.,]+\s*-->$")
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
LIST_RE = re.compile(r"^(?:[-*]\s+|\d+[.)]\s+)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
EN_ZH_TAG_RE = re.compile(r"^(English|Chinese|EN|ZH)\s*[:：]\s*", re.IGNORECASE)
NON_TRANSLATED_BLOCK_TYPES = {"image", "title", "authors", "affiliation", "frontmatter_note"}


@dataclass
class SourceBlock:
    block_id: str
    block_type: str
    text: str
    block_hash: str


@dataclass
class ValidationReport:
    ok: bool
    stale: bool
    source_hash: str
    provenance: dict[str, str] | None
    blocks_expected: int
    blocks_verified: int
    errors: list[str]
    warnings: list[str]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def compute_source_hash(source_path: Path) -> str:
    return sha256_text(source_path.read_text(encoding="utf-8"))


def normalize_block_text(text: str) -> str:
    return text.replace("\r\n", "\n").strip()


def cjk_char_count(text: str) -> int:
    return sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")


def looks_corrupted_translation(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    mojibake_tokens = ("鈥", "锛", "銆", "閳", "閿", "锟", "�")
    if any(token in stripped for token in mojibake_tokens):
        return True
    question_count = stripped.count("?")
    if question_count >= 6 and question_count / max(len(stripped), 1) >= 0.12:
        return True
    if "????" in stripped:
        return True
    return False


def classify_frontmatter_block(text: str, index_before_heading: int) -> str:
    compact = " ".join(text.split())
    if index_before_heading == 0:
        return "title"
    if "Supporting Information" in compact:
        return "frontmatter_note"
    if any(token in compact for token in ("University", "Department", "Laboratory", "College", "Center for")):
        return "affiliation"
    if any(symbol in compact for symbol in ("†", "‡", "*")) and ("," in compact or "\nand " in text.lower()):
        return "authors"
    return "paragraph"


def is_equation_like(text: str) -> bool:
    compact = text.strip()
    if len(compact) > 220 or "|" in compact or compact.startswith("<!--"):
        return False
    symbol_score = sum(token in compact for token in (" = ", "(", ")", "[", "]", "/", "->", "<-", "+", "±"))
    return symbol_score >= 3 and bool(re.search(r"[A-Za-z0-9]\s*=\s*[A-Za-z0-9]", compact))


def looks_like_caption(text: str) -> bool:
    stripped = " ".join(text.strip().split())
    if not stripped or not CAPTION_RE.match(stripped):
        return False
    if BODY_FIGURE_REF_RE.match(stripped):
        return False
    return bool(STRICT_CAPTION_RE.match(stripped))


def truncate(text: str, limit: int = 90) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def split_source_blocks(md_text: str) -> list[SourceBlock]:
    lines = md_text.replace("\r\n", "\n").split("\n")
    blocks: list[SourceBlock] = []
    i = 0
    counter = 1
    frontmatter_index = 0
    seen_main_heading = False

    def push(block_type: str, content_lines: list[str]) -> None:
        nonlocal counter
        text = normalize_block_text("\n".join(content_lines))
        if not text:
            return
        block_hash = sha256_text(text)
        blocks.append(SourceBlock(f"{counter:04d}", block_type, text, block_hash))
        counter += 1

    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        heading_match = HEADING_RE.match(stripped)
        if heading_match:
            heading_text = heading_match.group(2).strip()
            if heading_text.upper() == "REFERENCES":
                break
            seen_main_heading = True
            push("heading", [stripped])
            i += 1
            continue

        if IMAGE_PLACEHOLDER_RE.match(stripped):
            push("image", [stripped])
            i += 1
            continue

        if stripped.startswith("```"):
            code_lines = [stripped]
            i += 1
            while i < len(lines):
                code_line = lines[i].rstrip()
                code_lines.append(code_line)
                i += 1
                if code_line.strip().startswith("```"):
                    break
            push("code", code_lines)
            continue

        if "|" in stripped:
            table_lines = [stripped]
            i += 1
            while i < len(lines):
                next_line = lines[i].rstrip()
                next_stripped = next_line.strip()
                if not next_stripped or "|" not in next_stripped:
                    break
                table_lines.append(next_stripped)
                i += 1
            push("table", table_lines)
            continue

        if not seen_main_heading and stripped == "* S Supporting Information":
            push("frontmatter_note", [stripped])
            i += 1
            continue

        if LIST_RE.match(stripped):
            list_lines = [stripped]
            i += 1
            while i < len(lines):
                next_line = lines[i].rstrip()
                next_stripped = next_line.strip()
                if not next_stripped or not LIST_RE.match(next_stripped):
                    break
                list_lines.append(next_stripped)
                i += 1
            push("list", list_lines)
            continue

        para_lines = [stripped]
        i += 1
        while i < len(lines):
            next_line = lines[i].rstrip()
            next_stripped = next_line.strip()
            if not next_stripped:
                break
            if HEADING_RE.match(next_stripped) or IMAGE_PLACEHOLDER_RE.match(next_stripped):
                break
            if next_stripped.startswith("```") or LIST_RE.match(next_stripped):
                break
            if "|" in next_stripped:
                break
            para_lines.append(next_stripped)
            i += 1

        text = "\n".join(para_lines)
        if not seen_main_heading:
            block_type = classify_frontmatter_block(text, frontmatter_index)
            frontmatter_index += 1
        else:
            block_type = "caption" if looks_like_caption(para_lines[0]) else "equation" if is_equation_like(text) else "paragraph"
        push(block_type, para_lines)

    return blocks


def build_provenance_comment(source_hash: str, generated_at: str | None = None) -> str:
    generated = generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return (
        f'<!-- PDF2BILINGUAL_PROVENANCE source_sha256="{source_hash}" '
        f'workflow_version="{WORKFLOW_VERSION}" generated_at="{generated}" -->'
    )


def build_block_marker(block: SourceBlock, role: str) -> str:
    return (
        f'<!-- PDF2BILINGUAL_BLOCK id="{block.block_id}" role="{role}" '
        f'type="{block.block_type}" block_sha256="{block.block_hash}" -->'
    )


def build_scaffold(source_path: Path, output_path: Path) -> Path:
    source_text = source_path.read_text(encoding="utf-8")
    blocks = split_source_blocks(source_text)
    source_hash = sha256_text(source_text)
    parts = [build_provenance_comment(source_hash), ""]

    for block in blocks:
        parts.append(build_block_marker(block, "source"))
        parts.append(block.text)
        parts.append("")
        if block.block_type not in NON_TRANSLATED_BLOCK_TYPES:
            parts.append(build_block_marker(block, "translation"))
            parts.append("")
            parts.append("")

    output_path.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
    return output_path


def parse_provenance(md_text: str) -> dict[str, str] | None:
    for raw_line in md_text.replace("\r\n", "\n").split("\n"):
        stripped = raw_line.strip()
        if not stripped:
            continue
        match = PROVENANCE_RE.match(stripped)
        if match:
            return {
                "source_sha256": match.group("source"),
                "workflow_version": match.group("version"),
                "generated_at": match.group("generated"),
            }
        return None
    return None


def extract_marked_records(md_text: str) -> list[tuple[dict[str, str], str]]:
    lines = md_text.replace("\r\n", "\n").split("\n")
    records: list[tuple[dict[str, str], str]] = []
    current_meta: dict[str, str] | None = None
    current_lines: list[str] = []

    for raw_line in lines:
        stripped = raw_line.strip()
        if PROVENANCE_RE.match(stripped):
            continue
        marker_match = BLOCK_MARKER_RE.match(stripped)
        if marker_match:
            if current_meta is not None:
                records.append((current_meta, normalize_block_text("\n".join(current_lines))))
            current_meta = {
                "id": marker_match.group("id"),
                "role": marker_match.group("role"),
                "type": marker_match.group("type"),
                "block_sha256": marker_match.group("hash"),
            }
            current_lines = []
            continue
        if current_meta is not None:
            current_lines.append(raw_line)

    if current_meta is not None:
        records.append((current_meta, normalize_block_text("\n".join(current_lines))))

    return records


def validate_bilingual(source_path: Path, bilingual_path: Path) -> ValidationReport:
    source_text = source_path.read_text(encoding="utf-8")
    bilingual_text = bilingual_path.read_text(encoding="utf-8")
    source_hash = sha256_text(source_text)
    provenance = parse_provenance(bilingual_text)
    stale = provenance is None or provenance.get("source_sha256") != source_hash
    errors: list[str] = []
    warnings: list[str] = []

    if provenance is None:
        errors.append("missing provenance metadata comment")
    elif provenance.get("workflow_version") != WORKFLOW_VERSION:
        warnings.append(
            f'workflow version mismatch: expected {WORKFLOW_VERSION}, found {provenance.get("workflow_version")}'
        )

    if stale:
        errors.append("bilingual.md is stale for the current source.md")

    records = extract_marked_records(bilingual_text)
    expected_blocks = split_source_blocks(source_text)
    source_image_payloads = [block.text for block in expected_blocks if block.block_type == "image"]
    bilingual_image_payloads = [payload for meta, payload in records if meta["role"] == "source" and meta["type"] == "image"]
    index = 0

    for block in expected_blocks:
        if index >= len(records):
            errors.append(f"missing source block {block.block_id} ({block.block_type})")
            break

        source_meta, source_payload = records[index]
        index += 1
        if source_meta["role"] != "source":
            errors.append(f'expected source marker for block {block.block_id}, found role="{source_meta["role"]}"')
            break
        if source_meta["id"] != block.block_id:
            errors.append(f'block id mismatch: expected {block.block_id}, found {source_meta["id"]}')
        if source_meta["type"] != block.block_type:
            errors.append(
                f'block type mismatch for {block.block_id}: expected {block.block_type}, found {source_meta["type"]}'
            )
        if source_meta["block_sha256"] != block.block_hash:
            errors.append(f"source hash mismatch for block {block.block_id}")
        if normalize_block_text(source_payload) != block.text:
            errors.append(
                f"source payload mismatch for block {block.block_id}: expected '{truncate(block.text)}'"
            )

        if block.block_type in NON_TRANSLATED_BLOCK_TYPES:
            continue

        if index >= len(records):
            errors.append(f"missing translation block {block.block_id} ({block.block_type})")
            break

        translation_meta, translation_payload = records[index]
        index += 1
        if translation_meta["role"] != "translation":
            errors.append(
                f'expected translation marker for block {block.block_id}, found role="{translation_meta["role"]}"'
            )
            break
        if translation_meta["id"] != block.block_id:
            errors.append(
                f'translation block id mismatch: expected {block.block_id}, found {translation_meta["id"]}'
            )
        if translation_meta["type"] != block.block_type:
            errors.append(
                f'translation block type mismatch for {block.block_id}: expected {block.block_type}, found {translation_meta["type"]}'
            )
        if not translation_payload:
            errors.append(f"empty translation for block {block.block_id} ({block.block_type})")
        elif EN_ZH_TAG_RE.match(translation_payload):
            errors.append(f"language tag prefix is not allowed in block {block.block_id}")
        elif looks_corrupted_translation(translation_payload):
            errors.append(f"corrupted translation detected in block {block.block_id}")
        elif block.block_type in {"paragraph", "caption", "heading"} and cjk_char_count(translation_payload) < 2:
            warnings.append(f"translation block {block.block_id} has unusually low CJK content")

    if index < len(records):
        extras = len(records) - index
        errors.append(f"{extras} unexpected marked block(s) remain after validation")

    if source_image_payloads != bilingual_image_payloads:
        errors.append(
            f"image placeholder mismatch: source has {len(source_image_payloads)}, bilingual has {len(bilingual_image_payloads)}"
        )

    expected_section_labels = {
        block.text.strip()
        for block in expected_blocks
        if block.block_type == "heading" and block.text.strip() in {"## INTRODUCTION", "## RESULTS AND DISCUSSION", "## EXPERIMENTAL SECTION"}
    }
    for heading in expected_section_labels:
        if heading not in bilingual_text:
            errors.append(f"missing required section heading in bilingual.md: {heading}")

    ok = not errors
    return ValidationReport(
        ok=ok,
        stale=stale,
        source_hash=source_hash,
        provenance=provenance,
        blocks_expected=len(expected_blocks),
        blocks_verified=min(index, len(expected_blocks)),
        errors=errors,
        warnings=warnings,
    )


def report_to_json(report: ValidationReport) -> str:
    return json.dumps(
        {
            "ok": report.ok,
            "stale": report.stale,
            "source_hash": report.source_hash,
            "provenance": report.provenance,
            "blocks_expected": report.blocks_expected,
            "blocks_verified": report.blocks_verified,
            "errors": report.errors,
            "warnings": report.warnings,
        },
        ensure_ascii=False,
        indent=2,
    )


def print_validation(report: ValidationReport) -> int:
    print(f"[INFO] source_sha256={report.source_hash}")
    if report.provenance:
        print(
            f"[INFO] bilingual provenance: source_sha256={report.provenance['source_sha256']} "
            f'version={report.provenance["workflow_version"]} generated_at={report.provenance["generated_at"]}'
        )
    else:
        print("[WARN] bilingual provenance: missing")

    print(
        f"[INFO] blocks_expected={report.blocks_expected} blocks_verified={report.blocks_verified} "
        f"stale={str(report.stale).lower()}"
    )
    for warning in report.warnings:
        print(f"[WARN] {warning}")
    for error in report.errors:
        print(f"[ERROR] {error}")
    return 0 if report.ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Guard bilingual markdown against stale or lossy generation.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scaffold_parser = subparsers.add_parser("scaffold", help="Build a marked bilingual scaffold from source.md")
    scaffold_parser.add_argument("--source", required=True, help="Path to source.md")
    scaffold_parser.add_argument("--output", required=True, help="Path to scaffold markdown")

    verify_parser = subparsers.add_parser("verify", help="Validate bilingual.md against source.md")
    verify_parser.add_argument("--source", required=True, help="Path to source.md")
    verify_parser.add_argument("--bilingual", required=True, help="Path to bilingual.md")
    verify_parser.add_argument("--json", action="store_true", help="Print the validation report as JSON")

    args = parser.parse_args()

    if args.command == "scaffold":
        output_path = build_scaffold(Path(args.source).resolve(), Path(args.output).resolve())
        print(output_path)
        return 0

    if args.command == "verify":
        report = validate_bilingual(Path(args.source).resolve(), Path(args.bilingual).resolve())
        if args.json:
            print(report_to_json(report))
            return 0 if report.ok else 1
        return print_validation(report)

    print(f"[ERROR] Unsupported command: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
