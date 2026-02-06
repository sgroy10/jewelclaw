"""
Trend Scout Scraper Service - Scrape jewelry designs from competitors.

Sources:
- BlueStone
- CaratLane
- Tanishq
- Pinterest

Runs daily at 6 AM IST via scheduler.
"""

import logging
import re
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional
import httpx
from bs4 import BeautifulSoup
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Design

logger = logging.getLogger(__name__)

# User agent to avoid blocks
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Category mapping
CATEGORY_KEYWORDS = {
    "bridal": ["bridal", "wedding", "engagement", "mangalsutra", "choker", "heavy"],
    "dailywear": ["dailywear", "daily wear", "lightweight", "office", "casual", "simple"],
    "temple": ["temple", "traditional", "antique", "south indian", "kemp"],
    "contemporary": ["contemporary", "modern", "fusion", "western", "geometric"],
    "mens": ["mens", "men's", "gents", "male", "kada", "bracelet for men"],
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
    match = re.search(r'[\₹Rs\.]*\s*([\d,]+)', text.replace(',', ''))
    if match:
        try:
            return float(match.group(1))
        except:
            pass
    return None


class ScraperService:
    """Service for scraping jewelry designs from various sources."""

    def __init__(self):
        self.timeout = 30.0

    async def scrape_all(self, db: AsyncSession) -> Dict[str, int]:
        """Run all scrapers and return counts."""
        results = {
            "bluestone": 0,
            "caratlane": 0,
            "tanishq": 0,
            "pinterest": 0,
            "total": 0,
            "errors": []
        }

        # Run scrapers
        try:
            results["bluestone"] = await self.scrape_bluestone(db)
        except Exception as e:
            logger.error(f"BlueStone scraper failed: {e}")
            results["errors"].append(f"BlueStone: {str(e)}")

        try:
            results["caratlane"] = await self.scrape_caratlane(db)
        except Exception as e:
            logger.error(f"CaratLane scraper failed: {e}")
            results["errors"].append(f"CaratLane: {str(e)}")

        try:
            results["pinterest"] = await self.scrape_pinterest(db)
        except Exception as e:
            logger.error(f"Pinterest scraper failed: {e}")
            results["errors"].append(f"Pinterest: {str(e)}")

        results["total"] = results["bluestone"] + results["caratlane"] + results["pinterest"]
        logger.info(f"Scraping complete: {results['total']} designs found")

        return results

    async def scrape_bluestone(self, db: AsyncSession, limit: int = 20) -> int:
        """Scrape designs from BlueStone."""
        logger.info("Scraping BlueStone...")
        count = 0

        categories = [
            ("https://www.bluestone.com/jewellery/gold-necklaces.html", "necklace"),
            ("https://www.bluestone.com/jewellery/gold-earrings.html", "earring"),
            ("https://www.bluestone.com/jewellery/gold-rings.html", "ring"),
            ("https://www.bluestone.com/jewellery/gold-bangles.html", "bangle"),
        ]

        async with httpx.AsyncClient(headers=HEADERS, timeout=self.timeout, follow_redirects=True) as client:
            for url, item_type in categories:
                try:
                    response = await client.get(url)
                    if response.status_code != 200:
                        logger.warning(f"BlueStone {url} returned {response.status_code}")
                        continue

                    soup = BeautifulSoup(response.text, 'html.parser')

                    # Find product cards
                    products = soup.select('.product-card, .plp-prod-card, [data-product-id]')[:limit]

                    for product in products:
                        try:
                            # Extract data
                            title_elem = product.select_one('.product-title, .prod-name, h3, h4')
                            title = title_elem.get_text(strip=True) if title_elem else None

                            price_elem = product.select_one('.product-price, .prod-price, .price')
                            price_text = price_elem.get_text(strip=True) if price_elem else None
                            price = extract_price(price_text)

                            img_elem = product.select_one('img')
                            image_url = img_elem.get('src') or img_elem.get('data-src') if img_elem else None

                            link_elem = product.select_one('a')
                            source_url = link_elem.get('href') if link_elem else None
                            if source_url and not source_url.startswith('http'):
                                source_url = f"https://www.bluestone.com{source_url}"

                            if not title:
                                continue

                            # Check if already exists
                            existing = await db.execute(
                                select(Design).where(Design.source == "bluestone").where(Design.title == title)
                            )
                            if existing.scalar_one_or_none():
                                continue

                            # Create design
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
                                trending_score=50  # Default score
                            )
                            db.add(design)
                            count += 1

                        except Exception as e:
                            logger.debug(f"Error parsing BlueStone product: {e}")
                            continue

                    await asyncio.sleep(1)  # Be nice to servers

                except Exception as e:
                    logger.error(f"Error scraping BlueStone {url}: {e}")
                    continue

        await db.flush()
        logger.info(f"BlueStone: {count} designs scraped")
        return count

    async def scrape_caratlane(self, db: AsyncSession, limit: int = 20) -> int:
        """Scrape designs from CaratLane."""
        logger.info("Scraping CaratLane...")
        count = 0

        categories = [
            ("https://www.caratlane.com/jewellery/necklaces.html", "necklace"),
            ("https://www.caratlane.com/jewellery/earrings.html", "earring"),
            ("https://www.caratlane.com/jewellery/rings.html", "ring"),
            ("https://www.caratlane.com/jewellery/bangles-bracelets.html", "bangle"),
        ]

        async with httpx.AsyncClient(headers=HEADERS, timeout=self.timeout, follow_redirects=True) as client:
            for url, item_type in categories:
                try:
                    response = await client.get(url)
                    if response.status_code != 200:
                        logger.warning(f"CaratLane {url} returned {response.status_code}")
                        continue

                    soup = BeautifulSoup(response.text, 'html.parser')

                    # Find product cards
                    products = soup.select('.product-item, .plp-card, [data-sku]')[:limit]

                    for product in products:
                        try:
                            title_elem = product.select_one('.product-name, .prod-title, h3')
                            title = title_elem.get_text(strip=True) if title_elem else None

                            price_elem = product.select_one('.product-price, .price, .amount')
                            price_text = price_elem.get_text(strip=True) if price_elem else None
                            price = extract_price(price_text)

                            img_elem = product.select_one('img')
                            image_url = img_elem.get('src') or img_elem.get('data-src') if img_elem else None

                            link_elem = product.select_one('a')
                            source_url = link_elem.get('href') if link_elem else None
                            if source_url and not source_url.startswith('http'):
                                source_url = f"https://www.caratlane.com{source_url}"

                            if not title:
                                continue

                            # Check if already exists
                            existing = await db.execute(
                                select(Design).where(Design.source == "caratlane").where(Design.title == title)
                            )
                            if existing.scalar_one_or_none():
                                continue

                            design = Design(
                                source="caratlane",
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
                            logger.debug(f"Error parsing CaratLane product: {e}")
                            continue

                    await asyncio.sleep(1)

                except Exception as e:
                    logger.error(f"Error scraping CaratLane {url}: {e}")
                    continue

        await db.flush()
        logger.info(f"CaratLane: {count} designs scraped")
        return count

    async def scrape_pinterest(self, db: AsyncSession, limit: int = 20) -> int:
        """Scrape designs from Pinterest search."""
        logger.info("Scraping Pinterest...")
        count = 0

        search_terms = [
            "indian gold jewelry designs",
            "bridal gold necklace designs",
            "lightweight gold earrings",
            "temple jewelry designs",
        ]

        async with httpx.AsyncClient(headers=HEADERS, timeout=self.timeout, follow_redirects=True) as client:
            for term in search_terms:
                try:
                    # Pinterest search URL
                    url = f"https://www.pinterest.com/search/pins/?q={term.replace(' ', '%20')}"
                    response = await client.get(url)

                    if response.status_code != 200:
                        logger.warning(f"Pinterest search returned {response.status_code}")
                        continue

                    soup = BeautifulSoup(response.text, 'html.parser')

                    # Pinterest uses dynamic loading, but we can get some initial pins
                    pins = soup.select('[data-test-id="pin"], .pinWrapper, img[src*="pinimg"]')[:limit]

                    for pin in pins:
                        try:
                            img_elem = pin if pin.name == 'img' else pin.select_one('img')
                            if not img_elem:
                                continue

                            image_url = img_elem.get('src') or img_elem.get('data-src')
                            if not image_url or 'pinimg' not in str(image_url):
                                continue

                            # Generate title from search term
                            title = f"Pinterest: {term.title()}"

                            # Check if image already exists
                            existing = await db.execute(
                                select(Design).where(Design.image_url == image_url)
                            )
                            if existing.scalar_one_or_none():
                                continue

                            design = Design(
                                source="pinterest",
                                source_url=url,
                                image_url=image_url,
                                title=title,
                                category=detect_category(term),
                                metal_type="gold",
                                style_tags=term.split(),
                                trending_score=60  # Pinterest = trending
                            )
                            db.add(design)
                            count += 1

                        except Exception as e:
                            logger.debug(f"Error parsing Pinterest pin: {e}")
                            continue

                    await asyncio.sleep(2)  # Be extra nice to Pinterest

                except Exception as e:
                    logger.error(f"Error scraping Pinterest '{term}': {e}")
                    continue

        await db.flush()
        logger.info(f"Pinterest: {count} designs scraped")
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
