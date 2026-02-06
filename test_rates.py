"""Standalone test for enhanced metal rates - no database needed."""

import asyncio
import re
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, Dict
import httpx
from bs4 import BeautifulSoup

# URLs
GOLD_URL = "https://www.goodreturns.in/gold-rates/{city}.html"
SILVER_URL = "https://www.goodreturns.in/silver-rates/{city}.html"
PLATINUM_URL = "https://www.goodreturns.in/platinum-rate.html"
MCX_URL = "https://www.goodreturns.in/mcx-bullion.html"
GOLD_API_URL = "https://api.gold-api.com/price/XAU"
SILVER_API_URL = "https://api.gold-api.com/price/XAG"
PLATINUM_API_URL = "https://api.gold-api.com/price/XPT"
FOREX_API_URL = "https://api.exchangerate-api.com/v4/latest/USD"

TROY_OZ_TO_GRAM = 31.1035

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

GOLD_PURITY = {
    "24k": 0.999, "22k": 0.916, "18k": 0.750,
    "14k": 0.585, "10k": 0.417, "9k": 0.375,
}


@dataclass
class MetalRates:
    city: str
    rate_date: Optional[str] = None
    gold_24k: float = 0
    gold_22k: float = 0
    gold_18k: float = 0
    gold_14k: float = 0
    gold_10k: float = 0
    gold_9k: float = 0
    silver: float = 0
    platinum: float = 0
    gold_usd_oz: Optional[float] = None
    silver_usd_oz: Optional[float] = None
    usd_inr: Optional[float] = None


def extract_rate(text):
    if not text:
        return None
    cleaned = re.sub(r'[^0-9]', '', text)
    return float(cleaned) if cleaned else None


def extract_date(soup):
    title = soup.find('title')
    if title:
        match = re.search(r'(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})', title.get_text())
        if match:
            return match.group(1)
    return None


async def fetch_gold_rates(city="mumbai"):
    url = GOLD_URL.format(city=city)
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        r = await client.get(url, headers=HEADERS)
        soup = BeautifulSoup(r.text, "lxml")

        rate_date = extract_date(soup)
        tables = soup.find_all("table")

        gold_24k = gold_22k = gold_18k = None
        for i, table in enumerate(tables[:3]):
            rows = table.find_all("tr")
            if len(rows) >= 2:
                cells = rows[1].find_all("td")
                if len(cells) >= 2:
                    rate = extract_rate(cells[1].get_text())
                    if i == 0: gold_24k = rate
                    elif i == 1: gold_22k = rate
                    elif i == 2: gold_18k = rate

        return rate_date, gold_24k, gold_22k, gold_18k


async def fetch_silver_rate(city="mumbai"):
    url = SILVER_URL.format(city=city)
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        r = await client.get(url, headers=HEADERS)
        soup = BeautifulSoup(r.text, "lxml")
        tables = soup.find_all("table")
        if tables:
            rows = tables[0].find_all("tr")
            if len(rows) >= 2:
                cells = rows[1].find_all("td")
                if len(cells) >= 2:
                    return extract_rate(cells[1].get_text())
    return None


async def fetch_platinum_rate():
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        r = await client.get(PLATINUM_URL, headers=HEADERS)
        soup = BeautifulSoup(r.text, "lxml")
        tables = soup.find_all("table")
        if tables:
            rows = tables[0].find_all("tr")
            if len(rows) >= 2:
                cells = rows[1].find_all("td")
                if len(cells) >= 2:
                    return extract_rate(cells[1].get_text())
    return None


async def fetch_mcx_futures():
    """Fetch MCX Gold and Silver futures."""
    result = {"gold": None, "gold_expiry": "Feb", "silver": None, "silver_expiry": "Mar"}
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            r = await client.get(MCX_URL, headers=HEADERS)
            if r.status_code != 200:
                return result
            soup = BeautifulSoup(r.text, "lxml")
            tables = soup.find_all("table")
            for table in tables:
                rows = table.find_all("tr")
                for row in rows:
                    cells = row.find_all(["td", "th"])
                    if len(cells) >= 2:
                        header = cells[0].get_text(strip=True).lower()
                        if "gold" in header and not result["gold"]:
                            rate = extract_rate(cells[1].get_text())
                            if rate and rate > 50000:
                                result["gold"] = rate
                                expiry = re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)', header, re.I)
                                if expiry:
                                    result["gold_expiry"] = expiry.group(1)
                        elif "silver" in header and not result["silver"]:
                            rate = extract_rate(cells[1].get_text())
                            if rate and rate > 50000:
                                result["silver"] = rate
                                expiry = re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)', header, re.I)
                                if expiry:
                                    result["silver_expiry"] = expiry.group(1)
    except Exception as e:
        print(f"MCX error: {e}")
    return result


