# Design

Visual system for the Event Intelligence Platform dashboard. Dark,
instrument-grade, data-dense.

**This document describes what is in the code.** The single source of truth is
`frontend/src/app/globals.css` (`@theme inline` + the `:root, :root.dark` block).
When the two disagree, the CSS wins and this file is stale - fix it here.

## Theme

Dark, single theme. There is no light mode: `globals.css` defines exactly one
token set, under `:root, :root.dark`. No `:root.light` block, no
`prefers-color-scheme` override, no runtime theme switcher. The root element
carries a static `dark` class from `src/app/layout.tsx`.

Scene: a professional analyst at a desk, ambient indoor light, long monitoring
sessions, scanning many numbers and drilling into one. Dark reduces eye fatigue
over long sessions and lets the color-coded probability signals carry maximal
contrast against a near-black ground - the dark IS the instrument, not a style
choice. Color strategy: **Restrained** - tinted-neutral surfaces plus one teal
accent for selection/primary action, and a semantic green/amber/red set reserved
exclusively for data meaning.

## Color

All values OKLCH. Neutrals sit on a cool blue-slate hue (~248) so the surface
reads as one cool family rather than pure gray; the accent is teal (~195).

Token names are shadcn-compatible and are consumed as Tailwind utilities
(`bg-card`, `text-muted-foreground`, `border-border`, `text-pos`) via the
`@theme inline` mapping - not as raw `var()` reads.

### Surfaces

| Token | OKLCH | Use |
|---|---|---|
| `--background` | `oklch(0.165 0.012 248)` | App background (near-black blue-slate) |
| `--card` / `--popover` | `oklch(0.205 0.013 248)` | Panels, cards, popovers |
| `--muted` | `oklch(0.25 0.012 248)` | Inset wells, muted fills |
| `--secondary` | `oklch(0.26 0.013 248)` | Active nav item, secondary button |
| `--accent` | `oklch(0.29 0.014 248)` | Hover / raised |
| `--border` | `oklch(1 0 0 / 9%)` | Borders, dividers |
| `--input` | `oklch(1 0 0 / 12%)` | Input borders |

Borders are white at low alpha, not an opaque slate step - they read correctly
over any of the surfaces above.

### Ink (text)

| Token | OKLCH | Measured contrast | Use |
|---|---|---|---|
| `--foreground` | `oklch(0.93 0.006 240)` | **15.69:1** on `--background` | Primary text, data values |
| `--muted-foreground` | `oklch(0.72 0.012 245)` | **7.23:1** on `--card` | Secondary text, captions, table headers |

Both ratios are computed from WCAG relative luminance, not estimated from OKLCH
lightness. `--muted-foreground` is the floor for body-sized text; never go
muted-er for anything a user must read.

New surfaces must hold >=4.5:1 for body text and >=3:1 for large text and icons.
Any color introduced outside this table needs its own measured ratio - OKLCH `L`
is not a contrast proxy.

### Accent (selection + primary action only, never decoration)

| Token | OKLCH | Use |
|---|---|---|
| `--primary` | `oklch(0.74 0.12 195)` | Primary button bg, active selection, links |
| `--primary-foreground` | `oklch(0.18 0.02 230)` | Text on primary |
| `--ring` | `oklch(0.74 0.12 195)` | Focus ring (same teal as primary) |
| `--signal` | `oklch(0.74 0.12 195)` | Live/streaming indicator |

### Semantic data vocabulary (meaning only)

Direction (the rising/falling probability axis) and level (high/medium/low) are
the only places saturated color appears. Each pairs with a text label or sign so
meaning never rests on hue alone.

| Token | OKLCH | Meaning |
|---|---|---|
| `--pos` | `oklch(0.72 0.15 158)` | Rising probability, positive delta, HIGH |
| `--pos-foreground` | `oklch(0.16 0.02 160)` | Text on `--pos` fills |
| `--neg` | `oklch(0.64 0.2 22)` | Falling probability, negative delta, LOW |
| `--neg-foreground` | `oklch(0.96 0.01 20)` | Text on `--neg` fills |
| `--warn` | `oklch(0.78 0.13 75)` | MEDIUM level, caution, degraded state |
| `--destructive` | `oklch(0.62 0.2 25)` | Destructive action confirm |

Tints are expressed as alpha on these tokens (`bg-neg/10`, `border-neg/40`),
not as separate `*-bg` tokens.

### Charts

Five slots, deliberately hue-offset so adjacent series stay distinguishable:
`--chart-1` teal `oklch(0.74 0.12 195)`, `--chart-2` amber
`oklch(0.78 0.13 75)`, `--chart-3` green `oklch(0.72 0.15 158)`, `--chart-4` red
`oklch(0.64 0.2 22)`, `--chart-5` slate `oklch(0.6 0.012 245)`. Encode series
with fills, never outline-only.

State vocabulary (standardized across every interactive element): default,
hover (background shifts ±0.06-0.12 OKLCH `L`, or border/shadow only - the
foreground never dims), focus (`focus-visible` ring in `--ring`), active
(`--secondary` fill), disabled (opacity 0.5, `not-allowed`), loading (skeleton),
error (`--neg` border + tint).

## Typography

Two families on a real contrast axis (sans for UI, mono for data) - not two
similar sans.

- **UI / prose:** `--font-sans` = `ui-sans-serif, system-ui, -apple-system,
  BlinkMacSystemFont, "Segoe UI", sans-serif`. Familiar, fast, offline, no CDN
  dependency. Carries headings, labels, buttons, body.
- **Data / figures:** `--font-mono` = `ui-monospace, "Cascadia Code", Menlo,
  Consolas, monospace`. Every probability, percentage, delta, score, Brier,
  timestamp, and table figure is monospaced and uses
  `font-variant-numeric: tabular-nums` so columns of numbers align and digits
  do not jitter as values update. This is the core typographic move: numbers
  read as instrument readouts. Applied via the `.tnum` utility or Tailwind's
  `font-mono tabular-nums`.

Body sets `14px` / `1.6` line-height. Fixed scale, ratio ~1.2:
`11px` micro-label / `12px` caption / `13px` body / `14px` base / `16px`
sub-head / `20px` panel value / `28px` report headline figure. Headings via
weight (600-750), not size explosion.

CJK copy needs more leading than Latin: headings >=1.7, body 1.6-1.8.

## Layout

- **App shell:** sticky header holding a headline ticker (brand + probability
  movers) above the main nav, then a right-aligned cluster of live status and the
  operator-key control. The nav is grouped into four labelled tracks - 事件情报平台,
  Sports Prediction OS, 体育运营, 世界杯运营 - separated by hairline dividers, with
  group labels hidden below `md`. A skip-link targets `#main-content`.
- **Content region:** centered, `max-w-6xl`/`max-w-7xl` depending on density,
  16-24px gutter. Single language (Simplified Chinese); no lang switcher.
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
- **Charts:** recharts, with the existing brush/zoom selection reused rather than
  re-implemented. `src/components/ui/chart-lite.tsx` carries the lightweight
  inline readouts (sparkline-scale trajectories) where a full chart would be
  heavier than the data warrants.
- **Icons:** 1.6-1.8px single-stroke SVG from `lucide-react`, sized `size-3.5`
  in nav and dense tables. Never emoji.
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
