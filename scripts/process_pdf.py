#!/usr/bin/env python3
"""Deterministic helper for the PDF bilingual workflow.

This helper performs repeatable orchestration for one paper folder:
- validate that outputs stay beside the target PDF
- extract source.md in reading order from the PDF
- preserve figure positions through lightweight placeholders
- render bilingual.pdf from bilingual.md and reinsert figure regions
- avoid extra helper artifacts in the paper folder
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import fitz
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table as RLTable,
    TableStyle,
)

from bilingual_guard import split_source_blocks, validate_bilingual

for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="replace")
        except Exception:
            pass

ZH_FONT_CANDIDATES = (
    ("PaperZH", r"C:\Windows\Fonts\simhei.ttf"),
    ("PaperZH", r"C:\Windows\Fonts\msyh.ttc"),
    ("PaperZH", r"C:\Windows\Fonts\STSONG.TTF"),
    ("PaperZH", r"C:\Windows\Fonts\simsun.ttc"),
)
ZH_BOLD_FONT_CANDIDATES = (
    ("PaperZHBold", r"C:\Windows\Fonts\msyhbd.ttc"),
    ("PaperZHBold", r"C:\Windows\Fonts\simhei.ttf"),
    ("PaperZHBold", r"C:\Windows\Fonts\msyh.ttc"),
)
EN_FONT_CANDIDATES = (
    ("PaperEN", r"C:\Windows\Fonts\times.ttf"),
    ("PaperEN", r"C:\Windows\Fonts\georgia.ttf"),
)
EN_BOLD_FONT_CANDIDATES = (
    ("PaperENBold", r"C:\Windows\Fonts\timesbd.ttf"),
    ("PaperENBold", r"C:\Windows\Fonts\times.ttf"),
)
MONO_FONT_NAME = "Courier"
IMAGE_RE = re.compile(r"<!--\s*PDF_IMAGE\s+page=(\d+)\s+bbox=([0-9.]+),([0-9.]+),([0-9.]+),([0-9.]+)\s*-->")
BLOCK_MARKER_RE = re.compile(
    r'^<!--\s*PDF2BILINGUAL_BLOCK\s+id="(?P<id>\d{4})"\s+role="(?P<role>source|translation)"\s+'
    r'type="(?P<type>[a-z_]+)"\s+block_sha256="(?P<hash>[0-9a-f]{64})"\s*-->$'
)
GENERIC_HTML_COMMENT_RE = re.compile(r"^<!--(?!\s*PDF_IMAGE\b).+-->$")
SECTION_MARKERS = ("INTRODUCTION", "RESULTS AND DISCUSSION", "EXPERIMENTAL SECTION", "CONCLUSION", "CONCLUSIONS", "REFERENCES")
CAPTION_RE = re.compile(r"^(Figure|Table|Scheme|Fig\.|Eq\.)\s*\d+")
STRICT_CAPTION_RE = re.compile(
    r"^(Figure|Fig\.|Table|Scheme|Eq\.)\s*(S?\d+[A-Za-z]?)(?:\s*[\.:|]|(?:\s*\([a-z]\))+)",
    re.IGNORECASE,
)
BODY_FIGURE_REF_RE = re.compile(
    r"^(Figure|Fig\.|Table|Scheme|Eq\.)\s*(S?\d+[A-Za-z]?)\s+"
    r"(shows?|illustrates?|demonstrates?|presents?|reveals?|indicates?|summarizes?|compares?|describes?)\b",
    re.IGNORECASE,
)
INLINE_CITATION_PUNCT = r".?!;:,\)\]\"'\u201d\u2019"
INLINE_CITATION_AFTER_PUNCT_RE = re.compile(
    rf'(?P<prefix>(?<=[^\d])[{INLINE_CITATION_PUNCT}])(?P<cite>\d+(?:[-,]\d+)*)(?=(?:\s|$))'
)
INLINE_CITATION_AFTER_YEAR_RE = re.compile(
    r'(?P<prefix>(?<=\b\d{4})[.])(?P<cite>\d+(?:[-,]\d+)*)(?=(?:\s|$))'
)

HEADER_PATTERNS = (
    "Journal of the American Chemical Society",
    "pubs.acs.org/JACS",
    "Article",
)

FOOTER_PATTERNS = (
    "DOI:",
    "J. Am. Chem. Soc.",
)

NOISE_PATTERNS = (
    "See https://pubs.acs.org/sharingguidelines",
    "Received:",
    "Published:",
    "American Chemical Society",
)

PAPER_BG = colors.HexColor("#F6F1E7")
INK_BLUE = colors.HexColor("#1B365D")
WARM_TEXT = colors.HexColor("#2F2A24")
WARM_SUBTEXT = colors.HexColor("#6B6459")
WARM_RULE = colors.HexColor("#DED6C7")
WARM_RULE_STRONG = colors.HexColor("#C9C0AE")
TABLE_HEAD_BG = colors.HexColor("#EFE8D8")
IMAGE_MAX_WIDTH_RATIO_NORMAL = 0.78
IMAGE_MAX_WIDTH_RATIO_WIDE = 0.90
IMAGE_RENDER_SCALE_BASE = 2.8
IMAGE_RENDER_SCALE_SMALL_FIGURE = 3.4
IMAGE_TARGET_MIN_PIXEL_WIDTH = 1500
IMAGE_MAX_RENDER_SCALE = 4.0
TYPOGRAPHY = {
    "title": {"size": 18.0, "leading": 22.0, "space_after": 10.0},
    "h1": {"size": 14.5, "leading": 20.0, "space_before": 8.0, "space_after": 6.0},
    "h2": {"size": 12.5, "leading": 17.0, "space_before": 6.0, "space_after": 4.0},
    "body_zh": {"size": 10.4, "leading": 17.2, "space_after": 4.8},
    "caption_zh": {"size": 8.8, "leading": 13.2, "space_before": 1.2, "space_after": 3.0},
    "equation_zh": {"size": 9.8, "leading": 15.0, "space_before": 2.0, "space_after": 4.2},
    "bullet_zh": {"size": 10.4, "leading": 17.2},
    "code": {"size": 8.6, "leading": 11.0, "space_before": 2.0, "space_after": 4.0},
    "spacers": {
        "blank_paragraph": 4.0,
        "image_after": 4.0,
        "caption_pair": 1.2,
        "table_after": 5.0,
        "translation_to_source": 4.0,
    },
}

ExtractorMode = str


@dataclass
class Block:
    page_num: int
    kind: str
    bbox: tuple[float, float, float, float]
    text: str = ""
    lane: str = "full"

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class PageStats:
    page_num: int
    mode: str
    text_blocks: int
    image_blocks: int
    caption_blocks: int
    span_blocks: int
    left_blocks: int
    right_blocks: int
    warnings: list[str] = field(default_factory=list)


@dataclass
class LayoutBlockRecord:
    page_num: int
    kind: str
    bbox: tuple[float, float, float, float]
    lane: str
    text: str
    kept: bool
    reason: str


@dataclass
class ExtractionArtifacts:
    source_md: str
    source_reset_md: str
    page_stats: list[PageStats]
    warnings: list[str]
    layout_blocks: list[LayoutBlockRecord]


def normalize_text(text: str) -> str:
    mojibake_replacements = {
        "鈥檚": "’s",
        "鈥渟": "“",
        "鈥?": "”",
        "鈮?": "≈",
        "鈭?": "∼",
        "鈻": "■",
        "掳C": "°C",
        "渭L": "μL",
        "路": "·",
        "卤": "±",
        "燙enter": "Center",
        "tate": "State",
    }
    text = text.replace("\x00", "")
    text = text.replace("\u00ad", "")
    text = text.replace("\ufb00", "ff").replace("\ufb03", "ffi").replace("\ufb04", "ffl")
    text = text.replace("\ufb01", "fi").replace("\ufb02", "fl")
    text = text.replace("\u2010", "-").replace("\u2011", "-").replace("\u2013", "-")
    text = text.replace("\u2212", "-")
    text = text.replace("\u63b3C", "\u00b0C")
    text = text.replace("\u8def", "\u00b7")
    text = text.replace("\u6e2dL", "\u03bcL")
    text = text.replace("\ue0d5", " - ")
    for old, new in mojibake_replacements.items():
        text = text.replace(old, new)
    text = text.replace("Cd(Ac)2-only", "Cd(Ac)2-only")
    text = text.replace("entropy type", "entropic type")
    text = re.sub(r"(?<=\w)-\n(?=\w)", "", text)
    text = INLINE_CITATION_AFTER_PUNCT_RE.sub(r"\g<prefix>", text)
    text = INLINE_CITATION_AFTER_YEAR_RE.sub(r"\g<prefix>", text)
    text = re.sub(r"\n+", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return text.strip()


def reflow_wrapped_text(text: str) -> str:
    lines = [line.strip() for line in text.split("\n")]
    if len(lines) <= 1:
        return text.strip()
    merged: list[str] = []
    current = lines[0]
    for line in lines[1:]:
        if not line:
            if current:
                merged.append(current.strip())
                current = ""
            continue
        if not current:
            current = line
            continue
        current += " " + line
    if current:
        merged.append(current.strip())
    return "\n".join(merged).strip()


def block_text(block: dict) -> str:
    lines: list[str] = []
    for line in block.get("lines", []):
        spans = [span.get("text", "") for span in line.get("spans", [])]
        lines.append("".join(spans))
    return reflow_wrapped_text(normalize_text("\n".join(lines)))


def is_header_or_footer(text: str, y0: float, y1: float, page_height: float) -> bool:
    if not text:
        return True
    compact = " ".join(text.split())
    if y0 < 85 and any(token in compact for token in HEADER_PATTERNS):
        return True
    if y1 > page_height - 45 and any(token in compact for token in FOOTER_PATTERNS):
        return True
    if re.search(r"\bDOI:\s*10\.\S+", compact) and y0 > page_height * 0.84:
        return True
    if re.search(r"\bJ\.\s*Am\.\s*Chem\.\s*Soc\.", compact) and y0 > page_height * 0.84:
        return True
    if any(token in compact for token in NOISE_PATTERNS):
        return True
    if re.fullmatch(r"\d{5}", compact):
        return True
    return False


def looks_like_caption(text: str) -> bool:
    stripped = " ".join(text.strip().split())
    if not stripped or not CAPTION_RE.match(stripped):
        return False
    if BODY_FIGURE_REF_RE.match(stripped):
        return False
    return bool(STRICT_CAPTION_RE.match(stripped))


def side_has_column_signal(blocks: list[Block], page_height: float) -> bool:
    if not blocks:
        return False
    total_height = sum(block.height for block in blocks)
    total_chars = sum(len(block.text) for block in blocks)
    vertical_span = max(block.y1 for block in blocks) - min(block.y0 for block in blocks)
    return (
        len(blocks) >= 2
        or total_height > page_height * 0.20
        or vertical_span > page_height * 0.32
        or total_chars > 900
    )


def classify_page_mode(text_blocks: list[Block], page_width: float, override: ExtractorMode) -> str:
    if override != "auto":
        return {"single": "single", "double": "double", "mixed": "mixed"}[override]

    narrow = [block for block in text_blocks if block.width < page_width * 0.62 and len(block.text) > 20]
    left = [block for block in narrow if block.center_x < page_width * 0.47]
    right = [block for block in narrow if block.center_x > page_width * 0.53]
    page_height = max((block.y1 for block in text_blocks), default=792.0)
    has_left = side_has_column_signal(left, page_height)
    has_right = side_has_column_signal(right, page_height)
    has_spans = any(
        block.width > page_width * 0.72 or (looks_like_caption(block.text) and block.width > page_width * 0.6)
        for block in text_blocks
    )

    if has_left and has_right and has_spans:
        return "mixed"
    if has_left and has_right:
        return "double"
    return "single"


def compute_column_mid(text_blocks: list[Block], page_width: float, mode: str) -> float:
    if mode == "single":
        return page_width / 2
    narrow = [block for block in text_blocks if block.width < page_width * 0.62 and len(block.text) > 20]
    left = [block for block in narrow if block.center_x < page_width * 0.47]
    right = [block for block in narrow if block.center_x > page_width * 0.53]
    if left and right:
        return (max(block.x1 for block in left) + min(block.x0 for block in right)) / 2
    return page_width / 2


def assign_lane(block: Block, page_width: float, column_mid: float, mode: str) -> str:
    if mode == "single":
        return "full"
    crosses_mid = block.x0 < column_mid - 12 and block.x1 > column_mid + 12
    if block.kind == "image":
        if crosses_mid or block.width > page_width * 0.55:
            return "span"
        return "left" if block.center_x < column_mid else "right"
    if block.width < page_width * 0.55:
        return "left" if block.center_x < column_mid else "right"
    if block.width > page_width * 0.72 or crosses_mid:
        return "span"
    return "left" if block.center_x < column_mid else "right"


def collect_page_blocks(page: fitz.Page, override: ExtractorMode) -> tuple[list[Block], PageStats, list[LayoutBlockRecord]]:
    data = page.get_text("dict")
    page_width = page.rect.width
    page_height = page.rect.height
    all_blocks: list[Block] = []
    text_blocks: list[Block] = []
    image_blocks: list[Block] = []
    records: list[LayoutBlockRecord] = []

    for raw in data["blocks"]:
        bbox = tuple(raw["bbox"])
        if raw["type"] == 1:
            block = Block(page.number + 1, "image", bbox)
            all_blocks.append(block)
            image_blocks.append(block)
            records.append(
                LayoutBlockRecord(
                    page_num=page.number + 1,
                    kind="image",
                    bbox=bbox,
                    lane="pending",
                    text="",
                    kept=True,
                    reason="image-block",
                )
            )
            continue

        text = block_text(raw)
        if is_header_or_footer(text, bbox[1], bbox[3], page_height):
            records.append(
                LayoutBlockRecord(
                    page_num=page.number + 1,
                    kind="text",
                    bbox=bbox,
                    lane="filtered",
                    text=text,
                    kept=False,
                    reason="header-footer-or-noise",
                )
            )
            continue
        if looks_like_garbled_duplicate_block(text):
            records.append(
                LayoutBlockRecord(
                    page_num=page.number + 1,
                    kind="text",
                    bbox=bbox,
                    lane="filtered",
                    text=text,
                    kept=False,
                    reason="garbled-duplicate-layer",
                )
            )
            continue
        if "Downloaded via" in text or ((bbox[2] - bbox[0]) < 16 and (bbox[3] - bbox[1]) > 80):
            records.append(
                LayoutBlockRecord(
                    page_num=page.number + 1,
                    kind="text",
                    bbox=bbox,
                    lane="filtered",
                    text=text,
                    kept=False,
                    reason="download-watermark-or-vertical-noise",
                )
            )
            continue
        block = Block(page.number + 1, "text", bbox, text=text)
        all_blocks.append(block)
        text_blocks.append(block)
        records.append(
            LayoutBlockRecord(
                page_num=page.number + 1,
                kind="text",
                bbox=bbox,
                lane="pending",
                text=text,
                kept=True,
                reason="accepted-text-block",
            )
        )

    mode = classify_page_mode(text_blocks, page_width, override)
    column_mid = compute_column_mid(text_blocks, page_width, mode)
    for block in all_blocks:
        block.lane = assign_lane(block, page_width, column_mid, mode)
    record_index = 0
    for raw in records:
        if raw.kept:
            raw.lane = all_blocks[record_index].lane
            record_index += 1

    span_blocks = sum(1 for block in all_blocks if block.lane == "span")
    left_blocks = sum(1 for block in all_blocks if block.lane == "left")
    right_blocks = sum(1 for block in all_blocks if block.lane == "right")
    caption_blocks = sum(1 for block in text_blocks if looks_like_caption(block.text))
    warnings: list[str] = []
    if mode in {"double", "mixed"} and left_blocks == 0:
        warnings.append("double-like page without left-column blocks")
    if mode in {"double", "mixed"} and right_blocks == 0:
        warnings.append("double-like page without right-column blocks")
    if text_blocks and sum(len(block.text) for block in text_blocks) < 600:
        warnings.append("low text coverage on page")

    stats = PageStats(
        page_num=page.number + 1,
        mode=mode,
        text_blocks=len(text_blocks),
        image_blocks=len(image_blocks),
        caption_blocks=caption_blocks,
        span_blocks=span_blocks,
        left_blocks=left_blocks,
        right_blocks=right_blocks,
        warnings=warnings,
    )
    return all_blocks, stats, records


def group_adjacent(items: list[Block], gap: float = 14.0) -> list[list[Block]]:
    groups: list[list[Block]] = []
    for item in sorted(items, key=lambda block: (block.y0, block.x0)):
        if not groups:
            groups.append([item])
            continue
        prev = groups[-1][-1]
        if item.y0 <= prev.y1 + gap:
            groups[-1].append(item)
        else:
            groups.append([item])
    return groups


def infer_column_mid_from_blocks(blocks: list[Block], page_width: float, page_height: float) -> float:
    left = [
        block
        for block in blocks
        if block.lane == "left" and block.width < page_width * 0.60 and block.y0 > page_height * 0.25
    ]
    right = [
        block
        for block in blocks
        if block.lane == "right" and block.width < page_width * 0.60 and block.y0 > page_height * 0.25
    ]
    if not (left and right):
        left = [block for block in blocks if block.lane == "left" and block.width < page_width * 0.60]
        right = [block for block in blocks if block.lane == "right" and block.width < page_width * 0.60]
    if left and right:
        return (max(block.x1 for block in left) + min(block.x0 for block in right)) / 2
    return page_width / 2


def clipped_text_blocks(page: fitz.Page, rect: fitz.Rect) -> list[Block]:
    output: list[Block] = []
    data = page.get_text("dict", clip=rect)
    for raw in data["blocks"]:
        if raw["type"] != 0:
            continue
        text = block_text(raw)
        if not text:
            continue
        bbox = tuple(raw["bbox"])
        if is_header_or_footer(text, bbox[1], bbox[3], page.rect.height):
            continue
        if looks_like_garbled_duplicate_block(text):
            continue
        if "Downloaded via" in text or (bbox[2] - bbox[0] < 16 and bbox[3] - bbox[1] > 80):
            continue
        output.append(Block(page.number + 1, "text", bbox, text=text))
    return output


def extract_column_band(
    page: fitz.Page,
    blocks: list[Block],
    band_top: float,
    band_bottom: float,
    column_mid: float,
) -> list[Block]:
    if band_bottom <= band_top + 4:
        return []
    lane_items = [
        block
        for block in blocks
        if block.lane in {"left", "right"} and band_top <= block.center_y < band_bottom
    ]
    return order_band_columns(lane_items)


def order_page_blocks(blocks: list[Block], mode: str) -> list[Block]:
    if mode == "single":
        return sorted(blocks, key=lambda block: (block.y0, block.x0))

    span_blocks = [block for block in blocks if block.lane == "span"]
    non_span_blocks = [block for block in blocks if block.lane != "span"]
    groups = group_adjacent(span_blocks)
    ordered: list[Block] = []
    current_top = 0.0

    for group in groups:
        cutoff = min(block.y0 for block in group)
        band_items = [block for block in non_span_blocks if current_top <= block.center_y < cutoff]
        ordered.extend(order_band_columns(band_items))
        ordered.extend(sorted(group, key=lambda block: (block.y0, block.x0)))
        current_top = max(block.y1 for block in group)

    trailing = [block for block in non_span_blocks if block.center_y >= current_top]
    ordered.extend(order_band_columns(trailing))
    return ordered


def order_band_columns(blocks: list[Block]) -> list[Block]:
    left = [block for block in blocks if block.lane == "left"]
    right = [block for block in blocks if block.lane == "right"]
    full = [block for block in blocks if block.lane == "full"]
    ordered: list[Block] = []
    ordered.extend(sorted(full, key=lambda block: (block.y0, block.x0)))
    ordered.extend(sorted(left, key=lambda block: (block.y0, block.x0)))
    ordered.extend(sorted(right, key=lambda block: (block.y0, block.x0)))
    return ordered


def should_emit_image(block: Block) -> bool:
    return block.width * block.height > 3_000


def looks_like_heading(text: str) -> bool:
    return text.startswith("#")


INLINE_SUBHEADING_CONNECTORS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "to",
    "under",
    "using",
    "with",
    "without",
    "via",
    "vs",
}

SCIENTIFIC_PARAGRAPH_START_CUES = (
    "The analysis above implies",
    "As the most developed system,",
    "Though it is challenging",
    "Though distinguishable mechanisms",
    "Weller",
    "Free fatty acids were found",
    "It was reported that fatty amines",
    "The scheme in Figure 1a was modified",
    "Without purification of the seeds,",
    "UV-vis spectrum",
    "Results discussed above clearly indicated",
    "Results discussed above implied",
    "Figure 3a reveals",
    "For the reactions with",
    "By varying the",
    "The results in ",
    "The results described",
    "The spectral features related to Figure 4",
    "With optimized concentrations,",
    "Quantitative results",
    "During the first ",
    "Interestingly, for the 0D seeds",
    "It is interesting to notice",
    "The TEM images",
    "Interestingly,",
    "All the results",
    "Results in Figure",
    "All curves in",
    "It is interesting to notice",
    "As discussed above,",
    "The UV-vis spectrum",
    "TEM measurements",
    "High-resolution TEM images",
    "While the ",
    "In the first step,",
    "In the second step,",
    "In the third step,",
    "The first and second steps",
    "Synthesis of other types",
)

CONTEXTUAL_CONTINUATION_CUES_PRE_FIGURE = (
    "Synthesis of ",
)

CONTEXTUAL_CONTINUATION_CUES_POST_FIGURE = (
    "It is interesting to notice",
)

PROCEDURAL_SECTION_KEYWORDS = (
    "EXPERIMENTAL",
    "MATERIALS",
    "MEASUREMENTS",
    "SYNTHESIS",
    "PURIFICATION",
    "SEPARATION",
)


def starts_with_scientific_paragraph_cue(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    return any(stripped.startswith(cue) for cue in SCIENTIFIC_PARAGRAPH_START_CUES)


def starts_with_residual_fragment(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if stripped[0].islower():
        return True
    if re.match(r"^(reaction time to reach|structural information about|growth solution,|understanding of their formation mechanism)\b", stripped, re.IGNORECASE):
        return True
    if re.match(
        r"^[A-Z0-9][A-Za-z0-9{}()/,\- ]{0,45}\.\s+(However|Conversely|Thus|Instead|Furthermore|Nevertheless)\b",
        stripped,
    ):
        return True
    return False


def contains_internal_scientific_cue(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    for cue in SCIENTIFIC_PARAGRAPH_START_CUES:
        if f". {cue}" in stripped:
            return True
    return False


def split_at_internal_scientific_cue(text: str) -> tuple[str, str] | None:
    stripped = text.strip()
    if not stripped:
        return None
    best_start: int | None = None
    best_cue = ""
    for cue in SCIENTIFIC_PARAGRAPH_START_CUES:
        marker = f". {cue}"
        idx = stripped.find(marker)
        if idx == -1:
            continue
        start = idx + 2
        if best_start is None or start < best_start:
            best_start = start
            best_cue = cue
    if best_start is None:
        return None
    prefix = stripped[:best_start].strip()
    suffix = stripped[best_start:].strip()
    if not prefix or not suffix.startswith(best_cue):
        return None
    return prefix, suffix


def looks_like_inline_subheading(text: str) -> bool:
    stripped = text.strip().rstrip(".")
    if not stripped:
        return False
    if len(stripped) < 4 or len(stripped) > 90:
        return False
    if any(mark in stripped for mark in ("\u2020", "\u2021", "\u2022", "@")):
        return False
    if any(mark in stripped for mark in ":;!?"):
        return False
    if re.search(r"\b(Figures?|Fig\.|Table|Tables|Scheme|Eq\.)\b", stripped):
        return False
    if looks_like_caption(stripped) or looks_like_heading(stripped):
        return False
    tokens = re.findall(r"[A-Za-z0-9{}()/<>\-]+", stripped)
    if not tokens or len(tokens) > 14:
        return False
    if len(tokens) == 1 and tokens[0] not in {"Materials", "Measurements", "Conclusion", "Conclusions"}:
        return False
    if tokens[-1].rstrip(")") in {"Information", "Supporting"}:
        return False
    score = 0
    for token in tokens:
        plain = token.strip("{}()<>")
        if not plain:
            continue
        lowered = plain.lower()
        if lowered in INLINE_SUBHEADING_CONNECTORS:
            score += 1
        elif plain.isupper():
            score += 1
        elif plain[0].isupper():
            score += 1
        elif any(ch.isdigit() for ch in plain):
            score += 1
    return score / max(len(tokens), 1) >= 0.85


def starts_with_continuation(text: str) -> bool:
    stripped = text.lstrip()
    if not stripped:
        return False
    first = stripped[0]
    if first.islower():
        return True
    if first.isdigit():
        return True
    return stripped.startswith(
        (
            "(",
            "[",
            "{",
            ",",
            ".",
            ";",
            ":",
            "-",
            "and ",
            "or ",
            "which ",
            "that ",
            "with ",
            "without ",
            "while ",
            "where ",
            "whereas ",
            "because ",
            "although ",
            "but ",
        )
    )


def ends_like_continuation(text: str) -> bool:
    stripped = text.rstrip()
    if not stripped:
        return False
    return stripped[-1] not in ".!?)]}\"'" and not stripped.endswith(("--", "-"))


def starts_like_sentence(text: str) -> bool:
    stripped = text.lstrip()
    if not stripped:
        return False
    return bool(re.match(r"^[A-Z][a-z]", stripped))


def should_force_merge_short_paragraphs(previous: str, current: str) -> bool:
    prev = previous.strip()
    curr = current.strip()
    if not prev or not curr:
        return False
    if looks_like_heading(prev) or looks_like_heading(curr):
        return False
    if looks_like_caption(prev) or looks_like_caption(curr):
        return False
    if prev.startswith("<!--") or curr.startswith("<!--"):
        return False
    if prev.startswith("* ") or curr.startswith("* "):
        return False
    if prev.endswith((".", "!", "?")) and starts_with_residual_fragment(curr) and contains_internal_scientific_cue(curr):
        return False
    if prev.endswith("°") and re.match(r"^C(?:\b|\s|\()", curr):
        return True
    if prev.endswith(("~", "<", ">", "=")) and re.match(r"^[0-9A-Za-z(]", curr):
        return True
    if starts_with_continuation(curr):
        return True
    if ends_like_continuation(prev):
        return True
    if len(prev) <= 240 and not starts_like_sentence(curr):
        return True
    if len(prev) <= 120 and starts_like_sentence(curr) and not re.search(r"[.!?]$" , prev):
        return True
    return False


def looks_like_frontmatter_fragment(text: str) -> bool:
    compact = " ".join(text.split())
    if not compact:
        return False
    if "pubs.acs.org/JACS" in compact:
        return True
    if any(mark in compact for mark in ("\u2020", "\u2021", "\u2022")):
        return True
    if any(token in compact for token in ("Center for", "Department of", "Laboratory", "University", "College of")):
        return True
    if "Supporting Information" in compact:
        return True
    return False


def extract_embedded_section_marker(text: str) -> tuple[str, str | None]:
    compact = text.strip()
    for marker in ("INTRODUCTION", "RESULTS AND DISCUSSION", "EXPERIMENTAL SECTION", "CONCLUSION", "CONCLUSIONS", "REFERENCES"):
        token = f"\u25a0{marker}"
        if compact.endswith(token):
            body = compact[: -len(token)].rstrip()
            heading = marker if marker != "CONCLUSIONS" else "CONCLUSION"
            return body, f"## {heading}"
    return text, None


def normalize_section_markers(text: str) -> str:
    for marker in SECTION_MARKERS:
        text = re.sub(rf"(?:\u25a0|\u25aa)\s*{re.escape(marker)}\b", f"\n\n{marker}", text)
    for marker in ("ASSOCIATED CONTENT", "AUTHOR INFORMATION", "ACKNOWLEDGMENTS", "NOTE ADDED IN PROOF"):
        text = re.sub(rf"(?:\u25a0|\u25aa)\s*{re.escape(marker)}\b", f"\n\n{marker}", text)
    text = text.replace("掳C", "°C")
    text = re.sub(r"°\s+C\b", "°C", text)
    text = re.sub(r"(?<=\d)\s+(?=°C\b)", "", text)
    text = re.sub(
        r"(?<=\d)\s+(?=(?:rpm|min\.?|mins\.?|h|hr|hrs|s|sec|nm|mm|cm|mL|μL|uL|mg|g|kg|wt%|%)\b)",
        "",
        text,
    )
    return text


def finalize_source_reset(text: str) -> str:
    text = normalize_section_markers(text)
    refs_match = re.search(r"(?m)(?:^|\n)(?:##\s*)?REFERENCES\b", text)
    tail_matches = [
        re.search(r"(?m)(?:^|\n)(?:ASSOCIATED CONTENT|AUTHOR INFORMATION|ORCID\b|ACKNOWLEDGMENTS|NOTE ADDED IN PROOF)\b", text),
    ]
    tail_positions = [match.start() for match in tail_matches if match]
    if refs_match:
        cut_at = refs_match.start()
        if tail_positions:
            cut_at = min(cut_at, min(tail_positions))
        text = text[:cut_at].rstrip() + "\n\n## REFERENCES\n"
        return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"

    if tail_positions:
        text = text[: min(tail_positions)].rstrip() + "\n"
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def repair_experimental_section_artifacts(text: str) -> str:
    text = text.replace("Cd(Ac)2｡､2", "Cd(Ac)2·2H2O")
    text = text.replace("Cd(Ac)2·2H2OH2O", "Cd(Ac)2·2H2O")
    text = text.replace("Cd(Ac)2·2 999%)", "Cd(Ac)2·2H2O, 99.999%)")
    text = text.replace("｡紊", "°C")
    text = text.replace("ｦﾌL", "μL")
    text = re.sub(r"being \?([0-9.]+)\s*nm", r"being ∼\1 nm", text)
    text = re.sub(r"were \?([0-9.]+)\s+and\s+\?([0-9.]+)\s*nm", r"were ∼\1 and ∼\2 nm", text)
    text = re.sub(r"at \?([0-9.]+)\s*nm", r"at ∼\1 nm", text)
    text = re.sub(r"still at \?([0-9.]+)\s*°C", r"still at ∼\1 °C", text)
    text = re.sub(r"at\s+250\s*°C\s+under Ar protection\.\s+A total amount", "at 250 °C under Ar protection. A total amount", text)
    text = re.sub(r"Cadmium acetate dihydrate \(Cd\(Ac\)2·2\s*\n\s*999%\)", "Cadmium acetate dihydrate (Cd(Ac)2·2H2O, 99.999%)", text)
    text = re.sub(r"### Direct Synthesis of 5\s*\n\s*5-Monolayer CdSe 2D Nanocrystals\.", "### Direct Synthesis of 5.5-Monolayer CdSe 2D Nanocrystals", text)
    text = re.sub(r"### Direct Synthesis of 5\.5-Monolayer CdSe 2D Nanocrystals\s+In a typical synthesis,", "### Direct Synthesis of 5.5-Monolayer CdSe 2D Nanocrystals\n\nIn a typical synthesis,", text)
    text = re.sub(r"Cd\(Ac\)2·2\s*\n\s*### H2O \(0\s*\n\s*15 mmol\) was introduced\.", "Cd(Ac)2·2H2O (0.15 mmol) was introduced.", text)
    text = re.sub(r"Cd\(Ac\)2·2H2O\s*\n\s*### H2O \(0\s*\n\s*15 mmol\) was introduced\.", "Cd(Ac)2·2H2O (0.15 mmol) was introduced.", text)
    text = re.sub(r"Cd\(Ac\)2·2H2O\s*\n\s*15 mmol\) was introduced\.", "Cd(Ac)2·2H2O (0.15 mmol) was introduced.", text)
    text = re.sub(
        r"(The supernatant was quickly removed, with precipitate dissolved in ∼1\.0 mL of chloroform at 60 °C\.)\s*\n\s*2 mL\) was then added into it again",
        r"\1 Acetonitrile (1.2 mL) was then added into it again",
        text,
    )
    text = re.sub(r"Synthesis and Purification of CdSe Nanocrystals \(Seeds\) of Three Different Sizes \(First Excitonic Absorption Peak at 473 nm, 488 nm, 502 nm, Respectively\)\.", "### Synthesis and Purification of CdSe Nanocrystals (Seeds) of Three Different Sizes", text)
    text = re.sub(
        r"(### Synthesis and Purification of CdSe Nanocrystals \(Seeds\) of Three Different Sizes)\s+(A typical synthesis)",
        r"\1\n\n\2",
        text,
    )
    text = re.sub(
        r"which named Figure 6c illustrates(?P<figure_text>.+?)\n\n## CONCLUSION\n\nthe \{110\} facets as the attachment front for the face-centercubic lattice\.",
        r"which named the {110} facets as the attachment front for the face-centercubic lattice. Figure 6c illustrates\g<figure_text>\n\n## CONCLUSION\n",
        text,
        flags=re.S,
    )
    text = text.replace("\n\n### Figures 2 and 3)\n\n", " Figures 2 and 3).\n\n")
    text = text.replace("\n\n### Figures 1 and 4)\n\n", " Figures 1 and 4).\n\n")
    return text


def split_inline_subheading(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    if looks_like_inline_subheading(stripped):
        return [f"### {stripped.rstrip('.')}"]
    match = re.match(r"^([A-Z][A-Za-z0-9{}()/,<>\-& ]{3,90}\.)\s+(.+)$", stripped)
    if not match:
        return [stripped]
    heading, remainder = match.group(1).strip(), match.group(2).strip()
    if not looks_like_inline_subheading(heading):
        return [stripped]
    return [f"### {heading.rstrip('.')}", remainder]


def split_midparagraph_inline_subheading(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped or looks_like_caption(stripped) or looks_like_heading(stripped):
        return [stripped]
    for match in re.finditer(r"[A-Z][A-Za-z0-9{}()/,<>\-& ]{3,90}\.", stripped):
        heading = match.group(0).strip()
        body_before = stripped[: match.start()].strip()
        remainder = stripped[match.end():].strip()
        if len(body_before) < 40 or not remainder:
            continue
        if re.search(r"\b(purchased from|from|with|using|between|toward|towards|at|into|onto|near|named)$", body_before, re.IGNORECASE):
            continue
        if not looks_like_inline_subheading(heading):
            continue
        return [body_before, f"### {heading.rstrip('.')}", remainder]
    return [stripped]


def is_plain_text_block(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.startswith(("#", "<!--", "* ", "- ")):
        return False
    if re.match(r"^\d+[.)]\s+", stripped):
        return False
    return not looks_like_caption(stripped)


def should_rejoin_residual_paragraphs(previous: str, current: str) -> bool:
    prev = previous.strip()
    curr = current.strip()
    if not prev or not curr:
        return False
    if not (is_plain_text_block(prev) and is_plain_text_block(curr)):
        return False
    if starts_with_residual_fragment(curr):
        return True
    if prev.endswith((".", "!", "?", ":", ";")):
        return False
    if prev.endswith(("(", "[", "{", "/", "-")) or prev.lower().endswith((" the", " a", " an")):
        return True
    if curr.startswith((")", "]", "}", ",", ";", ":", "Figures ", "Figure ")):
        return True
    if starts_with_scientific_paragraph_cue(curr) and len(prev) >= 160:
        return False
    if starts_with_continuation(curr):
        return True
    return len(prev) >= 40 and not starts_like_sentence(curr)


def repair_residual_paragraph_breaks(text: str) -> str:
    parts = [part.strip() for part in text.split("\n\n") if part.strip()]
    if not parts:
        return text
    repaired: list[str] = []
    for item in parts:
        if repaired and should_rejoin_residual_paragraphs(repaired[-1], item):
            repaired[-1] = f"{repaired[-1].rstrip()} {item.lstrip()}"
        else:
            repaired.append(item)
    return "\n\n".join(repaired)


def repair_late_commentary_boundaries(text: str) -> str:
    parts = [part.strip() for part in text.split("\n\n") if part.strip()]
    repaired: list[str] = []
    for part in parts:
        if (
            repaired
            and is_plain_text_block(repaired[-1])
            and is_plain_text_block(part)
            and re.search(r"\bTo confirm this (hypothesis|possibility|assignment|interpretation)\b", repaired[-1], re.IGNORECASE)
            and re.match(r"^Results in Figure S\d+[A-Za-z]?\b", part)
        ):
            combined = f"{repaired[-1].rstrip()} {part.lstrip()}"
            split_pair = re.split(r"(?<=\.)\s+(?=Results discussed above implied\b)", combined, maxsplit=1)
            if len(split_pair) == 2:
                repaired[-1] = split_pair[0].strip()
                repaired.append(split_pair[1].strip())
            else:
                repaired[-1] = combined
            continue
        if (
            "It is interesting to notice that" in part
            and re.search(r"\b(activation energy|complex chemical kinetics|single-component fitting)\b", part, re.IGNORECASE)
        ):
            split_pair = re.split(r"(?<=\.)\s+(?=It is interesting to notice that\b)", part, maxsplit=1)
            if len(split_pair) == 2:
                repaired.extend([split_pair[0].strip(), split_pair[1].strip()])
                continue
        if "Results in Figure S" in part and "Results discussed above implied" in part:
            split_pair = re.split(r"(?<=\.)\s+(?=Results discussed above implied\b)", part, maxsplit=1)
            if len(split_pair) == 2:
                repaired.extend([split_pair[0].strip(), split_pair[1].strip()])
                continue
        repaired.append(part)
    return "\n\n".join(repaired)


def should_merge_emitted_text(previous: str, current: str) -> bool:
    if not previous or not current:
        return False
    if looks_like_heading(previous) or looks_like_heading(current):
        return False
    if previous.startswith("<!--") or current.startswith("<!--"):
        return False
    if looks_like_caption(previous) or looks_like_caption(current):
        return False
    if previous.startswith("* ") or current.startswith("* "):
        return False
    if looks_like_frontmatter_fragment(previous) or looks_like_frontmatter_fragment(current):
        return False
    if ends_like_continuation(previous) and starts_with_continuation(current):
        return True
    return should_force_merge_short_paragraphs(previous, current)


def smooth_image_interrupted_paragraphs(parts: list[str]) -> list[str]:
    smoothed: list[str] = []
    idx = 0
    while idx < len(parts):
        current = parts[idx]
        if current.startswith("<!-- PDF_IMAGE"):
            group = [current]
            idx += 1
            while idx < len(parts) and looks_like_caption(parts[idx]):
                group.append(parts[idx])
                idx += 1
            previous = smoothed[-1] if smoothed else None
            following = parts[idx] if idx < len(parts) else None
            if previous and following and should_merge_emitted_text(previous, following):
                smoothed[-1] = f"{previous.rstrip()} {following.lstrip()}"
                smoothed.extend(group)
                idx += 1
                continue
            smoothed.extend(group)
            continue
        smoothed.append(current)
        idx += 1
    return smoothed


def split_inline_subheadings_in_parts(parts: list[str]) -> list[str]:
    rewritten: list[str] = []
    for item in parts:
        if item.startswith("#") or item.startswith("<!--") or looks_like_caption(item):
            rewritten.append(item)
            continue
        segments = [item]
        changed = True
        while changed:
            changed = False
            next_segments: list[str] = []
            for segment in segments:
                split_segments = split_midparagraph_inline_subheading(segment)
                if len(split_segments) > 1:
                    changed = True
                next_segments.extend(split_segments)
            segments = next_segments
        rewritten.extend(segments)
    return rewritten


def merge_adjacent_text_parts(parts: list[str]) -> list[str]:
    merged: list[str] = []
    for item in parts:
        if merged and should_merge_emitted_text(merged[-1], item):
            merged[-1] = f"{merged[-1].rstrip()} {item.lstrip()}"
        else:
            merged.append(item)
    return merged


def merge_caption_split_paragraphs(parts: list[str]) -> list[str]:
    merged: list[str] = []
    idx = 0
    while idx < len(parts):
        current = parts[idx]
        if (
            idx + 2 < len(parts)
            and not current.startswith("#")
            and not current.startswith("<!--")
            and not looks_like_caption(current)
            and looks_like_caption(parts[idx + 1])
            and not parts[idx + 2].startswith("#")
            and not parts[idx + 2].startswith("<!--")
            and not looks_like_caption(parts[idx + 2])
            and should_force_merge_short_paragraphs(current, parts[idx + 2])
        ):
            merged.append(f"{current.rstrip()} {parts[idx + 2].lstrip()}")
            merged.append(parts[idx + 1])
            idx += 3
            continue
        merged.append(current)
        idx += 1
    return merged


def looks_like_incomplete_trailing_clause(text: str) -> bool:
    stripped = text.rstrip()
    if not stripped:
        return False
    lowered = stripped.lower()
    return lowered.endswith(
        (
            " to the",
            " to",
            " of the",
            " with the",
            " in the",
            " by the",
            " as the",
            " from the",
            " for the",
        )
    )


def starts_with_completion_phrase(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    return bool(
        re.match(
            r"^[A-Z0-9][A-Za-z0-9{}()/,\- ]{1,48}\.\s+(However|Nevertheless|Instead|Conversely|Thus|Furthermore)\b",
            stripped,
        )
    )


def nearest_unfinished_text_index(parts: list[str], before_index: int) -> int | None:
    for idx in range(before_index - 1, -1, -1):
        item = parts[idx]
        if not is_plain_text_block(item):
            continue
        if not item.strip().endswith((".", "!", "?", ":", ";")):
            return idx
    return None


def repair_heading_boundary_overflow(parts: list[str]) -> list[str]:
    repaired: list[str] = []
    idx = 0
    while idx < len(parts):
        current = parts[idx]
        if (
            current.startswith("### ")
            and idx + 3 < len(parts)
            and is_plain_text_block(parts[idx + 1])
            and is_plain_text_block(parts[idx + 2])
            and is_plain_text_block(parts[idx + 3])
            and looks_like_incomplete_trailing_clause(parts[idx + 1])
            and starts_with_residual_fragment(parts[idx + 2])
            and starts_with_completion_phrase(parts[idx + 3])
        ):
            lead = parts[idx + 1].strip()
            overflow = parts[idx + 2].strip()
            continuation = parts[idx + 3].strip()
            split_pair = split_at_internal_scientific_cue(overflow)
            overflow_prefix = overflow
            overflow_suffix = ""
            if split_pair:
                overflow_prefix, overflow_suffix = split_pair
            attach_idx = nearest_unfinished_text_index(repaired, len(repaired))
            if overflow_prefix:
                if attach_idx is not None:
                    repaired[attach_idx] = f"{repaired[attach_idx].rstrip()} {overflow_prefix.lstrip()}"
                else:
                    repaired.append(overflow_prefix)
            if overflow_suffix:
                repaired.append(overflow_suffix)
            repaired.append(current)
            repaired.append(f"{lead.rstrip()} {continuation.lstrip()}")
            idx += 4
            continue
        repaired.append(current)
        idx += 1
    return repaired


def is_image_group_item(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("<!-- PDF_IMAGE") or looks_like_caption(stripped)


def repair_image_group_residual_fragments(parts: list[str]) -> list[str]:
    repaired: list[str] = []
    idx = 0
    while idx < len(parts):
        current = parts[idx]
        if (
            repaired
            and is_plain_text_block(repaired[-1])
            and is_image_group_item(current)
        ):
            group = [current]
            idx += 1
            while idx < len(parts) and is_image_group_item(parts[idx]):
                group.append(parts[idx])
                idx += 1
            if idx < len(parts):
                following = parts[idx]
                if is_plain_text_block(following) and should_rejoin_residual_paragraphs(repaired[-1], following):
                    repaired[-1] = f"{repaired[-1].rstrip()} {following.lstrip()}"
                    repaired.extend(group)
                    idx += 1
                    continue
            repaired.extend(group)
            continue
        repaired.append(current)
        idx += 1
    return repaired


def split_scientific_overmerged_paragraph(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped or not is_plain_text_block(stripped):
        return [stripped]
    if len(stripped) < 420:
        return [stripped]

    sentences = [segment.strip() for segment in re.split(r"(?<=[.!?])\s+(?=[A-Z])", stripped) if segment.strip()]
    if len(sentences) < 3:
        return [stripped]

    chunks: list[str] = []
    current = sentences[0]
    for sentence in sentences[1:]:
        if (
            len(current) >= 220
            and starts_with_scientific_paragraph_cue(sentence)
            and not current.rstrip().endswith(":")
        ):
            chunks.append(current.strip())
            current = sentence
            continue
        current = f"{current.rstrip()} {sentence.lstrip()}"
    chunks.append(current.strip())

    cleaned = [chunk for chunk in chunks if chunk]
    if len(cleaned) <= 1:
        return [stripped]
    if any(len(chunk) < 80 for chunk in cleaned):
        return [stripped]
    return cleaned


def split_overmerged_scientific_paragraphs(parts: list[str]) -> list[str]:
    rewritten: list[str] = []
    for item in parts:
        if item.startswith("#") or item.startswith("<!--") or looks_like_caption(item):
            rewritten.append(item)
            continue
        rewritten.extend(split_scientific_overmerged_paragraph(item))
    return rewritten


def is_procedural_section_title(title: str) -> bool:
    stripped = title.strip().upper()
    if not stripped.startswith("#"):
        return False
    return any(keyword in stripped for keyword in PROCEDURAL_SECTION_KEYWORDS)


def looks_procedural_text(text: str) -> bool:
    compact = " ".join(text.split())
    if not compact:
        return False
    procedural_patterns = (
        r"\b\d+(?:\.\d+)?\s*(?:mmol|mol|mL|uL|渭L|mg|g|rpm|min|h|掳C)\b",
        r"\b(in a typical synthesis|was loaded|was injected|was added|was dissolved|was centrifuged|was purified|was dried)\b",
    )
    return any(re.search(pattern, compact, re.IGNORECASE) for pattern in procedural_patterns)


def looks_like_conceptual_overview_text(text: str) -> bool:
    compact = " ".join(text.split())
    if not compact:
        return False
    if looks_procedural_text(compact):
        return False
    if re.search(r"\b(activation energy|Arrhenius|inductive time|reaction time to reach|rpm|min|h|°C|掳C)\b", compact, re.IGNORECASE):
        return False
    conceptual_hits = (
        r"\b(mechanism|symmetry-breaking|symmetry breaking|formation mechanisms|unanswered question|bewildering)\b",
    )
    return any(re.search(pattern, compact, re.IGNORECASE) for pattern in conceptual_hits)


def should_merge_contextual_continuation(
    current: str,
    following: str,
    current_major_heading: str,
    current_minor_heading: str,
    seen_image_in_scope: bool,
) -> bool:
    if not (is_plain_text_block(current) and is_plain_text_block(following)):
        return False
    if is_procedural_section_title(current_major_heading) or is_procedural_section_title(current_minor_heading):
        return False
    if looks_procedural_text(current) or looks_procedural_text(following):
        return False

    next_part = following.strip()
    if (
        not seen_image_in_scope
        and any(next_part.startswith(cue) for cue in CONTEXTUAL_CONTINUATION_CUES_PRE_FIGURE)
        and len(current.strip()) >= 450
    ):
        return True
    if (
        seen_image_in_scope
        and any(next_part.startswith(cue) for cue in CONTEXTUAL_CONTINUATION_CUES_POST_FIGURE)
        and len(current.strip()) >= 320
        and looks_like_conceptual_overview_text(current)
    ):
        return True
    return False


def merge_contextual_continuation_paragraphs(parts: list[str]) -> list[str]:
    merged: list[str] = []
    idx = 0
    current_major_heading = ""
    current_minor_heading = ""
    seen_image_in_scope = False
    while idx < len(parts):
        current = parts[idx]
        stripped = current.strip()
        if stripped.startswith("## "):
            current_major_heading = stripped
            current_minor_heading = stripped
            seen_image_in_scope = False
            merged.append(current)
            idx += 1
            continue
        if stripped.startswith("### "):
            current_minor_heading = stripped
            seen_image_in_scope = False
            merged.append(current)
            idx += 1
            continue
        if stripped.startswith("<!-- PDF_IMAGE"):
            seen_image_in_scope = True

        if idx + 1 < len(parts):
            if should_merge_contextual_continuation(
                current,
                parts[idx + 1],
                current_major_heading,
                current_minor_heading,
                seen_image_in_scope,
            ):
                merged.append(f"{current.rstrip()} {parts[idx + 1].lstrip()}")
                idx += 2
                continue

        merged.append(current)
        idx += 1
    return merged


def merge_short_lead_in_paragraphs(parts: list[str]) -> list[str]:
    merged: list[str] = []
    idx = 0
    current_major_heading = ""
    current_minor_heading = ""
    while idx < len(parts):
        current = parts[idx]
        stripped = current.strip()
        if stripped.startswith("## "):
            current_major_heading = stripped
            current_minor_heading = stripped
            merged.append(current)
            idx += 1
            continue
        if stripped.startswith("### "):
            current_minor_heading = stripped
            merged.append(current)
            idx += 1
            continue
        if idx + 1 < len(parts):
            following = parts[idx + 1].strip()
            if (
                is_plain_text_block(current)
                and is_plain_text_block(following)
                and not is_procedural_section_title(current_major_heading)
                and not is_procedural_section_title(current_minor_heading)
                and len(stripped) <= 110
                and len(re.findall(r"(?<=[.!?])\s+", stripped)) <= 1
                and len(following) >= 400
                and starts_with_scientific_paragraph_cue(following)
            ):
                merged.append(f"{current.rstrip()} {parts[idx + 1].lstrip()}")
                idx += 2
                continue
        merged.append(current)
        idx += 1
    return merged


def merge_followup_observation_paragraphs(parts: list[str]) -> list[str]:
    merged: list[str] = []
    idx = 0
    while idx < len(parts):
        current = parts[idx]
        if idx + 1 < len(parts):
            following = parts[idx + 1]
            if (
                is_plain_text_block(current)
                and is_plain_text_block(following)
                and re.search(r"\bTo confirm existence of such special intermediates\b", current, re.IGNORECASE)
                and following.strip().startswith("Interestingly, for the ")
            ):
                merged.append(f"{current.rstrip()} {following.lstrip()}")
                idx += 2
                continue
        merged.append(current)
        idx += 1
    return merged


def merge_background_result_followups(parts: list[str]) -> list[str]:
    merged: list[str] = []
    idx = 0
    while idx < len(parts):
        current = parts[idx]
        if idx + 1 < len(parts):
            following = parts[idx + 1]
            compact_current = " ".join(current.split())
            compact_following = " ".join(following.split())
            if (
                is_plain_text_block(current)
                and is_plain_text_block(following)
                and len(compact_current) <= 420
                and len(compact_following) <= 520
                and re.search(
                    r"\b(It was reported that|It was suggested that|Previous studies|were tested in the reactions)\b",
                    compact_current,
                    re.IGNORECASE,
                )
                and re.match(r"^(Results in Figure|The results in Figure|Results from Figure)\b", compact_following)
            ):
                merged.append(f"{current.rstrip()} {following.lstrip()}")
                idx += 2
                continue
        merged.append(current)
        idx += 1
    return merged


def split_supporting_inference_paragraphs(parts: list[str]) -> list[str]:
    rewritten: list[str] = []
    for item in parts:
        stripped = item.strip()
        if (
            is_plain_text_block(stripped)
            and "Results in Figure S" in stripped
            and ". Results discussed above implied" in stripped
        ):
            split_pair = re.split(r"(?<=\.)\s+(?=Results discussed above implied\b)", stripped, maxsplit=1)
            if len(split_pair) == 2:
                rewritten.append(split_pair[0].strip())
                rewritten.append(split_pair[1].strip())
                continue
        rewritten.append(item)
    return rewritten


def repair_supporting_figure_validation_paragraphs(parts: list[str]) -> list[str]:
    repaired: list[str] = []
    for item in parts:
        current = item.strip()
        if (
            repaired
            and is_plain_text_block(repaired[-1])
            and is_plain_text_block(current)
            and re.search(r"\bTo confirm this (hypothesis|possibility|assignment|interpretation)\b", repaired[-1], re.IGNORECASE)
            and re.match(r"^Results in Figure S\d+[A-Za-z]?\b", current)
        ):
            split_pair = re.split(r"(?<=\.)\s+(?=Results discussed above implied\b)", current, maxsplit=1)
            if len(split_pair) == 2:
                repaired[-1] = f"{repaired[-1].rstrip()} {split_pair[0].lstrip()}"
                repaired.append(split_pair[1].strip())
                continue
        repaired.append(item)
    return repaired


def promote_page_one_abstract_image(ordered: list[Block]) -> list[Block]:
    intro_index = next(
        (
            idx
            for idx, block in enumerate(ordered)
            if block.kind == "text" and block.text.strip() == "## INTRODUCTION"
        ),
        None,
    )
    if intro_index is None:
        return ordered
    abstract_images = [
        idx
        for idx, block in enumerate(ordered)
        if block.kind == "image" and block.page_num == 1 and block.y0 < 360 and idx > intro_index
    ]
    if not abstract_images:
        return ordered
    image_index = abstract_images[0]
    image_block = ordered.pop(image_index)
    ordered.insert(intro_index, image_block)
    return ordered


def postprocess_markdown(text: str) -> str:
    text = normalize_section_markers(text)
    text = re.sub(r"(?m)^pubs\.acs\.org/JACS\s*$", "", text)
    text = re.sub(r"(?m)^Journal of the American Chemical Society\s*$", "", text)
    text = re.sub(r"(?m)^Article\s*$", "", text)
    text = re.sub(r"(?m)^DOI:\s*10\.\S+.*$", "", text)
    text = re.sub(r"(?m)^J\. Am\. Chem\. Soc\..*$", "", text)
    text = re.sub(r"\s+DOI:\s*10\.\S+\s+J\.\s*Am\.\s*Chem\.\s*Soc\.\s*2017,\s*139,\s*10009-10019\s+\d{5}", "", text)
    text = re.sub(r"\s+J\.\s*Am\.\s*Chem\.\s*Soc\.\s*2017,\s*139,\s*10009-10019\s+\d{5}", "", text)
    text = re.sub(r"(?m)^\d{5}\s*$", "", text)
    text = re.sub(r"(?m)^Design of Experimental System\.\s+", "### Design of Experimental System\n\n", text)
    text = re.sub(
        r"(?m)^Different Roles of Cadmium Acetate and Cadmium Stearate\.\s+",
        "### Different Roles of Cadmium Acetate and Cadmium Stearate\n\n",
        text,
    )
    text = re.sub(
        r"(?m)^Conversion Process from 0D Seeds to 2D Nanocrystals\.\s+",
        "### Conversion Process from 0D Seeds to 2D Nanocrystals\n\n",
        text,
    )
    text = re.sub(r"(?m)^Characterization of 2D Embryos\.\s+", "### Characterization of 2D Embryos\n\n", text)
    text = re.sub(
        r"(?m)^Formation Mechanism of 2D Nanocrystals from Seeds\.\s+",
        "### Formation Mechanism of 2D Nanocrystals from Seeds\n\n",
        text,
    )
    text = re.sub(r"(?m)^Materials\.\s+", "### Materials\n\n", text)
    text = re.sub(
        r"(?m)^Synthesis of Cadmium Stearate \(Cd\(St\)2\)\.\s+",
        "### Synthesis of Cadmium Stearate (Cd(St)2)\n\n",
        text,
    )
    text = re.sub(
        r"(?m)^Direct Synthesis of 5(?:[.\- ]?5)?-?Monolayer CdSe 2D Nanocrystals\.\s+",
        "### Direct Synthesis of 5.5-Monolayer CdSe 2D Nanocrystals\n\n",
        text,
    )
    text = re.sub(
        r"(?m)^Synthesis of CdSe 2D Nanocrystals Using Purified CdSe Nanocrystal Seeds\.\s+",
        "### Synthesis of CdSe 2D Nanocrystals Using Purified CdSe Nanocrystal Seeds\n\n",
        text,
    )
    text = re.sub(
        r"(?m)^Purification and Separation of CdSe 2D Nanocrystals and 2D Embryos\.\s+",
        "### Purification and Separation of CdSe 2D Nanocrystals and 2D Embryos\n\n",
        text,
    )
    text = re.sub(r"(?m)^Measurements\.\s+", "### Measurements\n\n", text)
    text = re.sub(r"(?m)^### C\s*$", "", text)
    text = re.sub(r"(?m)^### (H2O, 99|Sinopharm Reagents|Acetonitrile \(1|China \(2016YFB0401600\).*)\s*$", "", text)
    text = re.sub(
        r"(?m)^(INTRODUCTION|RESULTS AND DISCUSSION|EXPERIMENTAL SECTION|CONCLUSION|CONCLUSIONS|REFERENCES)\s*$",
        lambda match: f"## {'CONCLUSION' if match.group(1) == 'CONCLUSIONS' else match.group(1)}",
        text,
    )
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\n## ([A-Z][A-Z &-]+)\n([A-Z][a-z])", r"\n## \1\n\n\2", text)
    return finalize_source_reset(text)


def postprocess_raw_markdown(text: str) -> str:
    text = normalize_section_markers(text)
    text = re.sub(r"(?m)^pubs\.acs\.org/JACS\s*$", "", text)
    text = re.sub(r"(?m)^Journal of the American Chemical Society\s*$", "", text)
    text = re.sub(r"(?m)^Article\s*$", "", text)
    text = re.sub(r"(?m)^DOI:\s*10\.\S+.*$", "", text)
    text = re.sub(r"(?m)^J\. Am\. Chem\. Soc\..*$", "", text)
    text = re.sub(
        r"(?m)^(INTRODUCTION|RESULTS AND DISCUSSION|EXPERIMENTAL SECTION|CONCLUSION|CONCLUSIONS|REFERENCES)\s*$",
        lambda match: f"## {'CONCLUSION' if match.group(1) == 'CONCLUSIONS' else match.group(1)}",
        text,
    )
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def emit_raw_block_text(block: Block) -> list[str]:
    if block.kind == "image":
        if not should_emit_image(block):
            return []
        x0, y0, x1, y1 = block.bbox
        return [f"<!-- PDF_IMAGE page={block.page_num} bbox={x0:.1f},{y0:.1f},{x1:.1f},{y1:.1f} -->"]
    clean_text, embedded_heading = extract_embedded_section_marker(block.text)
    stripped_clean = clean_text.strip() if clean_text else ""
    items: list[str] = []
    if stripped_clean:
        if stripped_clean in SECTION_MARKERS:
            items.append(f"## {stripped_clean}")
        else:
            items.append(clean_text)
    if embedded_heading:
        items.append(embedded_heading)
    return [item for item in items if item]


def build_source_reset_from_parts(parts: list[str]) -> str:
    reset_parts = smooth_image_interrupted_paragraphs(parts)
    reset_parts = split_inline_subheadings_in_parts(reset_parts)
    reset_parts = repair_heading_boundary_overflow(reset_parts)
    reset_parts = merge_adjacent_text_parts(reset_parts)
    reset_parts = merge_caption_split_paragraphs(reset_parts)
    reset_parts = merge_adjacent_text_parts(reset_parts)
    reset_parts = split_overmerged_scientific_paragraphs(reset_parts)
    reset_parts = merge_contextual_continuation_paragraphs(reset_parts)
    reset_parts = merge_short_lead_in_paragraphs(reset_parts)
    reset_parts = merge_followup_observation_paragraphs(reset_parts)
    reset_parts = merge_background_result_followups(reset_parts)
    reset_parts = split_supporting_inference_paragraphs(reset_parts)
    reset_parts = repair_supporting_figure_validation_paragraphs(reset_parts)
    reset_parts = repair_image_group_residual_fragments(reset_parts)
    text = postprocess_markdown("\n\n".join(reset_parts))
    text = repair_residual_paragraph_breaks(text)
    text = repair_late_commentary_boundaries(text)
    return repair_experimental_section_artifacts(text)


def figure_table_token_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    patterns = {
        "Figure": r"\bFigure\s+S?\d+[A-Za-z]?\b",
        "Table": r"\bTable\s+S?\d+[A-Za-z]?\b",
        "Scheme": r"\bScheme\s+S?\d+[A-Za-z]?\b",
        "Eq": r"\bEq\.\s*\(?S?\d+[A-Za-z]?\)?\b",
    }
    for label, pattern in patterns.items():
        counts[label] = len(re.findall(pattern, text))
    return counts


def normalized_text_key(text: str) -> str:
    compact = " ".join(text.split())
    compact = compact.replace("–", "-").replace("—", "-")
    return compact.strip()


def build_missing_content_report(
    pdf_path: Path,
    source_md: str,
    source_reset_md: str,
    layout_blocks: list[LayoutBlockRecord],
    page_stats: list[PageStats],
    warnings: list[str],
) -> str:
    kept_text = [block for block in layout_blocks if block.kept and block.kind == "text"]
    filtered_text = [block for block in layout_blocks if (not block.kept) and block.kind == "text"]
    source_parts = [part.strip() for part in source_md.split("\n\n") if part.strip()]
    reset_parts = [part.strip() for part in source_reset_md.split("\n\n") if part.strip()]
    reset_keys = Counter(normalized_text_key(part) for part in reset_parts)
    source_reset_compact = normalized_text_key(source_reset_md)
    removed_in_reset: list[str] = []
    for part in source_parts:
        key = normalized_text_key(part)
        if key.startswith(("#", "<!--")):
            continue
        if not reset_keys.get(key) and key not in source_reset_compact:
            removed_in_reset.append(part)
    page_details: dict[int, dict[str, int]] = {}
    for record in layout_blocks:
        detail = page_details.setdefault(
            record.page_num,
            {
                "raw_blocks": 0,
                "accepted_blocks": 0,
                "filtered_blocks": 0,
                "raw_chars": 0,
                "accepted_chars": 0,
                "filtered_chars": 0,
                "accepted_captions": 0,
                "filtered_captions": 0,
            },
        )
        if record.kind != "text":
            continue
        chars = len(record.text)
        detail["raw_blocks"] += 1
        detail["raw_chars"] += chars
        if record.kept:
            detail["accepted_blocks"] += 1
            detail["accepted_chars"] += chars
            if looks_like_caption(record.text):
                detail["accepted_captions"] += 1
        else:
            detail["filtered_blocks"] += 1
            detail["filtered_chars"] += chars
            if looks_like_caption(record.text):
                detail["filtered_captions"] += 1
    source_counts = figure_table_token_counts(source_md)
    reset_counts = figure_table_token_counts(source_reset_md)
    lines = [
        f"# Missing Content Report: {pdf_path.name}",
        "",
        "## Summary",
        f"- source.md chars: {len(source_md)}",
        f"- source_reset.md chars: {len(source_reset_md)}",
        f"- accepted text blocks: {len(kept_text)}",
        f"- filtered text blocks: {len(filtered_text)}",
        f"- image placeholders in source.md: {source_md.count('<!-- PDF_IMAGE')}",
        "",
        "## Figure/Table Token Counts",
        f"- source.md: Figure={source_counts['Figure']}, Table={source_counts['Table']}, Scheme={source_counts['Scheme']}, Eq={source_counts['Eq']}",
        f"- source_reset.md: Figure={reset_counts['Figure']}, Table={reset_counts['Table']}, Scheme={reset_counts['Scheme']}, Eq={reset_counts['Eq']}",
        "",
        "## Page Coverage",
    ]
    for stat in page_stats:
        detail = page_details.get(stat.page_num, {})
        raw_chars = detail.get("raw_chars", 0)
        accepted_chars = detail.get("accepted_chars", 0)
        suspicious_reasons: list[str] = []
        if raw_chars and accepted_chars / raw_chars < 0.55:
            suspicious_reasons.append("low accepted-char ratio")
        if detail.get("filtered_blocks", 0) > detail.get("accepted_blocks", 0):
            suspicious_reasons.append("more filtered than accepted blocks")
        if stat.warnings:
            suspicious_reasons.append("page warnings")
        lines.append(
            f"- page {stat.page_num}: mode={stat.mode}, raw_blocks={detail.get('raw_blocks', 0)}, accepted_blocks={detail.get('accepted_blocks', 0)}, "
            f"filtered_blocks={detail.get('filtered_blocks', 0)}, raw_chars={raw_chars}, accepted_chars={accepted_chars}, "
            f"filtered_chars={detail.get('filtered_chars', 0)}, accepted_captions={detail.get('accepted_captions', 0)}, "
            f"filtered_captions={detail.get('filtered_captions', 0)}, image_blocks={stat.image_blocks}, "
            f"warnings={'; '.join(stat.warnings) if stat.warnings else 'none'}, suspicious={'; '.join(suspicious_reasons) if suspicious_reasons else 'no'}"
        )
    if warnings:
        lines.extend(["", "## Extraction Warnings"])
        lines.extend(f"- {warning}" for warning in warnings)
    suspicious_pages = [
        stat.page_num
        for stat in page_stats
        if stat.warnings
        or (
            page_details.get(stat.page_num, {}).get("raw_chars", 0)
            and page_details.get(stat.page_num, {}).get("accepted_chars", 0)
            / page_details.get(stat.page_num, {}).get("raw_chars", 1)
            < 0.55
        )
        or page_details.get(stat.page_num, {}).get("filtered_blocks", 0) > page_details.get(stat.page_num, {}).get("accepted_blocks", 0)
    ]
    lines.extend(["", "## Suspicious Pages"])
    if suspicious_pages:
        lines.extend(f"- page {page_num}" for page_num in suspicious_pages)
    else:
        lines.append("- none")
    if filtered_text:
        lines.extend(["", "## Filtered Blocks"])
        for block in filtered_text[:80]:
            snippet = truncate(block.text, 180)
            lines.append(
                f"- page {block.page_num} [{block.reason}] bbox={tuple(round(v,1) for v in block.bbox)}: {snippet}"
            )
    lines.extend(["", "## Source Blocks Removed In Reset"])
    if removed_in_reset:
        for part in removed_in_reset[:40]:
            lines.append(f"- {truncate(part, 220)}")
    else:
        lines.append("- none")
    suspicious = []
    for token in ("ASSOCIATED CONTENT", "AUTHOR INFORMATION", "ORCID", "ACKNOWLEDGMENTS"):
        if token in source_reset_md:
            suspicious.append(token)
    if suspicious:
        lines.extend(["", "## Suspicious Tail Content Still Present"])
        lines.extend(f"- {token}" for token in suspicious)
    return "\n".join(lines).rstrip() + "\n"


def validate_source_markdown(text: str, stats: list[PageStats]) -> list[str]:
    warnings: list[str] = []
    upper = text.upper()
    for marker in ("INTRODUCTION", "RESULTS AND DISCUSSION", "REFERENCES"):
        if marker not in upper:
            warnings.append(f"missing section marker: {marker}")
    if "EXPERIMENTAL SECTION" in upper and "REFERENCES" in upper and upper.index("EXPERIMENTAL SECTION") > upper.index("REFERENCES"):
        warnings.append("Experimental Section appears after References")
    if "<!-- PDF_IMAGE" not in text:
        warnings.append("no image placeholders detected")
    if sum(len(block_warning) for block_warning in (page.warnings for page in stats)) > 0:
        warnings.append("page-level coverage warnings present")
    return warnings


def truncate(text: str, limit: int = 120) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def try_markitdown_fallback(pdf_path: Path) -> str | None:
    try:
        from markitdown import MarkItDown
    except Exception:
        return None
    try:
        result = MarkItDown().convert(str(pdf_path))
    except Exception:
        return None
    return normalize_text(getattr(result, "text_content", "") or "")


def extract_extraction_artifacts(pdf_path: Path, extractor_mode: ExtractorMode = "auto") -> ExtractionArtifacts:
    doc = fitz.open(pdf_path)
    raw_parts: list[str] = []
    reset_parts: list[str] = []
    stats: list[PageStats] = []
    pending_heading: str | None = None
    allow_inline_subheadings = False
    layout_blocks: list[LayoutBlockRecord] = []

    for page in doc:
        blocks, page_stats, page_records = collect_page_blocks(page, extractor_mode)
        stats.append(page_stats)
        layout_blocks.extend(page_records)
        if page_stats.mode == "single":
            ordered = order_page_blocks(blocks, page_stats.mode)
        else:
            column_mid = infer_column_mid_from_blocks(blocks, page.rect.width, page.rect.height)
            span_groups = group_adjacent([block for block in blocks if block.lane == "span"])
            ordered = []
            emitted_ids: set[int] = set()
            current_top = min((block.y0 for block in blocks), default=0.0)
            for group in span_groups:
                group_min = min(block.y0 for block in group)
                group_max = max(block.y1 for block in group)
                band_bottom = min(block.y0 for block in group) - 2
                pre_band = extract_column_band(page, blocks, current_top, band_bottom, column_mid)
                ordered.extend(pre_band)
                emitted_ids.update(id(block) for block in pre_band)

                ordered.extend(sorted(group, key=lambda block: (block.y0, block.x0)))
                emitted_ids.update(id(block) for block in group)

                overlapping_side_items = [
                    block
                    for block in blocks
                    if id(block) not in emitted_ids
                    and block.lane in {"left", "right"}
                    and block.kind == "image"
                    and block.y0 < group_max
                    and block.y1 > group_min
                ]
                if overlapping_side_items:
                    ordered.extend(sorted(overlapping_side_items, key=lambda block: (block.y0, block.x0)))
                    emitted_ids.update(id(block) for block in overlapping_side_items)
                current_top = max(block.y1 for block in group) + 2
            page_bottom = max((block.y1 for block in blocks), default=page.rect.height)
            trailing = extract_column_band(page, blocks, current_top, page_bottom, column_mid)
            ordered.extend([block for block in trailing if id(block) not in emitted_ids])

        if page.number == 0:
            ordered = promote_page_one_abstract_image(ordered)

        for block in ordered:
            raw_parts.extend(emit_raw_block_text(block))
            if block.kind == "image":
                if should_emit_image(block):
                    x0, y0, x1, y1 = block.bbox
                    reset_parts.append(f"<!-- PDF_IMAGE page={block.page_num} bbox={x0:.1f},{y0:.1f},{x1:.1f},{y1:.1f} -->")
                continue
            if block.text:
                clean_text, embedded_heading = extract_embedded_section_marker(block.text)
                stripped_clean = clean_text.strip() if clean_text else ""
                if stripped_clean in SECTION_MARKERS:
                    emitted_items = [f"## {stripped_clean}"]
                else:
                    emitted_items = split_inline_subheading(clean_text) if clean_text and allow_inline_subheadings else ([clean_text] if clean_text else [])
                for item in emitted_items:
                    if pending_heading and item and not item.startswith("#"):
                        reset_parts.append(pending_heading)
                        pending_heading = None
                    if item and reset_parts and should_merge_emitted_text(reset_parts[-1], item):
                        reset_parts[-1] = f"{reset_parts[-1].rstrip()} {item.lstrip()}"
                    else:
                        if item:
                            reset_parts.append(item)
                            if item in {
                                "## RESULTS AND DISCUSSION",
                                "## EXPERIMENTAL SECTION",
                                "## CONCLUSION",
                                "## REFERENCES",
                                "RESULTS AND DISCUSSION",
                                "EXPERIMENTAL SECTION",
                                "CONCLUSION",
                                "CONCLUSIONS",
                                "REFERENCES",
                            }:
                                allow_inline_subheadings = True
                if embedded_heading:
                    pending_heading = embedded_heading

    if pending_heading:
        reset_parts.append(pending_heading)

    source_md = postprocess_raw_markdown("\n\n".join(raw_parts))
    source_reset_md = build_source_reset_from_parts(reset_parts)
    warnings = validate_source_markdown(source_reset_md, stats)

    if len(source_reset_md) < 3_000:
        fallback = try_markitdown_fallback(pdf_path)
        if fallback and len(fallback) > len(source_reset_md) * 1.2:
            warnings.append("used markitdown fallback because structured extraction looked too short")
            source_md = fallback.strip() + "\n"
            source_reset_md = fallback.strip() + "\n"

    return ExtractionArtifacts(
        source_md=source_md,
        source_reset_md=source_reset_md,
        page_stats=stats,
        warnings=warnings,
        layout_blocks=layout_blocks,
    )


def extract_source_markdown(pdf_path: Path, extractor_mode: ExtractorMode = "auto") -> tuple[str, list[PageStats], list[str]]:
    artifacts = extract_extraction_artifacts(pdf_path, extractor_mode=extractor_mode)
    return artifacts.source_reset_md, artifacts.page_stats, artifacts.warnings


def escape_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


PDF_SAFE_CHAR_REPLACEMENTS = (
    ("\u223c", "~"),
    ("\u2248", "~"),
    ("\u2264", "<="),
    ("\u2265", ">="),
    ("\u00b1", "+/-"),
    ("\u00b7", "."),
    ("\u00b5", "u"),
    ("\u03bc", "u"),
    ("\u2212", "-"),
    ("\u2010", "-"),
    ("\u2011", "-"),
    ("\u2012", "-"),
    ("\u2013", "-"),
    ("\u2014", "-"),
    ("\u00a0", " "),
    ("\u202f", " "),
    ("\u2009", " "),
    ("\u200a", " "),
    ("\u200b", ""),
    ("\ufeff", ""),
)


def sanitize_for_pdf_text(text: str) -> str:
    for old, new in PDF_SAFE_CHAR_REPLACEMENTS:
        text = text.replace(old, new)
    degree_c = "\u00b0C"
    micro_l = "\u03bcL"
    text = text.replace("??", degree_c)
    text = text.replace("?C", degree_c)
    text = re.sub(r"\u00b0\s+C\b", degree_c, text)
    text = re.sub(r"(?<=\d)\s+(?=\u00b0C\b)", "", text)
    text = re.sub(
        rf"(?<=\d)\s+(?=(?:rpm|min\.?|mins\.?|h|hr|hrs|s|sec|nm|mm|cm|mL|{micro_l}|uL|mg|g|kg|wt%|%)\b)",
        "",
        text,
    )
    return text

def inline_format(text: str) -> str:
    text = sanitize_for_pdf_text(text)
    text = escape_text(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text


ASCII_SPAN_RE = re.compile(
    r"([(\[\{,;:]?[A-Za-z0-9\u2020\u2021\u00a7*\u00b0][A-Za-z0-9\u2020\u2021\u00a7*\u00b0(){}\[\]/.,:%;+_=~'\"*&-]*[A-Za-z0-9\u2020\u2021\u00a7*\u00b0)][)\]\},;:.]?|[A-Za-z0-9\u2020\u2021\u00a7*\u00b0])"
)
HTML_TOKEN_RE = re.compile(r"(<[^>]+?>|&[A-Za-z0-9#]+;)")


def clean_frontmatter_note_text(text: str, block_meta: dict[str, str] | None = None) -> str:
    if not block_meta or block_meta.get("type") != "frontmatter_note":
        return text
    stripped = text.strip()
    if re.search(r"\bSupporting Information\b", stripped, re.IGNORECASE):
        return re.sub(
            r"^[*†‡§\u25a1\u25a0\s]*S?\s*(?=Supporting Information\b)",
            "",
            stripped,
            flags=re.IGNORECASE,
        )
    return text


def apply_mixed_font_markup(text: str, style: ParagraphStyle, font_names: dict[str, str]) -> str:
    formatted = inline_format(text)
    if style.fontName not in {font_names["zh"], font_names["zh_bold"]}:
        return formatted

    english_font = font_names["en_bold"] if style.fontName == font_names["zh_bold"] else font_names["en"]
    text_color = getattr(style, "textColor", None)
    color_attr = ""
    if text_color is not None:
        color_attr = ' color="#%02X%02X%02X"' % (
            int(round(text_color.red * 255)),
            int(round(text_color.green * 255)),
            int(round(text_color.blue * 255)),
        )

    def repl(match: re.Match[str]) -> str:
        chunk = match.group(1)
        return f'<font name="{english_font}"{color_attr}>{chunk}</font>'

    pieces: list[str] = []
    last = 0
    for token in HTML_TOKEN_RE.finditer(formatted):
        if token.start() > last:
            pieces.append(ASCII_SPAN_RE.sub(repl, formatted[last:token.start()]))
        pieces.append(token.group(1))
        last = token.end()
    if last < len(formatted):
        pieces.append(ASCII_SPAN_RE.sub(repl, formatted[last:]))
    return "".join(pieces)


def looks_like_equation(text: str) -> bool:
    if len(text) > 180 or "|" in text:
        return False
    if CAPTION_RE.match(text):
        return False
    symbol_score = sum(
        token in text
        for token in (" = ", "≈", "∼", "±", "·", "×", "→", "←", "⇌", "λ", "Δ", "Σ", "α", "β", "γ", "(", ")", "[", "]", "/")
    )
    has_math_pattern = bool(re.search(r"[A-Za-z]\s*=\s*|^\(?\d+\)?\s*[A-Za-z]|[A-Za-z0-9]\s*/\s*[A-Za-z0-9]", text))
    return symbol_score >= 3 and has_math_pattern


def parse_markdown_table(lines: list[str]) -> list[list[str]] | None:
    rows: list[list[str]] = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped or "|" not in stripped:
            return None
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        if any(cells):
            rows.append(cells)
    if len(rows) < 2:
        return None
    width = max(len(row) for row in rows)
    return [row + [""] * (width - len(row)) for row in rows]


def draw_page_background(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFillColor(PAPER_BG)
    canvas.rect(0, 0, doc.pagesize[0], doc.pagesize[1], fill=1, stroke=0)
    canvas.restoreState()


def register_first_available(candidates: tuple[tuple[str, str], ...], fallback: str) -> str:
    for font_name, font_path in candidates:
        try:
            if font_name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(font_name, font_path))
            return font_name
        except Exception:
            continue
    return fallback


def register_fonts() -> dict[str, str]:
    return {
        "zh": register_first_available(ZH_FONT_CANDIDATES, "Helvetica"),
        "zh_bold": register_first_available(ZH_BOLD_FONT_CANDIDATES, "Helvetica-Bold"),
        "en": register_first_available(EN_FONT_CANDIDATES, "Times-Roman"),
        "en_bold": register_first_available(EN_BOLD_FONT_CANDIDATES, "Times-Bold"),
        "mono": MONO_FONT_NAME,
    }


def cjk_char_count(text: str) -> int:
    return sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")


def ascii_letter_count(text: str) -> int:
    return sum(1 for ch in text if ch.isascii() and ch.isalpha())


def looks_like_garbled_duplicate_block(text: str) -> bool:
    compact = text.strip()
    if not compact:
        return False
    nul_count = compact.count("\x00")
    cjk_count = cjk_char_count(compact)
    ascii_count = ascii_letter_count(compact)
    if nul_count >= 2:
        return True
    if cjk_count >= 20 and ascii_count < max(4, int(cjk_count * 0.15)):
        return True
    return False


def paragraph_style(name: str, parent, font_name: str, size: float, leading: float, **kwargs):
    return ParagraphStyle(name, parent=parent, fontName=font_name, fontSize=size, leading=leading, **kwargs)


def image_render_profile(clip: fitz.Rect, page_rect: fitz.Rect, max_width: float) -> tuple[float, float]:
    width_ratio = clip.width / max(page_rect.width, 1.0)
    height_ratio = clip.height / max(page_rect.height, 1.0)
    aspect_ratio = clip.width / max(clip.height, 1.0)

    is_wide_figure = width_ratio >= 0.58 or aspect_ratio >= 1.35
    max_display_width = max_width * (IMAGE_MAX_WIDTH_RATIO_WIDE if is_wide_figure else IMAGE_MAX_WIDTH_RATIO_NORMAL)
    display_width = min(max_display_width, clip.width)

    is_small_figure = width_ratio <= 0.34 or height_ratio <= 0.18
    render_scale = IMAGE_RENDER_SCALE_SMALL_FIGURE if is_small_figure else IMAGE_RENDER_SCALE_BASE
    render_scale = max(render_scale, IMAGE_TARGET_MIN_PIXEL_WIDTH / max(clip.width, 1.0))
    render_scale = min(render_scale, IMAGE_MAX_RENDER_SCALE)
    return display_width, render_scale


def render_pdf_image(pdf_doc: fitz.Document, page_num: int, bbox: tuple[float, float, float, float], max_width: float) -> Image:
    page = pdf_doc[page_num - 1]
    clip = fitz.Rect(*bbox)
    display_width, render_scale = image_render_profile(clip, page.rect, max_width)
    pix = page.get_pixmap(matrix=fitz.Matrix(render_scale, render_scale), clip=clip, alpha=False)
    data = pix.tobytes("png")
    reader = ImageReader(io.BytesIO(data))
    width, height = reader.getSize()
    scale = min(1.0, display_width / max(width, 1))
    image = Image(io.BytesIO(data), width=width * scale, height=height * scale)
    image.hAlign = "CENTER"
    return image


def build_story(md_text: str, original_pdf: Path, font_names: dict[str, str]):
    styles = getSampleStyleSheet()
    title_zh = paragraph_style(
        "TitleZH",
        styles["Title"],
        font_names["zh_bold"],
        TYPOGRAPHY["title"]["size"],
        TYPOGRAPHY["title"]["leading"],
        alignment=TA_LEFT,
        textColor=INK_BLUE,
        spaceAfter=TYPOGRAPHY["title"]["space_after"],
        splitLongWords=0,
        wordWrap="CJK",
    )
    h1_zh = paragraph_style(
        "H1ZH",
        styles["Heading1"],
        font_names["zh_bold"],
        TYPOGRAPHY["h1"]["size"],
        TYPOGRAPHY["h1"]["leading"],
        textColor=INK_BLUE,
        spaceBefore=TYPOGRAPHY["h1"]["space_before"],
        spaceAfter=TYPOGRAPHY["h1"]["space_after"],
        splitLongWords=0,
        wordWrap="CJK",
    )
    h1_translation_zh = paragraph_style(
        "H1TranslationZH",
        h1_zh,
        font_names["zh_bold"],
        TYPOGRAPHY["h1"]["size"],
        TYPOGRAPHY["h1"]["leading"],
        textColor=INK_BLUE,
        spaceBefore=0,
        spaceAfter=TYPOGRAPHY["h1"]["space_after"] + TYPOGRAPHY["spacers"]["translation_to_source"],
        splitLongWords=0,
        wordWrap="CJK",
    )
    h2_zh = paragraph_style(
        "H2ZH",
        styles["Heading2"],
        font_names["zh_bold"],
        TYPOGRAPHY["h2"]["size"],
        TYPOGRAPHY["h2"]["leading"],
        textColor=INK_BLUE,
        spaceBefore=TYPOGRAPHY["h2"]["space_before"],
        spaceAfter=TYPOGRAPHY["h2"]["space_after"],
        splitLongWords=0,
        wordWrap="CJK",
    )
    h2_translation_zh = paragraph_style(
        "H2TranslationZH",
        h2_zh,
        font_names["zh_bold"],
        TYPOGRAPHY["h2"]["size"],
        TYPOGRAPHY["h2"]["leading"],
        textColor=INK_BLUE,
        spaceBefore=0,
        spaceAfter=TYPOGRAPHY["h2"]["space_after"] + TYPOGRAPHY["spacers"]["translation_to_source"],
        splitLongWords=0,
        wordWrap="CJK",
    )
    body_zh = paragraph_style(
        "BodyZH",
        styles["BodyText"],
        font_names["zh"],
        TYPOGRAPHY["body_zh"]["size"],
        TYPOGRAPHY["body_zh"]["leading"],
        textColor=WARM_TEXT,
        spaceAfter=TYPOGRAPHY["body_zh"]["space_after"],
        splitLongWords=0,
        wordWrap="CJK",
    )
    bullet_zh = paragraph_style(
        "BulletZH",
        body_zh,
        font_names["zh"],
        TYPOGRAPHY["bullet_zh"]["size"],
        TYPOGRAPHY["bullet_zh"]["leading"],
        leftIndent=14,
        firstLineIndent=-8,
        splitLongWords=0,
        wordWrap="CJK",
    )
    caption_zh = paragraph_style(
        "CaptionZH",
        body_zh,
        font_names["zh_bold"],
        TYPOGRAPHY["caption_zh"]["size"],
        TYPOGRAPHY["caption_zh"]["leading"],
        textColor=WARM_SUBTEXT,
        leftIndent=4,
        rightIndent=2,
        spaceBefore=TYPOGRAPHY["caption_zh"]["space_before"],
        spaceAfter=TYPOGRAPHY["caption_zh"]["space_after"],
        splitLongWords=0,
        wordWrap="CJK",
    )
    caption_pair_lead = paragraph_style(
        "CaptionPairLead",
        caption_zh,
        caption_zh.fontName,
        caption_zh.fontSize,
        caption_zh.leading,
        textColor=caption_zh.textColor,
        leftIndent=caption_zh.leftIndent,
        rightIndent=caption_zh.rightIndent,
        spaceBefore=caption_zh.spaceBefore,
        spaceAfter=0.6,
        splitLongWords=0,
        wordWrap="CJK",
    )
    caption_pair_follow = paragraph_style(
        "CaptionPairFollow",
        caption_zh,
        caption_zh.fontName,
        caption_zh.fontSize,
        caption_zh.leading,
        textColor=caption_zh.textColor,
        leftIndent=caption_zh.leftIndent,
        rightIndent=caption_zh.rightIndent,
        spaceBefore=0.0,
        spaceAfter=caption_zh.spaceAfter,
        splitLongWords=0,
        wordWrap="CJK",
    )
    equation_zh = paragraph_style(
        "EquationZH",
        body_zh,
        font_names["zh"],
        TYPOGRAPHY["equation_zh"]["size"],
        TYPOGRAPHY["equation_zh"]["leading"],
        alignment=TA_CENTER,
        spaceBefore=TYPOGRAPHY["equation_zh"]["space_before"],
        spaceAfter=TYPOGRAPHY["equation_zh"]["space_after"],
        splitLongWords=0,
        wordWrap="CJK",
    )
    code = paragraph_style(
        "CodeZH",
        styles["Code"],
        font_names["mono"],
        TYPOGRAPHY["code"]["size"],
        TYPOGRAPHY["code"]["leading"],
        textColor=WARM_TEXT,
        leftIndent=8,
        rightIndent=8,
        spaceBefore=TYPOGRAPHY["code"]["space_before"],
        spaceAfter=TYPOGRAPHY["code"]["space_after"],
    )

    def style_for(role: str, block_meta: dict[str, str] | None = None):
        if role == "title":
            return title_zh
        if role == "h1":
            if block_meta and block_meta.get("role") == "translation":
                return h1_translation_zh
            return h1_zh
        if role == "h2":
            if block_meta and block_meta.get("role") == "translation":
                return h2_translation_zh
            return h2_zh
        if role == "body":
            return body_zh
        if role == "bullet":
            return bullet_zh
        if role == "caption":
            return caption_zh
        if role == "equation":
            return equation_zh
        return body_zh

    def parse_block_marker(text: str) -> dict[str, str] | None:
        match = BLOCK_MARKER_RE.fullmatch(text.strip())
        if not match:
            return None
        return match.groupdict()

    def next_significant_item(lines: list[str], start_index: int) -> tuple[str, str | dict[str, str]] | None:
        for offset in range(start_index, len(lines)):
            stripped = lines[offset].strip()
            if not stripped:
                continue
            marker = parse_block_marker(stripped)
            if marker:
                return ("marker", marker)
            if GENERIC_HTML_COMMENT_RE.fullmatch(stripped):
                continue
            return ("text", stripped)
        return None

    def paragraph_markup(text: str, style: ParagraphStyle) -> str:
        return apply_mixed_font_markup(text, style, font_names)

    story = []
    in_code_block = False
    code_lines: list[str] = []
    table_lines: list[str] = []
    paragraph_lines: list[str] = []
    pdf_doc = fitz.open(original_pdf)
    max_image_width = A4[0] - 32 * mm

    def flush_code() -> None:
        nonlocal code_lines
        if code_lines:
            story.append(Preformatted("\n".join(code_lines), code))
            code_lines = []

    def flush_table() -> None:
        nonlocal table_lines
        if not table_lines:
            return
        rows = parse_markdown_table(table_lines)
        if rows:
            usable_width = A4[0] - 32 * mm
            col_width = usable_width / len(rows[0])
            table_data = [
                [
                    Paragraph(
                        paragraph_markup(
                            cell or " ",
                            style_for("caption" if row_idx == 0 else "body"),
                        ),
                        style_for("caption" if row_idx == 0 else "body"),
                    )
                    for cell in row
                ]
                for row_idx, row in enumerate(rows)
            ]
            table = RLTable(table_data, colWidths=[col_width] * len(rows[0]), repeatRows=1)
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEAD_BG),
                        ("TEXTCOLOR", (0, 0), (-1, 0), WARM_TEXT),
                        ("LINEABOVE", (0, 0), (-1, 0), 0.8, WARM_RULE_STRONG),
                        ("LINEBELOW", (0, 0), (-1, 0), 0.8, WARM_RULE_STRONG),
                        ("LINEBELOW", (0, -1), (-1, -1), 0.6, WARM_RULE),
                        ("INNERGRID", (0, 0), (-1, -1), 0.35, WARM_RULE),
                        ("BOX", (0, 0), (-1, -1), 0.6, WARM_RULE),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 5),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            story.append(table)
            story.append(Spacer(1, TYPOGRAPHY["spacers"]["table_after"]))
        else:
            story.append(Preformatted("\n".join(table_lines), code))
        table_lines = []

    last_emitted_role: str | None = None
    last_emitted_meta: dict[str, str] | None = None

    def emit_paragraph(text: str, block_meta: dict[str, str] | None = None) -> None:
        nonlocal last_emitted_role, last_emitted_meta
        stripped = clean_frontmatter_note_text(text, block_meta).strip()
        if not stripped:
            return
        is_caption_block = bool(block_meta and block_meta.get("type") == "caption")
        if block_meta and block_meta.get("type") == "title":
            style = style_for("title", block_meta)
            title_text = f"**{stripped}**"
            story.append(Paragraph(paragraph_markup(title_text, style), style))
        elif stripped.startswith("# "):
            heading_text = stripped[2:].strip()
            style = style_for("title", block_meta)
            story.append(Paragraph(paragraph_markup(heading_text, style), style))
        elif stripped.startswith("## "):
            heading_text = stripped[3:].strip()
            style = style_for("h1", block_meta)
            if block_meta and block_meta.get("role") == "translation":
                heading_text = f"**{heading_text}**"
            story.append(Paragraph(paragraph_markup(heading_text, style), style))
        elif stripped.startswith("### "):
            heading_text = stripped[4:].strip()
            style = style_for("h2", block_meta)
            if block_meta and block_meta.get("role") == "translation":
                heading_text = f"**{heading_text}**"
            story.append(Paragraph(paragraph_markup(heading_text, style), style))
        elif re.match(r"^[-*]\s+", stripped):
            bullet_text = re.sub(r"^[-*]\s+", "", stripped)
            style = style_for("bullet")
            story.append(Paragraph("\u2022 " + paragraph_markup(bullet_text, style), style))
        elif re.match(r"^\d+[.)]\s+", stripped):
            style = style_for("body")
            story.append(Paragraph(paragraph_markup(stripped, style), style))
        elif is_caption_block or looks_like_caption(stripped):
            if block_meta and block_meta.get("role") == "source":
                style = caption_pair_lead
            elif block_meta and block_meta.get("role") == "translation":
                style = caption_pair_follow
            else:
                style = style_for("caption")
            story.append(Paragraph(paragraph_markup(stripped, style), style))
        elif looks_like_equation(stripped):
            style = style_for("equation")
            story.append(Paragraph(paragraph_markup(stripped, style), style))
        else:
            style = style_for("body")
            story.append(Paragraph(paragraph_markup(reflow_wrapped_text(stripped), style), style))
        last_emitted_role = "caption" if (is_caption_block or looks_like_caption(stripped)) else "text"
        last_emitted_meta = block_meta

    def flush_paragraph(block_meta: dict[str, str] | None = None) -> None:
        nonlocal paragraph_lines
        if not paragraph_lines:
            return
        emit_paragraph("\n".join(paragraph_lines), block_meta=block_meta)
        paragraph_lines = []

    current_block_meta: dict[str, str] | None = None
    lines = md_text.splitlines()
    for index, raw_line in enumerate(lines):
        line = raw_line.rstrip("\n")
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_paragraph()
            flush_table()
            if in_code_block:
                flush_code()
                in_code_block = False
            else:
                in_code_block = True
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        if "|" in stripped and not IMAGE_RE.fullmatch(stripped):
            flush_paragraph(current_block_meta)
            current_block_meta = None
            table_lines.append(stripped)
            continue
        flush_table()

        if not stripped:
            flush_paragraph(current_block_meta)
            current_block_meta = None
            next_item = next_significant_item(lines, index + 1)
            if (
                last_emitted_role == "caption"
                and last_emitted_meta
                and last_emitted_meta.get("type") == "caption"
                and next_item
                and next_item[0] == "marker"
                and isinstance(next_item[1], dict)
                and next_item[1].get("type") == "caption"
                and last_emitted_meta.get("id") == next_item[1].get("id")
            ):
                story.append(Spacer(1, TYPOGRAPHY["spacers"]["caption_pair"]))
            elif last_emitted_role != "caption":
                story.append(Spacer(1, TYPOGRAPHY["spacers"]["blank_paragraph"]))
            continue

        match = IMAGE_RE.fullmatch(stripped)
        if match:
            flush_paragraph(current_block_meta)
            current_block_meta = None
            page_num = int(match.group(1))
            bbox = tuple(float(match.group(i)) for i in range(2, 6))
            story.append(render_pdf_image(pdf_doc, page_num, bbox, max_image_width))
            story.append(Spacer(1, TYPOGRAPHY["spacers"]["image_after"]))
            last_emitted_role = "image"
            last_emitted_meta = current_block_meta
            continue
        marker_meta = parse_block_marker(stripped)
        if marker_meta:
            flush_paragraph(current_block_meta)
            if (
                last_emitted_meta
                and last_emitted_meta.get("role") == "translation"
                and marker_meta.get("role") == "source"
                and last_emitted_role != "caption"
            ):
                story.append(Spacer(1, TYPOGRAPHY["spacers"]["translation_to_source"]))
            current_block_meta = marker_meta
            continue
        if GENERIC_HTML_COMMENT_RE.fullmatch(stripped):
            continue

        if stripped.startswith("# ") or stripped.startswith("## ") or stripped.startswith("### "):
            flush_paragraph(current_block_meta)
            emit_paragraph(stripped, block_meta=current_block_meta)
            current_block_meta = None
        elif re.match(r"^[-*]\s+", stripped) or re.match(r"^\d+[.)]\s+", stripped):
            flush_paragraph(current_block_meta)
            emit_paragraph(stripped, block_meta=current_block_meta)
            current_block_meta = None
        elif (current_block_meta and current_block_meta.get("type") == "caption") or looks_like_caption(stripped):
            flush_paragraph(current_block_meta)
            emit_paragraph(stripped, block_meta=current_block_meta)
            current_block_meta = None
        elif looks_like_equation(stripped):
            flush_paragraph(current_block_meta)
            emit_paragraph(stripped, block_meta=current_block_meta)
            current_block_meta = None
        else:
            paragraph_lines.append(stripped)

    flush_paragraph(current_block_meta)
    flush_table()
    if in_code_block:
        flush_code()
    return story


def ensure_same_folder(pdf_path: Path, source_path: Path, bilingual_path: Path, bilingual_pdf: Path) -> None:
    folder = pdf_path.parent.resolve()
    for candidate in (source_path, bilingual_path, bilingual_pdf):
        if candidate.parent.resolve() != folder:
            raise ValueError(f"Output must stay in the same folder as the PDF: {candidate}")


def paper_debug_dir(pdf_path: Path, debug_root: Path | None = None) -> Path:
    root = debug_root or (Path.cwd() / "_workspace_temp" / "pdf2bilingual_runs")
    slug = pdf_path.stem
    return root / slug


def write_debug_artifacts(
    pdf_path: Path,
    artifacts: ExtractionArtifacts,
    debug_root: Path | None = None,
) -> dict[str, Path]:
    debug_dir = paper_debug_dir(pdf_path, debug_root)
    debug_dir.mkdir(parents=True, exist_ok=True)
    source_blocks_path = debug_dir / "source_blocks.json"
    extraction_report_path = debug_dir / "extraction_report.md"
    missing_report_path = debug_dir / "missing_content_report.md"
    source_blocks_path.write_text(
        json.dumps([asdict(block) for block in artifacts.layout_blocks], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    extraction_lines = [
        f"# Extraction Report: {pdf_path.name}",
        "",
        f"- source.md chars: {len(artifacts.source_md)}",
        f"- source_reset.md chars: {len(artifacts.source_reset_md)}",
        "",
        "## Page Summary",
    ]
    for stat in artifacts.page_stats:
        extraction_lines.append(
            f"- page {stat.page_num}: mode={stat.mode}, text={stat.text_blocks}, image={stat.image_blocks}, "
            f"caption={stat.caption_blocks}, span={stat.span_blocks}, left={stat.left_blocks}, right={stat.right_blocks}"
        )
    if artifacts.warnings:
        extraction_lines.extend(["", "## Warnings"])
        extraction_lines.extend(f"- {warning}" for warning in artifacts.warnings)
    extraction_report_path.write_text("\n".join(extraction_lines).rstrip() + "\n", encoding="utf-8")
    missing_report_path.write_text(
        build_missing_content_report(
            pdf_path,
            artifacts.source_md,
            artifacts.source_reset_md,
            artifacts.layout_blocks,
            artifacts.page_stats,
            artifacts.warnings,
        ),
        encoding="utf-8",
    )
    return {
        "source_blocks": source_blocks_path,
        "extraction_report": extraction_report_path,
        "missing_content_report": missing_report_path,
    }


def extract_source(
    pdf_path: Path,
    source_path: Path,
    source_reset_path: Path,
    extractor_mode: ExtractorMode = "auto",
    force: bool = False,
    debug_root: Path | None = None,
) -> tuple[bool, ExtractionArtifacts | None, dict[str, Path]]:
    upstream_mtime = max(pdf_path.stat().st_mtime, source_path.stat().st_mtime if source_path.exists() else 0)
    if source_reset_path.exists() and not force and source_reset_path.stat().st_mtime >= upstream_mtime:
        return False, None, {}
    artifacts = extract_extraction_artifacts(pdf_path, extractor_mode=extractor_mode)
    source_path.write_text(artifacts.source_md, encoding="utf-8")
    source_reset_path.write_text(artifacts.source_reset_md, encoding="utf-8")
    debug_paths = write_debug_artifacts(pdf_path, artifacts, debug_root=debug_root)
    print(source_path)
    print(source_reset_path)
    return True, artifacts, debug_paths


def render_bilingual_pdf(
    original_pdf: Path,
    source_path: Path,
    md_path: Path,
    pdf_path: Path,
    force: bool = False,
) -> bool:
    if not md_path.exists():
        raise FileNotFoundError(f"Missing bilingual markdown: {md_path}")
    report = validate_bilingual(source_path, md_path)
    if not report.ok:
        message = "; ".join(report.errors[:4])
        raise ValueError(f"Refusing to render stale or invalid bilingual markdown: {message}")
    if pdf_path.exists() and not force and pdf_path.stat().st_mtime >= md_path.stat().st_mtime and pdf_path.stat().st_mtime >= source_path.stat().st_mtime:
        return False

    font_names = register_fonts()
    md_text = md_path.read_text(encoding="utf-8")
    story = build_story(md_text, original_pdf, font_names)
    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=pdf_path.stem,
        author="Codex",
    )
    doc.build(story, onFirstPage=draw_page_background, onLaterPages=draw_page_background)
    print(pdf_path)
    return True


def resolve_paths(
    pdf_path: Path,
    source_arg: str | None,
    source_reset_arg: str | None,
    bilingual_arg: str | None,
    pdf_out_arg: str | None,
):
    folder = pdf_path.parent
    source_path = Path(source_arg).expanduser().resolve() if source_arg else folder / "source.md"
    source_reset_path = Path(source_reset_arg).expanduser().resolve() if source_reset_arg else folder / "source_reset.md"
    bilingual_path = Path(bilingual_arg).expanduser().resolve() if bilingual_arg else folder / "bilingual.md"
    bilingual_pdf = Path(pdf_out_arg).expanduser().resolve() if pdf_out_arg else folder / "bilingual.pdf"
    ensure_same_folder(pdf_path, source_path, bilingual_path, bilingual_pdf)
    if source_reset_path.parent.resolve() != pdf_path.parent.resolve():
        raise ValueError(f"Output must stay in the same folder as the PDF: {source_reset_path}")
    return source_path, source_reset_path, bilingual_path, bilingual_pdf


def print_check_summary(stats: list[PageStats], warnings: list[str]) -> None:
    if not stats:
        return
    print("[INFO] Extraction summary:")
    for page in stats:
        summary = (
            f"page={page.page_num} mode={page.mode} "
            f"text={page.text_blocks} image={page.image_blocks} caption={page.caption_blocks} "
            f"span={page.span_blocks} left={page.left_blocks} right={page.right_blocks}"
        )
        print(summary)
        for warning in page.warnings:
            print(f"  [WARN] {warning}")
    for warning in warnings:
        print(f"[WARN] {warning}")


def print_bilingual_status(source_path: Path, bilingual_path: Path) -> None:
    if not bilingual_path.exists():
        print("[WARN] bilingual.md is missing")
        return
    report = validate_bilingual(source_path, bilingual_path)
    print(
        f"[INFO] bilingual status: ok={str(report.ok).lower()} stale={str(report.stale).lower()} "
        f"blocks_expected={report.blocks_expected} blocks_verified={report.blocks_verified}"
    )
    try:
        image_count = sum(1 for block in split_source_blocks(source_path.read_text(encoding="utf-8")) if block.block_type == "image")
        print(f"[INFO] image placeholders expected={image_count}")
    except Exception:
        pass
    if report.provenance:
        print(
            f"[INFO] bilingual provenance: source_sha256={report.provenance['source_sha256']} "
            f'version={report.provenance["workflow_version"]} generated_at={report.provenance["generated_at"]}'
        )
    else:
        print("[WARN] bilingual provenance metadata is missing")
    for warning in report.warnings:
        print(f"[WARN] {warning}")
    for error in report.errors:
        print(f"[WARN] {error}")


def main() -> int:
    parser = argparse.ArgumentParser(description="PDF bilingual workflow helper")
    parser.add_argument("pdf", help="Target paper PDF")
    parser.add_argument("--source", help="Path to source.md (default: same folder)")
    parser.add_argument("--source-reset", help="Path to source_reset.md (default: same folder)")
    parser.add_argument("--bilingual", help="Path to bilingual.md (default: same folder)")
    parser.add_argument("--pdf-out", help="Path to bilingual.pdf (default: same folder)")
    parser.add_argument("--debug-root", help="Root folder for debug artifacts (default: D:\\9-codex\\_workspace_temp\\pdf2bilingual_runs)")
    parser.add_argument("--refresh-source", action="store_true", help="Force refresh source.md from the PDF")
    parser.add_argument("--render-pdf", action="store_true", help="Force render bilingual.pdf from bilingual.md")
    parser.add_argument("--check-only", action="store_true", help="Only report what would happen")
    parser.add_argument(
        "--extractor-mode",
        choices=("auto", "single", "double", "mixed"),
        default="auto",
        help="Override layout detection for source extraction",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.exists():
        print(f"[ERROR] PDF not found: {pdf_path}", file=sys.stderr)
        return 1

    debug_root = Path(args.debug_root).expanduser().resolve() if args.debug_root else None
    source_path, source_reset_path, bilingual_path, bilingual_pdf = resolve_paths(
        pdf_path,
        args.source,
        args.source_reset,
        args.bilingual,
        args.pdf_out,
    )

    print(f"[INFO] PDF: {pdf_path}")
    print(f"[INFO] source.md: {source_path}")
    print(f"[INFO] source_reset.md: {source_reset_path}")
    print(f"[INFO] bilingual.md: {bilingual_path}")
    print(f"[INFO] bilingual.pdf: {bilingual_pdf}")
    print(f"[INFO] extractor-mode: {args.extractor_mode}")
    if debug_root:
        print(f"[INFO] debug-root: {debug_root}")

    if args.check_only:
        artifacts = extract_extraction_artifacts(pdf_path, extractor_mode=args.extractor_mode)
        print_check_summary(artifacts.page_stats, artifacts.warnings)
        print(f"[INFO] source.md chars={len(artifacts.source_md)} source_reset.md chars={len(artifacts.source_reset_md)}")
        print_bilingual_status(source_reset_path if source_reset_path.exists() else source_path, bilingual_path)
        return 0

    source_written, artifacts, debug_paths = extract_source(
        pdf_path,
        source_path,
        source_reset_path,
        extractor_mode=args.extractor_mode,
        force=args.refresh_source,
        debug_root=debug_root,
    )
    print("[OK] source/source_reset refreshed" if source_written else "[OK] source_reset.md already up to date")
    if source_written and artifacts is not None:
        print_check_summary(artifacts.page_stats, artifacts.warnings)
        for label, path in debug_paths.items():
            print(f"[INFO] {label}: {path}")

    if not bilingual_path.exists():
        print("[WARN] bilingual.md is missing; create it before rendering bilingual.pdf")
        return 0
    print_bilingual_status(source_reset_path if source_reset_path.exists() else source_path, bilingual_path)

    pdf_written = render_bilingual_pdf(
        pdf_path,
        source_reset_path if source_reset_path.exists() else source_path,
        bilingual_path,
        bilingual_pdf,
        force=args.render_pdf,
    )
    print("[OK] bilingual.pdf rendered" if pdf_written else "[OK] bilingual.pdf already up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
