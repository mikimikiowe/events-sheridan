"""
Scrape upcoming events from sheridancollege.ca/events and save them to
sheridancollegeevents.json, using the same shape as ssuevents.json.

How the site works (Sitecore SXA search page):
  1. /events loads its results with JavaScript, so Selenium renders the page.
     Each event is an li.search-result-list__item with:
       h3 a                          -> title + link
       .search-result-event__date    -> "Jun. 16, 2026 – Oct. 27, 2026"
       .search-result-event__time    -> "3:00 p.m. – 4:00 p.m." (sometimes missing)
       last <p>                      -> "Location: Online" / an address
     Results are paged 20 at a time with a .page-selector.
  2. Categories are not on the cards; they only exist as filter checkboxes
     (.facet-event-category .facet-value). Ticking one filters the list, so
     we tick each in turn to learn which events belong to it.
  3. Each detail page has the description in .event-content and sometimes an
     image there. Some links (e.g. Open House) go to a regular page instead,
     so we fall back to its meta description, og:image, and its text for the
     time and campuses.

Run:  .venv/bin/python scrape_sheridan.py
"""

import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from backend.normalize_events import normalize_file
from backend.scrape_edge import google_calendar_url, make_driver, to_12h

BASE_URL = "https://www.sheridancollege.ca"
EVENTS_URL = f"{BASE_URL}/events"
OUTPUT_FILE = "sheridancollegeevents.json"
LOCAL_TZ = ZoneInfo("America/Toronto")

CAMPUSES = ["Davis Campus", "Trafalgar Campus", "Hazel McCallion Campus"]
TIME_PATTERN = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?", re.I)


# ---------- step 1: event cards from the list page ----------

def wait_for_results(driver):
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, ".search-result-list h3 a"))
    )
    time.sleep(1)  # let the list finish re-rendering


def parse_cards(soup):
    cards = []
    for item in soup.select(".search-result-list > li"):
        link = item.select_one("h3 a")
        if not link:
            continue
        date = item.select_one(".search-result-event__date")
        time_ = item.select_one(".search-result-event__time")
        paragraphs = item.select(".search-result-list__item__text > p")
        location = " ".join(paragraphs[-1].stripped_strings) if paragraphs else ""
        cards.append({
            "url": urljoin(BASE_URL, link["href"]),
            "title": link.get_text(strip=True),
            "date_text": " ".join(date.stripped_strings) if date else "",
            "time_text": " ".join(time_.stripped_strings) if time_ else "",
            "location": re.sub(r"\s+,", ",", location.removeprefix("Location:").strip()),
        })
    return cards


def get_all_cards(driver):
    driver.get(EVENTS_URL)
    wait_for_results(driver)

    cards = parse_cards(BeautifulSoup(driver.page_source, "html.parser"))
    # Follow the "Next" page link while it is active (it gets .inactive on the last page)
    while True:
        next_links = driver.find_elements(By.CSS_SELECTOR, ".page-selector-item-next:not(.inactive) a")
        if not next_links:
            break
        driver.execute_script("arguments[0].click();", next_links[0])
        wait_for_results(driver)
        new_cards = parse_cards(BeautifulSoup(driver.page_source, "html.parser"))
        seen = {card["url"] for card in cards}
        if all(card["url"] in seen for card in new_cards):
            break  # safety net: the page didn't change
        cards += new_cards
    return cards


def get_categories(driver):
    """Map each event url to the category filters it shows up under."""
    categories = {}
    names = [
        el.get_attribute("data-facetvalue")
        for el in driver.find_elements(By.CSS_SELECTOR, ".facet-event-category .facet-value")
    ]
    for name in names:
        checkbox = f'.facet-event-category .facet-value[data-facetvalue="{name}"]'
        driver.execute_script("arguments[0].click();", driver.find_element(By.CSS_SELECTOR, checkbox))
        wait_for_results(driver)
        for card in parse_cards(BeautifulSoup(driver.page_source, "html.parser")):
            categories.setdefault(card["url"], []).append(name)
        # Untick it again before trying the next one
        driver.execute_script("arguments[0].click();", driver.find_element(By.CSS_SELECTOR, checkbox))
        wait_for_results(driver)
    return categories


# ---------- step 2: details from each event page ----------

