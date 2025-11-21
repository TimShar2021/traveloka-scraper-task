import asyncio
import copy
import datetime
import json
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import requests
from playwright.async_api import async_playwright

# ============================================
# Configuration
# ============================================

BASE_URL = "https://www.traveloka.com"
ROUTE_PREFIX = "en-th"

HOME_URL = f"{BASE_URL}/{ROUTE_PREFIX}"
ROOMS_URL = f"{BASE_URL}/api/v2/hotel/search/rooms"
SEARCHLIST_URL_PART = "/api/v2/hotel/searchList"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)

# ---- Input parameters (these will appear in output filename) ----
CHECKIN = datetime.date(2025, 12, 5)
CHECKOUT = datetime.date(2025, 12, 6)
NUM_ROOMS = 1
NUM_ADULTS = 1

CITY = "Bangkok"           # Used for filename
SEARCH_GEO_ID = "10000045" # Bangkok GeoID

MAX_HOTELS = 10            # Limit number of hotels to scrape


# ============================================
# Helpers for URL & deep-link construction
# ============================================

def generate_search_spec(
    checkin: datetime.date,
    checkout: datetime.date,
    num_rooms: int,
    num_adults: int,
    source_type: str,
    geo_id: str,
    location_name: str,
    tail: str = "1",
) -> str:
    """
    Generates the `spec=` string for /hotel/search pages.
    """
    checkin_str = checkin.strftime("%d-%m-%Y")
    checkout_str = checkout.strftime("%d-%m-%Y")
    location_part = urllib.parse.quote(location_name, safe="")
    return (
        f"{checkin_str}.{checkout_str}.{num_rooms}.{num_adults}."
        f"{source_type}.{geo_id}.{location_part}.{tail}"
    )


def generate_search_deep_link(
    checkin: datetime.date,
    checkout: datetime.date,
    num_rooms: int,
    num_adults: int,
    geo_id: str,
    location_name: str,
    source_type: str = "HOTEL_GEO",
) -> str:
    """
    Generates full search URL.
    """
    spec = generate_search_spec(
        checkin, checkout, num_rooms, num_adults,
        source_type, geo_id, location_name
    )
    return f"{BASE_URL}/{ROUTE_PREFIX}/hotel/search?spec={spec}"


def generate_hotel_detail_deep_link(
    checkin: datetime.date,
    checkout: datetime.date,
    num_rooms: int,
    num_adults: int,
    hotel_id: str,
    hotel_name: str,
) -> str:
    """
    Generates /hotel/detail deep link with slug based on hotel name.
    """
    checkin_str = checkin.strftime("%d-%m-%Y")
    checkout_str = checkout.strftime("%d-%m-%Y")
    slug_encoded = urllib.parse.quote(hotel_name, safe="")

    spec = (
        f"{checkin_str}.{checkout_str}.{num_rooms}.{num_adults}."
        f"HOTEL.{hotel_id}.{slug_encoded}.2"
    )
    return f"{BASE_URL}/{ROUTE_PREFIX}/hotel/detail?spec={spec}"


