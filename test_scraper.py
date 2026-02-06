"""Test the GoodReturns.in gold scraper with timestamp."""

import asyncio
import re
from datetime import datetime
from dataclasses import dataclass
from typing import Optional
import httpx
from bs4 import BeautifulSoup


GOLD_URL = "https://www.goodreturns.in/gold-rates/{city}.html"
SILVER_URL = "https://www.goodreturns.in/silver-rates/{city}.html"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def extract_rate(text: str) -> Optional[float]:
    """Extract numeric rate from text like '₹15,442'."""
    if not text:
        return None
    # Remove everything except digits
    cleaned = re.sub(r'[^0-9]', '', text)
    if cleaned:
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def extract_date(soup: BeautifulSoup) -> Optional[str]:
    """Extract the rate date from the page."""
    # Try h3 with class 'gd-date'
    date_el = soup.find(class_='gd-date')
    if date_el:
        return date_el.get_text(strip=True)

    # Try title
    title = soup.find('title')
    if title:
        date_match = re.search(
            r'(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})',
            title.get_text()
        )
        if date_match:
            return date_match.group(1)

    # Try headings
    for heading in soup.find_all(['h1', 'h2', 'h3']):
        text = heading.get_text(strip=True)
        date_match = re.search(
            r'(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})',
            text
        )
        if date_match:
            return date_match.group(1)

    return None


async def scrape_gold(city: str = "mumbai"):
    """Scrape gold rates from GoodReturns.in"""
    url = GOLD_URL.format(city=city)
    fetch_time = datetime.now()

    print(f"Fetching: {url}")
    print(f"Fetch time: {fetch_time.strftime('%Y-%m-%d %H:%M:%S')}")

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.get(url, headers=HEADERS)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "lxml")

        # Check for Cloudflare block
        title = soup.find('title')
        if title and 'cloudflare' in title.get_text().lower():
            print("  [BLOCKED] Cloudflare challenge detected")
            return None, None, None, None, fetch_time

        # Extract rate date from page
        rate_date = extract_date(soup)

        tables = soup.find_all("table")

        gold_24k = None
        gold_22k = None
        gold_18k = None

        for i, table in enumerate(tables[:3]):
            rows = table.find_all("tr")
            if len(rows) >= 2:
                data_row = rows[1]
                cells = data_row.find_all("td")
                if len(cells) >= 2:
                    rate = extract_rate(cells[1].get_text())
                    if rate:
                        if i == 0:
                            gold_24k = rate
                        elif i == 1:
                            gold_22k = rate
                        elif i == 2:
                            gold_18k = rate

        return gold_24k, gold_22k, gold_18k, rate_date, fetch_time


async def scrape_silver(city: str = "mumbai"):
    """Scrape silver rate from GoodReturns.in"""
    url = SILVER_URL.format(city=city)

    print(f"Fetching: {url}")

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.get(url, headers=HEADERS)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "lxml")

        # Check for Cloudflare block
        title = soup.find('title')
        if title and 'cloudflare' in title.get_text().lower():
            return None

        tables = soup.find_all("table")
        if tables:
            rows = tables[0].find_all("tr")
            if len(rows) >= 2:
                data_row = rows[1]
                cells = data_row.find_all("td")
                if len(cells) >= 2:
                    return extract_rate(cells[1].get_text())

        return None


async def main():
    print("=" * 65)
    print("GoodReturns.in Gold Rate Scraper Test")
    print("=" * 65)

    cities = ["mumbai", "delhi", "bangalore", "chennai"]

    for city in cities:
        print(f"\n[{city.upper()}]")
        print("-" * 45)

        try:
            result = await scrape_gold(city)
            gold_24k, gold_22k, gold_18k, rate_date, fetch_time = result

            if gold_24k and gold_22k:
                print(f"  Rate Date:   {rate_date or 'Not found'}")
                print(f"  Fetched at:  {fetch_time.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"  24K Gold:    Rs.{gold_24k:,.0f}/gram (Rs.{gold_24k * 10:,.0f}/10gm)")
                print(f"  22K Gold:    Rs.{gold_22k:,.0f}/gram (Rs.{gold_22k * 10:,.0f}/10gm)")
                if gold_18k:
                    print(f"  18K Gold:    Rs.{gold_18k:,.0f}/gram")
            else:
                print(f"  [FAIL] Could not parse gold rates")

        except Exception as e:
            print(f"  [ERROR] {e}")

    # Test silver for Mumbai
    print(f"\n[SILVER - Mumbai]")
    print("-" * 45)
    try:
        silver = await scrape_silver("mumbai")
        if silver:
            print(f"  Silver:      Rs.{silver:,.0f}/gram (Rs.{silver * 1000:,.0f}/kg)")
        else:
            print(f"  [FAIL] Could not parse silver rate")
    except Exception as e:
        print(f"  [ERROR] {e}")

    print("\n" + "=" * 65)
    print(f"Test completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())
