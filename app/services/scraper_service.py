"""
Trend Scout Scraper Service - Scrape jewelry designs from competitors.

Sources:
- BlueStone (API + HTML fallback)
- CaratLane (API + HTML fallback)
- Tanishq
- Pinterest

Runs daily at 6 AM IST via scheduler.
"""

import logging
import re
import asyncio
import random
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
import httpx
from bs4 import BeautifulSoup
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Design

logger = logging.getLogger(__name__)

# Realistic browser headers
def get_browser_headers(referer: str = None) -> Dict[str, str]:
    """Get realistic browser headers."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }
    if referer:
        headers["Referer"] = referer
    return headers


def get_api_headers(origin: str) -> Dict[str, str]:
    """Get headers for API requests."""
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": origin,
        "Referer": f"{origin}/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }


# Category mapping
CATEGORY_KEYWORDS = {
    "bridal": ["bridal", "wedding", "engagement", "mangalsutra", "choker", "heavy", "kundan", "polki"],
    "dailywear": ["dailywear", "daily wear", "lightweight", "office", "casual", "simple", "minimal"],
    "temple": ["temple", "traditional", "antique", "south indian", "kemp", "lakshmi"],
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
    # Remove commas and find numbers
    clean_text = text.replace(',', '').replace(' ', '')
    match = re.search(r'[\₹Rs\.]*(\d+)', clean_text)
    if match:
        try:
            return float(match.group(1))
        except:
            pass
    return None


async def random_delay(min_sec: float = 1.0, max_sec: float = 3.0):
    """Add random delay to be polite to servers."""
    delay = random.uniform(min_sec, max_sec)
    await asyncio.sleep(delay)


class ScraperService:
    """Service for scraping jewelry designs from various sources."""

    def __init__(self):
        self.timeout = 30.0

    async def scrape_all(self, db: AsyncSession) -> Dict[str, int]:
        """Run all scrapers and return counts. Only BlueStone (real data)."""
        results = {
            "bluestone": 0,
            "total": 0,
            "errors": []
        }

        try:
            logger.info("Starting BlueStone scraper...")
            results["bluestone"] = await self.scrape_bluestone(db)
            await db.commit()
        except Exception as e:
            logger.error(f"BlueStone scraper failed: {e}")
            results["errors"].append(f"BlueStone: {str(e)}")
            await db.rollback()
            results["errors"].append(f"Tanishq: {str(e)}")
            await db.rollback()  # Rollback failed transaction

        results["total"] = results["bluestone"]
        logger.info(f"Scraping complete: {results['total']} designs found")

        return results

    async def scrape_bluestone(self, db: AsyncSession, limit: int = 20) -> int:
        """Scrape designs from BlueStone using their API."""
        logger.info("Scraping BlueStone...")
        count = 0

        # BlueStone API endpoints
        api_urls = [
            ("https://www.bluestone.com/api/v2/products?category=gold-necklaces&page=1&size=20", "necklace"),
            ("https://www.bluestone.com/api/v2/products?category=gold-earrings&page=1&size=20", "earring"),
            ("https://www.bluestone.com/api/v2/products?category=gold-rings&page=1&size=20", "ring"),
            ("https://www.bluestone.com/api/v2/products?category=gold-bangles&page=1&size=20", "bangle"),
        ]

        # Fallback HTML URLs
        html_urls = [
            ("https://www.bluestone.com/jewellery/gold-necklaces.html", "necklace"),
            ("https://www.bluestone.com/jewellery/gold-earrings.html", "earring"),
            ("https://www.bluestone.com/jewellery/gold-rings.html", "ring"),
            ("https://www.bluestone.com/jewellery/gold-bangles.html", "bangle"),
        ]

        headers = get_api_headers("https://www.bluestone.com")

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            # Try API first
            for url, item_type in api_urls:
                try:
                    logger.info(f"BlueStone API: {url}")
                    response = await client.get(url, headers=headers)
                    logger.info(f"BlueStone API response: {response.status_code}")

                    if response.status_code == 200:
                        try:
                            data = response.json()
                            products = data.get("products", data.get("data", []))
                            logger.info(f"BlueStone API returned {len(products) if isinstance(products, list) else 0} products")

                            if isinstance(products, list):
                                for product in products[:limit]:
                                    title = product.get("name") or product.get("title")
                                    if not title:
                                        continue

                                    price = product.get("price") or product.get("salePrice")
                                    image_url = product.get("image") or product.get("imageUrl")
                                    source_url = product.get("url") or product.get("pdpUrl")

                                    if source_url and not source_url.startswith("http"):
                                        source_url = f"https://www.bluestone.com{source_url}"

                                    # Check if exists
                                    existing = await db.execute(
                                        select(Design).where(Design.source == "bluestone").where(Design.title == title)
                                    )
                                    if existing.scalar_one_or_none():
                                        continue

                                    design = Design(
                                        source="bluestone",
                                        source_url=source_url,
                                        image_url=image_url,
                                        title=title,
                                        category=detect_category(title),
                                        metal_type="gold",
                                        price_range_min=float(price) if price else None,
                                        price_range_max=float(price) if price else None,
                                        style_tags=[item_type],
                                        trending_score=50
                                    )
                                    db.add(design)
                                    count += 1

                        except json.JSONDecodeError:
                            logger.warning(f"BlueStone API returned non-JSON response")

                    await random_delay(1, 2)

                except Exception as e:
                    logger.error(f"BlueStone API error for {url}: {e}")

            # If API didn't work, try HTML scraping
            if count == 0:
                logger.info("BlueStone API failed, trying HTML scraping...")
                headers = get_browser_headers("https://www.bluestone.com")

                for url, item_type in html_urls:
                    try:
                        logger.info(f"BlueStone HTML: {url}")
                        response = await client.get(url, headers=headers)
                        logger.info(f"BlueStone HTML response: {response.status_code}, length: {len(response.text)}")

                        if response.status_code != 200:
                            continue

                        soup = BeautifulSoup(response.text, 'html.parser')

                        # Extract products from LD+JSON (BlueStone uses individual Product objects)
                        scripts = soup.find_all('script', type='application/ld+json')
                        for script in scripts:
                            try:
                                data = json.loads(script.string)

                                # Handle list of objects (BlueStone format)
                                if isinstance(data, list):
                                    for item in data:
                                        if isinstance(item, dict) and item.get("@type") == "Product":
                                            title = item.get("name")
                                            if not title:
                                                continue

                                            existing = await db.execute(
                                                select(Design).where(Design.source == "bluestone").where(Design.title == title)
                                            )
                                            if existing.scalar_one_or_none():
                                                continue

                                            offers = item.get("offers", {})
                                            price = offers.get("price") if isinstance(offers, dict) else None

                                            design = Design(
                                                source="bluestone",
                                                source_url=item.get("url"),
                                                image_url=item.get("image"),
                                                title=title,
                                                category=detect_category(title),
                                                metal_type="gold",
                                                price_range_min=float(price) if price else None,
                                                price_range_max=float(price) if price else None,
                                                style_tags=[item_type],
                                                trending_score=50
                                            )
                                            db.add(design)
                                            count += 1
                                            logger.info(f"BlueStone: Added '{title[:30]}...'")

                                            if count >= limit:
                                                break

                                # Handle ItemList format (fallback)
                                elif isinstance(data, dict) and data.get("@type") == "ItemList":
                                    items = data.get("itemListElement", [])
                                    logger.info(f"Found {len(items)} items in ItemList")
                                    for item in items[:limit]:
                                        product = item.get("item", {})
                                        title = product.get("name")
                                        if not title:
                                            continue

                                        existing = await db.execute(
                                            select(Design).where(Design.source == "bluestone").where(Design.title == title)
                                        )
                                        if existing.scalar_one_or_none():
                                            continue

                                        design = Design(
                                            source="bluestone",
                                            source_url=product.get("url"),
                                            image_url=product.get("image"),
                                            title=title,
                                            category=detect_category(title),
                                            metal_type="gold",
                                            style_tags=[item_type],
                                            trending_score=50
                                        )
                                        db.add(design)
                                        count += 1

                            except json.JSONDecodeError:
                                pass

                        logger.info(f"BlueStone {item_type}: {count} designs so far from LD+JSON")

                        # HTML parsing fallback (if LD+JSON didn't work)
                        if count == 0:
                            products = soup.select('.product-card, .plp-prod-card, [data-product-id], .product-item, .plp-product')
                            logger.info(f"BlueStone HTML found {len(products)} product elements")

                        for product in soup.select('.product-card, .plp-prod-card, [data-product-id]')[:limit]:
                            try:
                                title_elem = product.select_one('.product-title, .prod-name, h3, h4, .title, [class*="name"]')
                                title = title_elem.get_text(strip=True) if title_elem else None

                                if not title:
                                    continue

                                price_elem = product.select_one('.product-price, .prod-price, .price, [class*="price"]')
                                price_text = price_elem.get_text(strip=True) if price_elem else None
                                price = extract_price(price_text)

                                img_elem = product.select_one('img')
                                image_url = img_elem.get('src') or img_elem.get('data-src') if img_elem else None

                                link_elem = product.select_one('a')
                                source_url = link_elem.get('href') if link_elem else None
                                if source_url and not source_url.startswith('http'):
                                    source_url = f"https://www.bluestone.com{source_url}"

                                existing = await db.execute(
                                    select(Design).where(Design.source == "bluestone").where(Design.title == title)
                                )
                                if existing.scalar_one_or_none():
                                    continue

                                design = Design(
                                    source="bluestone",
                                    source_url=source_url,
                                    image_url=image_url,
                                    title=title,
                                    category=detect_category(title),
                                    metal_type="gold",
                                    price_range_min=price,
                                    price_range_max=price,
                                    style_tags=[item_type],
                                    trending_score=50
                                )
                                db.add(design)
                                count += 1

                            except Exception as e:
                                logger.debug(f"Error parsing BlueStone product: {e}")

                        await random_delay(1, 2)

                    except Exception as e:
                        logger.error(f"BlueStone HTML error for {url}: {e}")

        await db.flush()
        logger.info(f"BlueStone: {count} designs scraped")
        return count

    async def get_trending_designs(
        self,
        db: AsyncSession,
        category: str = None,
        limit: int = 5
    ) -> List[Design]:
        """Get trending designs, optionally filtered by category."""
        from sqlalchemy import desc

        query = select(Design).order_by(desc(Design.trending_score), desc(Design.scraped_at))

        if category:
            query = query.where(Design.category == category)

        query = query.limit(limit)
        result = await db.execute(query)
        return result.scalars().all()

    async def get_new_designs_count(self, db: AsyncSession, hours: int = 24) -> int:
        """Count designs scraped in the last N hours."""
        from datetime import timedelta
        from sqlalchemy import func

        cutoff = datetime.utcnow() - timedelta(hours=hours)
        result = await db.execute(
            select(func.count(Design.id)).where(Design.scraped_at >= cutoff)
        )
        return result.scalar() or 0

    async def record_preference(
        self,
        db: AsyncSession,
        user_id: int,
        design_id: int,
        action: str  # liked, skipped, saved
    ):
        """Record user's preference for a design."""
        from app.models import UserDesignPreference

        pref = UserDesignPreference(
            user_id=user_id,
            design_id=design_id,
            action=action
        )
        db.add(pref)
        await db.flush()

        # Update trending score based on likes
        if action == "liked":
            design = await db.get(Design, design_id)
            if design:
                design.trending_score = min(100, design.trending_score + 2)

        logger.info(f"Recorded preference: user={user_id}, design={design_id}, action={action}")

    async def get_user_saved_designs(self, db: AsyncSession, user_id: int) -> List[Design]:
        """Get designs saved/liked by a user."""
        from app.models import UserDesignPreference

        result = await db.execute(
            select(Design)
            .join(UserDesignPreference)
            .where(UserDesignPreference.user_id == user_id)
            .where(UserDesignPreference.action.in_(["liked", "saved"]))
            .order_by(UserDesignPreference.created_at.desc())
        )
        return result.scalars().all()


# Singleton instance
scraper_service = ScraperService()