def parse_hotel_from_detail_url(detail_url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parses hotel_id and hotel_name from a /hotel/detail URL.
    """
    try:
        parsed = urllib.parse.urlparse(detail_url)
        qs = urllib.parse.parse_qs(parsed.query)
        spec = qs.get("spec", [None])[0]
        if not spec:
            return None, None

        parts = spec.split(".")
        if len(parts) < 7:
            return None, None

        hotel_id = parts[5]
        raw_slug = urllib.parse.unquote(parts[6])
        hotel_name = raw_slug.replace("-", " ").replace("_", " ").title()
        return hotel_id, hotel_name
    except:
        return None, None


# ============================================
# Price decoding helper
# ============================================

def _decode_amount(block: Optional[Dict[str, Any]], decimals: int = 2) -> Optional[float]:
    """
    Converts {"amount": "208000", "numOfDecimalPoint": 2} → 2080.00
    """
    if not block:
        return None
    if block.get("amount") is None:
        return None
    try:
        return int(block["amount"]) / (10 ** decimals)
    except:
        return None


# ============================================
# Parsing /search/rooms JSON into required fields
# ============================================

def parse_rates(api_json: Dict[str, Any], deep_link: Optional[str] = None) -> Dict[str, Any]:
    """
    Extracts all displayed rate options from /search/rooms JSON.
    Output matches test assignment fields exactly.
    """

    result = {"deep_link": deep_link, "rates": []}

    # Recursive search for "inventory" nodes
    def find_rate_nodes(obj: Any):
        if isinstance(obj, dict):
            if "inventoryName" in obj and "finalPrice" in obj:
                yield obj
            for value in obj.values():
                yield from find_rate_nodes(value)
        elif isinstance(obj, list):
            for x in obj:
                yield from find_rate_nodes(x)

    rate_nodes = list(find_rate_nodes(api_json))

    for inv in rate_nodes:
        room_name = inv.get("inventoryName", "")
        rate_name = inv.get("roomInventoryGroupOption", "")

        # Number of guests
        try:
            guests = int(inv.get("maxOccupancy", None))
        except:
            guests = None

        # Cancellation Policy
        cancel_block = inv.get("roomCancellationPolicy", {})
        cancellation = (
            cancel_block.get("cancellationPolicyString")
            or cancel_block.get("cancellationPolicyLabel")
            or ""
        )

        # Breakfast
        breakfast = "Breakfast included" if inv.get("isBreakfastIncluded") else "No breakfast"

        # Final price structure
        final_price = inv.get("finalPrice", {})
        total_display = final_price.get("totalPriceRateDisplay", {})
        decimals = int(total_display.get("numOfDecimalPoint", "2"))

        base_fare = total_display.get("baseFare")
        taxes_block = total_display.get("taxes")
        inclusive = total_display.get("inclusiveFinalPrice")
        exclusive = total_display.get("exclusiveFinalPrice")

        currency = (
            inclusive or exclusive or base_fare or {}
        ).get("currency")

        total_taxes = _decode_amount(taxes_block, decimals)
        total_prices = _decode_amount(inclusive, decimals)
        price = _decode_amount(exclusive, decimals)

        # Discounted original price (if any)
        strike = inv.get("finalStrikethroughPrice", {})
        orig_block = strike.get("totalPriceRateDisplay", {})
        if orig_block:
            orig_decimals = int(orig_block.get("numOfDecimalPoint", "2"))
            orig_total = (
                orig_block.get("inclusiveFinalPrice")
                or orig_block.get("totalFare")
            )
            original_price = _decode_amount(orig_total, orig_decimals)
        else:
            original_price = None

        # Per-night breakdown
        per_night = final_price.get("perRoomPerNightDisplay", {})
        dec_night = int(per_night.get("numOfDecimalPoint", "2"))

        net_per_stay = _decode_amount(
            per_night.get("exclusiveFinalPrice") or per_night.get("baseFare"),
            dec_night
        )
        shown_per_stay = _decode_amount(
            per_night.get("totalFare"), dec_night
        )
        total_per_stay = _decode_amount(
            per_night.get("inclusiveFinalPrice"), dec_night
        )

        # Build final object
        result["rates"].append({
            "Room_name": room_name,
            "Rate_name": rate_name,
            "Number_of_Guests": guests,
            "Cancellation_Policy": cancellation,
            "Breakfast": breakfast,
            "Price": price,
            "Currency": currency,
            "Total_taxes": total_taxes,
            "Total_prices": total_prices,
            "original_price": original_price,
            "net_price_per_stay": net_per_stay,
            "shown_price_per_stay": shown_per_stay,
            "total_price_per_stay": total_per_stay,
        })

    return result


# ============================================
# Extract hotels from searchList JSON
# ============================================

def extract_hotels_from_search_json(api_json: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Recursively finds hotel objects looking for:
    - hotelId / id
    - hotelName / name / title / displayName
    """
    hotels = []

    def walk(obj: Any):
        if isinstance(obj, dict):
            keys = set(obj.keys())

            if (
                ("hotelId" in keys or "id" in keys)
                and ("hotelName" in keys or "name" in keys or "title" in keys or "displayName" in keys)
            ):
                hotel_id = obj.get("hotelId") or obj.get("id")
                name = (
                    obj.get("hotelName")
                    or obj.get("name")
                    or obj.get("title")
                    or obj.get("displayName")
                )
                hotels.append({"id": str(hotel_id), "name": str(name)})

            for v in obj.values():
                walk(v)

        elif isinstance(obj, list):
            for el in obj:
                walk(el)

    walk(api_json)

    # Deduplicate while preserving order
    seen = set()
    uniq = []
    for h in hotels:
        key = (h["id"], h["name"])
        if key not in seen:
            seen.add(key)
            uniq.append(h)

    return uniq


# ============================================
# Collect cookies + templates via Playwright
# ============================================

async def collect_with_playwright() -> Optional[Dict[str, Any]]:
    """
    1. Open homepage → you solve CAPTCHA manually.
    2. Open /hotel/search → capture searchList JSON.
    3. Extract hotels.
    4. Open first hotel detail → capture /search/rooms payload + headers.
    5. Save cookies.
    """

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=150)
        context = await browser.new_context(
            locale="en-US",
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 768},
        )
        page = await context.new_page()

        # Step 1 — homepage
        print("Opening Traveloka homepage. Solve CAPTCHA if shown.")
        await page.goto(HOME_URL, wait_until="load")
        await page.wait_for_timeout(5000)

        input("When the website works normally, press ENTER... ")

        # Step 2 — open search page
        search_url = generate_search_deep_link(
            CHECKIN, CHECKOUT, NUM_ROOMS, NUM_ADULTS,
            SEARCH_GEO_ID, CITY
        )
        print("Opening hotel search:", search_url)

        captured_search_resp = {"resp": None}
        recent_urls = []

        def on_response(resp):
            u = resp.url
            if len(recent_urls) < 40:
                recent_urls.append(u)
            if SEARCHLIST_URL_PART in u and captured_search_resp["resp"] is None:
                captured_search_resp["resp"] = resp

        page.on("response", on_response)

        await page.goto(search_url, wait_until="networkidle")
        await page.wait_for_timeout(8000)

        if not captured_search_resp["resp"]:
            print("❌ searchList not found. Logging first URLs:")
            for u in recent_urls:
                print(" ", u)
            await browser.close()
            return None

        search_resp = captured_search_resp["resp"]
        print("Captured searchList URL:", search_resp.url)

        # Load JSON
        try:
            search_json = await search_resp.json()
        except:
            raw = await search_resp.text()
            print("❌ Failed to decode JSON. Raw dump:")
            print(raw[:2000])
            await browser.close()
            return None

        # Optional debug dump
        with open("searchList_raw.json", "w", encoding="utf-8") as f:
            json.dump(search_json, f, indent=2, ensure_ascii=False)

        print("searchList_raw.json saved.")

        # Step 3 — extract hotels
        hotels = extract_hotels_from_search_json(search_json)
        print(f"Found {len(hotels)} hotels.")

        if not hotels:
            print("❌ No hotels found.")
            await browser.close()
            return None

        # Step 4 — capture /search/rooms template using the first hotel
        first_h = hotels[0]
        detail_link = generate_hotel_detail_deep_link(
            CHECKIN, CHECKOUT, NUM_ROOMS, NUM_ADULTS,
            first_h["id"], first_h["name"]
        )
        print("Opening first hotel to capture search/rooms template:")
        print(" ", detail_link)

        captured_rooms_req = {"req": None}

        def on_request(req):
            if "/api/v2/hotel/search/rooms" in req.url and captured_rooms_req["req"] is None:
                captured_rooms_req["req"] = req

        page.on("request", on_request)

        await page.goto(detail_link, wait_until="networkidle")
        await page.wait_for_timeout(8000)

        if not captured_rooms_req["req"]:
            print("❌ No /search/rooms request captured.")
            await browser.close()
            return None

        rooms_req = captured_rooms_req["req"]
        rooms_payload_template = rooms_req.post_data_json or {}
        rooms_headers_template = rooms_req.headers

        # Step 5 — cookies
        cookies_list = await context.cookies()
        cookies = {c["name"]: c["value"] for c in cookies_list}

        await browser.close()

        return {
            "hotels": hotels,
            "rooms_payload_template": rooms_payload_template,
            "rooms_headers_template": rooms_headers_template,
            "cookies": cookies,
        }


