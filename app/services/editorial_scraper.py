"""
Editorial Scraper - Global jewelry designs from editorial + marketplace sources.

Strategy: Stop fighting anti-scraping on product catalogs.
Scrape editorial content (fashion magazines, bestsellers, trend roundups)
that is SEO-optimized and designed to be found.

Sources:
1. Google Images (via ScraperAPI) - trending designs by category
2. Amazon India Bestsellers - real products with prices
3. Etsy Trending - global handcrafted jewelry
4. Vogue/Fashion editorial - trend articles with curated images
"""

import logging
import re
from dataclasses import dataclass
from typing import List, Optional

import httpx
from bs4 import BeautifulSoup

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class EditorialDesign:
    """A design scraped from editorial/marketplace sources."""
    title: str
    image_url: str
    source: str  # amazon, etsy, vogue, google_images
    source_url: str = ""
    source_type: str = "marketplace"  # editorial, marketplace, inspiration
    category: str = "general"
    price: Optional[float] = None
    metal_type: str = "gold"


# Category -> search queries for Google Images
GOOGLE_IMAGE_QUERIES = {
    "necklaces": "latest gold necklace designs 2026 indian jewelry",
    "earrings": "trending gold earring designs 2026 jewelry",
    "bangles": "gold bangle new designs 2026 indian",
    "rings": "modern gold ring designs 2026",
    "bridal": "indian bridal jewelry collection 2026 gold kundan",
    "luxury": "Cartier Tiffany Van Cleef latest jewelry 2026 collection",
    "contemporary": "minimalist modern gold jewelry designs 2026",
    "temple": "south indian temple jewelry gold traditional designs",
    "mens": "men gold jewelry chain bracelet ring latest designs",
}

# Amazon India bestseller category URLs
AMAZON_CATEGORIES = {
    "necklaces": "https://www.amazon.in/gp/bestsellers/jewelry/1951050031",
    "earrings": "https://www.amazon.in/gp/bestsellers/jewelry/1951048031",
    "rings": "https://www.amazon.in/gp/bestsellers/jewelry/1951046031",
    "bangles": "https://www.amazon.in/gp/bestsellers/jewelry/1951044031",
}

# Etsy search queries
ETSY_QUERIES = {
    "necklaces": "gold necklace handmade indian",
    "earrings": "gold earring handcrafted",
    "contemporary": "minimalist gold jewelry modern",
    "bridal": "indian bridal gold jewelry set",
    "luxury": "luxury gold necklace designer",
}


