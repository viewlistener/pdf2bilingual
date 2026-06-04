---
name: pdf2bilingual
description: Strict single-PDF workflow for converting one specified paper PDF into source.md, bilingual.md, and bilingual.pdf in the same folder. Use when the user wants a clean, same-directory bilingual translation package with no extra metadata, notes, figures, or helper files, and with faithful paragraph-aligned Chinese-English paper translation that preserves figures, tables, formulas, and the full Experimental Section.
---

# PDF 双语翻译整理

## Overview

Use this skill only for one explicitly named PDF at a time. Keep the original PDF and all outputs in the same literature folder and leave the folder structure unchanged.

This skill now targets a faithful bilingual paper edition instead of a summary-style reading note:

- `source.md` must be a source-faithful extraction of the paper body in reading order.
- The extractor should prefer structured PDF layout recovery over plain markdown conversion, and should auto-detect single-column, double-column, and mixed pages.
- `bilingual.md` must keep the extracted English text visible and follow each paragraph or logical block with the Chinese translation.
- `bilingual.pdf` must preserve figure/table/formula order and reinsert figure regions from the original PDF when available.
- The PDF renderer should use a restrained editorial print profile inspired by Kami's document system, but without taking an external runtime dependency on Kami itself.

`markitdown` remains a useful baseline extractor, but it is not sufficient on its own for double-column ACS-style papers. When reading order, figure captions, tables, or formulas are degraded, the workflow should use PDF extraction tools such as PyMuPDF / `pdfplumber` to rebuild `source.md` in a more faithful order.

## Workflow

1. Locate the target PDF in `D:\9-codex\reference\分类名\文献文件夹\`.
2. Extract `source.md` directly from the PDF in reading order.
3. Build `bilingual.md` from `source.md` as a faithful English-Chinese parallel edition.
4. Render `bilingual.pdf` from `bilingual.md`, reinserting figure regions from the original PDF when the markdown contains figure placeholders.
5. Keep output noise to zero.

See:

- [references/workflow.md](references/workflow.md)
- [references/extraction-boundary.md](references/extraction-boundary.md)
- [references/formatting.md](references/formatting.md)
- [references/translation-template.md](references/translation-template.md)

## File Contract

- Keep only these sibling files in the paper folder:
  - `文献文件夹名.pdf`
  - `source.md`
  - `bilingual.md`
  - `bilingual.pdf`
- Do not create `metadata.json`, `notes.md`, `figures.md`, export folders, or helper artifacts.
- If any generated file already exists, inspect whether the PDF or upstream markdown changed before overwriting.
- The canonical ordering is: PDF first, then `source.md`, then `bilingual.md`, then `bilingual.pdf`.

## Bilingual Rules

- Format `bilingual.md` as a faithful paragraph-level bilingual rendering of `source.md`.
- Keep the original extracted English text visible, followed immediately by the Chinese translation for the same paragraph or logical block.
- Do not prefix paragraphs with `English` / `Chinese`.
- Do not condense the paper into a summary, review note, or rewritten article.
- Preserve the original order of sections, paragraphs, captions, lists, tables, and in-body references.
- The full paper body must be covered, including the entire `Experimental Section`, through the start of `References` or the end of the article.

## Translation Responsibility

- `process_pdf.py` does not translate content; it only extracts `source.md`, renders `bilingual.pdf`, and reinserts recoverable figure regions.
- The agent invoking this skill is responsible for building `bilingual.md` from `source.md`.
- The translation step must follow `references/translation-template.md` as the canonical prompt and behavior contract.
- When the agent is unsure whether a damaged source block can be safely interpreted, it must preserve the English source and mark the block conservatively instead of guessing.

## Figure, Table, and Formula Rules

- Keep figures, tables, and formulas in original order and near their original textual context.
- Preserve labels such as `Figure 1`, `Scheme 2`, `Table 1`, and `Eq. (3)`.
- Preserve figure captions, table captions, equation labels, and nearby explanatory text in the bilingual flow.
- When the image itself is recoverable from the PDF, use a placeholder in markdown and reinsert the corresponding PDF region during PDF rendering.
- When only captions or extracted table text are recoverable, keep that content visible in place and do not silently drop it.
- If formula text is extracted as inline text or broken lines, normalize it into readable text while keeping numbering and meaning intact.
- During PDF rendering, use a calm A4 reading layout with restrained hierarchy, caption styling, and readable table rendering rather than a plain text dump.

## Extraction Controls

- Default extractor mode is `auto`.
- Allow `single`, `double`, or `mixed` only as a per-run override for difficult PDFs.
- `--check-only` should report page-level extraction diagnostics instead of only echoing paths.

## Validation Rules

- No paragraph-level language tags in the final bilingual markdown.
- No truncation before the end of the paper body.
- English blocks should be recognizable as source text rather than paraphrased summaries.
- Figure, table, and formula references must remain visible and ordered.
- Final folder contents must stay limited to the original PDF plus `source.md`, `bilingual.md`, and `bilingual.pdf`.
