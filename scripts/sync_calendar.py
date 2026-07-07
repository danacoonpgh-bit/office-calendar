#!/usr/bin/env python3
"""Sync the office calendar from a published Outlook ICS feed to events.json.

Runs in GitHub Actions on a schedule. Reads the feed URL from the ICS_URL
environment variable (repo secret). If the secret isn't configured yet, exits
cleanly without touching events.json, so the site keeps showing the last
published data.
"""

import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import icalendar
import recurring_ical_events

TZ = ZoneInfo("America/New_York")
OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "events.json")

# How far the published calendar looks back/ahead.
LOOKBACK_DAYS = 35   # keep the current + previous month browsable
LOOKAHEAD_DAYS = 120

CATEGORY_RULES = [
    ("WORKSHOP", ["workshop", "masterclass", "mastermind"]),
    ("TRAINING", ["training", "agent development", "agent panel", "skill", "bootcamp", "class", "coaching", "growth session"]),
    ("MEETING", ["meeting", "news to use", "town hall", "huddle", "office hours"]),
]

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


def categorize(title, outlook_categories):
    haystack = " ".join([title or ""] + list(outlook_categories or [])).lower()
    for cat, needles in CATEGORY_RULES:
        if any(n in haystack for n in needles):
            return cat
    return "OTHER"


def pick_link(location, description):
    """Prefer a meeting link (Teams/Zoom) from location, then description."""
    candidates = URL_RE.findall(location or "") + URL_RE.findall(description or "")
    for url in candidates:
        if any(host in url.lower() for host in ("teams.microsoft.com", "zoom.us", "meet.google")):
            return url.rstrip(".,;")
    return candidates[0].rstrip(".,;") if candidates else ""


def clean_notes(description, location):
    """Short human-readable notes: drop meeting boilerplate, IDs, passcodes, URLs."""
    lines = []
    for raw in (description or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if line.startswith("____") or URL_RE.search(line):
            continue
        if any(k in low for k in ("meeting id", "passcode", "join:", "dial in", "need help", "microsoft teams", "download teams", "join on the web")):
            continue
        lines.append(line)
    text = " ".join(lines)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 240:
        text = text[:237].rstrip() + "…"
    loc = (location or "").strip()
    if loc and not URL_RE.search(loc) and loc.lower() not in text.lower():
        text = (loc + ". " + text).strip() if text else loc
    return text


def fmt_time(dt_start, dt_end):
    def t(d):
        s = d.strftime("%I:%M %p")
        return s.lstrip("0")
    if dt_end and dt_end > dt_start:
        return t(dt_start) + "–" + t(dt_end)
    return t(dt_start)


def stable_id(uid, day_key):
    return "o" + hashlib.sha1((str(uid) + "|" + day_key).encode()).hexdigest()[:10]


def main():
    ics_url = os.environ.get("ICS_URL", "").strip()
    if not ics_url:
        print("ICS_URL secret is not set — skipping sync, keeping current events.json.")
        return 0

    resp = requests.get(ics_url, timeout=60, headers={"User-Agent": "office-calendar-sync"})
    resp.raise_for_status()
    cal = icalendar.Calendar.from_ical(resp.content)

    today = datetime.now(TZ).date()
    window_start = today - timedelta(days=LOOKBACK_DAYS)
    window_end = today + timedelta(days=LOOKAHEAD_DAYS)

    occurrences = recurring_ical_events.of(cal).between(window_start, window_end)

    days = {}
    for ev in occurrences:
        summary = str(ev.get("SUMMARY", "")).strip() or "(untitled)"
        status = str(ev.get("STATUS", "")).upper()
        if status == "CANCELLED":
            continue
        description = str(ev.get("DESCRIPTION", "") or "")
        location = str(ev.get("LOCATION", "") or "")
        uid = str(ev.get("UID", "")) or summary
        cats_prop = ev.get("CATEGORIES")
        outlook_cats = []
        if cats_prop is not None:
            try:
                outlook_cats = [str(c) for c in cats_prop.cats]
            except Exception:
                outlook_cats = [str(cats_prop)]

        dtstart = ev["DTSTART"].dt
        dtend = ev.get("DTEND")
        dtend = dtend.dt if dtend is not None else None

        if isinstance(dtstart, datetime):
            start_local = dtstart.astimezone(TZ)
            end_local = dtend.astimezone(TZ) if isinstance(dtend, datetime) else None
            event_days = [start_local.date()]
            time_label = fmt_time(start_local, end_local)
        else:
            # All-day event; DTEND is exclusive per RFC 5545.
            end_day = dtend if isinstance(dtend, date) else dtstart + timedelta(days=1)
            n = max((end_day - dtstart).days, 1)
            event_days = [dtstart + timedelta(days=i) for i in range(n)]
            time_label = "All day"

        for d in event_days:
            if d < window_start or d > window_end:
                continue
            key = d.isoformat()
            days.setdefault(key, []).append({
                "id": stable_id(uid, key),
                "title": summary,
                "time": time_label,
                "category": categorize(summary, outlook_cats),
                "link": pick_link(location, description),
                "notes": clean_notes(description, location),
            })

    def sort_key(e):
        m = re.match(r"(\d+):(\d+) (AM|PM)", e["time"])
        if not m:
            return (0, 0, e["title"])  # all-day first
        h = int(m.group(1)) % 12 + (12 if m.group(3) == "PM" else 0)
        return (1, h * 60 + int(m.group(2)), e["title"])

    for key in days:
        days[key].sort(key=sort_key)

    payload = {
        "generated": datetime.now(TZ).isoformat(timespec="seconds"),
        "source": "outlook",
        "days": {k: days[k] for k in sorted(days)},
    }

    new_text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    out = os.path.abspath(OUT_PATH)
    old_text = ""
    if os.path.exists(out):
        with open(out, encoding="utf-8") as f:
            old_text = f.read()

    # Ignore the timestamp when deciding whether anything actually changed.
    strip = lambda s: re.sub(r'"generated": "[^"]*"', "", s)
    if strip(new_text) == strip(old_text):
        print(f"No event changes ({sum(len(v) for v in days.values())} events across {len(days)} days).")
        return 0

    with open(out, "w", encoding="utf-8") as f:
        f.write(new_text)
    print(f"Updated events.json: {sum(len(v) for v in days.values())} events across {len(days)} days.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
