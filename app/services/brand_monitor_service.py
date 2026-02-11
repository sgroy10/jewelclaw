"""
Brand Sitemap Monitor — Competitive intelligence from public XML sitemaps.

Sitemaps are PUBLIC files designed for Google crawlers. They list every product URL
with <lastmod> dates. We don't scrape product pages (which block us) — we just
count new URLs to detect brand activity.

Monitored brands: Tanishq, CaratLane, BlueStone, Kalyan, Malabar
"""

import logging
import re
from datetime import datetime
from typing import Dict, List, Optional
from xml.etree import ElementTree

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

logger = logging.getLogger(__name__)

# Brand sitemap URLs (public, no auth needed)
BRAND_SITEMAPS = {
    "tanishq": [
        "https://www.tanishq.co.in/sitemap.xml",
    ],
    "caratlane": [
        "https://www.caratlane.com/sitemap.xml",
    ],
    "bluestone": [
        "https://www.bluestone.com/sitemap.xml",
    ],
    "kalyan": [
        "https://www.kalyanjewellers.net/sitemap.xml",
    ],
    "malabar": [
        "https://www.malabargoldanddiamonds.com/sitemap.xml",
    ],
}

# URL patterns that indicate product pages (not blog, category, etc.)
PRODUCT_PATTERNS = {
    "tanishq": [r"/product/", r"/p/", r"\.html$"],
    "caratlane": [r"/jewellery/", r"/diamond-", r"/gold-", r"/solitaire-"],
    "bluestone": [r"/jewellery/", r"/gold-", r"/diamond-", r"\.html$"],
    "kalyan": [r"/product/", r"/jewellery/"],
    "malabar": [r"/product/", r"/jewellery/"],
}

# Category detection from URL slugs
CATEGORY_SLUGS = {
    "necklace": "necklaces",
    "earring": "earrings",
    "ring": "rings",
    "bangle": "bangles",
    "bracelet": "bracelets",
    "pendant": "pendants",
    "chain": "chains",
    "mangalsutra": "bridal",
    "bridal": "bridal",
    "wedding": "bridal",
    "kundan": "bridal",
    "temple": "temple",
    "antique": "temple",
    "men": "mens",
    "kada": "mens",
}


