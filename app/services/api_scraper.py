"""
API-based Web Scraper for Trend Scout.

Uses ScraperAPI for JavaScript-rendered pages, works on any hosting platform.
No heavy browser dependencies required.

Sign up for free at: https://www.scraperapi.com/ (5000 free credits)
"""

import logging
import re
import json
import hashlib
from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import httpx
from bs4 import BeautifulSoup

import os
from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ScrapedDesign:
    """Scraped design data."""
    title: str
    price: Optional[float]
    image_url: str
    source_url: str
    source: str
    category: str
    metal_type: str = "gold"


# Category keyword mappings
CATEGORY_KEYWORDS = {
    "bridal": ["bridal", "wedding", "engagement", "mangalsutra", "choker", "kundan", "polki", "heavy"],
    "dailywear": ["dailywear", "daily wear", "lightweight", "office", "casual", "simple", "minimal", "everyday"],
    "temple": ["temple", "traditional", "antique", "south indian", "kemp", "lakshmi", "coin"],
    "contemporary": ["contemporary", "modern", "fusion", "western", "geometric", "abstract"],
    "mens": ["mens", "men's", "gents", "male", "kada", "bracelet for men", "chain for men"],
    "kids": ["kids", "children", "baby", "infant", "tiny"],
}


def detect_category(text: str) -> str:
    """Detect category from title/description."""
    text_lower = text.lower() if text else ""
    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text_lower:
                return category
    return "general"


def extract_price(text: str) -> Optional[float]:
    """Extract price from text like '₹45,000' or 'Rs. 45000'."""
    if not text:
        return None
    clean_text = text.replace(',', '').replace(' ', '')
    match = re.search(r'[\₹Rs\.]*([\d]+)', clean_text)
    if match:
        try:
            return float(match.group(1))
        except:
            pass
    return None


