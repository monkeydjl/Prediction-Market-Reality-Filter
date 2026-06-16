# Design

Visual system for the Event Intelligence Platform dashboard. Dark,
instrument-grade, data-dense. Evolved from the existing committed dark palette
(identity preserved), retuned in OKLCH for consistent contrast and a clearer
semantic color vocabulary.

## Theme

Dark, single theme (no light mode). Scene: a professional analyst at a desk,
ambient indoor light, long monitoring sessions, scanning many numbers and
drilling into one. Dark reduces eye fatigue over long sessions and lets the
color-coded probability signals (rising green / falling red, level pills) carry
maximal contrast against a near-black ground - the dark IS the instrument, not a
style choice. Color strategy: **Restrained** - tinted-neutral surfaces plus one
blue accent for selection/primary action, and a semantic green/amber/red set
reserved exclusively for data meaning.

## Color

All values OKLCH. The hue spine is a cool blue-slate (~250); neutrals are tinted
very slightly toward it so the surface reads as one cool family rather than pure
gray.

### Surfaces (darkest to lightest)

| Token | OKLCH | Use |
|---|---|---|
| `--bg` | `oklch(0.17 0.02 250)` | App background (near-black blue-slate) |
| `--surface` | `oklch(0.21 0.02 250)` | Panels, header |
| `--surface-2` | `oklch(0.19 0.02 250)` | Nested rows, inset wells, inputs |
| `--surface-3` | `oklch(0.25 0.025 250)` | Hover / raised |
| `--line` | `oklch(0.30 0.025 250)` | Borders, dividers |
| `--line-strong` | `oklch(0.42 0.03 250)` | Hover borders, emphasis dividers |

### Ink (text)

| Token | OKLCH | Contrast on `--surface` | Use |
|---|---|---|---|
| `--ink` | `oklch(0.95 0.01 250)` | ~15:1 | Primary text, data values |
| `--ink-2` | `oklch(0.78 0.02 250)` | ~7:1 | Secondary text, captions |
| `--ink-3` | `oklch(0.66 0.02 250)` | ~4.6:1 | Muted labels, table headers (still AA for normal text) |

`--ink-3` is the floor for body-sized text; never go muted-er for anything a
user must read. Uppercase micro-labels use `--ink-2`/`--ink-3` at >=11px with
letter-spacing.

### Accent (selection + primary action only, never decoration)

| Token | OKLCH | Use |
|---|---|---|
| `--accent` | `oklch(0.62 0.15 250)` | Primary button bg, active selection border, focus ring |
| `--accent-ink` | `oklch(0.97 0.02 250)` | Text on accent |
| `--accent-quiet` | `oklch(0.30 0.06 250)` | Selected-row tint, accent surface fill |

### Semantic data vocabulary (meaning only)

Direction (the rising/falling probability axis) and level (high/medium/low) are
the only places saturated color appears. Each pairs with a text label or sign so
meaning never rests on hue alone.

| Token | OKLCH | Meaning |
|---|---|---|
| `--up` | `oklch(0.74 0.16 152)` | Rising probability, positive delta, HIGH |
| `--up-bg` | `oklch(0.30 0.06 152)` | Rising pill / cell tint |
| `--down` | `oklch(0.68 0.18 22)` | Falling probability, negative delta, LOW |
| `--down-bg` | `oklch(0.30 0.08 22)` | Falling pill / cell tint |
| `--warn` | `oklch(0.80 0.13 85)` | MEDIUM level, caution |
| `--warn-bg` | `oklch(0.30 0.05 85)` | Medium pill tint |
| `--flat` | `--ink-3` | No change / neutral / stable |

State vocabulary (standardized across every interactive element): default,
hover (`--surface-3` + `--line-strong`), focus (2px `--accent` ring, 2px
offset), active (accent border), disabled (opacity 0.5, `not-allowed`), loading
(skeleton shimmer), error (toast + `--down`).

## Typography

Two families on a real contrast axis (sans for UI, mono for data) - not two
similar sans.