class EditorialScraperService:
    """Scrape jewelry designs from editorial and marketplace sources."""

    def __init__(self):
        self.scraper_api_key = settings.scraper_api_key if hasattr(settings, 'scraper_api_key') else ""

    async def scrape_editorial_sources(self, category: str = "necklaces", limit: int = 8) -> List[EditorialDesign]:
        """Scrape from all editorial/marketplace sources for a category."""
        all_designs = []

        # Source 1: Amazon India Bestsellers (most reliable, has prices)
        try:
            amazon = await self.scrape_amazon_bestsellers(category, limit=limit)
            all_designs.extend(amazon)
            logger.info(f"Amazon {category}: {len(amazon)} designs")
        except Exception as e:
            logger.warning(f"Amazon scrape failed for {category}: {e}")

        # Source 2: Google Images (broad, good variety)
        try:
            google = await self.scrape_google_images(category, limit=limit)
            all_designs.extend(google)
            logger.info(f"Google Images {category}: {len(google)} designs")
        except Exception as e:
            logger.warning(f"Google Images scrape failed for {category}: {e}")

        # Source 3: Etsy (global handcrafted)
        try:
            etsy = await self.scrape_etsy_trending(category, limit=limit)
            all_designs.extend(etsy)
            logger.info(f"Etsy {category}: {len(etsy)} designs")
        except Exception as e:
            logger.warning(f"Etsy scrape failed for {category}: {e}")

        logger.info(f"Editorial scrape {category}: {len(all_designs)} total designs")
        return all_designs

    async def scrape_amazon_bestsellers(self, category: str, limit: int = 8) -> List[EditorialDesign]:
        """Scrape Amazon India jewelry bestsellers."""
        url = AMAZON_CATEGORIES.get(category)
        if not url:
            return []

        designs = []
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept-Language": "en-IN,en;q=0.9",
                },
            )
            if resp.status_code != 200:
                logger.warning(f"Amazon returned {resp.status_code}")
                return []

            soup = BeautifulSoup(resp.text, 'html.parser')

            # Amazon bestseller items
            items = soup.select('.zg-item-immersion, .a-list-item, [data-asin]')[:limit * 2]

            for item in items:
                try:
                    # Title
                    title_el = item.select_one('.p13n-sc-truncate, .a-link-normal span, ._cDEzb_p13n-sc-css-line-clamp-1_1Fn1y')
                    title = title_el.get_text(strip=True) if title_el else None

                    # Image
                    img_el = item.select_one('img')
                    img_url = img_el.get('src', '') if img_el else ''
                    # Get higher res image
                    if img_url and '_AC_' in img_url:
                        img_url = re.sub(r'\._AC_.*?\.', '._AC_SL500_.', img_url)

                    # Price
                    price_el = item.select_one('.p13n-sc-price, .a-price .a-offscreen, ._cDEzb_p13n-sc-price_3mJ9Z')
                    price = None
                    if price_el:
                        price_text = price_el.get_text(strip=True)
                        price_match = re.search(r'[\d,]+(?:\.\d+)?', price_text.replace(',', ''))
                        if price_match:
                            price = float(price_match.group().replace(',', ''))

                    # Link
                    link_el = item.select_one('a.a-link-normal[href*="/dp/"]')
                    link = f"https://www.amazon.in{link_el['href']}" if link_el and link_el.get('href') else ""

                    if title and img_url and len(img_url) > 10:
                        designs.append(EditorialDesign(
                            title=title[:200],
                            image_url=img_url,
                            source="amazon",
                            source_url=link[:500],
                            source_type="marketplace",
                            category=category,
                            price=price,
                            metal_type="gold",
                        ))

                except Exception:
                    continue

                if len(designs) >= limit:
                    break

        return designs[:limit]

    async def scrape_google_images(self, category: str, limit: int = 8) -> List[EditorialDesign]:
        """Scrape Google Images for jewelry designs using ScraperAPI."""
        if not self.scraper_api_key:
            logger.info("ScraperAPI not configured, skipping Google Images")
            return []

        query = GOOGLE_IMAGE_QUERIES.get(category, GOOGLE_IMAGE_QUERIES["necklaces"])
        # Use Google Images with time filter (last month)
        search_url = f"https://www.google.com/search?q={query}&tbm=isch&tbs=qdr:m"

        designs = []
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                "http://api.scraperapi.com",
                params={
                    "api_key": self.scraper_api_key,
                    "url": search_url,
                    "render": "true",
                },
            )
            if resp.status_code != 200:
                return []

            soup = BeautifulSoup(resp.text, 'html.parser')

            # Extract image results
            for img in soup.select('img[data-src], img[src]'):
                src = img.get('data-src') or img.get('src', '')
                if not src or 'gstatic' in src or len(src) < 20 or src.startswith('data:'):
                    continue

                alt = img.get('alt', 'Jewelry Design')
                if len(alt) < 3:
                    alt = f"{category.title()} Design"

                # Get parent link for source URL
                parent_link = img.find_parent('a')
                source_url = parent_link.get('href', '') if parent_link else ''

                designs.append(EditorialDesign(
                    title=alt[:200],
                    image_url=src,
                    source="google_images",
                    source_url=source_url[:500],
                    source_type="inspiration",
                    category=category,
                    metal_type="gold",
                ))

                if len(designs) >= limit:
                    break

        return designs[:limit]

    async def scrape_etsy_trending(self, category: str, limit: int = 8) -> List[EditorialDesign]:
        """Scrape Etsy trending jewelry."""
        query = ETSY_QUERIES.get(category)
        if not query:
            return []

        url = f"https://www.etsy.com/search?q={query.replace(' ', '+')}&order=most_relevant"
        designs = []

        # Try direct fetch first (Etsy partially renders server-side)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            if resp.status_code != 200:
                return []

            soup = BeautifulSoup(resp.text, 'html.parser')

            # Etsy listing cards
            for listing in soup.select('[data-listing-id], .v2-listing-card'):
                try:
                    img_el = listing.select_one('img')
                    img_url = img_el.get('src', '') if img_el else ''

                    title_el = listing.select_one('.v2-listing-card__title, h3, [title]')
                    title = title_el.get_text(strip=True) if title_el else (img_el.get('alt', '') if img_el else '')

                    price_el = listing.select_one('.currency-value, .lc-price span')
                    price = None
                    if price_el:
                        price_text = price_el.get_text(strip=True)
                        price_match = re.search(r'[\d,.]+', price_text)
                        if price_match:
                            usd_price = float(price_match.group().replace(',', ''))
                            price = round(usd_price * 83)  # Approximate USD→INR

                    link_el = listing.select_one('a[href*="/listing/"]')
                    link = link_el.get('href', '') if link_el else ''

                    if title and img_url and len(img_url) > 10:
                        designs.append(EditorialDesign(
                            title=title[:200],
                            image_url=img_url,
                            source="etsy",
                            source_url=link[:500],
                            source_type="marketplace",
                            category=category,
                            price=price,
                            metal_type="gold",
                        ))

                except Exception:
                    continue

                if len(designs) >= limit:
                    break

        return designs[:limit]

    async def scrape_editorial_trends(self, limit: int = 8) -> List[EditorialDesign]:
        """Scrape fashion editorial jewelry articles."""
        designs = []

        urls = [
            ("https://www.vogue.in/fashion/jewellery", "vogue"),
            ("https://www.grazia.co.in/fashion/jewellery", "grazia"),
        ]

        async with httpx.AsyncClient(timeout=15) as client:
            for url, source in urls:
                try:
                    resp = await client.get(
                        url,
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                    )
                    if resp.status_code != 200:
                        continue

                    soup = BeautifulSoup(resp.text, 'html.parser')

                    # Look for article cards with images
                    for article in soup.select('article, .card, .story-card, [data-testid*="card"]')[:limit]:
                        try:
                            img_el = article.select_one('img[src]')
                            img_url = img_el.get('src', '') if img_el else ''

                            title_el = article.select_one('h2, h3, .headline, .title')
                            title = title_el.get_text(strip=True) if title_el else ''

                            link_el = article.select_one('a[href]')
                            link = link_el.get('href', '') if link_el else ''
                            if link and not link.startswith('http'):
                                link = f"https://{source}.in{link}" if 'vogue' in source else link

                            if title and img_url:
                                designs.append(EditorialDesign(
                                    title=title[:200],
                                    image_url=img_url,
                                    source=source,
                                    source_url=link[:500],
                                    source_type="editorial",
                                    category="general",
                                    metal_type="gold",
                                ))
                        except Exception:
                            continue

                except Exception as e:
                    logger.warning(f"Editorial scrape {source} failed: {e}")

        return designs[:limit]


# Singleton
editorial_scraper = EditorialScraperService()
