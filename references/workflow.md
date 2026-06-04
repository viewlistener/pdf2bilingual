# Workflow

This workflow is for one explicitly named paper only.

## Goal

Create a same-folder bilingual paper package:

- original PDF
- `source.md`
- `bilingual.md`
- `bilingual.pdf`

The package is for reading and verification, not for producing a shortened digest.

## Extraction Strategy

1. Start from the original PDF.
2. Extract text in reading order with a structured PDF-aware extractor first.
3. Detect single-column, double-column, and mixed-layout pages automatically, with a manual override only when auto detection fails on a specific paper.
4. Keep figure captions, table text, section headings, and experimental details in place.
5. Stop the bilingual body at `References` unless the user explicitly asks to include references.

`markitdown` is now a fallback or cross-check only. It is not the default first step when structured extraction can recover a cleaner reading order.

## Bilingual Build

1. Keep the extracted English block.
2. Add the Chinese translation directly after it.
3. Repeat for each paragraph, caption, or logical block.
4. Preserve headings and caption numbering.

## PDF Build

1. Render text from `bilingual.md`.
2. Detect image placeholders such as `<!-- PDF_IMAGE ... -->`.
3. Crop the corresponding figure region from the original PDF.
4. Insert the cropped figure back into the rendered bilingual PDF near its caption.
5. Apply the local editorial print profile during rendering:
   - A4 reading layout
   - restrained heading accent
   - smaller caption style for figure/table notes
   - readable table rendering instead of raw pipe text where possible
   - centered display treatment for recognizable standalone formulas

## Noise Control

- No helper files in the paper folder.
- No detached image export directories.
- No metadata sidecars.
- No notes or summary files.

## Diagnostics

- `--check-only` should print a per-page summary of detected layout mode and block counts.
- `--extractor-mode auto|single|double|mixed` should be available for stubborn PDFs.