class APIScraperService:
    """Scraper using ScraperAPI for JavaScript rendering."""

    def __init__(self):
        self.base_url = "http://api.scraperapi.com"

    @property
    def api_key(self) -> str:
        """Get API key dynamically - check both settings and direct env."""
        # Try settings first
        key = settings.scraper_api_key
        if key:
            return key
        # Fallback to direct env check (Railway sometimes has issues with pydantic)
        return os.environ.get("SCRAPER_API_KEY", "")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def fetch_rendered_page(self, url: str, render_js: bool = True) -> Optional[str]:
        """Fetch a page using ScraperAPI with optional JS rendering."""
        if not self.configured:
            logger.warning("ScraperAPI not configured, using direct fetch")
            return await self._direct_fetch(url)

        try:
            params = {
                "api_key": self.api_key,
                "url": url,
                "render": str(render_js).lower(),
                "country_code": "in",  # India for local pricing
            }

            logger.info(f"ScraperAPI request: {url}")
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.get(self.base_url, params=params)
                logger.info(f"ScraperAPI response: status={response.status_code}, length={len(response.text)}")
                if response.status_code == 200:
                    # Log first 500 chars for debug
                    logger.info(f"ScraperAPI HTML preview: {response.text[:500]}")
                    return response.text
                else:
                    logger.error(f"ScraperAPI error: {response.status_code} - {response.text[:200]}")
                    return None

        except Exception as e:
            logger.error(f"ScraperAPI fetch error: {e}")
            return None

    async def _direct_fetch(self, url: str) -> Optional[str]:
        """Direct fetch without API (fallback)."""
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
            }
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url, headers=headers, follow_redirects=True)
                if response.status_code == 200:
                    return response.text
        except Exception as e:
            logger.error(f"Direct fetch error: {e}")
        return None

    async def scrape_bluestone(self, category: str = "necklaces", limit: int = 20) -> List[ScrapedDesign]:
        """Scrape designs from BlueStone."""
        designs = []
        category_urls = {
            "necklaces": "https://www.bluestone.com/jewellery/gold-necklaces.html",
            "earrings": "https://www.bluestone.com/jewellery/gold-earrings.html",
            "rings": "https://www.bluestone.com/jewellery/gold-rings.html",
            "bangles": "https://www.bluestone.com/jewellery/gold-bangles.html",
            "bracelets": "https://www.bluestone.com/jewellery/gold-bracelets.html",
            "pendants": "https://www.bluestone.com/jewellery/gold-pendants.html",
        }

        url = category_urls.get(category, category_urls["necklaces"])
        logger.info(f"Scraping BlueStone: {url}")

        html = await self.fetch_rendered_page(url, render_js=True)
        if not html:
            return designs

        soup = BeautifulSoup(html, 'lxml')

        # Method 1: Try LD+JSON
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.string)
                logger.info(f"LD+JSON type: {data.get('@type')}")

                if data.get('@type') == 'Product':
                    designs.append(self._parse_product(data, 'bluestone', url))
                elif data.get('@type') == 'ItemList':
                    items = data.get('itemListElement', [])
                    logger.info(f"ItemList has {len(items)} items")
                    for item in items[:limit]:
                        if 'item' in item:
                            designs.append(self._parse_product(item['item'], 'bluestone', url))
                        elif item.get('@type') == 'Product':
                            designs.append(self._parse_product(item, 'bluestone', url))
                elif isinstance(data, list):
                    # Sometimes it's a list of products directly
                    for item in data[:limit]:
                        if item.get('@type') == 'Product':
                            designs.append(self._parse_product(item, 'bluestone', url))
            except Exception as e:
                logger.error(f"LD+JSON parse error: {e}")
                continue

        # Method 2: Parse HTML - BlueStone uses div.p-image with data-plink
        if not designs:
            product_divs = soup.select('div.p-image[data-plink]')
            logger.info(f"Found {len(product_divs)} BlueStone product divs")

            for div in product_divs[:limit]:
                try:
                    # Get product URL from data-plink
                    source_url = div.get('data-plink', '')
                    if not source_url:
                        continue

                    # Get title from img alt attribute
                    img = div.select_one('img[alt]')
                    title = img.get('alt', '') if img else ''
                    if not title or len(title) < 3:
                        continue

                    # Get image URL
                    image_url = ""
                    if img:
                        image_url = img.get('src') or img.get('data-src') or img.get('data-lazy-src') or ""

                    # Get price from nearby elements
                    parent = div.find_parent('div', class_=True)
                    price = None
                    if parent:
                        price_el = parent.select_one('.final-price, .our-price, [class*="price"]')
                        if price_el:
                            price = extract_price(price_el.get_text())

                    designs.append(ScrapedDesign(
                        title=title,
                        price=price,
                        image_url=image_url,
                        source_url=source_url,
                        source='bluestone',
                        category=detect_category(title)
                    ))
                except Exception as e:
                    logger.error(f"Error parsing BlueStone product: {e}")
                    continue

        logger.info(f"BlueStone: Found {len(designs)} designs")
        return designs[:limit]

    async def scrape_all(self, category: str = "necklaces", limit_per_site: int = 10) -> List[ScrapedDesign]:
        """Scrape from BlueStone (only reliable source)."""
        try:
            return await self.scrape_bluestone(category=category, limit=limit_per_site)
        except Exception as e:
            logger.error(f"BlueStone scraper error: {e}")
            return []

    async def scrape_all_with_pinterest(self, category: str = "necklaces", limit_per_site: int = 10) -> List[ScrapedDesign]:
        """Scrape from BlueStone (Pinterest removed — unreliable, no prices)."""
        return await self.scrape_all(category=category, limit_per_site=limit_per_site)


# --- Removed scrapers (faked data / blocked / unreliable) ---
# scrape_caratlane: Removed — JavaScript SPA, returned 10 hardcoded fake designs
# scrape_tanishq: Removed — 403 blocked, returned 10 hardcoded fake designs
# scrape_pinterest: Removed — No prices, unreliable titles, needs ScraperAPI


class _RemovedScraperPlaceholder:
    """Placeholder to document removed scraper methods."""
    pass  # CaratLane, Tanishq, Pinterest scrapers removed in Trend Scout rebuild


# Global instance
api_scraper = APIScraperService()
