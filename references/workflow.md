# Workflow

This workflow is for one explicitly named paper only.

## Goal

Create a same-folder bilingual paper package:

- original PDF
- `source.md`
- `source_reset.md`
- `bilingual.md`
- `bilingual.pdf`

The package is for reading and verification, not for producing a shortened digest.

## Extraction Strategy

1. Start from the original PDF.
2. Extract text in reading order with a structured PDF-aware extractor first and persist block-level debug data to a temporary debug directory.
3. Detect single-column, double-column, and mixed-layout pages automatically, with a manual override only when auto detection fails on a specific paper.
4. Save a source-faithful `source.md` before aggressive paragraph surgery:
   - keep accepted text blocks in reading order
   - keep figure placeholders
   - keep inline citation numbers and raw paragraph boundaries when still uncertain
5. Derive `source_reset.md` from `source.md` with explicit paragraph repair:
   - merge double-column short lines back into natural paragraphs
   - split inline subsection labels back out of paragraphs
   - repair image-interrupted paragraphs
   - normalize common ligature and encoding noise
   - remove inline citation clutter that harms readability
6. Compare the reviewed `source_reset.md` against the PDF structure before translation:
   - section order must be plausible
   - key sections must be present
   - image placeholders must still match the extracted figure regions
   - missing-content diagnostics must identify filtered blocks and suspicious tail content
7. Stop the bilingual body at `References` unless the user explicitly asks to include references.

`markitdown` is now a fallback or cross-check only. It is not the default first step when structured extraction can recover a cleaner reading order.

## Bilingual Build

1. Split `source_reset.md` into stable logical blocks before translation.
2. Create a scaffold with hidden provenance and block markers.
3. Keep the extracted English source block unchanged.
4. Add exactly one Chinese translation block directly after the matching English block.
5. Repeat for each paragraph, caption, list, table, or logical block.
6. Preserve headings and caption numbering.
7. Keep the paper title, author names, affiliations, and supporting-information front matter in the original language only.
8. Validate `bilingual.md` against the current `source_reset.md` before it is treated as usable.
9. Treat long runs of `?` or other obvious corruption markers in Chinese translation blocks as invalid output.

If `source_reset.md` changes, any existing `bilingual.md` and `bilingual.pdf` are stale until the bilingual markdown is rebuilt and revalidated.

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
- `source_blocks.json`, `extraction_report.md`, and `missing_content_report.md` should be written to a debug directory under `_workspace_temp`, not to the paper folder.
- Bilingual validation should report provenance, block counts, and stale/invalid status.
