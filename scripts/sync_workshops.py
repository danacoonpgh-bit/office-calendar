#!/usr/bin/env python3
"""Pull CBR marketing workshops into manual-events.json.

Source: https://www.cbrmarketingworkshops.com/ (WordPress + The Events Calendar).

Reads the plugin's REST API, falls back to the JSON-LD the plugin embeds in the
schedule page, and merges anything new into manual-events.json as WORKSHOP
entries. Existing entries are never modified or removed, so the file stays the
hand-maintained source of truth for everything else on the calendar.

The site publishes in batches and sits empty between them. Finding zero
workshops is a normal outcome, not a failure.

Env:
  DRY_RUN=true   report what would be added, write nothing
  HORIZON_DAYS   how far ahead to look (default 120)
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

SITE = "https://www.cbrmarketingworkshops.com"
API = f"{SITE}/wp-json/tribe/events/v1/events"
SCHEDULE_PAGE = f"{SITE}/workshop-schedule/"

REPO = Path(__file__).resolve().parent.parent
MANUAL = REPO / "manual-events.json"

HORIZON_DAYS = int(os.environ.get("HORIZON_DAYS", "120"))
DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")

# The four CBR workshop tiers. Anything else in an event's categories is noise.
LEVELS = ("Overview", "Essentials", "Advanced", "Unlocked")

TIMEOUT = 30
UA = {"User-Agent": "office-calendar-sync (+https://github.com/danacoonpgh-bit/office-calendar)"}


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------

def fetch_api(start: date, end: date) -> list[dict]:
    """Page through The Events Calendar REST API."""
    events, page = [], 1
    while True:
        try:
            r = requests.get(
                API,
                params={
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "per_page": 50,
                    "page": page,
                },
                headers=UA,
                timeout=TIMEOUT,
            )
        except requests.RequestException as e:
            print(f"  ! API request failed: {e}")
            return events

        # The plugin 404s past the last page rather than returning an empty list.
        if r.status_code == 404:
            break
        if not r.ok:
            print(f"  ! API returned HTTP {r.status_code}")
            break

        batch = r.json().get("events") or []
        events.extend(batch)
        if len(batch) < 50:
            break
        page += 1

    return events


def fetch_jsonld() -> list[dict]:
    """Fallback: The Events Calendar embeds schema.org Event blocks in the page.

    Only used when the API yields nothing but the page still renders events —
    e.g. if the REST route gets disabled.
    """
    try:
        r = requests.get(SCHEDULE_PAGE, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  ! schedule page fetch failed: {e}")
        return []

    out = []
    for block in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        r.text,
        re.S | re.I,
    ):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "Event":
                out.append(
                    {
                        "title": item.get("name", ""),
                        "start_date": (item.get("startDate") or "").replace("T", " "),
                        "end_date": (item.get("endDate") or "").replace("T", " "),
                        "description": item.get("description", ""),
                        "website": item.get("url", ""),
                        "url": item.get("url", ""),
                        "categories": [],
                    }
                )
    return out


# --------------------------------------------------------------------------
# shaping — match the conventions already in manual-events.json
# --------------------------------------------------------------------------

def parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    value = value.strip().replace("T", " ")
    value = re.sub(r"([+-]\d{2}):?(\d{2})$", "", value).strip()  # drop any offset
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def fmt_time(start: datetime, end: datetime | None) -> str:
    """'1:00–2:00 PM' when both ends share a meridiem, else '1:00 PM–2:00 AM'."""

    def clock(dt: datetime) -> str:
        return dt.strftime("%I:%M %p").lstrip("0")

    if not end or end <= start:
        return clock(start)
    s, e = clock(start), clock(end)
    if s[-2:] == e[-2:]:  # same AM/PM — collapse it, as the file does
        return f"{s[:-3]}–{e}"
    return f"{s}–{e}"


def clean_text(raw: str, limit: int = 400) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[: stop + 1] if stop > limit // 2 else cut.rstrip() + "…").strip()


def level_of(event: dict) -> str | None:
    names = [c.get("name", "") for c in event.get("categories") or [] if isinstance(c, dict)]
    haystack = " ".join(names) + " " + (event.get("title") or "")
    for lvl in LEVELS:
        if re.search(rf"\b{lvl}\b", haystack, re.I):
            return lvl
    return None


def register_link(event: dict) -> str:
    """Prefer the Teams registration URL; fall back to the event page."""
    for field in ("website", "url"):
        val = (event.get(field) or "").strip()
        if "events.teams.microsoft.com" in val:
            return val
    blob = json.dumps(event)
    m = re.search(r"https://events\.teams\.microsoft\.com/event/[^\s\"'<>\\]+", blob)
    if m:
        return html.unescape(m.group(0))
    return (event.get("website") or event.get("url") or "").strip()


def slug_of(title: str) -> str:
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    skip = {"the", "a", "an", "to", "for", "with", "and", "your", "of", "in", "on"}
    keep = [w for w in words if w not in skip] or words
    return "-".join(keep[:3]) or "workshop"


def norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", html.unescape(title or "").lower())


def to_entry(event: dict, existing_ids: set[str]) -> tuple[str, dict] | None:
    start = parse_dt(event.get("start_date", ""))
    if not start:
        return None
    end = parse_dt(event.get("end_date", ""))
    title = html.unescape((event.get("title") or "").strip())
    if not title:
        return None

    lvl = level_of(event)
    desc = clean_text(event.get("description", ""))
    prefix = f"CBR Marketing Workshop ({lvl}, virtual via Teams)." if lvl \
        else "CBR Marketing Workshop (virtual via Teams)."
    notes = " ".join(x for x in (prefix, desc, "Click the link to register (Teams).") if x)

    base = f"ws-{start:%m%d}-{slug_of(title)}"
    eid, n = base, 2
    while eid in existing_ids:
        eid, n = f"{base}-{n}", n + 1
    existing_ids.add(eid)

    return start.date().isoformat(), {
        "id": eid,
        "title": title,
        "time": fmt_time(start, end),
        "category": "WORKSHOP",
        "link": register_link(event),
        "notes": notes,
    }


# --------------------------------------------------------------------------
# writing — preserve the file's existing hand-edited layout
# --------------------------------------------------------------------------

def dump(days: dict[str, list[dict]]) -> str:
    """One line per event, dates in order — matches how the file is written today."""
    lines = ["{"]
    keys = sorted(days)
    for i, day in enumerate(keys):
        lines.append(f'  "{day}": [')
        entries = days[day]
        for j, e in enumerate(entries):
            fields = ", ".join(
                f"{json.dumps(k)}: {json.dumps(e[k], ensure_ascii=False)}"
                for k in ("id", "title", "time", "category", "link", "notes")
                if k in e
            )
            lines.append(f"    {{ {fields} }}" + ("," if j < len(entries) - 1 else ""))
        lines.append("  ]" + ("," if i < len(keys) - 1 else ""))
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> int:
    today = date.today()
    horizon = today + timedelta(days=HORIZON_DAYS)
    print(f"Looking for workshops {today} → {horizon}")

    events = fetch_api(today, horizon)
    print(f"  API returned {len(events)} event(s)")
    if not events:
        events = fetch_jsonld()
        if events:
            print(f"  JSON-LD fallback found {len(events)} event(s)")

    if not events:
        print("\nNo workshops published right now — nothing to add.")
        print("(The CBR site sits empty between publishing batches; this is normal.)")
        return 0

    days = json.loads(MANUAL.read_text(encoding="utf-8"))
    known_ids = {e.get("id") for v in days.values() for e in v}
    seen = {(d, norm_title(e.get("title", ""))) for d, v in days.items() for e in v}

    added, skipped = [], 0
    for ev in events:
        shaped = to_entry(ev, known_ids)
        if not shaped:
            continue
        day, entry = shaped
        if (day, norm_title(entry["title"])) in seen:
            skipped += 1
            continue
        if not entry["link"]:
            print(f"  ! no registration link for {entry['title']} ({day}) — adding anyway")
        days.setdefault(day, []).append(entry)
        seen.add((day, norm_title(entry["title"])))
        added.append((day, entry))

    print(f"\n{len(added)} new, {skipped} already on the calendar")
    for day, e in sorted(added):
        print(f"  + {day}  {e['time']:<16} {e['title']}")

    if not added:
        return 0
    if DRY_RUN:
        print("\nDRY_RUN — manual-events.json not written.")
        return 0

    MANUAL.write_text(dump(days), encoding="utf-8")
    print(f"\nWrote {MANUAL.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
