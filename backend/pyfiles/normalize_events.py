"""
Make sure every event in every *events.json file has these top-level fields:
  title, url, image_url, month, day, time, venue, categories, tags

The values are pulled from the nested data each scraper already writes
(list_date_box, occurrences, address...) and filled in from fallbacks where
the site left something out. Anything that still can't be found is reported.

Run:  .venv/bin/python normalize_events.py            (all *events.json files)
      .venv/bin/python normalize_events.py edgeevents.json
"""

import glob
import json
import re
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("America/Toronto")
REQUIRED = ["title", "url", "image_url", "month", "day", "time", "venue", "categories", "tags"]


def clean(text):
    # Some sites use a narrow no-break space in "4:00 PM"
    return text.replace(" ", " ").replace(" ", " ").strip() if isinstance(text, str) else text


def start_from_calendar_url(event):
    """Start time from a Google Calendar link: ...&dates=20261015T150000Z/..."""
    match = re.search(r"dates=(\d{8}T\d{6}Z)", event.get("google_calendar_url") or "")
    if not match:
        return None
    start = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return start.astimezone(LOCAL_TZ)


def fill_missing_times(event):
    occurrences = event.get("occurrences") or []
    for occurrence in occurrences:
        occurrence["time_12h"] = clean(occurrence.get("time_12h"))

    if occurrences and not occurrences[0].get("time_12h"):
        start = start_from_calendar_url(event)
        if start and start.strftime("%Y-%m-%d") == occurrences[0].get("date"):
            occurrences[0]["time_12h"] = start.strftime("%-I:%M %p")
            occurrences[0]["time_24h"] = start.strftime("%H:%M")


def normalize_event(event, fallback_image=None):
    fill_missing_times(event)

    date_box = event.get("list_date_box") or {}
    first = (event.get("occurrences") or [{}])[0]
    first_date = datetime.strptime(first["date"], "%Y-%m-%d") if first.get("date") else None

    event["title"] = clean(event.get("title"))
    event["image_url"] = event.get("image_url") or fallback_image
    event["month"] = date_box.get("month") or (first_date and first_date.strftime("%b"))
    event["day"] = date_box.get("day") or (first_date and str(first_date.day))
    event["time"] = first.get("time_12h")
    event["venue"] = (event.get("address") or {}).get("venue")
    event["categories"] = event.get("categories") or []
    event["tags"] = event.get("tags") or []
    return event


def normalize_file(path, fallback_image=None):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    for event in data["events"]:
        normalize_event(event, fallback_image)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"{path}: {len(data['events'])} events")
    for event in data["events"]:
        missing = [field for field in REQUIRED if not event.get(field)]
        if missing:
            print(f"  {event['title'][:50]:50} still empty: {', '.join(missing)}")


if __name__ == "__main__":
    for path in sys.argv[1:] or sorted(glob.glob("*events.json")):
        normalize_file(path)
