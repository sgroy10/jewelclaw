"""
Trend Intelligence Service — Google Trends + data aggregation for jewelry market intelligence.

Uses pytrends (free, no API key) to get real search interest data from Google India.
Combines with brand sitemap data, price benchmarks, and user engagement to generate
weekly intelligence reports.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc

logger = logging.getLogger(__name__)

# Jewelry category → representative Google search terms (India)
JEWELRY_TOPICS = {
    "bridal": ["bridal jewelry india", "wedding jewelry", "kundan set", "bridal necklace gold"],
    "temple": ["temple jewelry", "south indian jewelry", "kemp jewelry", "antique gold jewelry"],
    "dailywear": ["lightweight gold jewelry", "office wear jewelry", "daily wear earrings gold"],
    "mens": ["mens gold chain", "mens gold ring", "mens bracelet gold", "gold kada"],
    "contemporary": ["minimalist jewelry gold", "modern gold jewelry", "geometric jewelry"],
    "necklaces": ["gold necklace design", "choker necklace gold", "mangalsutra design"],
    "earrings": ["gold earring design", "jhumka gold", "gold stud earrings"],
    "rings": ["gold ring design", "engagement ring gold india", "diamond ring"],
    "bangles": ["gold bangle design", "gold kada design", "daily wear bangles"],
}

# Representative keyword per category for comparison
CATEGORY_KEYWORDS = {
    "bridal": "bridal jewelry india",
    "temple": "temple jewelry",
    "dailywear": "lightweight gold jewelry",
    "mens": "mens gold chain",
    "contemporary": "minimalist jewelry gold",
    "necklaces": "gold necklace design",
    "earrings": "gold earring design",
    "rings": "gold ring design",
    "bangles": "gold bangle design",
}


class TrendIntelligenceService:
    """Get real trend data from Google Trends for jewelry categories."""

    async def get_category_trends(self, timeframe: str = "today 1-m", geo: str = "IN") -> Dict[str, dict]:
        """
        Get Google Trends interest for each jewelry category in India.
        Returns: {"bridal": {"interest": 78, "change": +35}, "temple": {"interest": 45, "change": +18}, ...}
        """
        try:
            from pytrends.request import TrendReq

            pytrends = TrendReq(hl='en-IN', tz=330)  # IST = UTC+5:30

            results = {}

            # Compare categories in batches of 5 (pytrends limit)
            categories = list(CATEGORY_KEYWORDS.items())

            for i in range(0, len(categories), 5):
                batch = categories[i:i+5]
                keywords = [kw for _, kw in batch]
                cat_names = [name for name, _ in batch]

                try:
                    pytrends.build_payload(keywords, timeframe=timeframe, geo=geo)
                    interest = pytrends.interest_over_time()

                    if interest.empty:
                        for name in cat_names:
                            results[name] = {"interest": 0, "change": 0}
                        continue

                    for name, kw in batch:
                        if kw in interest.columns:
                            values = interest[kw].values
                            # Current week avg vs previous week avg
                            if len(values) >= 14:
                                current_week = values[-7:].mean()
                                prev_week = values[-14:-7].mean()
                            elif len(values) >= 2:
                                mid = len(values) // 2
                                current_week = values[mid:].mean()
                                prev_week = values[:mid].mean()
                            else:
                                current_week = values[-1] if len(values) > 0 else 0
                                prev_week = current_week

                            # Calculate % change
                            if prev_week > 0:
                                change = round(((current_week - prev_week) / prev_week) * 100)
                            else:
                                change = 0

                            results[name] = {
                                "interest": round(float(current_week)),
                                "change": change,
                            }
                        else:
                            results[name] = {"interest": 0, "change": 0}

                except Exception as e:
                    logger.warning(f"pytrends batch error: {e}")
                    for name in cat_names:
                        results[name] = {"interest": 0, "change": 0}

            return results

        except ImportError:
            logger.warning("pytrends not installed, returning empty trends")
            return {}
        except Exception as e:
            logger.error(f"Google Trends error: {e}")
            return {}

    async def get_rising_queries(self, geo: str = "IN") -> List[str]:
        """Get trending jewelry-related search queries in India."""
        try:
            from pytrends.request import TrendReq

            pytrends = TrendReq(hl='en-IN', tz=330)

            # Get related queries for "gold jewelry"
            pytrends.build_payload(["gold jewelry"], timeframe="today 1-m", geo=geo)
            related = pytrends.related_queries()

            rising = []
            if "gold jewelry" in related and related["gold jewelry"]["rising"] is not None:
                df = related["gold jewelry"]["rising"]
                rising = df["query"].tolist()[:10]

            return rising

        except Exception as e:
            logger.warning(f"Rising queries error: {e}")
            return []

    async def generate_trend_report(self, db: AsyncSession) -> str:
        """
        Generate weekly intelligence report combining:
        1. Google Trends data (category % changes)
        2. Brand sitemap new products
        3. Price benchmarks (Amazon + BlueStone)
        4. Industry news highlights
        5. User engagement data
        """
        parts = []

        # --- 1. Google Trends ---
        trends = await self.get_category_trends()
        if trends:
            rising = [(k, v) for k, v in trends.items() if v["change"] > 5]
            cooling = [(k, v) for k, v in trends.items() if v["change"] < -5]

            rising.sort(key=lambda x: -x[1]["change"])
            cooling.sort(key=lambda x: x[1]["change"])

            week_str = datetime.now().strftime("%d %b")
            parts.append(f"*Trend Scout — Week of {week_str}*")

            if rising:
                parts.append("\n*Rising:*")
                for name, data in rising[:5]:
                    parts.append(f"  {name.title()} ↑{data['change']}% searches")

            if cooling:
                parts.append("\n*Cooling:*")
                for name, data in cooling[:3]:
                    parts.append(f"  {name.title()} ↓{abs(data['change'])}%")
        else:
            parts.append("*Trend Scout — Weekly Report*")
            parts.append("_Google Trends data unavailable this week_")

        # --- 2. Brand Activity ---
        try:
            from app.services.brand_monitor_service import brand_monitor
            brand_summary = await brand_monitor.get_brand_activity_summary(db)
            if brand_summary:
                parts.append(f"\n*Brand Activity:*\n{brand_summary}")
        except Exception:
            pass

        # --- 3. Price Benchmarks ---
        try:
            from app.services.editorial_scraper import editorial_scraper
            for cat in ["necklaces", "earrings", "rings"]:
                benchmarks = await editorial_scraper.get_price_benchmarks(cat)
                if benchmarks and benchmarks.get("count", 0) >= 3:
                    avg = benchmarks["avg_price"]
                    if avg >= 100000:
                        price_str = f"₹{avg/100000:.1f}L"
                    else:
                        price_str = f"₹{avg:,.0f}"
                    parts.append(f"  Amazon {cat.title()} avg: {price_str}")
        except Exception:
            pass

        # --- 4. Industry News ---
        try:
            from app.services.industry_news_service import industry_news_service
            recent = await industry_news_service.get_for_morning_brief(db)
            if recent:
                parts.append("\n*Industry Highlights:*")
                for item in recent[:3]:
                    if item.summary:
                        parts.append(f"  {item.summary[:80]}")
        except Exception:
            pass

        # --- 5. Hot Category ---
        if trends:
            hottest = max(trends.items(), key=lambda x: x[1]["change"]) if trends else None
            if hottest and hottest[1]["change"] > 10:
                name, data = hottest
                parts.append(f"\n*Hot Category: {name.title()}*")
                parts.append(f"Searches up {data['change']}% this week.")
                parts.append(f"_Reply '{name}' for deep dive._")

        # --- 6. Rising Queries ---
        queries = await self.get_rising_queries()
        if queries:
            parts.append(f"\n*Trending Searches:* {', '.join(queries[:5])}")

        parts.append(f"\n_Updated weekly. Reply 'help' for commands._")

        report = "\n".join(parts)

        # Cache in TrendReport table
        try:
            from app.models import TrendReport

            # Build data for DB
            top_categories = []
            if trends:
                for name, data in sorted(trends.items(), key=lambda x: -x[1]["change"]):
                    top_categories.append({
                        "category": name,
                        "interest": data["interest"],
                        "change": data["change"],
                    })

            db_report = TrendReport(
                report_type="weekly",
                report_date=datetime.now(),
                top_categories=top_categories,
                new_arrivals_count=0,
            )
            db.add(db_report)
        except Exception as e:
            logger.warning(f"Failed to cache trend report: {e}")

        return report

    async def get_cached_report(self, db: AsyncSession) -> Optional[str]:
        """Get the most recent cached weekly report (generated on Monday)."""
        try:
            from app.models import TrendReport

            one_week_ago = datetime.now() - timedelta(days=7)
            result = await db.execute(
                select(TrendReport)
                .where(TrendReport.report_type == "weekly")
                .where(TrendReport.report_date >= one_week_ago)
                .order_by(desc(TrendReport.report_date))
                .limit(1)
            )
            report = result.scalar_one_or_none()

            if report and report.top_categories:
                return await self._format_cached_report(report)
            return None
        except Exception:
            return None

    async def _format_cached_report(self, report) -> str:
        """Format a cached TrendReport into WhatsApp text."""
        parts = []
        week_str = report.report_date.strftime("%d %b")
        parts.append(f"*Trend Scout — Week of {week_str}*")

        categories = report.top_categories or []
        rising = [c for c in categories if c.get("change", 0) > 5]
        cooling = [c for c in categories if c.get("change", 0) < -5]

        if rising:
            parts.append("\n*Rising:*")
            for c in rising[:5]:
                parts.append(f"  {c['category'].title()} ↑{c['change']}% searches")

        if cooling:
            parts.append("\n*Cooling:*")
            for c in cooling[:3]:
                parts.append(f"  {c['category'].title()} ↓{abs(c['change'])}%")

        if not rising and not cooling:
            parts.append("\n_Market stable this week — no major shifts._")

        parts.append(f"\n_Reply 'fresh' for today's intel._")
        return "\n".join(parts)

    async def get_category_deep_dive(self, db: AsyncSession, category: str) -> str:
        """Generate a category-specific intelligence report."""
        parts = []
        parts.append(f"*{category.title()} Intelligence*")

        # Google Trends for this category
        trends = await self.get_category_trends()
        if trends and category in trends:
            data = trends[category]
            trend_dir = f"↑{data['change']}%" if data["change"] > 0 else f"↓{abs(data['change'])}%"
            parts.append(f"\n*Search Trend:* {trend_dir} this month")

        # Price benchmarks
        try:
            from app.services.editorial_scraper import editorial_scraper
            # Map categories to Amazon categories
            amazon_cat_map = {
                "bridal": "necklaces",
                "temple": "necklaces",
                "dailywear": "earrings",
                "mens": "rings",
                "contemporary": "earrings",
            }
            amazon_cat = amazon_cat_map.get(category, category)
            benchmarks = await editorial_scraper.get_price_benchmarks(amazon_cat)
            if benchmarks and benchmarks.get("count", 0) >= 3:
                avg = benchmarks["avg_price"]
                mn = benchmarks["min_price"]
                mx = benchmarks["max_price"]

                def _fmt(v):
                    if v >= 100000:
                        return f"₹{v/100000:.1f}L"
                    return f"₹{v:,.0f}"

                parts.append(f"*Price Range:* {_fmt(mn)} - {_fmt(mx)} (avg {_fmt(avg)})")
        except Exception:
            pass

        # Brand activity for this category
        try:
            from app.services.brand_monitor_service import brand_monitor
            brand_summary = await brand_monitor.get_brand_activity_summary(db)
            if brand_summary:
                parts.append(f"\n*Brand Moves:*\n{brand_summary}")
        except Exception:
            pass

        # Designs from BlueStone (actual images)
        try:
            from app.models import Design
            result = await db.execute(
                select(Design)
                .where(Design.source == "bluestone")
                .where(Design.category == category)
                .where(Design.image_url.isnot(None))
                .order_by(desc(Design.scraped_at))
                .limit(3)
            )
            designs = result.scalars().all()
            if designs:
                parts.append(f"\n*Latest BlueStone {category.title()}:*")
                for d in designs:
                    price_str = f" — ₹{d.price_range_min:,.0f}" if d.price_range_min else ""
                    parts.append(f"  {d.title[:50]}{price_str}")
        except Exception:
            pass

        parts.append(f"\n_Reply 'trends' for full market report_")
        return "\n".join(parts)


# Singleton
trend_intelligence = TrendIntelligenceService()
