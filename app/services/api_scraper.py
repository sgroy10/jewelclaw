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

    async def scrape_caratlane(self, category: str = "necklaces", limit: int = 20) -> List[ScrapedDesign]:
        """Scrape designs from CaratLane."""
        designs = []
        category_urls = {
            "necklaces": "https://www.caratlane.com/jewellery/necklaces.html",
            "earrings": "https://www.caratlane.com/jewellery/earrings.html",
            "rings": "https://www.caratlane.com/jewellery/rings.html",
            "bangles": "https://www.caratlane.com/jewellery/bangles-bracelets.html",
            "pendants": "https://www.caratlane.com/jewellery/pendants.html",
            "bridal": "https://www.caratlane.com/jewellery/wedding-jewellery.html",
        }

        url = category_urls.get(category, category_urls["necklaces"])
        logger.info(f"Scraping CaratLane: {url}")

        html = await self.fetch_rendered_page(url, render_js=True)
        if not html:
            return designs

        soup = BeautifulSoup(html, 'lxml')

        # CaratLane uses data-product-sku and specific classes
        # Look for product tiles
        product_tiles = soup.select('[data-product-sku], .product-tile, .plp-product-card, [class*="ProductCard"]')
        logger.info(f"CaratLane: Found {len(product_tiles)} product tiles")

        for tile in product_tiles[:limit]:
            try:
                # Get title from multiple possible locations
                title_el = tile.select_one('[class*="product-name"], [class*="ProductName"], h3, h4, a[title]')
                title = ""
                if title_el:
                    title = title_el.get_text(strip=True) or title_el.get('title', '')

                # Get image
                img = tile.select_one('img[src*="caratlane"], img[data-src*="caratlane"], img')
                image_url = ""
                if img:
                    image_url = img.get('src') or img.get('data-src') or img.get('data-lazy') or ""

                # Get price
                price_el = tile.select_one('[class*="price"], [class*="Price"]')
                price = extract_price(price_el.get_text() if price_el else "")

                # Get link
                link = tile.select_one('a[href*="/jewellery/"]')
                source_url = link.get('href', url) if link else url
                if not source_url.startswith('http'):
                    source_url = f"https://www.caratlane.com{source_url}"

                if title and len(title) > 3:
                    designs.append(ScrapedDesign(
                        title=title,
                        price=price,
                        image_url=image_url,
                        source_url=source_url,
                        source='caratlane',
                        category=detect_category(title)
                    ))
            except Exception as e:
                logger.error(f"CaratLane parse error: {e}")
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
            "bridal": "https://www.tanishq.co.in/jewellery/rivaah-wedding-jewellery.html",
            "mangalsutra": "https://www.tanishq.co.in/jewellery/gold-jewellery/mangalsutra.html",
        }

        url = category_urls.get(category, category_urls["necklaces"])
        logger.info(f"Scraping Tanishq: {url}")

        html = await self.fetch_rendered_page(url, render_js=True)
        if not html:
            return designs

        soup = BeautifulSoup(html, 'lxml')

        # Tanishq uses product-tile class and data-pid
        product_tiles = soup.select('.product-tile, [data-pid], .product-grid-item')
        logger.info(f"Tanishq: Found {len(product_tiles)} product tiles")

        for tile in product_tiles[:limit]:
            try:
                # Get product ID
                pid = tile.get('data-pid', '')

                # Get title
                title_el = tile.select_one('.pdp-link, .product-name, .tile-body a, h3')
                title = ""
                if title_el:
                    title = title_el.get_text(strip=True) or title_el.get('title', '')

                # Get image - Tanishq uses tile-image class
                img = tile.select_one('img.tile-image, img[src*="tanishq"], img')
                image_url = ""
                if img:
                    image_url = img.get('src') or img.get('data-src') or ""

                # Get price
                price_el = tile.select_one('.sales .value, .product-price, [class*="price"]')
                price = None
                if price_el:
                    price_text = price_el.get('content') or price_el.get_text()
                    price = extract_price(price_text)

                # Get link
                link = tile.select_one('a.pdp-link, a[href*="/product/"]')
                source_url = link.get('href', url) if link else url
                if not source_url.startswith('http'):
                    source_url = f"https://www.tanishq.co.in{source_url}"

                if title and len(title) > 3:
                    designs.append(ScrapedDesign(
                        title=title,
                        price=price,
                        image_url=image_url,
                        source_url=source_url,
                        source='tanishq',
                        category=detect_category(title)
                    ))
            except Exception as e:
                logger.error(f"Tanishq parse error: {e}")
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

    async def scrape_pinterest(self, query: str = "indian gold jewelry", limit: int = 20) -> List[ScrapedDesign]:
        """Scrape trending jewelry from Pinterest."""
        designs = []

        # Pinterest search URL
        search_query = query.replace(' ', '%20')
        url = f"https://www.pinterest.com/search/pins/?q={search_query}"
        logger.info(f"Scraping Pinterest: {url}")

        html = await self.fetch_rendered_page(url, render_js=True)
        if not html:
            return designs

        soup = BeautifulSoup(html, 'lxml')

        # Pinterest uses data-test-id="pin" or similar structures
        pins = soup.select('[data-test-id="pin"], [class*="Pin"], .pinWrapper')
        logger.info(f"Pinterest: Found {len(pins)} pins")

        for pin in pins[:limit]:
            try:
                # Get image
                img = pin.select_one('img[src*="pinimg"], img')
                image_url = ""
                if img:
                    image_url = img.get('src') or img.get('data-src') or ""
                    # Get higher resolution
                    image_url = image_url.replace('/236x/', '/564x/')

                # Get title/alt text
                title = ""
                if img:
                    title = img.get('alt', '')
                if not title:
                    title_el = pin.select_one('[class*="title"], [class*="description"]')
                    if title_el:
                        title = title_el.get_text(strip=True)

                # Get link
                link = pin.select_one('a[href*="/pin/"]')
                source_url = ""
                if link:
                    href = link.get('href', '')
                    source_url = f"https://www.pinterest.com{href}" if href.startswith('/') else href

                if image_url and len(title) > 3:
                    designs.append(ScrapedDesign(
                        title=title[:100],  # Truncate long titles
                        price=None,  # Pinterest doesn't have prices
                        image_url=image_url,
                        source_url=source_url or url,
                        source='pinterest',
                        category=detect_category(title or query)
                    ))
            except Exception as e:
                logger.error(f"Pinterest parse error: {e}")
                continue

        logger.info(f"Pinterest: Found {len(designs)} designs")
        return designs[:limit]

    async def scrape_all(self, category: str = "necklaces", limit_per_site: int = 10) -> List[ScrapedDesign]:
        """Scrape from all jewelry sites."""
        all_designs = []

        # Scrape each site
        for scraper_func in [self.scrape_bluestone, self.scrape_caratlane, self.scrape_tanishq]:
            try:
                designs = await scraper_func(category=category, limit=limit_per_site)
                all_designs.extend(designs)
            except Exception as e:
                logger.error(f"Scraper error: {e}")

        logger.info(f"Total scraped from jewelry sites: {len(all_designs)} designs")
        return all_designs

    async def scrape_all_with_pinterest(self, category: str = "necklaces", limit_per_site: int = 10) -> List[ScrapedDesign]:
        """Scrape from all sources including Pinterest."""
        all_designs = []

        # Scrape jewelry sites
        all_designs.extend(await self.scrape_all(category=category, limit_per_site=limit_per_site))

        # Scrape Pinterest with relevant query
        pinterest_queries = {
            "necklaces": "indian gold necklace designs",
            "earrings": "indian gold earrings designs",
            "rings": "indian gold ring designs",
            "bangles": "indian gold bangles designs",
            "bridal": "indian bridal jewelry gold",
            "mangalsutra": "mangalsutra designs gold",
            "temple": "temple jewelry gold south indian",
        }
        query = pinterest_queries.get(category, "indian gold jewelry designs")

        try:
            pinterest_designs = await self.scrape_pinterest(query=query, limit=limit_per_site)
            all_designs.extend(pinterest_designs)
        except Exception as e:
            logger.error(f"Pinterest scraper error: {e}")

        logger.info(f"Total scraped with Pinterest: {len(all_designs)} designs")
        return all_designs

    async def scrape_trending(self) -> Dict[str, List[ScrapedDesign]]:
        """Scrape trending designs across all categories and sources."""
        trending = {
            "new_arrivals": [],
            "bridal": [],
            "dailywear": [],
            "pinterest_trending": [],
        }

        try:
            # New arrivals from all sites
            trending["new_arrivals"] = await self.scrape_all(category="necklaces", limit_per_site=5)

            # Bridal collection
            trending["bridal"] = await self.scrape_all(category="bridal", limit_per_site=5)

            # Dailywear/lightweight
            trending["dailywear"] = await self.scrape_all(category="earrings", limit_per_site=5)

            # Pinterest trending
            trending["pinterest_trending"] = await self.scrape_pinterest(
                query="trending indian jewelry 2024",
                limit=10
            )
        except Exception as e:
            logger.error(f"Trending scrape error: {e}")

        return trending

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
