"""
Background Agent Service - JewelClaw's brain that works while users sleep.

Features:
1. Price threshold alerts - instant WhatsApp when gold crosses buy/sell targets
2. Market intelligence - overnight news scraping + Claude summary
3. Inventory portfolio tracker - track metal holdings value changes
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

import httpx
import anthropic
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc

from app.config import settings
from app.models import User, MetalRate, BusinessMemory

logger = logging.getLogger(__name__)

# News sources for market intelligence
NEWS_SOURCES = [
    {
        "url": "https://www.google.com/search?q=gold+price+india+today&tbm=nws&tbs=qdr:d",
        "name": "Google News - Gold India",
    },
    {
        "url": "https://newsapi.org/v2/everything?q=(gold+OR+silver+OR+jewellery)+AND+(india+OR+RBI+OR+import+duty+OR+rupee)&language=en&sortBy=publishedAt&pageSize=10",
        "name": "NewsAPI",
        "needs_key": True,
    },
]

# Free RSS/API sources that don't need keys
FREE_NEWS_URLS = [
    "https://economictimes.indiatimes.com/commoditysummary/symbol-GOLD.cms",
    "https://www.livemint.com/market/commodities",
    "https://www.moneycontrol.com/commodity/gold-price.html",
]


@dataclass
class PriceAlert:
    """A triggered price alert."""
    user_id: int
    phone_number: str
    user_name: str
    alert_type: str  # "buy" or "sell"
    threshold: float
    current_price: float
    difference: float


@dataclass
class PortfolioHolding:
    """A user's metal holding."""
    metal: str  # "gold", "silver", "platinum"
    weight_grams: float
    karat: str  # "24k", "22k", "18k" for gold, "pure" for silver/platinum
    current_value: float
    yesterday_value: float
    change: float
    change_pct: float


