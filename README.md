# pdf2bilingual

`pdf2bilingual` is a Codex skill for turning one specified paper PDF into a clean bilingual package in the same folder:

- original PDF
- `source.md`
- `bilingual.md`
- `bilingual.pdf`

It is designed for paper reading and verification, not for producing a summary note.

## What it does

- extracts paper text in reading order from the original PDF
- handles single-column, double-column, and mixed-layout papers more robustly than plain markdown conversion alone
- builds a faithful English-Chinese parallel edition from `source.md`
- preserves figure, table, formula, and caption order
- reinserts recoverable figure regions from the original PDF into the final bilingual PDF
- keeps output noise low by avoiding extra helper files in the paper folder

## Workflow

The intended workflow is:

1. PDF -> `source.md`
2. `source.md` -> `bilingual.md`
3. `bilingual.md` -> `bilingual.pdf`

All four files stay in the same literature folder.

## Translation behavior

The translation layer is governed by a fixed template:

- faithful literal translation by default
- English source block first, Chinese translation second
- default stop point before `References`
- English-first terminology with minimal Chinese explanation
- preserve headings, captions, lists, values, units, and formula references
- do not rewrite the paper into a summary or reading note

See:

- `references/workflow.md`
- `references/extraction-boundary.md`
- `references/formatting.md`
- `references/translation-template.md`

## Files

- `SKILL.md`: main skill contract
- `agents/openai.yaml`: entry prompt for the skill agent
- `scripts/process_pdf.py`: extraction and rendering helper
- `references/`: workflow, formatting, extraction, and translation rules

## Notes

- This repository contains the skill itself, not a standalone web app or Python package.
- The helper script handles extraction and PDF rendering; translation is performed by the invoking Codex agent under the skill's translation template.
