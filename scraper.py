from playwright.sync_api import sync_playwright
import json
import re
import base64
import csv

SEARCH_URL = (
    "https://www.airbnb.com/s/Marrakesh--Morocco/homes"
    "?check_in=2026-07-01"
    "&check_out=2026-07-03"
    "&adults=2"
)

MAX_LISTINGS = 5
MAX_SEARCH_PAGES = 3

def clean_url(url):
    room_id = re.search(r"/rooms/(\d+)", url).group(1)

    return (
        f"https://www.airbnb.com/rooms/{room_id}"
        "?check_in=2026-07-01"
        "&check_out=2026-07-03"
        "&adults=2"
    )

def extract_price(page):
    text = page.locator("body").text_content()

    # Preferred:
    matches = re.findall(
        r"DH\s*([\d,]+)\s*for\s*\d+\s*nights?",
        text,
        re.IGNORECASE
    )

    if matches:
        return f"DH {matches[0]}"

    # Fallback:
    prices = re.findall(
        r"DH\s*([\d,]+)",
        text
    )

    print("PRICE MATCHES:", prices)

    if prices:
        nums = [
            int(p.replace(",", ""))
            for p in prices
        ]

        return f"DH {min(nums)}"

    return None



def build_cursor(offset):
    payload = {
        "section_offset": 0,
        "items_offset": offset,
        "version": 1
    }

    return base64.urlsafe_b64encode(
        json.dumps(
            payload,
            separators=(",", ":")
        ).encode()
    ).decode()


def scrape_listing(page, url):
    page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=60000
    )

    page.wait_for_timeout(2000)


    ld_scripts = page.locator(
        'script[type="application/ld+json"]'
        )

    data = None

    for i in range(ld_scripts.count()):
        try:
            obj = json.loads(
                ld_scripts.nth(i).text_content()
            )

            if obj.get("@type") == "VacationRental":
                data = obj
                break

        except Exception:
            pass

    if not data:
        print("No VacationRental JSON:", url)
        return None

    return {
        "id": data.get("identifier"),
        "title": data.get("name"),
        "description": data.get("description"),
        "price": extract_price(page),
        "images": data.get("image", []),
        "guests": (
            data.get("containsPlace", {})
            .get("occupancy", {})
            .get("value")
        ),
        "city": (
            data.get("address", {})
            .get("addressLocality")
        ),
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "rating": (
            data.get("aggregateRating", {})
            .get("ratingValue")
        ),
        "reviews": (
            data.get("aggregateRating", {})
            .get("ratingCount")
        ),
        "url": url
    }


with sync_playwright() as p:

    browser = p.chromium.launch(
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled"
        ]
    )

    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/137.0.0.0 Safari/537.36"
        )
    )

    page = context.new_page()
    print("Opening search page...")

    page.goto(
        SEARCH_URL,
        wait_until="domcontentloaded",
        timeout=60000
    )

    urls = set()
    previous_count = 0

    print("Collecting listing URLs...")
    for search_page in range(MAX_SEARCH_PAGES):
        offset = search_page * 18

        if search_page == 0:
            url = SEARCH_URL
        else:
            cursor = build_cursor(offset)

            url = (
                SEARCH_URL
                + "&pagination_search=true"
                + f"&cursor={cursor}"
            )

        print(f"\nLoading page {search_page+1}")
        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        page.wait_for_timeout(3000)

        room_urls = page.locator(
            'a[href*="/rooms/"]'
        ).evaluate_all(
            "links => links.map(link => link.href)"
        )

        print("Found:", len(room_urls))
        ids = set()

        for room_url in room_urls:
            match = re.search(r"/rooms/(\d+)", room_url)
            if match:
                ids.add(match.group(1))

        print("Sample room IDs:", list(ids)[:5])
        for room_url in room_urls:
            urls.add(clean_url(room_url))

        print("Unique listings:", len(urls))
        if search_page > 0:
            if len(urls) == previous_count:
                print("No new listings found")
                break

        previous_count = len(urls)
        
        if len(urls) >= MAX_LISTINGS:
            urls = list(urls)[:MAX_LISTINGS]
            break
    page.wait_for_timeout(5000)

    with open("search_page.html", "w", encoding="utf-8") as f:
        f.write(page.content())

    room_urls = page.locator('a[href*="/rooms/"]').evaluate_all(
        "links => links.map(link => link.href)"
    )

    print("ROOM URLS FOUND:", len(room_urls))

    for url in room_urls[:20]:
        print(url)
    urls = list(urls)[:MAX_LISTINGS]

    print(
        f"\nScraping {len(urls)} listings...\n"
    )

    results = []

    listing_page = context.new_page()

    for i, url in enumerate(urls, start=1):

        try:

            listing = scrape_listing(
                listing_page,
                url
            )

            if listing:

                results.append(listing)

                print(
                    f"[{i}/{len(urls)}] "
                    f"{listing['title']} | "
                    f"{listing['price']}"
                )

        except Exception as e:

            print(f"Failed: {url}")
            print(e)

    with open(
        "airbnb_listings.csv",
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=[
                "id",
                "title",
                "description",
                "price",
                "guests",
                "city",
                "latitude",
                "longitude",
                "rating",
                "reviews",
                "url",
                "images"
            ]
        )

        writer.writeheader()

        for row in results:
            row = row.copy()

            # Convert image list into a single string
            row["images"] = " | ".join(
                row.get("images", [])
            )

            writer.writerow(row)

    print(
        f"\nSaved {len(results)} listings "
        f"to airbnb_listings.csv"
    )

    browser.close()