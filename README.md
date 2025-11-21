# Traveloka Hotel Rates Scraper

This project is a small, self-contained Python script that:

1. Simulates a real user in a browser using **Playwright** to:
   - Pass the human verification (CAPTCHA) manually.
   - Trigger the internal Traveloka API:
     - `/api/v2/hotel/searchList` – to get a list of hotels for a given city and dates.
     - `/api/v2/hotel/search/rooms` – to analyze how room rates are requested.
   - Capture:
     - A list of hotels (IDs and names).
     - A valid request payload and headers for `/search/rooms`.
     - Real cookies for the session.

2. Then uses **Python `requests`** (as required in the assignment) to:
   - Rebuild the deep links for hotel detail pages.
   - Call `/api/v2/hotel/search/rooms` for each hotel.
   - Extract all displayed rates and normalize them into a JSON structure.

All heavy scraping work is done using the `requests` library. Playwright is used only once at the beginning to bootstrap a valid session and reverse-engineer the API calls.
