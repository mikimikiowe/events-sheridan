"""
Scrape upcoming events from the EDGE organizer page on Eventbrite and save
them to edgeevents.json, using the same shape as ssuevents.json.

How the page works:
  1. The organizer page is a Next.js app. Its event list is not in the HTML
     cards but in a <script id="__NEXT_DATA__"> JSON blob, under
     props.pageProps.upcomingEvents (name, url, dates, image, price...).
  2. Each event page has a schema.org <script type="application/ld+json">
     block with the venue address and exact start/end times, plus the full
     description in [data-testid="section-wrapper-overview"].

Run:  .venv/bin/python scrape_edge.py
"""

import json
from datetime import datetime, timezone
from urllib.parse import quote

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from backend.pyfiles.normalize_events import normalize_file

ORGANIZER_URL = "https://www.eventbrite.ca/o/edge-13162231152"
OUTPUT_FILE = "edgeevents.json"


# ---------- browser ----------

def make_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1280,2000")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
    )
    # Selenium Manager downloads a matching chromedriver automatically
    return webdriver.Chrome(options=options)


def load_soup(driver, url, wait_for_css):
    driver.get(url)
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, wait_for_css))
    )
    return BeautifulSoup(driver.page_source, "html.parser")


# ---------- step 1: list of events from the organizer page ----------

def get_event_list(driver):
    soup = load_soup(driver, ORGANIZER_URL, "script#__NEXT_DATA__")
    next_data = json.loads(soup.find("script", id="__NEXT_DATA__").string)
    page_props = next_data["props"]["pageProps"]

    if page_props.get("hasMoreUpcoming"):
        print("Warning: organizer has more upcoming events than the first page shows")

    # Organizer logo, used for events that have no image of their own
    logo = None
    for script in soup.find_all("script", type="application/ld+json"):
        data = json.loads(script.string or "{}")
        logo = logo or data.get("mainEntity", {}).get("logo")

    return page_props.get("upcomingEvents", []), logo


# ---------- step 2: details from each event page ----------

def get_event_details(driver, url):
    soup = load_soup(driver, url, 'script[type="application/ld+json"]')

    # The ld+json block whose @type ends in "Event" (Event, EducationEvent, ...)
    event_ld, breadcrumbs = {}, []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except json.JSONDecodeError:
            continue
        kind = data.get("@type", "")
        if kind.endswith("Event"):
            event_ld = data
        elif kind == "BreadcrumbList":
            breadcrumbs = [item["item"]["name"] for item in data.get("itemListElement", [])]

    overview = soup.select_one('[data-testid="section-wrapper-overview"]')
    if overview:
        title = overview.select_one('[class*="Overview"][class*="title"]')
        if title:
            title.decompose()  # drop the "Overview" heading
        description = overview.get_text("\n", strip=True)
    else:
        description = event_ld.get("description", "")

    return event_ld, breadcrumbs, description


# ---------- helpers to match ssuevents.json ----------

def to_12h(time_24):
    return datetime.strptime(time_24, "%H:%M").strftime("%-I:%M %p")


def to_utc(iso_string):
    return datetime.fromisoformat(iso_string).astimezone(timezone.utc)


def build_address(event_ld, is_online):
    if is_online:
        return {"venue": "Online", "lines": ["Online"], "map_url": None}

    location = event_ld.get("location", {})
    venue = location.get("name", "")
    street = location.get("address", {}).get("streetAddress", "")
    lines = [line for line in [venue, street] if line]
    map_url = f"http://maps.google.com?q={street}" if street else None
    return {"venue": venue, "lines": lines, "map_url": map_url}


def google_calendar_url(title, start, end, location):
    dates = f"{start:%Y%m%dT%H%M%SZ}/{end:%Y%m%dT%H%M%SZ}"
    return (
        "http://www.google.com/calendar/event?action=TEMPLATE"
        f"&text={quote(title)}&dates={dates}&location={quote(location)}"
    )


def build_event(summary, event_ld, breadcrumbs, description):
    start_date = summary["start_date"]               # "2026-10-14"
    start_time = summary["start_time"][:5]           # "11:00"
    start_day = datetime.strptime(start_date, "%Y-%m-%d")

    # Exact times with timezone come from ld+json; fall back to the summary
    start_iso = event_ld.get("startDate") or f"{start_date}T{start_time}:00-04:00"
    end_iso = event_ld.get("endDate") or start_iso
    start_utc, end_utc = to_utc(start_iso), to_utc(end_iso)

    address = build_address(event_ld, summary.get("is_online_event"))
    tickets = summary.get("ticket_availability", {})
    price = tickets.get("minimum_ticket_price", {}).get("display")

    categories = ["FREE"] if tickets.get("is_free") else []
    if summary.get("is_online_event"):
        categories.append("Online")

    return {
        "title": summary["name"].strip(),
        "url": summary["url"],
        "image_url": summary.get("image", {}).get("url") or event_ld.get("image"),
        "list_date_box": {"month": start_day.strftime("%b"), "day": str(start_day.day)},
        "excerpt": summary.get("summary") or event_ld.get("description", ""),
        "last_occurrence_ends_at": end_utc.isoformat(),
        "occurrences": [
            {
                "date": start_date,
                "date_text": f"{start_day:%A, %B} {start_day.day}, {start_day.year}",
                "time_12h": to_12h(start_time),
                "time_24h": start_time,
            }
        ],
        "address": address,
        "categories": categories,
        # Eventbrite's breadcrumbs end with topic pages like "Brampton seminars"
        "tags": breadcrumbs[3:],
        "google_calendar_url": google_calendar_url(
            summary["name"].strip(), start_utc, end_utc, ", ".join(address["lines"])
        ),
        "ics_url": None,
        "description": description,
        # Extra Eventbrite-only metadata
        "eventbrite_id": summary.get("id"),
        "is_online": summary.get("is_online_event", False),
        "is_cancelled": summary.get("is_cancelled", False),
        "price": price,
        "sales_status": summary.get("event_sales_status", {}).get("message"),
    }


# ---------- main ----------

def main():
    driver = make_driver()
    try:
        summaries, logo = get_event_list(driver)
        print(f"Found {len(summaries)} events on the organizer page")

        events = []
        for summary in summaries:
            print(f"  scraping: {summary['name'].strip()}")
            event_ld, breadcrumbs, description = get_event_details(driver, summary["url"])
            events.append(build_event(summary, event_ld, breadcrumbs, description))
    finally:
        driver.quit()

    output = {
        "source": ORGANIZER_URL,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "count": len(events),
        "events": events,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(events)} events to {OUTPUT_FILE}")

    # Add the flat title/month/day/time/venue... fields shared by every source
    normalize_file(OUTPUT_FILE, fallback_image=logo)


if __name__ == "__main__":
    main()
