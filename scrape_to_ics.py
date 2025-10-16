#!/usr/bin/env python3
import re
import sys
import uuid
from datetime import datetime, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dtparse
from dateutil import tz

CAL_URL = "https://landmarktheatre.org/events/calendar/"
SITE_TZ = tz.gettz("America/New_York")
DEFAULT_EVENT_DURATION_HOURS = 2  # fallback if only a start time is known

MONTHS = {
    'January': 1, 'February': 2, 'March': 3, 'April': 4,
    'May': 5, 'June': 6, 'July': 7, 'August': 8,
    'September': 9, 'October': 10, 'November': 11, 'December': 12,
    'Jan.': 1, 'Feb.': 2, 'Mar.': 3, 'Apr.': 4, 'Jun.': 6, 'Jul.': 7,
    'Aug.': 8, 'Sept.': 9, 'Oct.': 10, 'Nov.': 11, 'Dec.': 12
}

DATE_RANGE_BULLET = re.compile(r"^(?P<mon>[A-Za-z]{3,4}\.?)[\s]+(?P<day>\d{1,2})\s*[–-]\s*(?P<time>[0-9:apmAPM\.]+)")
SINGLE_DATE_LINE = re.compile(r"^(?P<month>[A-Za-z]{3,9})\s+(?P<day>\d{1,2})(?:,\s*(?P<year>\d{4}))?\s*[–-]\s*(?P<time>[^\n]+)$")
RANGE_DATE_LINE = re.compile(r"^(?P<start_mon>[A-Za-z]{3,9})\s+(?P<start_day>\d{1,2})\s*[–-]\s*(?P<end_mon>[A-Za-z]{3,9})?\s*(?P<end_day>\d{1,2}),\s*(?P<year>\d{4})$")
TIME_ONLY = re.compile(r"(?P<hour>\d{1,2})(?::(?P<min>\d{2}))?\s*(?P<ampm>[ap]m|AM|PM)\*?", re.IGNORECASE)

class Event:
    def __init__(self, title, start_dt, end_dt=None, url=None, desc=None):
        self.title = title.strip()
        self.start = start_dt
        self.end = end_dt or (start_dt + timedelta(hours=DEFAULT_EVENT_DURATION_HOURS))
        self.url = url
        self.desc = (desc or "").strip()
        self.uid = f"{uuid.uuid4()}@landmarktheatre.org"

    def to_ics(self):
        dtstamp = datetime.now(tz=tz.UTC).strftime("%Y%m%dT%H%M%SZ")
        dtstart = self.start.astimezone(tz.UTC).strftime("%Y%m%dT%H%M%SZ")
        dtend = self.end.astimezone(tz.UTC).strftime("%Y%m%dT%H%M%SZ")
        lines = [
            "BEGIN:VEVENT",
            f"UID:{self.uid}",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART:{dtstart}",
            f"DTEND:{dtend}",
            f"SUMMARY:{escape_ics(self.title)}",
        ]
        if self.url:
            lines.append(f"URL:{escape_ics(self.url)}")
        if self.desc:
            lines.append(f"DESCRIPTION:{escape_ics(self.desc)}")
        lines.append("END:VEVENT")
        return "\n".join(lines)

