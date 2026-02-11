"""
Industry News Service - 24/7 jewelry industry intelligence.

Scrapes RSS feeds every 4 hours, uses Claude to categorize and prioritize.
HIGH priority news → instant WhatsApp alert.
MEDIUM priority → included in morning brief.
"""

import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import httpx
import anthropic
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc

from app.config import settings

logger = logging.getLogger(__name__)


# RSS feeds for jewelry industry news (all free, no API keys needed)
JEWELRY_RSS_FEEDS = [
    {
        "url": "https://news.google.com/rss/search?q=Tanishq+OR+%22Kalyan+Jewellers%22+OR+%22Malabar+Gold%22+OR+%22Titan+Company%22+jewellery+launch+store&hl=en-IN&gl=IN&ceid=IN:en",
        "name": "Indian Brands",
        "source": "google_news_brands",
    },
    {
        "url": "https://news.google.com/rss/search?q=jewelry+industry+india+collection+GJEPC+exhibition+%22jewelry+show%22&hl=en-IN&gl=IN&ceid=IN:en",
        "name": "India Industry",
        "source": "google_news_industry",
    },
    {
        "url": "https://news.google.com/rss/search?q=Cartier+OR+Tiffany+OR+Bulgari+OR+%22Van+Cleef%22+OR+Chopard+jewelry+collection+launch&hl=en&gl=US&ceid=US:en",
        "name": "Global Luxury",
        "source": "google_news_luxury",
    },
    {
        "url": "https://news.google.com/rss/search?q=gold+import+duty+india+RBI+hallmark+policy+SEBI+commodity&hl=en-IN&gl=IN&ceid=IN:en",
        "name": "Regulation",
        "source": "google_news_regulation",
    },
    {
        "url": "https://news.google.com/rss/search?q=%22jewelry+trend%22+OR+%22jewellery+trend%22+OR+%22bridal+jewelry%22+2026&hl=en&gl=US&ceid=US:en",
        "name": "Global Trends",
        "source": "google_news_trends",
    },
]