def get_details(driver, url):
    driver.get(url)
    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "main")))
    soup = BeautifulSoup(driver.page_source, "html.parser")

    content = soup.select_one(".event-content")
    meta_description = soup.find("meta", attrs={"name": "description"})
    og_image = soup.find("meta", property="og:image")

    if content:
        description = "\n".join(
            " ".join(p.get_text(" ").split()) for p in content.find_all(["p", "li"]) if p.get_text(strip=True)
        )
        image = content.find("img")
        image_url = urljoin(BASE_URL, image["src"]) if image else None
    else:
        description = meta_description["content"].strip() if meta_description else ""
        image_url = None

    if not image_url and og_image:
        image_url = og_image["content"]
    if not image_url:
        # Fall back to the banner photo at the top of the page
        banner = soup.select_one(".hero-banner img")
        image_url = urljoin(BASE_URL, banner["src"]) if banner else None

    main = soup.find("main")
    for tag in main(["script", "style", "noscript"]):
        tag.decompose()
    return {"description": description, "image_url": image_url, "page_text": main.get_text(" ", strip=True)}


# ---------- helpers to match ssuevents.json ----------

def parse_date(text):
    """'Jun. 16, 2026' or 'Sept. 3, 2026' -> date"""
    month, rest = text.replace(".", "").strip().split(" ", 1)
    return datetime.strptime(f"{month[:3]} {rest}", "%b %d, %Y").date()


def parse_times(text):
    """'3:00 p.m. – 4:00 p.m.' or '10 a.m.–2 p.m.' -> ['15:00', '16:00']"""
    times = []
    for hour, minute, half in TIME_PATTERN.findall(text):
        hour = int(hour) % 12 + (12 if half.lower() == "p" else 0)
        times.append(f"{hour:02d}:{minute or '00'}")
    return times


def campuses_in(text):
    """Sheridan campus names mentioned in the page text"""
    return [campus for campus in CAMPUSES if campus in text]


def at(day, time_24):
    hour, minute = map(int, time_24.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=LOCAL_TZ)


def build_event(card, details, categories):
    dates = [parse_date(d) for d in card["date_text"].split("–") if d.strip()]
    start_day, end_day = dates[0], dates[-1]

    # Time from the card, or from the detail page text when the card has none
    times = parse_times(card["time_text"]) or parse_times(details["page_text"])[:2]
    start_time = times[0] if times else "00:00"
    end_time = times[1] if len(times) > 1 else "23:59"

    start, end = at(start_day, start_time), at(end_day, end_time)
    start_utc, end_utc = start.astimezone(timezone.utc), end.astimezone(timezone.utc)

    campuses = campuses_in(details["page_text"]) if not card["location"] else []
    venue = card["location"] or ", ".join(campuses) or "Sheridan College"
    is_online = venue.lower() == "online"
    address = {
        "venue": "Online" if is_online else venue if campuses else venue.split(",")[0].strip(),
        "lines": [part.strip() for part in venue.split(",")] if not campuses else campuses,
        "map_url": None if is_online else f"http://maps.google.com?q={venue}",
    }

    occurrences = []
    for day in dict.fromkeys([start_day, end_day]):
        occurrences.append({
            "date": day.isoformat(),
            "date_text": f"{day:%A, %B} {day.day}, {day.year}",
            "time_12h": to_12h(start_time) if times else None,
            "time_24h": start_time if times else None,
        })

    description = details["description"]
    return {
        "title": card["title"],
        "url": card["url"],
        "image_url": details["image_url"],
        "list_date_box": {"month": start_day.strftime("%b"), "day": str(start_day.day)},
        "excerpt": description[:200] + ("…" if len(description) > 200 else ""),
        "last_occurrence_ends_at": end_utc.isoformat(),
        "occurrences": occurrences,
        "address": address,
        "categories": categories + (["Online"] if is_online else []),
        # Where it happens: "Online", or the campus names
        "tags": ["Online"] if is_online else campuses or [address["venue"]],
        "google_calendar_url": google_calendar_url(card["title"], start_utc, end_utc, venue),
        "ics_url": None,
        "description": description,
        "is_online": is_online,
    }


# ---------- main ----------

def main():
    driver = make_driver()
    try:
        cards = get_all_cards(driver)
        print(f"Found {len(cards)} events on the list page")
        categories = get_categories(driver)

        events = []
        for card in cards:
            print(f"  scraping: {card['title']}")
            details = get_details(driver, card["url"])
            events.append(build_event(card, details, categories.get(card["url"], [])))
    finally:
        driver.quit()

    output = {
        "source": EVENTS_URL,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "count": len(events),
        "events": events,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(events)} events to {OUTPUT_FILE}")

    normalize_file(OUTPUT_FILE)


if __name__ == "__main__":
    main()