def escape_ics(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

def fetch_soup(url):
    r = requests.get(url, timeout=30, headers={"User-Agent":"Mozilla/5.0"})
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")

def parse_calendar():
    soup = fetch_soup(CAL_URL)
    # Candidates likely to house events
    candidates = []
    candidates += soup.select(".wp-block-post")
    candidates += [x for x in soup.select("article") if x not in candidates]
    for a in soup.find_all("a"):
        if a.get_text(strip=True).lower() == "read more":
            candidates.append(a.find_parent(["article", "div", "section"]) or a.parent)

    events = []
    seen_titles = set()

    for node in candidates:
        title_el = node.find(["h2", "h3"]) or node.find("a")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if not title or title in seen_titles:
            continue

        # attempt to find a date line
        date_text = None
        for sib in node.stripped_strings:
            if re.search(r"\b(\d{4})\b", sib) or re.search(r"\b(am|pm|AM|PM)\b", sib):
                date_text = sib
                break

        # detail URL
        read_more = node.find("a", string=lambda s: s and s.strip().lower() == "read more")
        url = urljoin(CAL_URL, read_more["href"]) if read_more and read_more.get("href") else None

        # parse sessions
        sessions = []
        if date_text and RANGE_DATE_LINE.match(date_text):
            sessions = parse_range_block(node, date_text)
        elif date_text and SINGLE_DATE_LINE.match(date_text):
            sessions = parse_single_date_line(date_text)
        else:
            if url:
                sessions = parse_event_page(url)

        if not sessions:
            continue

        # optional description
        desc = None
        p = node.find("p")
        if p:
            desc = p.get_text(strip=True)

        for start_dt in sessions:
            events.append(Event(title, start_dt, url=url, desc=desc))
        seen_titles.add(title)

    # future only and sort
    now = datetime.now(tz=SITE_TZ)
    events = [e for e in events if e.start > now - timedelta(days=1)]
    events.sort(key=lambda e: e.start)
    return events

def parse_event_page(url):
    try:
        soup = fetch_soup(url)
    except Exception:
        return []
    text = "\n".join(soup.stripped_strings)
    # Single line pattern
    m = re.search(r"([A-Za-z]{3,9}\s+\d{1,2},\s*\d{4})\s*[–-]\s*([^\n]+)", text)
    if m:
        dt = parse_date_time(m.group(1), m.group(2))
        return [dt] if dt else []
    # Range bullets
    sessions = []
    for line in text.splitlines():
        bm = DATE_RANGE_BULLET.search(line)
        if bm:
            mon = bm.group("mon")
            day = int(bm.group("day"))
            time_txt = bm.group("time")
            year = infer_year()
            month = MONTHS.get(mon, None)
            if month:
                dt = parse_date_time(f"{month}/{day}/{year}", time_txt)
                if dt:
                    sessions.append(dt)
    return sessions

def parse_single_date_line(line):
    m = SINGLE_DATE_LINE.match(line)
    if not m:
        return []
    month_name = m.group("month")
    day = int(m.group("day"))
    year = int(m.group("year") or datetime.now().year)
    time_txt = m.group("time")
    month = MONTHS.get(month_name, None)
    if not month:
        try:
            month = dtparse.parse(month_name).month
        except Exception:
            return []
    dt = parse_date_time(f"{month}/{day}/{year}", time_txt)
    return [dt] if dt else []

def parse_range_block(node, header_line):
    m = RANGE_DATE_LINE.match(header_line)
    if not m:
        return []
    smon = MONTHS.get(m.group("start_mon"))
    sd = int(m.group("start_day"))
    emon = MONTHS.get(m.group("end_mon") or m.group("start_mon"))
    ed = int(m.group("end_day"))
    year = int(m.group("year"))

    bullets = [li.get_text(strip=True) for li in node.find_all("li")]
    sessions = []
    for b in bullets:
        bm = DATE_RANGE_BULLET.search(b)
        if not bm:
            continue
        mon = MONTHS.get(bm.group("mon"))
        day = int(bm.group("day"))
        time_txt = bm.group("time")
        if (mon, day) < (smon, sd) or (mon, day) > (emon, ed):
            continue
        dt = parse_date_time(f"{mon}/{day}/{year}", time_txt)
        if dt:
            sessions.append(dt)
    return sessions

def parse_date_time(date_str, time_str):
    try:
        tmatch = TIME_ONLY.search(time_str)
        if tmatch:
            hour = int(tmatch.group("hour"))
            minute = int(tmatch.group("min") or 0)
            ampm = (tmatch.group("ampm") or "").lower()
            if ampm == "pm" and hour != 12:
                hour += 12
            if ampm == "am" and hour == 12:
                hour = 0
            base = dtparse.parse(date_str)
            dt_local = base.replace(tzinfo=SITE_TZ).replace(hour=hour, minute=minute, second=0, microsecond=0)
        else:
            dt_local = dtparse.parse(f"{date_str} {time_str}").replace(tzinfo=SITE_TZ)
        return dt_local
    except Exception:
        return None

def write_ics(events, path="calendar.ics"):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//landmark-ics//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Landmark Theatre",
        "X-WR-TIMEZONE:America/New_York",
    ]
    for e in events:
        lines.append(e.to_ics())
    lines.append("END:VCALENDAR")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

def infer_year():
    today = datetime.now(tz=SITE_TZ).date()
    return today.year if today.month <= 11 else today.year + 1

def main():
    events = parse_calendar()
    if not events:
        print("No events parsed", file=sys.stderr)
        sys.exit(1)
    write_ics(events, path="calendar.ics")
    print(f"Wrote calendar.ics with {len(events)} events")

if __name__ == "__main__":
    main()
