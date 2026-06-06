# pdf2bilingual

`pdf2bilingual` is a Codex skill for turning one specified paper PDF into a clean bilingual reading package in the same folder:

- original PDF
- `source.md`
- `source_reset.md`
- `bilingual.md`
- `bilingual.pdf`

It is built for faithful paper reconstruction and verification, not for summary notes or literature digests.

## Current workflow

The intended pipeline is:

1. PDF -> `source.md`
2. `source.md` -> `source_reset.md`
3. `source_reset.md` -> `bilingual.md`
4. `bilingual.md` -> `bilingual.pdf`

All files stay beside the original PDF.

## What is different from a plain PDF-to-Markdown converter

- `source.md` is the raw reading-order extraction, kept as faithful to the PDF as possible.
- `source_reset.md` is the repaired reading edition, with paragraph recovery, cross-page rejoining, caption placement cleanup, and inline citation cleanup.
- The extractor is tuned for single-column, double-column, and mixed-layout papers.
- Figure regions can be reinserted from the original PDF during final rendering.
- The bilingual markdown keeps block-level provenance markers so source/translation alignment can be validated.

## What the skill does

- extracts text blocks from the PDF with layout-aware recovery
- rebuilds a cleaner reading edition before translation
- preserves headings, figures, tables, formulas, and caption order
- scaffolds bilingual markdown with source/translation block markers
- validates bilingual alignment against the current `source_reset.md`
- normalizes over-retained English terms inside Chinese translation blocks
- renders a restrained A4 bilingual PDF with mixed Chinese/English font handling

## Repository layout

- `SKILL.md`
  - main skill contract
- `agents/openai.yaml`
  - entry prompt for the skill agent
- `scripts/process_pdf.py`
  - extraction, `source_reset` generation, validation, and PDF rendering
- `scripts/bilingual_guard.py`
  - scaffold and validation helper for `bilingual.md`
- `scripts/normalize_bilingual_terms.py`
  - post-processes translation blocks to reduce unnecessary English retention
- `scripts/compare_reset.py`
  - compares auto-generated `source_reset.md` against a manually repaired reference
- `references/`
  - workflow, formatting, extraction, and translation rules

## Translation model

- English source block first, Chinese translation block second
- paragraph-aligned rather than summary-style
- Chinese-first terminology when a natural Chinese rendering exists
- preserve necessary English only for formulas, abbreviations, chemical names, figure labels, and terms that would become unclear if fully localized
- do not rewrite the paper into a review note

See:

- `references/workflow.md`
- `references/extraction-boundary.md`
- `references/formatting.md`
- `references/translation-template.md`

## Rendering behavior

- Chinese is rendered with a Chinese base font, with English spans overlaid in Times New Roman
- source and translation figure captions are grouped so they read as one caption unit
- figure-caption discussion sentences such as `Figure 3a reveals...` are treated as body paragraphs, not captions
- front-matter ornaments such as stray supporting-information symbols are cleaned at render time when appropriate

## Validation and diagnostics

- `bilingual_guard.py verify` checks provenance, block ordering, and source/translation alignment
- `process_pdf.py --check-only` reports page-level extraction diagnostics
- debug runs can emit artifacts such as `source_blocks.json`, `extraction_report.md`, and `missing_content_report.md`
- `compare_reset.py` can generate `reset_diff_report.md` and `paragraph_map.json` for paragraph-reordering analysis

## Notes

- This repository contains the skill itself, not a standalone Python package or web app.
- Translation is performed by the invoking Codex agent under the repository's translation rules.
- The skill is intended to keep output noise low in the paper folder, while allowing debug artifacts to live outside the literature folder when needed.
