# CB Realty North Hills — Office Calendar

Live site: **https://danacoonpgh-bit.github.io/office-calendar/**

Agents just open the link — no login, works on desktop and phone.
The page shows a **Month** view and a phone-friendly **List** view (it picks
automatically, and remembers your choice).

## How events get on the calendar

The calendar updates **automatically from an Outlook calendar**. A GitHub
Action checks the Outlook feed every couple of hours and publishes any changes
to `events.json`, which the page reads. Nobody edits this site by hand.

```
Outlook "Office Calendar"  →  published ICS feed  →  GitHub Action (every 2h)  →  events.json  →  the site
```

## One-time setup (Dana)

1. **Create a dedicated calendar in Outlook** called `Office Calendar`
   (Outlook → Calendar → Add calendar → Create blank calendar).
   Put office-wide events on it: Best Hour, sales meetings, trainings,
   workshops, office outings, who's out of office. Personal appointments stay
   on your main calendar and never appear on the site.
   *Tip: for existing events, you can copy them onto this calendar, or invite
   the calendar — anything on it gets published.*

2. **Publish it as an ICS link**: Outlook on the web → Settings ⚙ →
   Calendar → **Shared calendars** → "Publish a calendar" → pick
   `Office Calendar`, permission **"Can view all details"** → Publish →
   copy the **ICS** link.

3. **Give the link to GitHub**: this repo → Settings → Secrets and variables →
   **Actions** → "New repository secret" → Name: `ICS_URL`, Value: the ICS
   link → Save.

4. **Run it once**: repo → Actions tab → "Sync calendar from Outlook" →
   "Run workflow". After that it runs by itself every 2 hours (6 AM–8 PM ET).

From then on: **add or change an event in Outlook, and the site updates itself
within a couple of hours.** To force an immediate update, run the workflow
from the Actions tab.

## How events are displayed

- **Category colors** are picked automatically from the event title:
  "Best Hour" → navy · "workshop" → gold · "training / agent development /
  panel / class" → mid-navy · "meeting / news to use" → slate · everything
  else → charcoal. (Rules live in `scripts/sync_calendar.py`.)
- **Teams/Zoom links** in the event location or body become a "Join Meeting"
  button.
- Meeting IDs, passcodes, and boilerplate are stripped from the notes.
- All-day events that span several days (vacations, travel) show on every day.

## Manual extras (optional)

If something should appear on the site but doesn't belong in Outlook, add it
to `manual-events.json` (edit it right on GitHub):

```json
{
  "2026-08-15": [
    { "id": "m1", "title": "Office Picnic", "time": "12:00 PM",
      "category": "OTHER", "link": "", "notes": "North Park, Lodge 3." }
  ]
}
```

Categories: `BEST_HOUR` | `TRAINING` | `MEETING` | `WORKSHOP` | `OTHER`.
Manual events are merged with the Outlook events; the sync never touches this
file.

## Files

| File | What it is |
|---|---|
| `index.html` | The site (viewer only — no editing UI) |
| `events.json` | Published events, written by the sync (don't hand-edit once sync is live) |
| `manual-events.json` | Hand-maintained extras, merged in by the page |
| `scripts/sync_calendar.py` | Outlook ICS → events.json converter |
| `.github/workflows/sync-calendar.yml` | The schedule that runs the sync |
