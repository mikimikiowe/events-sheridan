"""
Scrape upcoming club events from Sheridan's Engage (CampusLabs) site and save
them to clubevents.json, using the same shape as ssuevents.json.

How the site works:
  1. /engage/events is a React app: the raw HTML is an empty shell, so Selenium
     has to render it. Event cards appear inside #event-discovery-list as
     <a href="/engage/event/ID"> links with an <h3> title, a background-image
     picture, and the date/location in aria-label attributes. Over 15 events,
     a "Load More" button appears at the bottom.
  2. Each event page embeds its full data as JSON in a script:
     window.initialAppState = {...}  ->  preFetchedData.event
     That has local start/end times, full address, categories, benefits and
     theme. Once rendered, the page also lists every host club as an
     <a href="/engage/organization/..."> link (cards only say "Hosted by N
     organizations" for co-hosted events).

Run:  .venv/bin/python scrape_clubs.py
"""

import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from backend.pyfiles.normalize_events import normalize_file
from backend.pyfiles.scrape_edge import google_calendar_url, make_driver, to_12h

BASE_URL = "https://sheridancollege.campuslabs.ca"
EVENTS_URL = f"{BASE_URL}/engage/events"
OUTPUT_FILE = "clubevents.json"

# Engage stores some values as code names; these are the labels the site shows
THEMES = {
    "GroupBusiness": "Group Business",
    "ThoughtfulLearning": "Thoughtful Learning",
    "CommunityService": "Community Service",
}
BENEFITS = {"FreeFood": "Free Food", "Merchandise": "Free Stuff"}


# ---------- step 1: event cards from the list page ----------

def load_all_cards(driver):
    driver.get(EVENTS_URL)
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.ID, "event-discovery-list"))
    )

    # Keep clicking "Load More" until every event is on the page
    while True:
        buttons = driver.find_elements(By.XPATH, "//button[.//text()[contains(., 'Load More')]]")
        if not buttons:
            break
        driver.execute_script("arguments[0].click();", buttons[0])
        time.sleep(2)

    soup = BeautifulSoup(driver.page_source, "html.parser")
    cards = []
    for link in soup.select('#event-discovery-list a[href^="/engage/event/"]'):
        image = link.select_one('[role="img"]')
        image_match = re.search(r'url\("?([^")]+)', image.get("style", "")) if image else None
        location = link.select_one('[aria-label^="located at"]')
        cards.append({
            "url": urljoin(BASE_URL, link["href"]),
            "title": link.h3.get_text(strip=True) if link.h3 else "",
            "image_url": image_match.group(1) if image_match else None,
            "location": location["aria-label"].removeprefix("located at ").strip() if location else "",
            # The club name is the last line of text on the card
            "organization": list(link.stripped_strings)[-1],
        })
    return cards


# ---------- step 2: full data from each event page ----------

def get_event_data(driver, url):
    """Returns (event JSON, list of host club names)."""
    driver.get(url)
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'a[href^="/engage/organization/"]'))
        )
    except TimeoutException:
        pass  # still try whatever loaded

    soup = BeautifulSoup(driver.page_source, "html.parser")
    hosts = [
        link.get_text(strip=True)
        for link in soup.select('a[href^="/engage/organization/"]')
        if link.get_text(strip=True)
    ]

    for script in soup.find_all("script"):
        text = script.string or ""
        match = re.search(r"window\.initialAppState\s*=\s*(\{.*\});?\s*$", text, re.S)
        if match:
            state = json.loads(match.group(1))
            return state.get("preFetchedData", {}).get("event") or {}, hosts
    return {}, hosts


# ---------- build a record matching ssuevents.json ----------

def html_to_text(html):
    """One line per paragraph, so bold/italic words stay inside their sentence."""
    soup = BeautifulSoup(html or "", "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    blocks = soup.find_all(["p", "li", "h1", "h2", "h3", "h4"])
    if not blocks:
        return soup.get_text(" ", strip=True)
    lines = (" ".join(block.get_text(" ").split()) for block in blocks)
    return "\n".join(line for line in lines if line)


def build_address(card, data):
    address = data.get("address") or {}
    if address.get("onlineLocation"):
        return {"venue": "Online", "lines": ["Online", address["onlineLocation"]], "map_url": None}

    venue = (address.get("name") or card["location"]).strip()
    street = (address.get("address") or "").strip()
    lines = [line for line in [venue, street] if line]
    map_query = street or venue
    return {"venue": venue, "lines": lines, "map_url": f"http://maps.google.com?q={map_query}"}


def build_event(card, data, hosts):
    start = datetime.fromisoformat(data["startsOn"])
    end = datetime.fromisoformat(data.get("endsOn") or data["startsOn"])
    start_utc, end_utc = start.astimezone(timezone.utc), end.astimezone(timezone.utc)

    title = (data.get("name") or card["title"]).strip()
    description = html_to_text(data.get("description"))
    address = build_address(card, data)

    theme = data.get("theme")
    theme_label = THEMES.get(theme, theme)
    category_names = [c["name"].strip() for c in data.get("categories") or []]
    benefits = [BENEFITS.get(b, b) for b in data.get("benefits") or []]
    hosts = hosts or [card["organization"]]

    return {
        "title": title,
        "url": card["url"],
        "image_url": data.get("imageUrl") or card["image_url"],
        "list_date_box": {"month": start.strftime("%b"), "day": str(start.day)},
        "excerpt": description[:200] + ("…" if len(description) > 200 else ""),
        "last_occurrence_ends_at": end_utc.isoformat(),
        "occurrences": [
            {
                "date": start.strftime("%Y-%m-%d"),
                "date_text": f"{start:%A, %B} {start.day}, {start.year}",
                "time_12h": to_12h(start.strftime("%H:%M")),
                "time_24h": start.strftime("%H:%M"),
            }
        ],
        "address": address,
        # Engage categories (Social, Workshop...) plus the event's theme
        "categories": list(dict.fromkeys(category_names + ([theme_label] if theme_label else []))),
        # Perks (Free Food...) plus the clubs running it
        "tags": benefits + hosts,
        "google_calendar_url": google_calendar_url(
            title, start_utc, end_utc, ", ".join(address["lines"])
        ),
        "ics_url": None,
        "description": description,
        # Extra Engage-only metadata
        "engage_id": str(data["id"]),
        "organizations": hosts,
        "rsvp_total": (data.get("rsvpSettings") or {}).get("totalRsvps"),
    }


# ---------- main ----------

def main():
    driver = make_driver()
    try:
        cards = load_all_cards(driver)
        print(f"Found {len(cards)} events on the list page")

        events = []
        for card in cards:
            print(f"  scraping: {card['title']}")
            data, hosts = get_event_data(driver, card["url"])
            if not data.get("startsOn"):
                print("    skipped: no event data on the page")
                continue
            events.append(build_event(card, data, hosts))
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
