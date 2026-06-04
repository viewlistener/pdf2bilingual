# Extraction Boundary

## What the skill should guarantee

- `source.md` follows paper reading order as closely as possible.
- Major headings, figure captions, table captions, and experimental procedures are preserved.
- If figures can be localized in the PDF, they can be reinserted into `bilingual.pdf`.
- The workflow does not silently drop figure/table/formula context.
- The extractor can distinguish single-column, double-column, and mixed-layout pages on a page-by-page basis.

## What baseline markdown conversion does not guarantee

Plain text/OCR extraction alone often fails on:

- double-column reading order
- figure placement
- table structure
- special symbols
- ligatures
- formula spacing
- page headers/footers and publication noise

Because of this, a raw markdown converter should be treated as a starting point, not as an unquestioned final source.

## Recovery Rules

- Remove page headers, page numbers, DOI footers, and publication boilerplate from the paper body.
- Reconstruct reading order for two-column layouts.
- Detect and preserve mixed pages that combine cross-column blocks with double-column body text.
- Preserve image positions by storing lightweight placeholders in `source.md` / `bilingual.md`.
- Reinsert figure regions into the final PDF from the original paper.
- Preserve caption adjacency whenever possible so image placeholders, figure captions, and nearby explanation survive as one local cluster.
- Keep markdown table structure when it can be recovered well enough for later table rendering.
- When symbols are corrupted, repair obvious encoding noise if the intended meaning is clear from local context.
- If a value or symbol is genuinely ambiguous after extraction, keep the closest faithful text and avoid inventing a replacement.
- When auto layout detection is wrong for a specific paper, allow a per-run override instead of changing the folder workflow.