# ============================================
# Request-based call to /search/rooms
# ============================================

def build_rooms_payload(template: Dict[str, Any], detail_url: str) -> Dict[str, Any]:
    """
    Inserts hotelDetailURL into the template payload.
    """
    payload = copy.deepcopy(template)
    data = payload.setdefault("data", {})
    contexts = data.setdefault("contexts", {})
    contexts["hotelDetailURL"] = detail_url
    return payload


def fetch_rates(
    detail_url: str,
    payload_template: Dict[str, Any],
    headers_template: Dict[str, str],
    cookies: Dict[str, str],
) -> Dict[str, Any]:
    """
    Performs POST /api/v2/hotel/search/rooms with real cookies + headers.
    """

    payload = build_rooms_payload(payload_template, detail_url)

    headers = {
        k: v for k, v in headers_template.items()
        if k.lower() not in ("host", "content-length")
    }
    headers["content-type"] = "application/json"

    resp = requests.post(
        ROOMS_URL,
        json=payload,
        headers=headers,
        cookies=cookies,
    )
    print("   /search/rooms status:", resp.status_code)

    if not resp.ok:
        print("   Response:", resp.text[:700])
        resp.raise_for_status()

    return parse_rates(resp.json(), detail_url)


# ============================================
# MAIN
# ============================================

