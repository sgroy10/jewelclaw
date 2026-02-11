"""
Editorial Scraper - Amazon India Bestsellers for price intelligence + real designs.

Strategy: Only scrape sources that return real jewelry data.
Amazon India Bestsellers = real products, real prices, real rankings.

All other sources removed (returned garbage or were irrelevant to Indian market).
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


# Removed: GOOGLE_IMAGE_QUERIES (returned garbage — ice hockey doodles as "jewelry")

# Amazon India bestseller category URLs
AMAZON_CATEGORIES = {
    "necklaces": "https://www.amazon.in/gp/bestsellers/jewelry/1951050031",
    "earrings": "https://www.amazon.in/gp/bestsellers/jewelry/1951048031",
    "rings": "https://www.amazon.in/gp/bestsellers/jewelry/1951046031",
    "bangles": "https://www.amazon.in/gp/bestsellers/jewelry/1951044031",
}

# Removed: ETSY_QUERIES (irrelevant to Indian market, USD pricing confusing)


class EditorialScraperService:
    """Scrape jewelry designs from editorial and marketplace sources."""

    def __init__(self):
        self.scraper_api_key = settings.scraper_api_key if hasattr(settings, 'scraper_api_key') else ""

    async def scrape_editorial_sources(self, category: str = "necklaces", limit: int = 8) -> List[EditorialDesign]:
        """Scrape from Amazon bestsellers (only reliable marketplace source)."""
        all_designs = []

        # Amazon India Bestsellers — real products, real prices, real rankings
        try:
            amazon = await self.scrape_amazon_bestsellers(category, limit=limit)
            # Quality gate: only keep actual jewelry items
            validated = [d for d in amazon if self._validate_design(d)]
            all_designs.extend(validated)
            logger.info(f"Amazon {category}: {len(validated)} designs (filtered from {len(amazon)})")
        except Exception as e:
            logger.warning(f"Amazon scrape failed for {category}: {e}")

        return all_designs

    def _validate_design(self, design: EditorialDesign) -> bool:
        """Quality gate — reject non-jewelry items before they enter DB."""
        jewelry_keywords = {
            "necklace", "earring", "ring", "bangle", "bracelet", "pendant",
            "chain", "jewelry", "jewellery", "gold", "diamond", "kundan",
            "polki", "mangalsutra", "jhumka", "choker", "kada", "stud",
            "hoop", "charm", "anklet", "nose", "toe", "set", "silver",
            "plated", "carat", "karat",
        }
        title_lower = design.title.lower()
        if not any(kw in title_lower for kw in jewelry_keywords):
            return False
        if design.price and (design.price < 200 or design.price > 5000000):
            return False
        if not design.image_url or len(design.image_url) < 20:
            return False
        return True

    async def get_price_benchmarks(self, category: str = "necklaces") -> dict:
        """Get price benchmarks from Amazon bestsellers for a category."""
        designs = await self.scrape_amazon_bestsellers(category, limit=20)
        if not designs:
            return {}
        prices = [d.price for d in designs if d.price and d.price > 1000]
        if not prices:
            return {}
        return {
            "category": category,
            "avg_price": round(sum(prices) / len(prices)),
            "min_price": min(prices),
            "max_price": max(prices),
            "count": len(prices),
            "source": "amazon_bestsellers",
        }

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

    # Removed scrapers (all returned garbage or were irrelevant):
    # - Google Images: returned ice hockey doodles as "jewelry"
    # - Etsy: irrelevant to Indian market, USD pricing
    # - Vogue/Grazia: broken, couldn't parse articles


# Singleton
editorial_scraper = EditorialScraperService()
