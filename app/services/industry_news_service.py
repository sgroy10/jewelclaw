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


# RSS feeds for jewelry industry news — broad coverage, not brand-specific
JEWELRY_RSS_FEEDS = [
    {
        "url": "https://news.google.com/rss/search?q=%22gold+price%22+OR+%22gold+rate%22+india+today&hl=en-IN&gl=IN&ceid=IN:en",
        "name": "Gold Price India",
        "source": "google_news_gold",
    },
    {
        "url": "https://news.google.com/rss/search?q=%22jewellery+industry%22+OR+%22jewelry+market%22+OR+%22gems+and+jewellery%22+india&hl=en-IN&gl=IN&ceid=IN:en",
        "name": "Jewelry Industry India",
        "source": "google_news_industry",
    },
    {
        "url": "https://news.google.com/rss/search?q=gold+import+duty+OR+hallmark+OR+GJEPC+OR+%22BIS+hallmark%22+OR+%22gold+ETF%22+india&hl=en-IN&gl=IN&ceid=IN:en",
        "name": "Gold Policy & Regulation",
        "source": "google_news_regulation",
    },
    {
        "url": "https://news.google.com/rss/search?q=%22diamond+industry%22+OR+%22lab+grown+diamond%22+OR+%22Surat+diamond%22+OR+%22polished+diamond%22&hl=en-IN&gl=IN&ceid=IN:en",
        "name": "Diamond Industry",
        "source": "google_news_diamond",
    },
    {
        "url": "https://news.google.com/rss/search?q=%22jewelry+trend%22+OR+%22jewellery+design%22+OR+%22bridal+jewellery%22+OR+%22wedding+jewelry%22&hl=en&gl=US&ceid=US:en",
        "name": "Global Trends",
        "source": "google_news_trends",
    },
    {
        "url": "https://news.google.com/rss/search?q=silver+price+OR+platinum+price+OR+%22precious+metals%22+market&hl=en&gl=US&ceid=US:en",
        "name": "Precious Metals",
        "source": "google_news_metals",
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

    def _parse_pub_date(self, date_str: str) -> Optional[datetime]:
        """Parse RSS pubDate string to datetime."""
        from email.utils import parsedate_to_datetime
        try:
            return parsedate_to_datetime(date_str)
        except Exception:
            return None

    def _normalize_headline(self, headline: str) -> str:
        """Normalize headline for fuzzy dedup — strip filler words and lowercase."""
        import re
        text = headline.lower()
        # Remove source attribution like " - Economic Times" at the end
        text = re.sub(r'\s*[-–|]\s*[\w\s]+$', '', text)
        # Remove common filler words
        for word in ["inaugurates", "inaugurated", "inauguration", "opens", "opened",
                      "launches", "launched", "unveils", "unveiled", "announces",
                      "announced", "new", "the", "a", "an", "in", "at", "for", "of", "to"]:
            text = re.sub(rf'\b{word}\b', '', text)
        # Collapse whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    async def scrape_all_feeds(self, db: AsyncSession) -> List[Dict]:
        """Scrape all RSS feeds and return new (non-duplicate) headlines."""
        from app.models import IndustryNews

        all_headlines = []
        age_cutoff = datetime.utcnow() - timedelta(hours=48)

        async with httpx.AsyncClient(timeout=15) as client:
            for feed in JEWELRY_RSS_FEEDS:
                try:
                    resp = await client.get(
                        feed["url"],
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                    )
                    if resp.status_code != 200:
                        logger.warning(f"RSS feed {feed['name']} returned {resp.status_code}")
                        continue

                    root = ET.fromstring(resp.text)
                    items = root.findall('.//item')

                    for item in items[:10]:  # Max 10 per feed
                        title_el = item.find('title')
                        link_el = item.find('link')
                        pub_el = item.find('pubDate')

                        if title_el is None or not title_el.text:
                            continue

                        # Skip articles older than 48 hours by publish date
                        if pub_el is not None and pub_el.text:
                            pub_date = self._parse_pub_date(pub_el.text)
                            if pub_date and pub_date.replace(tzinfo=None) < age_cutoff:
                                continue

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

        # Deduplicate against DB (last 7 days for wider dedup window)
        db_cutoff = datetime.utcnow() - timedelta(days=7)
        result = await db.execute(
            select(IndustryNews.headline)
            .where(IndustryNews.scraped_at >= db_cutoff)
        )
        existing_headlines = {row[0].lower() for row in result.fetchall()}
        existing_normalized = {self._normalize_headline(h) for h in existing_headlines}

        new_headlines = []
        seen_normalized = set()
        for h in all_headlines:
            lower = h["headline"].lower()
            normalized = self._normalize_headline(h["headline"])

            # Skip exact match
            if lower in existing_headlines:
                continue
            # Skip fuzzy match (same story, different wording)
            if normalized in existing_normalized or normalized in seen_normalized:
                continue
            # Skip very short headlines (usually junk)
            if len(normalized) < 10:
                continue

            new_headlines.append(h)
            seen_normalized.add(normalized)

        logger.info(f"Industry news: {len(all_headlines)} scraped, {len(new_headlines)} new after dedup")
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
        """Get recent news items for the 'news' command. Prioritizes fresh, high-priority items."""
        from app.models import IndustryNews

        # Show last 48 hours, prioritize high/medium, newest first
        cutoff = datetime.utcnow() - timedelta(hours=48)
        result = await db.execute(
            select(IndustryNews)
            .where(IndustryNews.scraped_at >= cutoff)
            .order_by(
                desc(IndustryNews.priority == "high"),
                desc(IndustryNews.priority == "medium"),
                desc(IndustryNews.scraped_at),
            )
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