class IndustryNewsService:
    """Scrape, categorize, and serve jewelry industry news."""

    def __init__(self):
        self._client = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        return self._client

    async def scrape_all_feeds(self, db: AsyncSession) -> List[Dict]:
        """Scrape all RSS feeds and return new (non-duplicate) headlines."""
        from app.models import IndustryNews

        all_headlines = []

        async with httpx.AsyncClient(timeout=15) as client:
            for feed in JEWELRY_RSS_FEEDS:
                try:
                    resp = await client.get(
                        feed["url"],
                        headers={"User-Agent": "Mozilla/5.0 (compatible; JewelClaw/1.0)"},
                    )
                    if resp.status_code != 200:
                        logger.warning(f"RSS feed {feed['name']} returned {resp.status_code}")
                        continue

                    root = ET.fromstring(resp.text)
                    items = root.findall('.//item')

                    for item in items[:10]:  # Max 10 per feed
                        title_el = item.find('title')
                        link_el = item.find('link')
                        if title_el is not None and title_el.text:
                            headline = title_el.text.strip()
                            link = link_el.text.strip() if link_el is not None and link_el.text else ""
                            all_headlines.append({
                                "headline": headline,
                                "source_url": link,
                                "source": feed["source"],
                            })

                    logger.info(f"RSS {feed['name']}: fetched {min(len(items), 10)} items")

                except ET.ParseError:
                    logger.warning(f"RSS {feed['name']}: XML parse error")
                except Exception as e:
                    logger.warning(f"RSS {feed['name']} failed: {e}")

        if not all_headlines:
            logger.info("No headlines scraped from any feed")
            return []

        # Deduplicate against DB (last 48 hours)
        cutoff = datetime.now() - timedelta(hours=48)
        result = await db.execute(
            select(IndustryNews.headline)
            .where(IndustryNews.scraped_at >= cutoff)
        )
        existing_headlines = {row[0].lower() for row in result.fetchall()}

        new_headlines = [
            h for h in all_headlines
            if h["headline"].lower() not in existing_headlines
        ]

        logger.info(f"Industry news: {len(all_headlines)} total, {len(new_headlines)} new")
        return new_headlines

    async def categorize_and_save(self, db: AsyncSession, headlines: List[Dict]) -> int:
        """Use Claude to categorize headlines, then save to DB. Returns count saved."""
        from app.models import IndustryNews

        if not headlines:
            return 0

        # Batch headlines for Claude (max 30 at a time)
        batch = headlines[:30]
        headlines_text = "\n".join(f"- {h['headline']}" for h in batch)

        try:
            response = self.client.messages.create(
                model=settings.classifier_model,
                max_tokens=2000,
                messages=[{
                    "role": "user",
                    "content": f"""You are a jewelry industry news analyst. Categorize each headline.

Headlines:
{headlines_text}

For each headline, return a JSON array entry:
{{"headline": "exact headline text", "category": "launch|store_opening|collection|regulation|market|trend", "priority": "high|medium|low", "brands": ["BrandName"], "summary": "one-line summary for WhatsApp"}}

Priority rules:
- HIGH: New Tanishq/Kalyan/Malabar/Cartier/Tiffany collection launch, import duty change, major store opening, gold price >3% move, new hallmark rules
- MEDIUM: Industry trends, exhibition news, quarterly results, wedding season reports
- LOW: Generic market commentary, opinion pieces, old news

Return ONLY a JSON array, nothing else."""
                }]
            )

            text = response.content[0].text.strip()

            # Parse JSON
            categorized = None
            try:
                categorized = json.loads(text)
            except json.JSONDecodeError:
                import re
                json_match = re.search(r'\[[\s\S]*\]', text)
                if json_match:
                    categorized = json.loads(json_match.group())

            if not categorized or not isinstance(categorized, list):
                logger.warning("Could not parse Claude categorization response")
                # Save uncategorized
                for h in batch:
                    news = IndustryNews(
                        headline=h["headline"][:500],
                        source_url=h.get("source_url", "")[:500],
                        source=h.get("source", "unknown"),
                        category="uncategorized",
                        priority="low",
                        summary=h["headline"][:200],
                    )
                    db.add(news)
                await db.flush()
                return len(batch)

            # Save categorized headlines
            count = 0
            headline_map = {h["headline"].lower(): h for h in batch}

            for item in categorized:
                original = headline_map.get(item.get("headline", "").lower())
                source_url = original.get("source_url", "") if original else ""
                source = original.get("source", "unknown") if original else "unknown"

                news = IndustryNews(
                    headline=item.get("headline", "")[:500],
                    source_url=source_url[:500],
                    source=source,
                    category=item.get("category", "other"),
                    priority=item.get("priority", "low"),
                    brands=item.get("brands", []),
                    summary=item.get("summary", item.get("headline", ""))[:500],
                )
                db.add(news)
                count += 1

            await db.flush()
            logger.info(f"Categorized and saved {count} news items")
            return count

        except Exception as e:
            logger.error(f"News categorization failed: {e}")
            # Save uncategorized as fallback
            for h in batch:
                news = IndustryNews(
                    headline=h["headline"][:500],
                    source_url=h.get("source_url", "")[:500],
                    source=h.get("source", "unknown"),
                    priority="low",
                    summary=h["headline"][:200],
                )
                db.add(news)
            await db.flush()
            return len(batch)

    async def get_urgent_unsent(self, db: AsyncSession) -> list:
        """Get HIGH priority news items not yet sent as alerts."""
        from app.models import IndustryNews

        result = await db.execute(
            select(IndustryNews).where(
                and_(
                    IndustryNews.priority == "high",
                    IndustryNews.is_alerted == False,
                )
            ).order_by(desc(IndustryNews.scraped_at)).limit(5)
        )
        return list(result.scalars().all())

    async def get_for_morning_brief(self, db: AsyncSession) -> list:
        """Get MEDIUM+ priority news not yet included in a morning brief."""
        from app.models import IndustryNews

        result = await db.execute(
            select(IndustryNews).where(
                and_(
                    IndustryNews.priority.in_(["high", "medium"]),
                    IndustryNews.is_briefed == False,
                )
            ).order_by(desc(IndustryNews.scraped_at)).limit(3)
        )
        return list(result.scalars().all())

    async def mark_as_alerted(self, db: AsyncSession, news_ids: List[int]):
        """Mark news items as alerted."""
        from app.models import IndustryNews
        for nid in news_ids:
            result = await db.execute(select(IndustryNews).where(IndustryNews.id == nid))
            item = result.scalar_one_or_none()
            if item:
                item.is_alerted = True
        await db.flush()

    async def mark_as_briefed(self, db: AsyncSession, news_ids: List[int]):
        """Mark news items as included in morning brief."""
        from app.models import IndustryNews
        for nid in news_ids:
            result = await db.execute(select(IndustryNews).where(IndustryNews.id == nid))
            item = result.scalar_one_or_none()
            if item:
                item.is_briefed = True
        await db.flush()

    async def get_recent(self, db: AsyncSession, limit: int = 10) -> list:
        """Get recent news items for the 'news' command."""
        from app.models import IndustryNews

        cutoff = datetime.now() - timedelta(hours=24)
        result = await db.execute(
            select(IndustryNews)
            .where(IndustryNews.scraped_at >= cutoff)
            .order_by(desc(IndustryNews.scraped_at))
            .limit(limit)
        )
        return list(result.scalars().all())

    def format_news_message(self, news_items: list) -> str:
        """Format news items for WhatsApp display."""
        if not news_items:
            return "No recent jewelry industry news. Check back later!"

        lines = ["📰 *Jewelry Industry News*", ""]

        # Group by category
        categories = {}
        for item in news_items:
            cat = item.category or "other"
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(item)

        cat_emojis = {
            "launch": "🚀", "store_opening": "🏪", "collection": "💎",
            "regulation": "📋", "market": "📈", "trend": "🔥", "other": "📌",
        }

        for cat, items in categories.items():
            emoji = cat_emojis.get(cat, "📌")
            for item in items[:3]:
                summary = item.summary or item.headline
                brands = ""
                if item.brands:
                    brands = f" ({', '.join(item.brands[:2])})"
                lines.append(f"{emoji} {summary[:150]}{brands}")

        lines.append("")
        lines.append("_JewelClaw Industry Intelligence_")

        return "\n".join(lines)

    def format_urgent_alert(self, item) -> str:
        """Format a single HIGH priority news item as an urgent alert."""
        summary = item.summary or item.headline
        brands = ""
        if item.brands:
            brands = f"\nBrands: {', '.join(item.brands)}"

        cat_labels = {
            "launch": "New Launch", "store_opening": "Store Opening",
            "collection": "New Collection", "regulation": "Policy Update",
            "market": "Market Move", "trend": "Trending",
        }
        label = cat_labels.get(item.category, "Industry Update")

        return (
            f"🚨 *{label}!*\n\n"
            f"{summary}{brands}\n\n"
            f"_JewelClaw Industry Intelligence_"
        )


# Singleton
industry_news_service = IndustryNewsService()
