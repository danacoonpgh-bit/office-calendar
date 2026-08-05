# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

A read-only office calendar for CB Realty North Hills agents, served as a static
site from GitHub Pages: <https://danacoonpgh-bit.github.io/office-calendar/>.
Agents open the link — there is no login and no editing UI on the site.

There is **no build step, no package manager, no test suite, and no linter**.
`index.html` is a single self-contained file (inline `<style>` and vanilla ES5-ish
`<script>`, no framework, no bundler). The two Python scripts are run by GitHub
Actions, not by a local toolchain. To preview the site, open `index.html` over
`http://` (e.g. `python3 -m http.server`) — `file://` breaks the `fetch()` calls.

`main` is the deployed branch: anything merged there is live immediately.

## Data flow

The page fetches two JSON files at load and merges them client-side
(`load()` in `index.html`), auto events first, then manual events appended per day:

| File | Written by | Hand-editable? |
|---|---|---|
| `events.json` | `scripts/sync_calendar.py` (Outlook) | **No** — the sync overwrites it |
| `manual-events.json` | Humans **and** `scripts/sync_workshops.py` | Yes, carefully — see below |

Both files are keyed by ISO date (`"2026-08-15": [ ...events ]`). `events.json`
wraps its map in `{generated, source, days}`; the loader tolerates either shape.

An event object is exactly six fields: `id`, `title`, `time`, `category`, `link`,
`notes`.

## Things that will bite you

**`events.json` is currently dormant and empty.** The company's Microsoft 365
tenant has calendar publishing (ICS links) disabled, so there is no feed URL. The
`ICS_URL` repo secret is unset, and `sync_calendar.py` exits cleanly without
writing when it's missing. That is the expected state, not a bug. Today the
calendar's real content is published to Outlook by a scheduled Claude task on
Dana's computer, plus `manual-events.json`. Don't "fix" the empty `events.json`
by hand-filling it.

**`manual-events.json` has a machine-enforced format.** `sync_workshops.py`
rewrites the whole file through its own `dump()`, which emits one line per event
with a fixed field order (`id, title, time, category, link, notes`). Hand edits
should match that layout or the next sync produces a large noise diff. Never
reformat this file with a generic JSON pretty-printer.

**The workshop source is a client-side widget, not the WordPress API.**
`cbrmarketingworkshops.com` runs The Events Calendar plugin but publishes nothing
through it — `tribe_events` is empty, the iCal feed is 0 bytes, no JSON-LD. The
schedule is rendered by an Elfsight widget that loads from
`core.service.elfsight.com/p/boot/`. A scraper reading the served HTML sees an
empty page and concludes there are no workshops; that mistake dropped eight
August 2026 sessions. **An empty result from the widget is a red flag, not a
normal empty state** — the widget normally holds hundreds of events. Check the
widget id and the boot payload shape before believing it.

**Workshop times are not all Eastern.** Each Elfsight event carries its own IANA
`timeZone`; some are authored in Pacific. `to_eastern()` converts every time to
`America/New_York` before writing. Taking the raw clock value puts agents on the
calendar three hours early.

**Categories live in two places.** `CATEGORY_RULES` in `sync_calendar.py` maps
title/Outlook-category keywords onto `WORKSHOP | TRAINING | MEETING | OTHER`;
`CATEGORY_META` in `index.html` maps those same four onto labels and CSS classes
(`.cat-*`). Adding a category means editing both, plus the CSS.

**Cron is UTC and has no DST.** `sync-workshops.yml` therefore lists two
schedules, EDT and EST; the script is idempotent so the off-season run is a
harmless no-op. Keep that property when changing the sync.

## Conventions

- Dedupe is by `(date, normalized title)` — `norm_title()` strips everything but
  lowercase alphanumerics. When correcting a title by hand, use the full title as
  it appears in the source, or the next sync will re-add the event as a duplicate.
- Manual event ids are free-form (`m1`); workshop ids are `ws-MMDD-slug`, suffixed
  `-2`, `-3` on collision. Ids only need to be unique within the merged day.
- Times are display strings, not timestamps: `"1:00–2:00 PM"` (en dash, meridiem
  collapsed when both ends share it) or `"All day"`.
- `sync_workshops.py` honors `DRY_RUN=true`, `HORIZON_DAYS`, and `WIDGET_ID`. Use
  `DRY_RUN=true python3 scripts/sync_workshops.py` to check the source without
  writing — manual workflow runs default to a dry run.

## Testing changes

There are no automated tests. Verify by hand:

- Python changes: run the script with `DRY_RUN=true` and read the printed plan.
- `index.html` changes: serve locally and check both Month and List views, the
  day modal, and the mobile breakpoint. The view preference persists in
  `localStorage` under `cbnh_view_pref`.
- JSON changes: confirm the page still loads — a malformed `events.json` surfaces
  as the "couldn't be loaded" banner, and a malformed `manual-events.json` is
  swallowed silently by the `.catch()` in `load()`.
