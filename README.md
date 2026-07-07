# CB Realty North Hills — Office Calendar

Live site: **https://danacoonpgh-bit.github.io/office-calendar/**

Agents just open the link — no login, works on desktop and phone.
The page shows a **Month** view and a phone-friendly **List** view (it picks
automatically, and remembers your choice).

## How events get on the calendar

The calendar updates **automatically from Dana's Outlook**. A scheduled
Claude task on Dana's computer reads two sources through her Outlook
connection and publishes any changes to `events.json`, which the page reads:

1. Everything on the dedicated **"Office Calendar"** in Outlook, and
2. Events on Dana's main calendar tagged with the **"In Office"** category.

So Dana's workflow is: create the event on the Office Calendar (or tag an
existing event "In Office") — the website updates itself on the next sync.
Nobody edits this site by hand.

> Note: the company's Microsoft 365 tenant has calendar publishing (ICS links)
> disabled, so the classic feed approach doesn't work. The GitHub Action in
> `.github/workflows/sync-calendar.yml` is kept as a dormant fallback — if IT
> ever enables publishing, add the ICS link as an `ICS_URL` repo secret and it
> takes over.

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
