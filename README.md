# Landmark Theatre → auto-updating `.ics` feed

This repo mirrors https://landmarktheatre.org/events/calendar/ into a public iCalendar feed you can subscribe to.

**Subscribe URL (after your first deploy):**
```
https://<your-username>.github.io/landmark-ics/calendar.ics
```

## How to use
1. Create a new repo (e.g. `landmark-ics`) and upload these files: `scrape_to_ics.py`, `requirements.txt`, `.github/workflows/publish.yml`.
2. Settings → **Pages** → Source: **GitHub Actions**.
3. Actions → **Build & Publish ICS** → Run workflow.
4. Open the GitHub Pages URL and verify `calendar.ics` exists, then add that URL to Google/Apple/Outlook.

> If the Landmark site markup changes, tweak `scrape_to_ics.py` (regex near `SINGLE_DATE_LINE` and `DATE_RANGE_BULLET`).
