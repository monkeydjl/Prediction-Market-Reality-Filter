# Product

## Register

product

## Users

Analysts and researchers who monitor forward-looking events and watch how their
probability shifts over time. They are domain-fluent (prediction markets,
macro/policy/crypto news, calibration) and patient: they sit with the interface
for long sessions, scanning many events, drilling into one, comparing it to
precedent, and judging whether the evidence justifies the probability move. The
job to be done is human judgement, not automation - the platform surfaces a
ranked, scored, sourced picture and the user decides what matters. They are
never placing trades here; they are reviewing intelligence.

## Product Purpose

The Event Intelligence Platform turns public information into reviewable
intelligence: it discovers candidate events from multiple sources, scores their
credibility and impact, estimates a probability change against a baseline, and
produces a human-readable report. Success is a reviewer trusting the numbers
at a glance - seeing which events moved, how much, how trustworthy the evidence
is, and what a similar past event did - without leaving the screen or
second-guessing a mislabeled control. The interface is an instrument panel for
probability change, not a feed and not a trading terminal.

## Brand Personality

Authoritative, composed, instrument-grade. Three words: precise, calm,
trustworthy. The voice is that of a senior analyst's terminal - it states
numbers plainly, color-codes meaning consistently, and never decorates. It
should feel like a tool a professional reaches for daily and stops noticing,
because it never gets in the way. Confidence through restraint, not flourish.

## Anti-references

- The generic AI look: Inter everywhere, purple-to-blue gradients, gradient
  text, nested cards, the big-number hero-metric template. Disqualifying.
- Toy / playful: rounded-everything, saturated candy colors, emoji as UI,
  bouncy motion. This is a professional instrument, not a consumer app.
- Marketing-page energy: oversized hero, sparse information, whitespace as the
  point. This screen is dense by design; the user wants signal per pixel.
- Legacy admin templates: Bootstrap defaults, flat gray-on-gray, dense but
  hierarchy-free tables that read as a spreadsheet dump.

## Design Principles

- **Numbers are the interface.** Probability, change, credibility, impact, and
  calibration are the content. Typeset them as data (tabular, aligned,
  monospaced figures), color-code their meaning, and let everything else recede.
- **Color carries meaning, never decoration.** One semantic vocabulary -
  rising/falling, high/medium/low - applied identically everywhere. If a color
  appears, it means something the user can rely on.
- **Density with hierarchy.** Show a lot at once (the analyst wants it), but
  rank it ruthlessly: the headline number is unmissable, the supporting detail
  is present but quiet. Dense is not the same as flat.
- **The tool disappears.** Earned familiarity over novelty. Standard
  affordances, consistent controls, no invented widgets. The user thinks about
  events, not about the UI.
- **Human-in-the-loop, always.** Every output frames a judgement for a person
  (escalate / track / watch). Never imply automation or a trade.

## Accessibility & Inclusion

- WCAG 2.1 AA target. Body text >= 4.5:1, large/secondary text >= 3:1, verified
  against the dark surfaces. No meaning conveyed by color alone - direction and
  level always carry a text label or sign alongside the hue (important for
  red/green color-vision deficiency, the core rising/falling axis).
- Full keyboard reachability for every control; visible focus rings.
- `prefers-reduced-motion` honored - transitions degrade to instant/crossfade.
- Bilingual (English / Simplified Chinese) as a first-class requirement, two
  parity surfaces.