class BrandMonitorService:
    """Monitor brand sitemaps for competitive intelligence."""

    async def scan_brand_sitemaps(self, db: AsyncSession) -> Dict[str, dict]:
        """
        Scan all brand sitemaps, detect new product additions.
        Returns: {"tanishq": {"total": 450, "new": 8, "categories": {"bridal": 3, "rings": 2}}, ...}
        """
        from app.models import BrandSitemapEntry

        results = {}

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for brand, sitemap_urls in BRAND_SITEMAPS.items():
                try:
                    product_urls = await self._fetch_product_urls(client, brand, sitemap_urls)
                    total_count = len(product_urls)

                    # Get last known count from DB
                    existing = await db.execute(
                        select(BrandSitemapEntry).where(BrandSitemapEntry.brand == brand)
                    )
                    entry = existing.scalar_one_or_none()

                    last_count = entry.product_count if entry else 0
                    new_products = max(0, total_count - last_count)

                    # Detect categories from new URLs
                    category_breakdown = self._categorize_urls(product_urls)

                    # Update or create DB entry
                    if entry:
                        entry.product_count = total_count
                        entry.category_breakdown = category_breakdown
                        entry.last_scanned_at = datetime.now()
                    else:
                        entry = BrandSitemapEntry(
                            brand=brand,
                            product_count=total_count,
                            category_breakdown=category_breakdown,
                            sitemap_url=sitemap_urls[0],
                        )
                        db.add(entry)

                    results[brand] = {
                        "total": total_count,
                        "new": new_products,
                        "categories": category_breakdown,
                    }

                    logger.info(f"Brand {brand}: {total_count} products ({new_products} new)")

                except Exception as e:
                    logger.warning(f"Sitemap scan failed for {brand}: {e}")
                    results[brand] = {"total": 0, "new": 0, "categories": {}, "error": str(e)}

        # Store as IndustryNews if significant changes
        try:
            from app.models import IndustryNews
            for brand, data in results.items():
                if data.get("new", 0) > 3:
                    cats = data.get("categories", {})
                    cat_str = ", ".join(f"{k}: {v}" for k, v in sorted(cats.items(), key=lambda x: -x[1])[:3])
                    news = IndustryNews(
                        headline=f"{brand.title()}: {data['new']} new products detected",
                        source=f"brand_sitemap",
                        category="launch",
                        priority="medium",
                        brands=[brand.title()],
                        summary=f"{brand.title()} added {data['new']} products ({cat_str})" if cat_str else f"{brand.title()} added {data['new']} products",
                    )
                    db.add(news)
        except Exception as e:
            logger.warning(f"Failed to save brand news: {e}")

        return results

    async def _fetch_product_urls(
        self, client: httpx.AsyncClient, brand: str, sitemap_urls: List[str]
    ) -> List[str]:
        """Fetch and parse sitemap XML, return product URLs."""
        all_product_urls = []
        patterns = PRODUCT_PATTERNS.get(brand, [r"/product/"])

        for sitemap_url in sitemap_urls:
            try:
                resp = await client.get(
                    sitemap_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
                    },
                )
                if resp.status_code != 200:
                    logger.warning(f"{brand} sitemap {resp.status_code}: {sitemap_url}")
                    continue

                root = ElementTree.fromstring(resp.text)

                # Handle sitemap index (sitemap of sitemaps)
                ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
                child_sitemaps = root.findall(".//sm:sitemap/sm:loc", ns)

                if child_sitemaps:
                    # This is a sitemap index — fetch child sitemaps (only product ones)
                    for child in child_sitemaps:
                        child_url = child.text
                        if not child_url:
                            continue
                        # Only fetch product-related sitemaps
                        if any(kw in child_url.lower() for kw in ["product", "jewellery", "jewelry", "gold", "diamond"]):
                            try:
                                child_resp = await client.get(
                                    child_url,
                                    headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"},
                                )
                                if child_resp.status_code == 200:
                                    child_root = ElementTree.fromstring(child_resp.text)
                                    for url_el in child_root.findall(".//sm:url/sm:loc", ns):
                                        if url_el.text:
                                            all_product_urls.append(url_el.text)
                            except Exception:
                                continue
                else:
                    # This is a regular sitemap — extract URLs
                    for url_el in root.findall(".//sm:url/sm:loc", ns):
                        if url_el.text:
                            all_product_urls.append(url_el.text)

            except ElementTree.ParseError as e:
                logger.warning(f"{brand} sitemap XML parse error: {e}")
            except Exception as e:
                logger.warning(f"{brand} sitemap fetch error: {e}")

        # Filter to product URLs only
        product_urls = []
        for url in all_product_urls:
            url_lower = url.lower()
            if any(re.search(pattern, url_lower) for pattern in patterns):
                product_urls.append(url)

        return product_urls

    def _categorize_urls(self, urls: List[str]) -> Dict[str, int]:
        """Categorize product URLs by jewelry type based on URL slugs."""
        categories = {}
        for url in urls:
            url_lower = url.lower()
            matched = False
            for slug, cat in CATEGORY_SLUGS.items():
                if slug in url_lower:
                    categories[cat] = categories.get(cat, 0) + 1
                    matched = True
                    break
            if not matched:
                categories["other"] = categories.get("other", 0) + 1
        return categories

    async def get_brand_activity_summary(self, db: AsyncSession) -> str:
        """Get formatted brand activity for trend reports."""
        try:
            from app.models import BrandSitemapEntry

            result = await db.execute(
                select(BrandSitemapEntry).order_by(BrandSitemapEntry.brand)
            )
            entries = result.scalars().all()

            if not entries:
                return ""

            lines = []
            for entry in entries:
                if entry.product_count > 0:
                    cats = entry.category_breakdown or {}
                    top_cats = sorted(cats.items(), key=lambda x: -x[1])[:3]
                    cat_str = ", ".join(f"{k}" for k, _ in top_cats) if top_cats else "mixed"
                    lines.append(f"  {entry.brand.title()}: {entry.product_count} products ({cat_str})")

            return "\n".join(lines) if lines else ""

        except Exception as e:
            logger.warning(f"Brand summary error: {e}")
            return ""


# Singleton
brand_monitor = BrandMonitorService()