- **UI / prose:** `--font-sans` = system stack
  (`-apple-system, "Segoe UI", system-ui, sans-serif`). Familiar, fast, offline,
  no CDN dependency. Carries headings, labels, buttons, body.
- **Data / figures:** `--font-mono` = `ui-monospace, "Cascadia Code",
  "Segoe UI Mono", "SF Mono", Menlo, monospace`. Every probability, percentage,
  delta, score, Brier, timestamp, and table figure is monospaced and uses
  `font-variant-numeric: tabular-nums` so columns of numbers align and digits
  do not jitter as values update. This is the core typographic move: numbers
  read as instrument readouts.

Fixed rem scale (product UI, not fluid). Ratio ~1.2:
`11px` micro-label / `12px` caption / `13px` body / `14px` base / `16px`
sub-head / `20px` panel value / `28px` report headline figure. Headings via
weight (600-750), not size explosion. `text-wrap: balance` on the report title.

## Layout

- **App shell:** fixed header (brand + global actions + lang switch) over a
  fluid content region, `max-width` ~1480px, centered, 20px gutter.
- **Primary grid:** two columns - a narrow event list (scan column) and a wider
  detail/report column - on a 12px-16px gap. Collapses to one column under
  ~960px (structural, breakpoint-driven; not fluid type).
- **Density:** tables run dense (`8px` row padding, `tabular-nums`), many rows
  visible without scrolling. Panels are bordered regions with a 44-50px header
  bar (title + scoped action) and a padded body.
- Flexbox for the 1D rows of pills; grid for the 2D metric clusters
  (`repeat(auto-fit, minmax(...))` so metric tiles reflow without breakpoints).
- Semantic z-index scale: base / sticky-header / sparkline-tooltip / toast.
  No arbitrary 9999.

## Components

- **Panel:** bordered surface, header bar (uppercase-tracked title + optional
  scoped button), body. The single repeating container - not a card grid.
- **Event row:** title + a horizontal row of pills (trust level, impact level,
  value, signed delta). Hover raises border; selected gets accent border + quiet
  accent tint. The list's clickable unit.
- **Stat tiles:** the four top KPIs - label + big mono figure. Restrained, not
  the gradient hero-metric template (flat surface, no accent fill).
- **Pill:** small bordered tag. Level pills use the semantic tint+ink pair;
  delta pills carry a sign and direction color. Always text + color, never color
  alone.
- **Metric tile cluster:** 3-up grid inside the report (baseline / estimated /
  change, then trust / impact / evidence). Inset wells, mono figures.
- **Sparkline:** inline hand-rolled SVG of the probability trajectory in the
  Probability History panel - a small line chart (up/down colored) above the
  snapshot table, turning the existing table-only history into a glanceable
  shape. No charting library (stays hermetic, no build, no CDN required).
- **Skeleton:** shimmer blocks for loading list/report/history instead of a
  centered spinner.
- **Empty state:** dashed-border well that teaches the next action ("Run
  discovery to identify events."), not "nothing here".
- **Toast:** bottom-right, bordered surface, auto-dismiss; error variant uses
  `--down` border.
- **Inputs / textarea:** `--surface-2` fill, `--line` border, accent focus ring.
- **Buttons:** default (surface + line), primary (accent fill), both with hover
  + focus + disabled states.

## Motion

- 150-250ms on state transitions (hover, selection, panel content swap). Users
  are in flow; no choreography.
- Motion conveys state only: selection border, hover lift, skeleton shimmer,
  toast slide-in, sparkline draw-on-load (single subtle reveal of an
  already-visible default, not gated visibility).
- Easing: ease-out (cubic-bezier ~ .22,.61,.36,1). No bounce, no elastic.
- `@media (prefers-reduced-motion: reduce)`: shimmer and slides become instant /
  crossfade; sparkline renders final state immediately.

## Bans honored

No gradient text, no glassmorphism, no side-stripe accent borders, no nested
cards, no hero-metric gradient template, no per-section uppercase eyebrows, no
emoji-as-UI. Color appears only where it means something.
