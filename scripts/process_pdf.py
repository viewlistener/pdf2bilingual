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
import re
import sys
from dataclasses import dataclass, field
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

ZH_FONT_CANDIDATES = (
    ("PaperZH", r"C:\Windows\Fonts\STSONG.TTF"),
    ("PaperZH", r"C:\Windows\Fonts\simsun.ttc"),
    ("PaperZH", r"C:\Windows\Fonts\msyh.ttc"),
)
EN_FONT_CANDIDATES = (
    ("PaperEN", r"C:\Windows\Fonts\times.ttf"),
    ("PaperEN", r"C:\Windows\Fonts\georgia.ttf"),
)
MONO_FONT_NAME = "Courier"
IMAGE_RE = re.compile(r"<!--\s*PDF_IMAGE\s+page=(\d+)\s+bbox=([0-9.]+),([0-9.]+),([0-9.]+),([0-9.]+)\s*-->")
SECTION_MARKERS = ("INTRODUCTION", "RESULTS AND DISCUSSION", "EXPERIMENTAL SECTION", "CONCLUSIONS", "REFERENCES")
CAPTION_RE = re.compile(r"^(Figure|Table|Scheme|Fig\.|Eq\.)\s*\d+")

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

PAPER_BG = colors.HexColor("#F5F4ED")
INK_BLUE = colors.HexColor("#1B365D")
WARM_TEXT = colors.HexColor("#3A352E")
WARM_SUBTEXT = colors.HexColor("#666159")
WARM_RULE = colors.HexColor("#E0DCCF")
WARM_RULE_STRONG = colors.HexColor("#CFC8B5")
TABLE_HEAD_BG = colors.HexColor("#EEE9DA")
IMAGE_MAX_WIDTH_RATIO_NORMAL = 0.78
IMAGE_MAX_WIDTH_RATIO_WIDE = 0.90
IMAGE_RENDER_SCALE_BASE = 2.8
IMAGE_RENDER_SCALE_SMALL_FIGURE = 3.4
IMAGE_TARGET_MIN_PIXEL_WIDTH = 1500
IMAGE_MAX_RENDER_SCALE = 4.0
IMAGE_AFTER_SPACER = 4

