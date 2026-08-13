#!/usr/bin/env python3
import hashlib
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dtparse
from dateutil import tz

TZ = tz.gettz("America/New_York")
EVENTS_URL = "https://landmarktheatre.org/events/"
NATIVE_ICAL = "https://landmarktheatre.org/events/?ical=1"
OUT = "public/calendar.ics"
PREFIX = "Landmark: "
LOCATION = "Landmark Theatre, 362 S. Salina Street, Syracuse, NY 13202"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=35)
    r.raise_for_status()
    return r.text


def esc(s):
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def prefix_native_ical(text):
    """Prefer Landmark's own iCalendar export and only adjust presentation fields."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if "BEGIN:VCALENDAR" not in normalized or "BEGIN:VEVENT" not in normalized:
        return None

    lines = normalized.split("\n")
    out = []
    saw_name = False
    for line in lines:
        if line.startswith("X-WR-CALNAME:"):
            out.append("X-WR-CALNAME:Landmark Theatre")
            saw_name = True
        elif line.startswith("SUMMARY:"):
            value = line[len("SUMMARY:"):]
            if not value.startswith(PREFIX):
                value = PREFIX + value
            out.append("SUMMARY:" + value)
        else:
            out.append(line)

    if not saw_name:
        try:
            idx = out.index("VERSION:2.0") + 1
        except ValueError:
            idx = 1
        out.insert(idx, "X-WR-CALNAME:Landmark Theatre")

    # Advertise a reasonable refresh cadence to clients that honor it.
    if not any(x.startswith("X-PUBLISHED-TTL:") for x in out):
        try:
            idx = out.index("BEGIN:VCALENDAR") + 1
        except ValueError:
            idx = 1
        out[idx:idx] = ["REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H"]

    return "\r\n".join(out).rstrip("\r\n") + "\r\n"


def deterministic_uid(url, start, title):
    raw = f"{url}|{start.isoformat()}|{title}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest() + "@nsmac008-landmark-feed"


def fallback_events():
    """Fallback if the native feed endpoint is temporarily unavailable."""
    soup = BeautifulSoup(fetch(EVENTS_URL), "html.parser")
    events = []
    cutoff = datetime.now(TZ) - timedelta(days=2)

    # Current Landmark site uses The Events Calendar; support its list rows plus generic articles.
    rows = soup.select(".tribe-events-calendar-list__event-row, article")
    if not rows:
        rows = soup.select(".tribe-events-calendar-list__event")

    for row in rows:
        title_el = row.select_one(".tribe-events-calendar-list__event-title a") or row.find(["h2", "h3"])
        if not title_el:
            continue
        title = title_el.get_text(" ", strip=True)
        href = title_el.get("href") if getattr(title_el, "get", None) else None
        url = urljoin(EVENTS_URL, href) if href else EVENTS_URL
        text = " ".join(row.stripped_strings)

        # Capture every visible date/time on a card, which preserves multiple Broadway performances.
        matches = re.finditer(
            r"\b(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+"
            r"(?P<day>\d{1,2})(?:,?\s+(?P<year>20\d{2}))?\s*[-–|]?\s*"
            r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>AM|PM)\b",
            text,
            re.I,
        )
        for m in matches:
            year = int(m.group("year")) if m.group("year") else datetime.now(TZ).year
            try:
                start = dtparse.parse(
                    f"{m.group('mon')} {m.group('day')} {year} {m.group('hour')}:{m.group('minute') or '00'} {m.group('ampm')}"
                ).replace(tzinfo=TZ)
            except Exception:
                continue
            if start < cutoff:
                continue
            events.append((title, start, start + timedelta(hours=3), url))

        # Single-event cards often expose a machine-readable datetime.
        if not any(e[0] == title for e in events):
            time_el = row.find("time", attrs={"datetime": True})
            if time_el:
                try:
                    start = dtparse.parse(time_el["datetime"])
                    if start.tzinfo is None:
                        start = start.replace(tzinfo=TZ)
                    if start >= cutoff:
                        events.append((title, start, start + timedelta(hours=3), url))
                except Exception:
                    pass

    # Stable dedupe.
    uniq = []
    seen = set()
    for e in sorted(events, key=lambda x: x[1]):
        key = (e[0].lower(), e[1].strftime("%Y%m%d%H%M"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(e)
    return uniq


def build_fallback_ics(events):
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//nsmac008//Landmark Theatre Feed//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Landmark Theatre",
        "X-WR-TIMEZONE:America/New_York",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    for title, start, end, url in events:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{deterministic_uid(url, start, title)}",
            f"DTSTAMP:{now}",
            f"DTSTART:{start.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            f"DTEND:{end.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            f"SUMMARY:{esc(PREFIX + title)}",
            f"LOCATION:{esc(LOCATION)}",
            f"URL:{esc(url)}",
            f"DESCRIPTION:{esc('Source: ' + url)}",
            "STATUS:CONFIRMED",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def main():
    os.makedirs("public", exist_ok=True)

    try:
        native = prefix_native_ical(fetch(NATIVE_ICAL))
    except Exception as exc:
        print(f"Native iCal fetch failed: {exc}")
        native = None

    if native:
        with open(OUT, "w", encoding="utf-8", newline="") as f:
            f.write(native)
        count = native.count("BEGIN:VEVENT")
        print(f"Wrote {OUT} from Landmark native iCal with {count} events")
        return

    events = fallback_events()
    if not events:
        raise RuntimeError("Landmark native iCal unavailable and fallback parsed zero events")
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        f.write(build_fallback_ics(events))
    print(f"Wrote {OUT} from fallback parser with {len(events)} events")


if __name__ == "__main__":
    main()