async def main():
    print("🚀 Starting: collecting hotels + room rates")

    base = await collect_with_playwright()
    if base is None:
        print("❌ Failed to collect data via Playwright.")
        return

    hotels = base["hotels"]
    rooms_payload_template = base["rooms_payload_template"]
    rooms_headers_template = base["rooms_headers_template"]
    cookies = base["cookies"]

    print(f"Total hotels found: {len(hotels)}")

    hotels = hotels[:MAX_HOTELS]
    print(f"Processing first {len(hotels)} hotels...")

    results = []

    for idx, h in enumerate(hotels, start=1):
        hotel_id = h["id"]
        hotel_name = h["name"]

        detail_url = generate_hotel_detail_deep_link(
            CHECKIN, CHECKOUT, NUM_ROOMS, NUM_ADULTS,
            hotel_id, hotel_name
        )

        print(f"\n[{idx}/{len(hotels)}] Collecting rates for: {hotel_name} ({hotel_id})")

        try:
            parsed = fetch_rates(
                detail_url,
                rooms_payload_template,
                rooms_headers_template,
                cookies,
            )
            results.append({
                "hotel_id": hotel_id,
                "hotel_name": hotel_name,
                "detail_link": detail_url,
                "rates": parsed["rates"],
            })
        except Exception as e:
            print("   ❌ Error fetching hotel:", e)

    # Build filename
    filename_city = CITY.replace(" ", "_")
    filename_date = CHECKIN.strftime("%Y%m%d")
    filename = f"traveloka_{filename_city}_{filename_date}_{NUM_ADULTS}N.json"

    # Save
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Done! Saved to {filename}")


if __name__ == "__main__":
    asyncio.run(main())