TYPOGRAPHY = {
    "title": {"size": 18.0, "leading": 22.0, "space_after": 10.0},
    "h1": {"size": 14.5, "leading": 20.0, "space_before": 8.0, "space_after": 6.0},
    "h2": {"size": 12.5, "leading": 17.0, "space_before": 6.0, "space_after": 4.0},
    "body_en": {"size": 10.2, "leading": 15.8, "space_after": 4.2},
    "body_zh": {"size": 10.4, "leading": 17.2, "space_after": 4.8},
    "caption_en": {"size": 8.8, "leading": 13.2, "space_before": 1.2, "space_after": 2.8},
    "caption_zh": {"size": 8.8, "leading": 13.2, "space_before": 1.2, "space_after": 3.0},
    "equation_en": {"size": 9.8, "leading": 15.0, "space_before": 2.0, "space_after": 4.0},
    "equation_zh": {"size": 9.8, "leading": 15.0, "space_before": 2.0, "space_after": 4.2},
    "bullet_en": {"size": 10.2, "leading": 15.8},
    "bullet_zh": {"size": 10.4, "leading": 17.2},
    "code": {"size": 8.6, "leading": 11.0, "space_before": 2.0, "space_after": 4.0},
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


def normalize_text(text: str) -> str:
    text = text.replace("\u00ad", "")
    text = text.replace("\ufb01", "fi").replace("\ufb02", "fl")
    text = text.replace("\u2010", "-").replace("\u2011", "-").replace("\u2013", "-")
    text = text.replace("\u2212", "-")
    text = text.replace("\ue0d5", " - ")
    text = re.sub(r"(?<=\w)-\n(?=\w)", "", text)
    text = re.sub(r"\n+", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return text.strip()


def block_text(block: dict) -> str:
    lines: list[str] = []
    for line in block.get("lines", []):
        spans = [span.get("text", "") for span in line.get("spans", [])]
        lines.append("".join(spans))
    return normalize_text("\n".join(lines))


def is_header_or_footer(text: str, y0: float, y1: float, page_height: float) -> bool:
    if not text:
        return True
    compact = " ".join(text.split())
    if y0 < 70 and any(token in compact for token in HEADER_PATTERNS):
        return True
    if y1 > page_height - 25 and any(token in compact for token in FOOTER_PATTERNS):
        return True
    if any(token in compact for token in NOISE_PATTERNS):
        return True
    if re.fullmatch(r"\d{5}", compact):
        return True
    return False


def looks_like_caption(text: str) -> bool:
    return bool(CAPTION_RE.match(text))


def classify_page_mode(text_blocks: list[Block], page_width: float, override: ExtractorMode) -> str:
    if override != "auto":
        return {"single": "single", "double": "double", "mixed": "mixed"}[override]

    narrow = [block for block in text_blocks if block.width < page_width * 0.62 and len(block.text) > 20]
    left = [block for block in narrow if block.center_x < page_width * 0.47]
    right = [block for block in narrow if block.center_x > page_width * 0.53]
    has_left = len(left) >= 2 and sum(block.height for block in left) > 100
    has_right = len(right) >= 2 and sum(block.height for block in right) > 100
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


def collect_page_blocks(page: fitz.Page, override: ExtractorMode) -> tuple[list[Block], PageStats]:
    data = page.get_text("dict")
    page_width = page.rect.width
    page_height = page.rect.height
    all_blocks: list[Block] = []
    text_blocks: list[Block] = []
    image_blocks: list[Block] = []

    for raw in data["blocks"]:
        bbox = tuple(raw["bbox"])
        if raw["type"] == 1:
            block = Block(page.number + 1, "image", bbox)
            all_blocks.append(block)
            image_blocks.append(block)
            continue

        text = block_text(raw)
        if is_header_or_footer(text, bbox[1], bbox[3], page_height):
            continue
        if "Downloaded via" in text or ((bbox[2] - bbox[0]) < 16 and (bbox[3] - bbox[1]) > 80):
            continue
        block = Block(page.number + 1, "text", bbox, text=text)
        all_blocks.append(block)
        text_blocks.append(block)

    mode = classify_page_mode(text_blocks, page_width, override)
    column_mid = compute_column_mid(text_blocks, page_width, mode)
    for block in all_blocks:
        block.lane = assign_lane(block, page_width, column_mid, mode)

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
    return all_blocks, stats


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


def postprocess_markdown(text: str) -> str:
    text = text.replace("■", "\n\n## ")
    text = re.sub(r"(?m)^pubs\.acs\.org/JACS\s*$", "", text)
    text = re.sub(r"(?m)^Journal of the American Chemical Society\s*$", "", text)
    text = re.sub(r"(?m)^Article\s*$", "", text)
    text = re.sub(r"(?m)^DOI:\s*10\.\S+.*$", "", text)
    text = re.sub(r"(?m)^J\. Am\. Chem\. Soc\..*$", "", text)
    text = re.sub(r"(?m)^\d{5}\s*$", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\n## ([A-Z][A-Z &-]+)\n([A-Z][a-z])", r"\n## \1\n\n\2", text)
    return text.strip() + "\n"


def validate_source_markdown(text: str, stats: list[PageStats]) -> list[str]:
    warnings: list[str] = []
    upper = text.upper()
    for marker in ("INTRODUCTION", "RESULTS AND DISCUSSION", "REFERENCES"):
        if marker not in upper:
            warnings.append(f"missing section marker: {marker}")
    if "EXPERIMENTAL SECTION" in upper and upper.index("EXPERIMENTAL SECTION") > upper.index("REFERENCES"):
        warnings.append("Experimental Section appears after References")
    if "<!-- PDF_IMAGE" not in text:
        warnings.append("no image placeholders detected")
    if sum(len(block_warning) for block_warning in (page.warnings for page in stats)) > 0:
        warnings.append("page-level coverage warnings present")
    return warnings


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


def extract_source_markdown(pdf_path: Path, extractor_mode: ExtractorMode = "auto") -> tuple[str, list[PageStats], list[str]]:
    doc = fitz.open(pdf_path)
    parts: list[str] = []
    stats: list[PageStats] = []

    for page in doc:
        blocks, page_stats = collect_page_blocks(page, extractor_mode)
        stats.append(page_stats)
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

        for index, block in enumerate(ordered):
            if block.kind == "image":
                if should_emit_image(block):
                    x0, y0, x1, y1 = block.bbox
                    parts.append(f"<!-- PDF_IMAGE page={block.page_num} bbox={x0:.1f},{y0:.1f},{x1:.1f},{y1:.1f} -->")
                continue
            if block.text:
                parts.append(block.text)

    text = postprocess_markdown("\n\n".join(parts))
    warnings = validate_source_markdown(text, stats)

    if len(text) < 3_000:
        fallback = try_markitdown_fallback(pdf_path)
        if fallback and len(fallback) > len(text) * 1.2:
            text = fallback.strip() + "\n"
            warnings.append("used markitdown fallback because structured extraction looked too short")

    return text, stats, warnings


def escape_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def inline_format(text: str) -> str:
    text = escape_text(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text


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
        "en": register_first_available(EN_FONT_CANDIDATES, "Times-Roman"),
        "mono": MONO_FONT_NAME,
    }


def cjk_char_count(text: str) -> int:
    return sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")


def use_zh_style(text: str) -> bool:
    if not text:
        return False
    cjk_count = cjk_char_count(text)
    if cjk_count >= 3:
        return True
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return False
    return cjk_count / max(len(compact), 1) >= 0.12


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
    title_en = paragraph_style(
        "TitleEN",
        styles["Title"],
        font_names["en"],
        TYPOGRAPHY["title"]["size"],
        TYPOGRAPHY["title"]["leading"],
        alignment=TA_LEFT,
        textColor=INK_BLUE,
        spaceAfter=TYPOGRAPHY["title"]["space_after"],
    )
    title_zh = paragraph_style(
        "TitleZH",
        styles["Title"],
        font_names["zh"],
        TYPOGRAPHY["title"]["size"],
        TYPOGRAPHY["title"]["leading"],
        alignment=TA_LEFT,
        textColor=INK_BLUE,
        spaceAfter=TYPOGRAPHY["title"]["space_after"],
    )
    h1_en = paragraph_style(
        "H1EN",
        styles["Heading1"],
        font_names["en"],
        TYPOGRAPHY["h1"]["size"],
        TYPOGRAPHY["h1"]["leading"],
        textColor=INK_BLUE,
        spaceBefore=TYPOGRAPHY["h1"]["space_before"],
        spaceAfter=TYPOGRAPHY["h1"]["space_after"],
    )
    h1_zh = paragraph_style(
        "H1ZH",
        styles["Heading1"],
        font_names["zh"],
        TYPOGRAPHY["h1"]["size"],
        TYPOGRAPHY["h1"]["leading"],
        textColor=INK_BLUE,
        spaceBefore=TYPOGRAPHY["h1"]["space_before"],
        spaceAfter=TYPOGRAPHY["h1"]["space_after"],
    )
    h2_en = paragraph_style(
        "H2EN",
        styles["Heading2"],
        font_names["en"],
        TYPOGRAPHY["h2"]["size"],
        TYPOGRAPHY["h2"]["leading"],
        textColor=WARM_TEXT,
        spaceBefore=TYPOGRAPHY["h2"]["space_before"],
        spaceAfter=TYPOGRAPHY["h2"]["space_after"],
    )
    h2_zh = paragraph_style(
        "H2ZH",
        styles["Heading2"],
        font_names["zh"],
        TYPOGRAPHY["h2"]["size"],
        TYPOGRAPHY["h2"]["leading"],
        textColor=WARM_TEXT,
        spaceBefore=TYPOGRAPHY["h2"]["space_before"],
        spaceAfter=TYPOGRAPHY["h2"]["space_after"],
    )
    body_en = paragraph_style(
        "BodyEN",
        styles["BodyText"],
        font_names["en"],
        TYPOGRAPHY["body_en"]["size"],
        TYPOGRAPHY["body_en"]["leading"],
        textColor=WARM_TEXT,
        spaceAfter=TYPOGRAPHY["body_en"]["space_after"],
    )
    body_zh = paragraph_style(
        "BodyZH",
        styles["BodyText"],
        font_names["zh"],
        TYPOGRAPHY["body_zh"]["size"],
        TYPOGRAPHY["body_zh"]["leading"],
        textColor=WARM_TEXT,
        spaceAfter=TYPOGRAPHY["body_zh"]["space_after"],
    )
    bullet_en = paragraph_style(
        "BulletEN",
        body_en,
        font_names["en"],
        TYPOGRAPHY["bullet_en"]["size"],
        TYPOGRAPHY["bullet_en"]["leading"],
        leftIndent=14,
        firstLineIndent=-8,
    )
    bullet_zh = paragraph_style(
        "BulletZH",
        body_zh,
        font_names["zh"],
        TYPOGRAPHY["bullet_zh"]["size"],
        TYPOGRAPHY["bullet_zh"]["leading"],
        leftIndent=14,
        firstLineIndent=-8,
    )
    caption_en = paragraph_style(
        "CaptionEN",
        body_en,
        font_names["en"],
        TYPOGRAPHY["caption_en"]["size"],
        TYPOGRAPHY["caption_en"]["leading"],
        textColor=WARM_SUBTEXT,
        leftIndent=4,
        rightIndent=2,
        spaceBefore=TYPOGRAPHY["caption_en"]["space_before"],
        spaceAfter=TYPOGRAPHY["caption_en"]["space_after"],
    )
    caption_zh = paragraph_style(
        "CaptionZH",
        body_zh,
        font_names["zh"],
        TYPOGRAPHY["caption_zh"]["size"],
        TYPOGRAPHY["caption_zh"]["leading"],
        textColor=WARM_SUBTEXT,
        leftIndent=4,
        rightIndent=2,
        spaceBefore=TYPOGRAPHY["caption_zh"]["space_before"],
        spaceAfter=TYPOGRAPHY["caption_zh"]["space_after"],
    )
    equation_en = paragraph_style(
        "EquationEN",
        body_en,
        font_names["en"],
        TYPOGRAPHY["equation_en"]["size"],
        TYPOGRAPHY["equation_en"]["leading"],
        alignment=TA_CENTER,
        spaceBefore=TYPOGRAPHY["equation_en"]["space_before"],
        spaceAfter=TYPOGRAPHY["equation_en"]["space_after"],
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

    def style_for(role: str, text: str):
        zh = use_zh_style(text)
        if role == "title":
            return title_zh if zh else title_en
        if role == "h1":
            return h1_zh if zh else h1_en
        if role == "h2":
            return h2_zh if zh else h2_en
        if role == "body":
            return body_zh if zh else body_en
        if role == "bullet":
            return bullet_zh if zh else bullet_en
        if role == "caption":
            return caption_zh if zh else caption_en
        if role == "equation":
            return equation_zh if zh else equation_en
        return body_zh if zh else body_en

    story = []
    in_code_block = False
    code_lines: list[str] = []
    table_lines: list[str] = []
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
                        inline_format(cell or " "),
                        style_for("caption" if row_idx == 0 else "body", cell or " "),
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
            story.append(Spacer(1, 5))
        else:
            story.append(Preformatted("\n".join(table_lines), code))
        table_lines = []

    for raw_line in md_text.splitlines():
        line = raw_line.rstrip("\n")
        stripped = line.strip()

        if stripped.startswith("```"):
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
            table_lines.append(stripped)
            continue
        flush_table()

        if not stripped:
            story.append(Spacer(1, 4))
            continue

        match = IMAGE_RE.fullmatch(stripped)
        if match:
            page_num = int(match.group(1))
            bbox = tuple(float(match.group(i)) for i in range(2, 6))
            story.append(render_pdf_image(pdf_doc, page_num, bbox, max_image_width))
            story.append(Spacer(1, IMAGE_AFTER_SPACER))
            continue

        if stripped.startswith("# "):
            heading_text = stripped[2:].strip()
            story.append(Paragraph(inline_format(heading_text), style_for("title", heading_text)))
        elif stripped.startswith("## "):
            heading_text = stripped[3:].strip()
            story.append(Paragraph(inline_format(heading_text), style_for("h1", heading_text)))
        elif stripped.startswith("### "):
            heading_text = stripped[4:].strip()
            story.append(Paragraph(inline_format(heading_text), style_for("h2", heading_text)))
        elif re.match(r"^[-*]\s+", stripped):
            bullet_text = re.sub(r"^[-*]\s+", "", stripped)
            story.append(Paragraph("\u2022 " + inline_format(bullet_text), style_for("bullet", bullet_text)))
        elif re.match(r"^\d+[.)]\s+", stripped):
            story.append(Paragraph(inline_format(stripped), style_for("body", stripped)))
        elif looks_like_caption(stripped):
            story.append(Paragraph(inline_format(stripped), style_for("caption", stripped)))
        elif looks_like_equation(stripped):
            story.append(Paragraph(inline_format(stripped), style_for("equation", stripped)))
        else:
            story.append(Paragraph(inline_format(stripped), style_for("body", stripped)))

    flush_table()
    if in_code_block:
        flush_code()
    return story


def ensure_same_folder(pdf_path: Path, source_path: Path, bilingual_path: Path, bilingual_pdf: Path) -> None:
    folder = pdf_path.parent.resolve()
    for candidate in (source_path, bilingual_path, bilingual_pdf):
        if candidate.parent.resolve() != folder:
            raise ValueError(f"Output must stay in the same folder as the PDF: {candidate}")


def extract_source(
    pdf_path: Path,
    source_path: Path,
    extractor_mode: ExtractorMode = "auto",
    force: bool = False,
) -> tuple[bool, list[PageStats], list[str]]:
    if source_path.exists() and not force and source_path.stat().st_mtime >= pdf_path.stat().st_mtime:
        return False, [], []
    markdown, stats, warnings = extract_source_markdown(pdf_path, extractor_mode=extractor_mode)
    source_path.write_text(markdown, encoding="utf-8")
    print(source_path)
    return True, stats, warnings


def render_bilingual_pdf(original_pdf: Path, md_path: Path, pdf_path: Path, force: bool = False) -> bool:
    if not md_path.exists():
        raise FileNotFoundError(f"Missing bilingual markdown: {md_path}")
    if pdf_path.exists() and not force and pdf_path.stat().st_mtime >= md_path.stat().st_mtime:
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


def resolve_paths(pdf_path: Path, source_arg: str | None, bilingual_arg: str | None, pdf_out_arg: str | None):
    folder = pdf_path.parent
    source_path = Path(source_arg).expanduser().resolve() if source_arg else folder / "source.md"
    bilingual_path = Path(bilingual_arg).expanduser().resolve() if bilingual_arg else folder / "bilingual.md"
    bilingual_pdf = Path(pdf_out_arg).expanduser().resolve() if pdf_out_arg else folder / "bilingual.pdf"
    ensure_same_folder(pdf_path, source_path, bilingual_path, bilingual_pdf)
    return source_path, bilingual_path, bilingual_pdf


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


def main() -> int:
    parser = argparse.ArgumentParser(description="PDF bilingual workflow helper")
    parser.add_argument("pdf", help="Target paper PDF")
    parser.add_argument("--source", help="Path to source.md (default: same folder)")
    parser.add_argument("--bilingual", help="Path to bilingual.md (default: same folder)")
    parser.add_argument("--pdf-out", help="Path to bilingual.pdf (default: same folder)")
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

    source_path, bilingual_path, bilingual_pdf = resolve_paths(pdf_path, args.source, args.bilingual, args.pdf_out)

    print(f"[INFO] PDF: {pdf_path}")
    print(f"[INFO] source.md: {source_path}")
    print(f"[INFO] bilingual.md: {bilingual_path}")
    print(f"[INFO] bilingual.pdf: {bilingual_pdf}")
    print(f"[INFO] extractor-mode: {args.extractor_mode}")

    if args.check_only:
        _, stats, warnings = extract_source_markdown(pdf_path, extractor_mode=args.extractor_mode)
        print_check_summary(stats, warnings)
        return 0

    source_written, stats, warnings = extract_source(
        pdf_path,
        source_path,
        extractor_mode=args.extractor_mode,
        force=args.refresh_source,
    )
    print("[OK] source.md refreshed" if source_written else "[OK] source.md already up to date")
    if source_written:
        print_check_summary(stats, warnings)

    if not bilingual_path.exists():
        print("[WARN] bilingual.md is missing; create it before rendering bilingual.pdf")
        return 0

    pdf_written = render_bilingual_pdf(pdf_path, bilingual_path, bilingual_pdf, force=args.render_pdf)
    print("[OK] bilingual.pdf rendered" if pdf_written else "[OK] bilingual.pdf already up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
