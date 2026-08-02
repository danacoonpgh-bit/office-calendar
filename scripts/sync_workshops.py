#!/usr/bin/env python3
"""Pull CBR marketing workshops into manual-events.json.

Source: the Elfsight event-calendar widget embedded on
https://www.cbrmarketingworkshops.com/workshop-schedule/.

WHY NOT THE WORDPRESS API: the site runs The Events Calendar plugin, but CBR
does not publish through it — `tribe_events` is empty, the iCal feed is 0 bytes,
and no schema.org JSON-LD is emitted. The real schedule is rendered client-side
by an Elfsight widget that fetches its data from core.service.elfsight.com. Any
scraper reading the served HTML sees an empty page and wrongly concludes there
are no workshops. (That mistake cost August 2026 eight missing sessions.)

The boot endpoint is public, unauthenticated, and returns every event the widget
knows about in one JSON document.

TIMEZONES: each event carries its own IANA `timeZone`. Most are
America/New_York, but some are authored in Pacific — e.g. the 2026-08-19 luxury
session is stored 12:00 America/Los_Angeles, which is 3:00 PM Eastern. Taking
the raw clock value puts agents on the calendar three hours early, so every time
is converted to Eastern before it is written.

Env:
  DRY_RUN=true   report what would be added, write nothing
  HORIZON_DAYS   how far ahead to look (default 120)
  WIDGET_ID      override the Elfsight widget id
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SITE = "https://www.cbrmarketingworkshops.com"
SCHEDULE_PAGE = f"{SITE}/workshop-schedule/"
BOOT = "https://core.service.elfsight.com/p/boot/"

# Discovered from the schedule page at runtime; this is the fallback.
DEFAULT_WIDGET_ID = "83f341c0-9795-447c-83a3-6d9450aed62b"

REPO = Path(__file__).resolve().parent.parent
MANUAL = REPO / "manual-events.json"

HORIZON_DAYS = int(os.environ.get("HORIZON_DAYS", "120"))
DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")

EASTERN = ZoneInfo("America/New_York")

# The four CBR workshop tiers, when one is tagged on an event.
LEVELS = ("Overview", "Essentials", "Advanced", "Unlocked")

TIMEOUT = 30
UA = {"User-Agent": "office-calendar-sync (+https://github.com/danacoonpgh-bit/office-calendar)"}


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------

def discover_widget_id() -> str:
    """Read the widget id off the schedule page so a CBR rebuild doesn't break us."""
    try:
        r = requests.get(SCHEDULE_PAGE, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  ! could not read schedule page ({e}); using default widget id")
        return DEFAULT_WIDGET_ID

    m = re.search(r"eapps-event-calendar-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", r.text)
    if m:
        found = m.group(1)
        if found != DEFAULT_WIDGET_ID:
            print(f"  widget id changed: {found}")
        return found

    print("  ! widget id not found on page; using default")
    return DEFAULT_WIDGET_ID


def fetch_widget(widget_id: str) -> tuple[list[dict], dict[str, str]]:
    """Return (events, event_type_id -> name) from the Elfsight boot payload."""
    try:
        r = requests.get(BOOT, params={"w": widget_id}, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        payload = r.json()
    except (requests.RequestException, ValueError) as e:
        print(f"  ! boot request failed: {e}")
        return [], {}

    try:
        settings = payload["data"]["widgets"][widget_id]["data"]["settings"]
    except (KeyError, TypeError):
        print("  ! unexpected boot payload shape — widget id wrong, or Elfsight changed the API")
        return [], {}

    types = {t.get("id"): t.get("name", "") for t in settings.get("eventTypes") or [] if isinstance(t, dict)}
    return settings.get("events") or [], types


# --------------------------------------------------------------------------
# shaping — match the conventions already in manual-events.json
# --------------------------------------------------------------------------

def to_eastern(part: dict, tz_name: str) -> datetime | None:
    """Combine an Elfsight {date,time} in its own zone and return Eastern."""
    if not isinstance(part, dict):
        return None
    d, t = part.get("date"), part.get("time") or "00:00"
    if not d:
        return None
    try:
        naive = datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    try:
        source_tz = ZoneInfo(tz_name or "America/New_York")
    except Exception:
        source_tz = EASTERN
    return naive.replace(tzinfo=source_tz).astimezone(EASTERN)


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


def level_of(event: dict, type_names: list[str]) -> str | None:
    haystack = " ".join(type_names + [str(t) for t in event.get("tags") or []] + [event.get("name") or ""])
    for lvl in LEVELS:
        if re.search(rf"\b{lvl}\b", haystack, re.I):
            return lvl
    return None


def register_link(event: dict) -> str:
    """The 'register' button's URL."""
    actions = event.get("actions") or []
    for want_primary in (True, False):
        for a in actions:
            if not isinstance(a, dict) or (want_primary and not a.get("primary")):
                continue
            link = a.get("link") or {}
            url = (link.get("value") or link.get("rawValue") or "").strip()
            if url:
                return url
    m = re.search(r"https://events\.teams\.microsoft\.com/event/[^\s\"'<>\\]+", json.dumps(event))
    return html.unescape(m.group(0)) if m else ""


def slug_of(title: str) -> str:
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    skip = {"the", "a", "an", "to", "for", "with", "and", "your", "of", "in", "on"}
    keep = [w for w in words if w not in skip] or words
    return "-".join(keep[:3]) or "workshop"


def norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", html.unescape(title or "").lower())


def to_entry(event: dict, types: dict[str, str], existing_ids: set[str]) -> tuple[str, dict] | None:
    tz_name = event.get("timeZone") or "America/New_York"
    start = to_eastern(event.get("start") or {}, tz_name)
    if not start:
        return None
    end = to_eastern(event.get("end") or {}, tz_name)

    title = html.unescape((event.get("name") or "").strip())
    title = re.sub(r"\s+", " ", title)
    if not title:
        return None

    type_names = [types.get(t, "") for t in event.get("eventType") or []]
    lvl = level_of(event, type_names)
    desc = clean_text(event.get("description", ""))
    prefix = f"CBR Marketing Workshop ({lvl}, virtual via Teams)." if lvl \
        else "CBR Marketing Workshop (virtual via Teams)."
    notes = " ".join(x for x in (prefix, desc, "Click the link to register (Teams).") if x)

    time_text = "All day" if event.get("isAllDay") else fmt_time(start, end)

    base = f"ws-{start:%m%d}-{slug_of(title)}"
    eid, n = base, 2
    while eid in existing_ids:
        eid, n = f"{base}-{n}", n + 1
    existing_ids.add(eid)

    return start.date().isoformat(), {
        "id": eid,
        "title": title,
        "time": time_text,
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

    widget_id = os.environ.get("WIDGET_ID") or discover_widget_id()
    raw_events, types = fetch_widget(widget_id)
    print(f"  widget holds {len(raw_events)} event(s) in total")

    if not raw_events:
        print("\nNo events came back from the widget.")
        print("This is now a red flag, not a normal empty state — the widget")
        print("normally carries hundreds of events. Check the widget id and the")
        print("boot payload shape before assuming CBR published nothing.")
        return 0

    days = json.loads(MANUAL.read_text(encoding="utf-8"))
    known_ids = {e.get("id") for v in days.values() for e in v}
    seen = {(d, norm_title(e.get("title", ""))) for d, v in days.items() for e in v}

    added, skipped, out_of_range = [], 0, 0
    for ev in raw_events:
        shaped = to_entry(ev, types, known_ids)
        if not shaped:
            continue
        day, entry = shaped
        if not (today.isoformat() <= day <= horizon.isoformat()):
            out_of_range += 1
            continue
        if (day, norm_title(entry["title"])) in seen:
            skipped += 1
            continue
        if not entry["link"]:
            print(f"  ! no registration link for {entry['title']} ({day}) — adding anyway")
        days.setdefault(day, []).append(entry)
        seen.add((day, norm_title(entry["title"])))
        added.append((day, entry))

    print(f"  {out_of_range} outside the {HORIZON_DAYS}-day window")
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
