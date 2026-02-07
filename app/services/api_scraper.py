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
                if data.get('@type') == 'Product':
                    designs.append(self._parse_product(data, 'bluestone', url))
                elif data.get('@type') == 'ItemList':
                    for item in data.get('itemListElement', [])[:limit]:
                        if 'item' in item:
                            designs.append(self._parse_product(item['item'], 'bluestone', url))
            except:
                continue

        # Method 2: Parse HTML directly
        if not designs:
            cards = soup.select('[data-product-id], .product-card, .plp-card, .product-item')
            for card in cards[:limit]:
                try:
                    title_el = card.select_one('.product-title, .plp-prod-name, h3, h4')
                    price_el = card.select_one('.product-price, .plp-price, .final-price')
                    img_el = card.select_one('img')
                    link_el = card.select_one('a')

                    if title_el:
                        title = title_el.get_text(strip=True)
                        price = extract_price(price_el.get_text() if price_el else "")
                        image_url = img_el.get('src') or img_el.get('data-src') if img_el else ""
                        source_url = link_el.get('href', url) if link_el else url

                        if not source_url.startswith('http'):
                            source_url = f"https://www.bluestone.com{source_url}"

                        designs.append(ScrapedDesign(
                            title=title,
                            price=price,
                            image_url=image_url,
                            source_url=source_url,
                            source='bluestone',
                            category=detect_category(title)
                        ))
                except Exception as e:
                    continue

        logger.info(f"BlueStone: Found {len(designs)} designs")
        return designs[:limit]

    async def scrape_caratlane(self, category: str = "necklaces", limit: int = 20) -> List[ScrapedDesign]:
        """Scrape designs from CaratLane."""
        designs = []
        category_urls = {
            "necklaces": "https://www.caratlane.com/jewellery/necklaces.html",
            "earrings": "https://www.caratlane.com/jewellery/earrings.html",
            "rings": "https://www.caratlane.com/jewellery/rings.html",
            "bangles": "https://www.caratlane.com/jewellery/bangles-bracelets.html",
            "pendants": "https://www.caratlane.com/jewellery/pendants.html",
        }

        url = category_urls.get(category, category_urls["necklaces"])
        logger.info(f"Scraping CaratLane: {url}")

        html = await self.fetch_rendered_page(url, render_js=True)
        if not html:
            return designs

        soup = BeautifulSoup(html, 'lxml')

        # Try LD+JSON first
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.string)
                if data.get('@type') == 'Product':
                    designs.append(self._parse_product(data, 'caratlane', url))
                elif data.get('@type') == 'ItemList':
                    for item in data.get('itemListElement', [])[:limit]:
                        if 'item' in item:
                            designs.append(self._parse_product(item['item'], 'caratlane', url))
            except:
                continue

        # Parse HTML
        if not designs:
            cards = soup.select('[data-product-id], .product-tile, .product-card')
            for card in cards[:limit]:
                try:
                    title_el = card.select_one('.product-name, .product-title, h2, h3')
                    price_el = card.select_one('.product-price, .price')
                    img_el = card.select_one('img')
                    link_el = card.select_one('a')

                    if title_el:
                        title = title_el.get_text(strip=True)
                        price = extract_price(price_el.get_text() if price_el else "")
                        image_url = img_el.get('src') or img_el.get('data-src') if img_el else ""
                        source_url = link_el.get('href', url) if link_el else url

                        if not source_url.startswith('http'):
                            source_url = f"https://www.caratlane.com{source_url}"

                        designs.append(ScrapedDesign(
                            title=title,
                            price=price,
                            image_url=image_url,
                            source_url=source_url,
                            source='caratlane',
                            category=detect_category(title)
                        ))
                except:
                    continue

        logger.info(f"CaratLane: Found {len(designs)} designs")
        return designs[:limit]

    async def scrape_tanishq(self, category: str = "necklaces", limit: int = 20) -> List[ScrapedDesign]:
        """Scrape designs from Tanishq."""
        designs = []
        category_urls = {
            "necklaces": "https://www.tanishq.co.in/jewellery/gold-jewellery/necklaces.html",
            "earrings": "https://www.tanishq.co.in/jewellery/gold-jewellery/earrings.html",
            "rings": "https://www.tanishq.co.in/jewellery/gold-jewellery/rings.html",
            "bangles": "https://www.tanishq.co.in/jewellery/gold-jewellery/bangles.html",
        }

        url = category_urls.get(category, category_urls["necklaces"])
        logger.info(f"Scraping Tanishq: {url}")

        html = await self.fetch_rendered_page(url, render_js=True)
        if not html:
            return designs

        soup = BeautifulSoup(html, 'lxml')

        # Try LD+JSON
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.string)
                if data.get('@type') == 'Product':
                    designs.append(self._parse_product(data, 'tanishq', url))
                elif data.get('@type') == 'ItemList':
                    for item in data.get('itemListElement', [])[:limit]:
                        if 'item' in item:
                            designs.append(self._parse_product(item['item'], 'tanishq', url))
            except:
                continue

        # Parse HTML
        if not designs:
            cards = soup.select('.product-tile, .product-card, [data-pid]')
            for card in cards[:limit]:
                try:
                    title_el = card.select_one('.product-name, .pdp-link, h3')
                    price_el = card.select_one('.product-price, .price, .sales')
                    img_el = card.select_one('img')
                    link_el = card.select_one('a')

                    if title_el:
                        title = title_el.get_text(strip=True)
                        price = extract_price(price_el.get_text() if price_el else "")
                        image_url = img_el.get('src') or img_el.get('data-src') if img_el else ""
                        source_url = link_el.get('href', url) if link_el else url

                        if not source_url.startswith('http'):
                            source_url = f"https://www.tanishq.co.in{source_url}"

                        designs.append(ScrapedDesign(
                            title=title,
                            price=price,
                            image_url=image_url,
                            source_url=source_url,
                            source='tanishq',
                            category=detect_category(title)
                        ))
                except:
                    continue

        logger.info(f"Tanishq: Found {len(designs)} designs")
        return designs[:limit]

    def _parse_product(self, data: dict, source: str, fallback_url: str) -> ScrapedDesign:
        """Parse LD+JSON product data."""
        title = data.get('name', 'Unknown')
        price = None
        if 'offers' in data:
            offers = data['offers']
            if isinstance(offers, list):
                offers = offers[0]
            price = offers.get('price')
            if price:
                price = float(price)

        image_url = data.get('image', '')
        if isinstance(image_url, list):
            image_url = image_url[0] if image_url else ''

        return ScrapedDesign(
            title=title,
            price=price,
            image_url=image_url,
            source_url=data.get('url', fallback_url),
            source=source,
            category=detect_category(title)
        )

    async def scrape_all(self, category: str = "necklaces", limit_per_site: int = 10) -> List[ScrapedDesign]:
        """Scrape from all sources."""
        all_designs = []

        # Scrape each site
        for scraper_func in [self.scrape_bluestone, self.scrape_caratlane, self.scrape_tanishq]:
            try:
                designs = await scraper_func(category=category, limit=limit_per_site)
                all_designs.extend(designs)
            except Exception as e:
                logger.error(f"Scraper error: {e}")

        logger.info(f"Total scraped: {len(all_designs)} designs")
        return all_designs

    async def search(self, query: str, limit_per_site: int = 10) -> List[ScrapedDesign]:
        """Search for designs by query."""
        all_designs = []

        # Search URLs
        search_urls = {
            'bluestone': f"https://www.bluestone.com/jewellery.html?q={query.replace(' ', '+')}",
            'caratlane': f"https://www.caratlane.com/search?q={query.replace(' ', '+')}",
            'tanishq': f"https://www.tanishq.co.in/search?q={query.replace(' ', '+')}",
        }

        for source, url in search_urls.items():
            try:
                html = await self.fetch_rendered_page(url, render_js=True)
                if not html:
                    continue

                soup = BeautifulSoup(html, 'lxml')

                # Generic product card parsing
                cards = soup.select('[data-product-id], .product-card, .product-tile, .plp-card')
                for card in cards[:limit_per_site]:
                    try:
                        title_el = card.select_one('[class*="name"], [class*="title"], h2, h3, h4')
                        price_el = card.select_one('[class*="price"]')
                        img_el = card.select_one('img')
                        link_el = card.select_one('a')

                        if title_el:
                            all_designs.append(ScrapedDesign(
                                title=title_el.get_text(strip=True),
                                price=extract_price(price_el.get_text() if price_el else ""),
                                image_url=img_el.get('src') or img_el.get('data-src') if img_el else "",
                                source_url=link_el.get('href', url) if link_el else url,
                                source=source,
                                category=detect_category(title_el.get_text())
                            ))
                    except:
                        continue

            except Exception as e:
                logger.error(f"Search error for {source}: {e}")

        logger.info(f"Search '{query}': Found {len(all_designs)} designs")
        return all_designs


# Global instance
api_scraper = APIScraperService()
