# Deck pipeline — slide-making with measured layout

Self-contained ability of this repo. Use it whenever a .pptx is created,
extended, or reviewed for layout quality — new decks, slides added to a
foreign deck, or auditing a generated one. It does not depend on any Claude
plugin/skill machinery; everything runs from here with `../../.venv/bin/python`.

For a deck authored as hand-written HTML rather than as a .pptx, the engine and
the user-fillable image slot live in `html/` — see `html/README.md` for wiring,
the animation-gating rule, and the CSS class-name collision that silently
truncates layout.

**Core discipline: never emit or trust guessed geometry.** Every failure mode
this pipeline exists for came from estimated text sizes and hand-arithmetic
alignment. Measure (browser or TTF), align by engine or by assertion, verify
by lint before eyeballs.

## The loop

1. **Observe** — existing deck? Derive its implicit design system first:
   ```
   .venv/bin/python scripts/deck/observe.py deck.pptx            # profile
   .venv/bin/python scripts/deck/observe.py deck.pptx --slides   # + per-shape dump
   ```
   Font-size inventory, column x-clusters, row pitches, anchors, px-smell.
   New slides may only use values from this profile (and the deck's own
   masters/theme). Never invent a second grid or a new point size.

2. **Layout** — two legs:
   - *Browser leg* (new layouts, mixed scripts): author slides as HTML against
     `harness/tokens.css` (all sizes in pt, canvas 1920x1080px = 20x11.25in).
     Tag exported elements `data-pptx="text|card|image"` (cards before their
     text; images carry `data-src`). Put the HTML + assets under
     `config/deck-harness/` (container-visible), then:
     ```
     scripts/deck/harness/harvest.py file:///config/deck-harness/x.html g.json --shot ref.png
     scripts/deck/harness/emit_pptx.py g.json out.pptx --asset-root config/deck-harness
     ```
     Chromium (launch: `scripts/launch_chromium.sh`, CDP :9222) lays out and
     measures; the emitter writes native shapes at measured px (9525 EMU/px,
     pt = px*0.75) with `anchor=MIDDLE` so Blink-vs-PowerPoint metric drift is
     absorbed, never visible. Keep `g.json` next to the deck source — rebuilds
     don't need the browser until the layout changes.
   - *Direct leg* (small edits, fixes): python-pptx, but box sizes come from
     `scripts/deck/measure.py` (real TTF metrics, Arial→Liberation fallback):
     ```
     scripts/deck/measure.py fit "текст" --pt 25 --box-w 5.0 --box-h 1.2
     scripts/deck/measure.py wrap "текст" --pt 25 --box-w 5.0 --balance
     ```

3. **Lint** — gate before any visual QA; errors block:
   ```
   .venv/bin/python scripts/deck/lint.py out.pptx        # --strict for handoff
   ```
   Checks rendered-text bboxes (pptx never clips text — frame-box arithmetic
   lies): E-align (sibling rows), E-collide, E-bounds; warns on stagger,
   arithmetic centering, normAutofit, token-budget breaks, picture drift.

4. **Render QA** — only after lint passes, for gestalt (composition, rhythm,
   dead voids — things no linter sees):
   ```
   soffice --headless --convert-to pdf out.pptx && pdftoppm -jpeg -r 110 out.pdf slide
   ```
   Inspect every page. Local Arial renders as metric-compatible Liberation
   Sans, so these images match venue-machine layout.

## Hard rules

- **Tokens before shapes.** A new deck starts with explicit numbers (≤6 font
  sizes, grid columns, row pitch, margins) written into the deck project's
  DESIGN.md. Adjectives («в стиле афиши») are not a spec.
- **Anchors, not arithmetic.** Vertical centering is `anchor=ctr`; a
  top-anchored box floated by computed y-offset is a defect (W-center).
- **Design is editing.** A 30-word sentence has exactly one layout — a wall.
  Atomize content into labeled units (kicker / chip / card / banner) before
  layout, and confirm the rewrite with the author when the source text is
  binding.
- **px-smell** (deck converted from a pixel canvas without measurement):
  20x11.25in slide, quarter-point sizes (18.75/20.25/21.75 = px*0.75),
  five-digit line-spacing percentages, every anchor `t`, bare `normAutofit`.
  Such a deck needs observe + lint before anything is added to it.

## Files

- `observe.py` — profile a deck's implicit design system
- `measure.py` — TTF text measurement: width / wrap / balance / fit
- `lint.py` — rendered-text-bbox assertions, non-zero exit on errors
- `harness/tokens.css` — design tokens + components (slide/kicker/title/chip/card/banner/qr)
- `harness/harvest.py` — browser-measured geometry JSON (+ reference screenshot)
- `harness/emit_pptx.py` — geometry JSON → native pptx