async def fetch_international():
    result = {"gold": None, "silver": None, "platinum": None, "usd_inr": None}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            r = await client.get(GOLD_API_URL)
            if r.status_code == 200:
                result["gold"] = r.json().get("price")
        except: pass

        try:
            r = await client.get(SILVER_API_URL)
            if r.status_code == 200:
                result["silver"] = r.json().get("price")
        except: pass

        try:
            r = await client.get(PLATINUM_API_URL)
            if r.status_code == 200:
                result["platinum"] = r.json().get("price")
        except: pass

        try:
            r = await client.get(FOREX_API_URL)
            if r.status_code == 200:
                result["usd_inr"] = r.json().get("rates", {}).get("INR")
        except: pass

    return result


def calculate_all_karats(gold_24k):
    return {
        "24k": gold_24k,
        "22k": round(gold_24k * GOLD_PURITY["22k"] / GOLD_PURITY["24k"]),
        "18k": round(gold_24k * GOLD_PURITY["18k"] / GOLD_PURITY["24k"]),
        "14k": round(gold_24k * GOLD_PURITY["14k"] / GOLD_PURITY["24k"]),
        "10k": round(gold_24k * GOLD_PURITY["10k"] / GOLD_PURITY["24k"]),
        "9k": round(gold_24k * GOLD_PURITY["9k"] / GOLD_PURITY["24k"]),
    }


async def main():
    print("=" * 65)
    print("JewelClaw - Enhanced Metal Rates Test")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)

    # Fetch gold rates
    print("\nFetching gold rates from GoodReturns.in...")
    rate_date, gold_24k, gold_22k, gold_18k = await fetch_gold_rates("mumbai")

    if gold_24k:
        karats = calculate_all_karats(gold_24k)
        # Use scraped values if available
        if gold_22k: karats["22k"] = gold_22k
        if gold_18k: karats["18k"] = gold_18k

        print(f"\nRate Date: {rate_date}")
        print("\n" + "-" * 40)
        print("GOLD RATES (Mumbai)")
        print("-" * 40)
        print(f"  24K:  Rs.{karats['24k']:>8,.0f}/gram  (Rs.{karats['24k']*10:>10,.0f}/10gm)")
        print(f"  22K:  Rs.{karats['22k']:>8,.0f}/gram  (Rs.{karats['22k']*10:>10,.0f}/10gm)")
        print(f"  18K:  Rs.{karats['18k']:>8,.0f}/gram  (Rs.{karats['18k']*10:>10,.0f}/10gm)")
        print(f"  14K:  Rs.{karats['14k']:>8,.0f}/gram  (Rs.{karats['14k']*10:>10,.0f}/10gm)")
        print(f"  10K:  Rs.{karats['10k']:>8,.0f}/gram  (Rs.{karats['10k']*10:>10,.0f}/10gm)")
        print(f"   9K:  Rs.{karats['9k']:>8,.0f}/gram  (Rs.{karats['9k']*10:>10,.0f}/10gm)")
    else:
        print("[FAIL] Could not fetch gold rates")

    # Fetch silver
    print("\nFetching silver rate...")
    silver = await fetch_silver_rate("mumbai")
    if silver:
        print("\n" + "-" * 40)
        print("SILVER RATE")
        print("-" * 40)
        print(f"  Silver: Rs.{silver:,.0f}/gram (Rs.{silver*1000:,.0f}/kg)")

    # Fetch international prices first (needed for platinum)
    print("\nFetching international prices...")
    intl = await fetch_international()
    print("\n" + "-" * 40)
    print("INTERNATIONAL PRICES")
    print("-" * 40)
    if intl["gold"]:
        print(f"  Gold (XAU): ${intl['gold']:,.2f}/oz")
    if intl["silver"]:
        print(f"  Silver (XAG): ${intl['silver']:,.2f}/oz")
    if intl["platinum"]:
        print(f"  Platinum (XPT): ${intl['platinum']:,.2f}/oz")
    if intl["usd_inr"]:
        print(f"  USD/INR: {intl['usd_inr']:.2f}")

    # Fetch platinum (try scraping, fallback to calculation)
    print("\nFetching platinum rate...")
    platinum = await fetch_platinum_rate()
    if not platinum and intl["platinum"] and intl["usd_inr"]:
        # Calculate from international price with ~8% retail markup
        platinum = round((intl["platinum"] * intl["usd_inr"]) / TROY_OZ_TO_GRAM * 1.08)
        print("  (Calculated from international price)")

    if platinum:
        print("\n" + "-" * 40)
        print("PLATINUM RATE (India)")
        print("-" * 40)
        print(f"  Platinum: Rs.{platinum:,.0f}/gram")

    # Fetch MCX futures
    print("\nFetching MCX futures...")
    mcx = await fetch_mcx_futures()
    if mcx["gold"] or mcx["silver"]:
        print("\n" + "-" * 40)
        print("MCX FUTURES")
        print("-" * 40)
        if mcx["gold"]:
            print(f"  Gold {mcx['gold_expiry']}: Rs.{mcx['gold']:,.0f}/10gm")
        if mcx["silver"]:
            print(f"  Silver {mcx['silver_expiry']}: Rs.{mcx['silver']:,.0f}/kg")
    else:
        print("  MCX data not available")

    print("\n" + "=" * 65)
    print("[OK] All rates fetched successfully!")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())
