#!/usr/bin/env python3
"""Reduce unnecessary English retention inside bilingual translation blocks.

This helper rewrites only `role="translation"` blocks in bilingual.md while:
- preserving provenance and block markers
- keeping source blocks byte-for-byte intact
- retaining formulas, abbreviations, and figure labels
- preferring common Chinese scientific renderings over excessive English carry-over
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path


TRANSLATION_MARKER_RE = re.compile(
    r'^<!--\s*PDF2BILINGUAL_BLOCK\s+id="(?P<id>\d{4})"\s+role="translation"\s+'
    r'type="(?P<type>[a-z_]+)"\s+block_sha256="(?P<hash>[0-9a-f]{64})"\s*-->$'
)

BLOCK_MARKER_PREFIX_RE = re.compile(r"^<!--\s*PDF2BILINGUAL_BLOCK\s+")


TERM_REPLACEMENTS: list[tuple[str, str]] = [
    ("symmetry-breaking", "对称性破缺"),
    ("symmetry breaking", "对称性破缺"),
    ("oriented attachment", "定向附着"),
    ("intraparticle ripening", "颗粒内熟化"),
    ("single-dot intermediates", "单点中间体"),
    ("single-dot intermediate", "单点中间体"),
    ("2D embryos", "二维胚体"),
    ("2D embryo", "二维胚体"),
    ("quantum dots", "量子点"),
    ("quantum rods", "量子棒"),
    ("lateral extension", "侧向延伸"),
    ("face-center-cubic", "面心立方"),
    ("rock salt", "岩盐型"),
    ("zinc-blende structure", "闪锌矿结构"),
    ("wurtzite structure", "纤锌矿结构"),
    ("zinc-blende", "闪锌矿"),
    ("wurtzite", "纤锌矿"),
    ("Supporting Information", "补充信息"),
    ("as-synthesized", "合成所得"),
    ("seeds", "晶种"),
    ("seed", "晶种"),
    ("nanocrystals", "纳米晶"),
    ("nanocrystal", "纳米晶"),
    ("monolayer", "单层"),
]


def load_guard():
    path = Path(r"C:\Users\zheng\.codex\skills\pdf2bilingual\scripts\bilingual_guard.py")
    spec = importlib.util.spec_from_file_location("bilingual_guard", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bilingual_guard"] = mod
    spec.loader.exec_module(mod)
    return mod


def smart_replace(text: str) -> str:
    out = text
    for old, new in TERM_REPLACEMENTS:
        out = re.sub(rf"\b{re.escape(old)}\b", new, out)

    phrase_rules = [
        (r"\bUV-vis absorption spectra\b", "紫外-可见吸收光谱"),
        (r"\bUV-vis spectrum\b", "紫外-可见光谱"),
        (r"\bUV-vis spectra\b", "紫外-可见光谱"),
        (r"\bPL spectra\b", "PL 光谱"),
        (r"\bPL spectrum\b", "PL 光谱"),
        (r"\bTEM images\b", "TEM 图像"),
        (r"\bTEM image\b", "TEM 图像"),
        (r"\bHRTEM images\b", "HRTEM 图像"),
        (r"\bHRTEM image\b", "HRTEM 图像"),
        (r"\bFFT patterns\b", "FFT 图样"),
        (r"\bFFT pattern\b", "FFT 图样"),
        (r"\bfluorescence excitation spectra\b", "荧光激发光谱"),
        (r"\bfirst excitonic absorption peak\b", "第一激子吸收峰"),
        (r"\bfirst excitonic peaks\b", "第一激子吸收峰"),
        (r"\bPL decay dynamics\b", "PL 衰减动力学"),
        (r"\bphotoluminescence\b", "光致发光"),
        (r"\bStoke's shift\b", "斯托克斯位移"),
        (r"\bStokes shift\b", "斯托克斯位移"),
        (r"\bzone axis\b", "晶带轴"),
        (r"\battachment front\b", "附着前沿"),
        (r"\bsoft templates\b", "软模板"),
        (r"\bsoft template\b", "软模板"),
        (r"\bmagic-size clusters\b", "魔法尺寸团簇"),
        (r"\bmagic size clusters\b", "魔法尺寸团簇"),
        (r"\bdot-shaped\b", "点状"),
        (r"\bpanel a\b", "a 图"),
        (r"\bpanel b\b", "b 图"),
        (r"\bpanel c\b", "c 图"),
        (r"\bpanel d\b", "d 图"),
        (r"\bpanels\b", "图中各分图"),
        (r"\bpanel\b", "分图"),
        (r"\baliquots\b", "分取样"),
        (r"\baliquot\b", "分取样"),
        (r"\bfirst step\b", "第一步"),
        (r"\bsecond step\b", "第二步"),
        (r"\bthird step\b", "第三步"),
    ]
    for pattern, replacement in phrase_rules:
        out = re.sub(pattern, replacement, out)
    return out


def compact_cjk_spacing(text: str) -> str:
    out = text
    patterns = (
        (r"([\u4e00-\u9fff])\s+([\u4e00-\u9fff])", r"\1\2"),
        (r"([\u4e00-\u9fff])\s+([，。；：！？、】【（）《》“”‘’])", r"\1\2"),
        (r"([（《“‘])\s+([\u4e00-\u9fff])", r"\1\2"),
    )
    changed = True
    while changed:
        changed = False
        for pattern, replacement in patterns:
            updated = re.sub(pattern, replacement, out)
            if updated != out:
                changed = True
                out = updated
    return out


def normalize_translation_blocks(md_text: str) -> str:
    lines = md_text.splitlines()
    output: list[str] = []
    pending: list[str] = []
    current_translation = False

    def flush_pending() -> None:
        nonlocal pending
        if current_translation and pending:
            payload = "\n".join(pending).strip("\n")
            if payload:
                output.extend(compact_cjk_spacing(smart_replace(payload)).splitlines())
            else:
                output.extend(pending)
        else:
            output.extend(pending)
        pending = []

    for line in lines:
        stripped = line.strip()
        if TRANSLATION_MARKER_RE.match(stripped):
            flush_pending()
            current_translation = True
            output.append(line)
            continue
        if BLOCK_MARKER_PREFIX_RE.match(stripped):
            flush_pending()
            current_translation = False
            output.append(line)
            continue
        pending.append(line)

    flush_pending()
    return "\n".join(output).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize bilingual translation blocks to reduce unnecessary English retention.")
    parser.add_argument("--source", required=True, help="Path to source_reset.md")
    parser.add_argument("--bilingual", required=True, help="Path to bilingual.md")
    args = parser.parse_args()

    guard = load_guard()
    source_path = Path(args.source).resolve()
    bilingual_path = Path(args.bilingual).resolve()

    original = bilingual_path.read_text(encoding="utf-8")
    normalized = normalize_translation_blocks(original)
    bilingual_path.write_text(normalized, encoding="utf-8")

    report = guard.validate_bilingual(source_path, bilingual_path)
    print(bilingual_path)
    print(
        f"ok={str(report.ok).lower()} stale={str(report.stale).lower()} "
        f"errors={len(report.errors)} warnings={len(report.warnings)}"
    )
    if not report.ok:
        for error in report.errors[:20]:
            print(error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