class BackgroundAgentService:
    """Background intelligence that runs without user interaction."""

    def __init__(self):
        self._client = None
        # Track last alert sent to avoid spam (user_id -> last_alert_time)
        self._last_alerts: Dict[int, datetime] = {}
        # Minimum gap between alerts for same user (1 hour)
        self.ALERT_COOLDOWN_MINUTES = 60

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        return self._client

    # =========================================================================
    # FEATURE 1: PRICE THRESHOLD ALERTS
    # =========================================================================

    async def check_price_alerts(self, db: AsyncSession, gold_24k: float, silver: float = 0):
        """
        Called after every rate scrape (every 15 min).
        Checks all users' buy/sell thresholds and sends WhatsApp alerts.
        """
        if not gold_24k or gold_24k <= 0:
            return

        logger.info(f"Checking price alerts: Gold ₹{gold_24k:,.0f}, Silver ₹{silver:,.0f}")

        # Get all users with thresholds set
        result = await db.execute(
            select(User).where(
                (User.gold_buy_threshold.isnot(None)) |
                (User.gold_sell_threshold.isnot(None))
            )
        )
        users_with_thresholds = result.scalars().all()

        if not users_with_thresholds:
            return

        alerts_to_send: List[PriceAlert] = []

        for user in users_with_thresholds:
            # Check cooldown - don't spam same user
            if self._is_on_cooldown(user.id):
                continue

            # Check buy threshold (alert when price drops BELOW target)
            if user.gold_buy_threshold and gold_24k <= user.gold_buy_threshold:
                diff = gold_24k - user.gold_buy_threshold
                alerts_to_send.append(PriceAlert(
                    user_id=user.id,
                    phone_number=user.phone_number,
                    user_name=user.name or "Friend",
                    alert_type="buy",
                    threshold=user.gold_buy_threshold,
                    current_price=gold_24k,
                    difference=diff,
                ))

            # Check sell threshold (alert when price rises ABOVE target)
            if user.gold_sell_threshold and gold_24k >= user.gold_sell_threshold:
                diff = gold_24k - user.gold_sell_threshold
                alerts_to_send.append(PriceAlert(
                    user_id=user.id,
                    phone_number=user.phone_number,
                    user_name=user.name or "Friend",
                    alert_type="sell",
                    threshold=user.gold_sell_threshold,
                    current_price=gold_24k,
                    difference=diff,
                ))

        # Send alerts
        if alerts_to_send:
            from app.services.whatsapp_service import whatsapp_service
            sent = 0
            for alert in alerts_to_send:
                message = self._format_price_alert(alert)
                phone = f"whatsapp:{alert.phone_number}"
                success = await whatsapp_service.send_message(phone, message)
                if success:
                    sent += 1
                    self._last_alerts[alert.user_id] = datetime.now()
                    logger.info(f"PRICE ALERT sent to {alert.user_name} ({alert.phone_number}): {alert.alert_type} @ ₹{alert.current_price:,.0f}")

            logger.info(f"Price alerts: {sent}/{len(alerts_to_send)} sent")

    def _is_on_cooldown(self, user_id: int) -> bool:
        """Check if user received an alert recently."""
        last = self._last_alerts.get(user_id)
        if not last:
            return False
        return (datetime.now() - last).total_seconds() < self.ALERT_COOLDOWN_MINUTES * 60

    def _format_price_alert(self, alert: PriceAlert) -> str:
        """Format a price alert WhatsApp message."""
        if alert.alert_type == "buy":
            return (
                f"🚨 *GOLD BUY ALERT!*\n\n"
                f"Hey {alert.user_name}, gold just hit *₹{alert.current_price:,.0f}/gm*\n"
                f"That's *₹{abs(alert.difference):,.0f} below* your buy target of ₹{alert.threshold:,.0f}\n\n"
                f"💰 Good time to stock up!\n\n"
                f"_Set by your JewelClaw alert. Reply 'gold' for full rates._"
            )
        else:
            return (
                f"📈 *GOLD SELL ALERT!*\n\n"
                f"Hey {alert.user_name}, gold just hit *₹{alert.current_price:,.0f}/gm*\n"
                f"That's *₹{abs(alert.difference):,.0f} above* your sell target of ₹{alert.threshold:,.0f}\n\n"
                f"📊 Consider booking profits!\n\n"
                f"_Set by your JewelClaw alert. Reply 'gold' for full rates._"
            )

    # =========================================================================
    # FEATURE 2: OVERNIGHT MARKET INTELLIGENCE
    # =========================================================================

    async def gather_market_intelligence(self) -> str:
        """
        Scrape financial news and generate a market intelligence summary.
        Called at midnight by scheduler. Returns formatted summary for morning brief.
        """
        logger.info("Gathering overnight market intelligence...")

        raw_headlines = await self._scrape_news_headlines()

        if not raw_headlines:
            logger.warning("No news headlines scraped")
            return ""

        # Use Claude to filter and summarize jewelry-relevant news
        summary = await self._generate_intelligence_summary(raw_headlines)
        return summary

    async def _scrape_news_headlines(self) -> List[str]:
        """Scrape headlines from financial news sources."""
        headlines = []

        async with httpx.AsyncClient(timeout=15) as client:
            # Source 1: Google News RSS for gold/silver India
            try:
                resp = await client.get(
                    "https://news.google.com/rss/search?q=gold+silver+price+india+jewelry&hl=en-IN&gl=IN",
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if resp.status_code == 200:
                    # Parse RSS XML
                    import xml.etree.ElementTree as ET
                    root = ET.fromstring(resp.text)
                    for item in root.findall('.//item')[:15]:
                        title = item.find('title')
                        if title is not None and title.text:
                            headlines.append(title.text)
                    logger.info(f"Google News RSS: {len(headlines)} headlines")
            except Exception as e:
                logger.warning(f"Google News RSS failed: {e}")

            # Source 2: Economic Times commodities
            try:
                resp = await client.get(
                    "https://economictimes.indiatimes.com/commoditysummary/symbol-GOLD.cms",
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                )
                if resp.status_code == 200:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(resp.text, 'html.parser')
                    # Get news headlines from the page
                    for tag in soup.find_all(['h2', 'h3', 'h4'], limit=10):
                        text = tag.get_text(strip=True)
                        if text and len(text) > 20:
                            headlines.append(f"[ET] {text}")
                    logger.info(f"ET headlines scraped: {len(headlines)} total")
            except Exception as e:
                logger.warning(f"ET scrape failed: {e}")

            # Source 3: Moneycontrol gold page
            try:
                resp = await client.get(
                    "https://www.moneycontrol.com/commodity/gold-price.html",
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                )
                if resp.status_code == 200:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(resp.text, 'html.parser')
                    for tag in soup.find_all(['h2', 'h3'], limit=10):
                        text = tag.get_text(strip=True)
                        if text and len(text) > 20:
                            headlines.append(f"[MC] {text}")
            except Exception as e:
                logger.warning(f"Moneycontrol scrape failed: {e}")

        logger.info(f"Total headlines gathered: {len(headlines)}")
        return headlines[:30]  # Cap at 30

    async def _generate_intelligence_summary(self, headlines: List[str]) -> str:
        """Use Claude to filter and summarize jewelry-relevant news."""
        if not headlines:
            return ""

        headlines_text = "\n".join(f"- {h}" for h in headlines)

        try:
            response = self.client.messages.create(
                model=settings.classifier_model,  # Haiku for speed/cost
                max_tokens=300,
                messages=[{
                    "role": "user",
                    "content": f"""You are a market analyst for Indian jewelry businesses.
From these news headlines, pick the 3-5 most relevant ones for a jeweler (gold/silver prices, RBI policy, import duty, rupee movement, jewelry demand, wedding season, festival impact).

Headlines:
{headlines_text}

Write a brief 3-5 bullet summary in this format (WhatsApp markdown):
• *Headline* - one line explanation
Keep it under 500 characters total. Skip irrelevant headlines. If nothing relevant, say "Markets quiet overnight."
Only return the bullet points, nothing else.""",
                }],
            )
            summary = response.content[0].text.strip()
            logger.info(f"Intelligence summary generated: {len(summary)} chars")
            return summary
        except Exception as e:
            logger.error(f"Intelligence summary failed: {e}")
            return ""

    # =========================================================================
    # FEATURE 3: INVENTORY PORTFOLIO TRACKER
    # =========================================================================

    async def store_inventory(
        self, db: AsyncSession, user_id: int,
        metal: str, weight_grams: float, karat: str = "24k"
    ) -> Dict[str, Any]:
        """Store or update a user's metal holding."""
        from app.services.business_memory_service import business_memory_service

        key = f"inventory_{metal}_{karat}".lower()
        value = f"{weight_grams}g {karat} {metal}"

        await business_memory_service.store_fact(
            db=db,
            user_id=user_id,
            category="inventory",
            key=key,
            value=value,
            value_numeric=weight_grams,
            metal_type=metal,
        )

        return {
            "stored": True,
            "metal": metal,
            "weight_grams": weight_grams,
            "karat": karat,
            "key": key,
        }

    async def get_portfolio_summary(
        self, db: AsyncSession, user_id: int
    ) -> Dict[str, Any]:
        """Calculate current portfolio value from stored inventory + live rates."""
        from app.services.business_memory_service import business_memory_service

        # Get inventory facts
        memories = await business_memory_service.get_user_memory(
            db, user_id, category="inventory"
        )

        if not memories:
            return {"error": "No inventory stored. Tell me what you hold, e.g. 'I have 500g 22K gold and 2kg silver'"}

        # Get latest rates
        result = await db.execute(
            select(MetalRate)
            .where(MetalRate.city == "Mumbai")
            .order_by(desc(MetalRate.recorded_at))
            .limit(1)
        )
        rate = result.scalar_one_or_none()
        if not rate:
            return {"error": "No rates available to calculate portfolio value."}

        # Get yesterday's rate for change calculation
        yesterday = datetime.now() - timedelta(hours=24)
        result_yday = await db.execute(
            select(MetalRate)
            .where(MetalRate.city == "Mumbai")
            .where(MetalRate.recorded_at <= yesterday)
            .order_by(desc(MetalRate.recorded_at))
            .limit(1)
        )
        rate_yday = result_yday.scalar_one_or_none()

        # Rate lookup for karat prices
        karat_rates = {
            "24k": rate.gold_24k,
            "22k": rate.gold_22k,
            "18k": rate.gold_18k or (rate.gold_24k * 0.75),
            "14k": rate.gold_14k or (rate.gold_24k * 0.585),
        }
        karat_rates_yday = {}
        if rate_yday:
            karat_rates_yday = {
                "24k": rate_yday.gold_24k,
                "22k": rate_yday.gold_22k,
                "18k": rate_yday.gold_18k or (rate_yday.gold_24k * 0.75),
                "14k": rate_yday.gold_14k or (rate_yday.gold_24k * 0.585),
            }

        holdings: List[Dict] = []
        total_value = 0
        total_yesterday = 0

        for mem in memories:
            if not mem.value_numeric or mem.value_numeric <= 0:
                continue

            weight = mem.value_numeric
            metal = mem.metal_type or "gold"
            # Extract karat from key (inventory_gold_22k -> 22k)
            karat = "24k"
            key_parts = mem.key.split("_")
            for part in key_parts:
                if part in karat_rates:
                    karat = part
                    break

            # Get rate per gram
            if metal == "gold":
                rate_per_gram = karat_rates.get(karat, rate.gold_24k)
                rate_yday_per_gram = karat_rates_yday.get(karat, rate_per_gram) if karat_rates_yday else rate_per_gram
            elif metal == "silver":
                rate_per_gram = rate.silver or 0
                rate_yday_per_gram = rate_yday.silver if rate_yday and rate_yday.silver else rate_per_gram
            elif metal == "platinum":
                rate_per_gram = rate.platinum or 0
                rate_yday_per_gram = rate_yday.platinum if rate_yday and rate_yday.platinum else rate_per_gram
            else:
                continue

            if rate_per_gram <= 0:
                continue

            current_val = weight * rate_per_gram
            yesterday_val = weight * rate_yday_per_gram
            change = current_val - yesterday_val
            change_pct = (change / yesterday_val * 100) if yesterday_val > 0 else 0

            holdings.append({
                "metal": metal,
                "karat": karat,
                "weight_grams": weight,
                "rate_per_gram": rate_per_gram,
                "current_value": round(current_val),
                "yesterday_value": round(yesterday_val),
                "change": round(change),
                "change_pct": round(change_pct, 2),
            })

            total_value += current_val
            total_yesterday += yesterday_val

        total_change = total_value - total_yesterday
        total_change_pct = (total_change / total_yesterday * 100) if total_yesterday > 0 else 0

        return {
            "holdings": holdings,
            "total_value": round(total_value),
            "total_yesterday": round(total_yesterday),
            "total_change": round(total_change),
            "total_change_pct": round(total_change_pct, 2),
            "rate_date": rate.rate_date or "Today",
        }

    def format_portfolio_message(self, portfolio: Dict) -> str:
        """Format portfolio summary for WhatsApp."""
        if "error" in portfolio:
            return portfolio["error"]

        lines = [
            "📦 *YOUR INVENTORY PORTFOLIO*",
            f"📅 {portfolio['rate_date']}",
            "",
        ]

        for h in portfolio["holdings"]:
            weight = h["weight_grams"]
            # Format weight nicely
            if weight >= 1000:
                weight_str = f"{weight/1000:.1f}kg"
            else:
                weight_str = f"{weight:.0f}g"

            metal_emoji = {"gold": "🥇", "silver": "🥈", "platinum": "⚪"}.get(h["metal"], "💎")
            karat_str = f" {h['karat'].upper()}" if h["metal"] == "gold" else ""

            change = h["change"]
            if change > 0:
                change_str = f"↑₹{abs(change):,.0f} (+{abs(h['change_pct']):.1f}%)"
            elif change < 0:
                change_str = f"↓₹{abs(change):,.0f} (-{abs(h['change_pct']):.1f}%)"
            else:
                change_str = "→ No change"

            lines.append(f"{metal_emoji} *{h['metal'].title()}{karat_str}* — {weight_str}")
            lines.append(f"   ₹{h['current_value']:,.0f} | {change_str}")
            lines.append("")

        # Total
        total_change = portfolio["total_change"]
        if total_change > 0:
            total_symbol = "📈"
            total_str = f"+₹{abs(total_change):,.0f} (+{abs(portfolio['total_change_pct']):.1f}%)"
        elif total_change < 0:
            total_symbol = "📉"
            total_str = f"-₹{abs(total_change):,.0f} (-{abs(portfolio['total_change_pct']):.1f}%)"
        else:
            total_symbol = "➡️"
            total_str = "No change"

        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"{total_symbol} *TOTAL: ₹{portfolio['total_value']:,.0f}*")
        lines.append(f"   Today: {total_str}")
        lines.append("")
        lines.append("_Update: 'I have 500g 22K gold'_")
        lines.append("_Clear: 'clear inventory'_")

        return "\n".join(lines)

    async def generate_weekly_portfolio_report(self, db: AsyncSession) -> int:
        """
        Send weekly portfolio summary to all users with inventory.
        Called by scheduler on Sunday 10 AM.
        Returns count of messages sent.
        """
        from app.services.whatsapp_service import whatsapp_service

        # Get users with inventory
        result = await db.execute(
            select(BusinessMemory.user_id).where(
                and_(
                    BusinessMemory.category == "inventory",
                    BusinessMemory.is_active == True,
                )
            ).distinct()
        )
        user_ids = [row[0] for row in result.fetchall()]

        if not user_ids:
            return 0

        # Get weekly rate change
        now_result = await db.execute(
            select(MetalRate).where(MetalRate.city == "Mumbai")
            .order_by(desc(MetalRate.recorded_at)).limit(1)
        )
        current_rate = now_result.scalar_one_or_none()

        week_ago = datetime.now() - timedelta(days=7)
        week_result = await db.execute(
            select(MetalRate).where(MetalRate.city == "Mumbai")
            .where(MetalRate.recorded_at <= week_ago)
            .order_by(desc(MetalRate.recorded_at)).limit(1)
        )
        week_rate = week_result.scalar_one_or_none()

        sent_count = 0
        for user_id in user_ids:
            try:
                # Get user
                user_result = await db.execute(select(User).where(User.id == user_id))
                user = user_result.scalar_one_or_none()
                if not user:
                    continue

                portfolio = await self.get_portfolio_summary(db, user_id)
                if "error" in portfolio:
                    continue

                # Build weekly message
                name = user.name or "Friend"
                message = f"📊 *Weekly Portfolio Report*\nHi {name}!\n\n"
                message += self.format_portfolio_message(portfolio)

                # Add weekly market context
                if current_rate and week_rate:
                    gold_week_change = current_rate.gold_24k - week_rate.gold_24k
                    gold_week_pct = (gold_week_change / week_rate.gold_24k) * 100
                    if gold_week_change > 0:
                        message += f"\n\n📈 Gold this week: +₹{abs(gold_week_change):,.0f} (+{abs(gold_week_pct):.1f}%)"
                    else:
                        message += f"\n\n📉 Gold this week: -₹{abs(gold_week_change):,.0f} (-{abs(gold_week_pct):.1f}%)"

                phone = f"whatsapp:{user.phone_number}"
                sent = await whatsapp_service.send_message(phone, message)
                if sent:
                    sent_count += 1

            except Exception as e:
                logger.error(f"Weekly portfolio error for user {user_id}: {e}")

        logger.info(f"Weekly portfolio reports: {sent_count}/{len(user_ids)} sent")
        return sent_count

    # =========================================================================
    # INVENTORY PARSING (natural language)
    # =========================================================================

    def parse_inventory_input(self, message: str) -> List[Dict[str, Any]]:
        """
        Parse natural language inventory input.
        Examples:
            "I have 500g 22K gold" -> [{metal: gold, weight: 500, karat: 22k}]
            "200g gold and 5kg silver" -> [{...}, {...}]
            "I hold 1kg 24K gold, 10kg silver, 50g platinum"
        """
        items = []
        text = message.lower()

        # Pattern: weight + optional karat + metal
        # Matches: "500g 22k gold", "5kg silver", "50g platinum", "200 grams 18k gold"
        pattern = r'(\d+(?:\.\d+)?)\s*(?:gm|gms|gram|grams|g|kg)\s*(?:of\s+)?(?:(\d+k)\s+)?(gold|silver|platinum|sona|chandi)'

        for match in re.finditer(pattern, text):
            weight = float(match.group(1))
            karat = match.group(2) or "24k"
            metal = match.group(3)

            # Convert kg to grams
            weight_text = text[match.start():match.end()]
            if "kg" in weight_text:
                weight *= 1000

            # Normalize metal names
            if metal in ("sona",):
                metal = "gold"
            elif metal in ("chandi",):
                metal = "silver"

            # Default karat for non-gold
            if metal != "gold":
                karat = "pure"

            items.append({
                "metal": metal,
                "weight_grams": weight,
                "karat": karat,
            })

        return items


# Singleton
background_agent = BackgroundAgentService()
