# Formatting Rules

## Page Tone

- The final `bilingual.pdf` should read like a calm print artifact rather than a raw text dump.
- Use restrained editorial styling inspired by Kami, but keep the workflow self-contained:
  - warm paper-like background instead of stark white where the renderer can support it
  - one primary ink-blue accent for headings and structural emphasis
  - warm gray text for captions and table metadata
- Prefer readability and verification over decorative styling.

## Markdown Layout

- Use a paper-title heading at the top.
- Keep major paper sections as `##` headings.
- Keep experimental subsections as `###` headings where useful.
- For each paragraph or logical block:
  - English source block first
  - Chinese translation block second
- Keep a blank line between the English and Chinese blocks.
- Do not switch to left-right dual columns for bilingual text. Keep an upper-lower reading flow.

## Typography and Spacing

- Treat the PDF as an A4 reading document, not a slide deck.
- Fix the type system rather than tuning per paper:
  - Chinese body font: prefer `msyh.ttc` (Microsoft YaHei)
  - Chinese heading/caption emphasis font: prefer `msyhbd.ttc`
  - English body font: prefer `times.ttf`
  - English heading/caption emphasis font: prefer `timesbd.ttf`
  - Monospaced content: `Courier`
- Keep title spacing compact and clear.
- Keep body text in a stable reading range with moderate leading.
- Keep captions visibly smaller than body text.
- Keep section rhythm consistent: headings should tighten the page, while body text and captions should breathe.
- Avoid oversized separators, decorative icons, colored blocks, or dashboard-style panels.
- Use the renderer's typography constants rather than scattered magic numbers:
  - Title: `18pt / 22pt`
  - `##`: `14.5pt / 20pt`
  - `###`: `12.5pt / 17pt`
  - English body: `10.2pt / 15.8pt`
  - Chinese body: `10.4pt / 17.2pt`
  - Captions: `8.8pt / 13.2pt`
  - Standalone equations: `9.8pt / 15pt`
- Keep the English body visually neutral with no extra letter-spacing.
- Keep the Chinese body slightly more open through font choice and leading rather than manual character-by-character tracking.
- Fix colors to a restrained print palette:
  - Heading accent: `#1B365D`
  - Main body ink: `#2F2A24`
  - Caption/table metadata: `#6B6459`
- Before rendering to PDF, normalize unsupported or unstable inline symbols to PDF-safe forms:
  - `∼ -> ~`
  - `≈ -> ~`
  - `≤ -> <=`
  - `≥ -> >=`
  - normalize Unicode dash variants to ASCII `-`
  - remove zero-width and non-breaking spacing artifacts that can surface as empty boxes
- Keep emphasis rules close to Kami's editorial rhythm:
  - title and section headings use the bold heading font plus ink-blue color
  - body text stays regular-weight
  - captions use the emphasis font but remain smaller and lighter in color than headings

## Figures

- Store recoverable figure positions as HTML comments:
  - `<!-- PDF_IMAGE page=2 bbox=... -->`
- Render figures with a stable image profile rather than page-wide expansion:
  - normal figures: cap display width to about `78%` of the text block width
  - clearly wide or cross-column figures: allow up to about `90%` of the text block width
  - do not enlarge very small figures beyond their original visual footprint unless needed for basic legibility
- Separate display size from extraction quality:
  - keep PNG export
  - use adaptive PDF crop sampling rather than a fixed `2x` matrix
  - prefer higher sampling for small figures, abstract figures, line-heavy mechanism diagrams, and figures with small labels
  - target a minimum intrinsic pixel width so that downscaled figures still look crisp in the final PDF
- Keep the English caption immediately below the image placeholder.
- Keep the Chinese caption immediately below the English caption.
- In the final PDF, the figure should appear above its captions.
- Figure captions should be rendered in a caption style distinct from normal body text.
- Keep figure-to-caption spacing compact and stable so images feel embedded in the reading flow rather than floating like posters.
- If the original figure cannot be recovered as an image, keep the caption pair and surrounding discussion in place.

## Tables

- Preserve extracted markdown-style tables when they remain readable.
- Render readable markdown tables as actual PDF tables when possible instead of monospaced raw text.
- Use restrained header emphasis and warm light rules; avoid heavy full-grid styling unless the source table is dense enough to require it.
- If a table is too degraded to stay tabular, keep the table label and the extracted content as aligned text blocks rather than deleting it.

## Formulas

- Keep formulas close to the paragraph where they appear.
- Do not rewrite scientific meaning.
- Preserve labels such as `Eq. (1)` in both languages.
- Standalone formula lines should be rendered as centered equation blocks when their structure is recognizable.
- Explanatory lines immediately around formulas should stay in the normal bilingual paragraph flow.

## Translation Style

- Faithful rather than polished.
- Do not summarize.
- Do not merge multiple English paragraphs into one Chinese paragraph unless extraction made them inseparable.
- Keep scientific names, abbreviations, and units close to the original wording.
