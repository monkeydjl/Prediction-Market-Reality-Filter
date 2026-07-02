# Task 4 Report: Extend `write_report` + global assertions

**Status:** DONE
**Base commit:** `dca9c78`
**Task commit:** `a7ba066`
**Plan:** `docs/superpowers/plans/2026-07-02-replay-html-report.md` (Task 4, lines 723-894)

## Summary

Task 4 closes spec §4.5 by extending `write_report` to emit `report.html`
alongside the existing `report.md` / `metrics.json` / `cases.jsonl`, and by
adding 5 global/integration tests to `TestRenderHtml` that lock in the
vocabulary, self-containment, XSS-escape, all-sections, and write_report
integration guarantees.

## Files modified

- `backend/app/replay/report.py` — `write_report` extended with one new
  call (verbatim from plan Step 3): writes `report.html` immediately after
  `metrics.json`, before the optional `cases.jsonl` block. Docstring updated
  to list 4 files. Return value unchanged (`md_path`) for backward compat.
- `backend/tests/test_replay_report.py` — added `from pathlib import Path`
  import and 5 new tests inside `TestRenderHtml`:
  1. `test_render_html_includes_all_sections` — verifies all 7 section ids.
  2. `test_render_html_contains_no_banned_terms` — vocabulary lock (WITH
     controller-authorized amendment, see below).
  3. `test_render_html_is_self_contained` — no external src/href/@import/
     url()/`<link>`/`<script src>`.
  4. `test_render_html_escapes_event_ids` — `<script>` in event_id is
     escaped to `&lt;script&gt;...&lt;/script&gt;`; exactly 1 `<script>`
     tag (the inline JS block) in output.
  5. `test_write_report_creates_html_file` — integration: `report.html`
     exists, starts with `<!DOCTYPE html>`, non-empty; `report.md` and
     `metrics.json` still produced; return value is `report.md` path.

## Plan amendment applied (controller-authorized)

The plan's `test_render_html_contains_no_banned_terms` used naive substring
matching (`assertNotIn(term, lower)`). This is a PLAN BUG because:
- CSS property `position` (in `.bar-container { position: relative; }`)
  contains the banned term `position`.
- CSS property `border` contains the substring `order`.

The vocabulary lock targets TRADING terminology, not CSS keywords. Fix
applied verbatim per controller authorization:
- `long`, `short`, `buy`, `sell`, `kelly`: word-boundary regex
  (`\b<term>\b`, case-insensitive) must NOT match.
- `position`: count of `\bposition\b` equals count of `\bposition\s*:`
  (i.e. every whole-word occurrence is a CSS property declaration).
- `order`: `\border\b` count is 0 (allows CSS `border` which contains
  `order` only as a substring, not as a whole word).

All other Task 4 code is verbatim from the plan.

## TDD trace

1. **Red:** Wrote 5 tests; ran them. `test_write_report_creates_html_file`
   FAILED as expected (`report.html` not yet created). The other 4 passed
   because `render_html` already exists from Tasks 1-3.
2. **Green:** Extended `write_report` with the HTML write call (verbatim
   from plan Step 3). All 5 new tests now pass.
3. **Regression:** Full `test_replay_report.py` = 21 passed (3 in
   TestRenderMarkdown/TestRenderJson + 18 in TestRenderHtml).

## Test results

- `TestRenderHtml` (Tasks 1-3 + Task 4): **18 passed** (13 existing + 5 new)
- `tests/test_replay_report.py` (full file): **21 passed**, 0 failed
- Full backend suite (`python -m pytest --ignore=tests/test_gbm_engine.py -q`):
  **1876 passed, 11 skipped, 20 subtests passed, 0 failures** in 352.11s
  (`test_gbm_engine.py` excluded as pre-existing env issue, per plan)

## Self-review checklist

- [x] 5 new tests pass + 13 existing still pass (18 total in TestRenderHtml)
- [x] Full backend suite passes (0 failures, `test_gbm_engine.py` excluded)
- [x] Vocabulary lock test passes with regex approach (CSS `position`/`border`
      do not trip it — `position` only appears as CSS property declaration,
      `order` never appears as a whole word)
- [x] `write_report` returns `report.md` path (backward compat verified by
      `test_write_report_creates_html_file`)
- [x] `render_markdown`/`render_json` untouched (only `write_report` body
      changed in report.py; `render_html` and helpers unchanged)
- [x] XSS escape works (event_id with `<script>` is escaped to
      `&lt;script&gt;alert(1)&lt;/script&gt;`; only 1 `<script>` tag in output)
- [x] UTF-8 encoding used (`encoding="utf-8"` on the new `write_text` call)
- [x] No new files created (only modified the 2 specified files)
- [x] No new dependencies
- [x] `render_html` remains pure (no IO; `write_report` is the only IO site)
- [x] Commit message uses plan Step 7 text verbatim

## Concerns

None. All Task 4 acceptance criteria met. The plan amendment was applied
exactly as authorized; no other deviations from the plan.
