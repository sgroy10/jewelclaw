"""
Metal rate service - scrapes actual Indian retail rates from GoodReturns.in
Supports Gold (all karats), Silver, and Platinum with smart market analysis.
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
import httpx
import pytz
from bs4 import BeautifulSoup
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.models import MetalRate
import anthropic
from app.config import settings

# Indian Standard Time
IST = pytz.timezone('Asia/Kolkata')

logger = logging.getLogger(__name__)

# URLs (updated - old city-specific URLs now 404)
GOLD_URL = "https://www.goodreturns.in/gold-rates/"
SILVER_URL = "https://www.goodreturns.in/silver-rates/"
PLATINUM_URL = "https://www.goodreturns.in/platinum-rates/"
MCX_URL = "https://www.goodreturns.in/mcx-bullion.html"
FOREX_API_URL = "https://api.exchangerate-api.com/v4/latest/USD"
GOLD_API_URL = "https://api.gold-api.com/price/XAU"
SILVER_API_URL = "https://api.gold-api.com/price/XAG"
PLATINUM_API_URL = "https://api.gold-api.com/price/XPT"

# Gold purity percentages
GOLD_PURITY = {
    "24k": 0.999,
    "22k": 0.916,
    "18k": 0.750,
    "14k": 0.585,
    "10k": 0.417,
    "9k": 0.375,
}

# Headers for scraping
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Troy ounce to gram conversion
TROY_OZ_TO_GRAM = 31.1035


@dataclass
class MetalRateData:
    """Complete metal rate data."""
    city: str
    rate_date: Optional[str] = None
    recorded_at: datetime = field(default_factory=datetime.now)

    # Gold rates (INR per gram)
    gold_24k: float = 0.0
    gold_22k: float = 0.0
    gold_18k: float = 0.0
    gold_14k: float = 0.0
    gold_10k: float = 0.0
    gold_9k: float = 0.0

    # Other metals
    silver: float = 0.0
    platinum: float = 0.0

    # International prices (USD per oz)
    gold_usd_oz: Optional[float] = None
    silver_usd_oz: Optional[float] = None
    platinum_usd_oz: Optional[float] = None

    # Exchange rate
    usd_inr: Optional[float] = None

    # MCX Futures (per 10gm for gold, per kg for silver)
    mcx_gold_futures: Optional[float] = None
    mcx_gold_futures_expiry: Optional[str] = None
    mcx_silver_futures: Optional[float] = None
    mcx_silver_futures_expiry: Optional[str] = None

    # Yesterday's rates for comparison
    yesterday_24k: Optional[float] = None
    yesterday_22k: Optional[float] = None
    yesterday_silver: Optional[float] = None

    source: str = "goodreturns.in"


@dataclass
class MarketAnalysis:
    """Smart market analysis."""
    # Direction
    direction: str = "stable"  # "rising", "falling", "stable"
    direction_symbol: str = "→"  # ↑, ↓, →
    consecutive_days: int = 0

    # Volatility
    volatility: str = "low"  # "low", "medium", "high"

    # Recommendation
    recommendation: str = "hold"  # "buy", "wait", "hold"
    recommendation_text: str = ""

    # Changes
    daily_change: float = 0.0
    daily_change_percent: float = 0.0
    weekly_change: float = 0.0
    weekly_change_percent: float = 0.0
    monthly_change: float = 0.0
    monthly_change_percent: float = 0.0

    # Expert view
    expert_summary: str = ""


class MetalService:
    """Service for fetching and analyzing metal rates."""

    def __init__(self):
        self.timeout = 30.0
        self.claude_client = None
        if settings.anthropic_api_key:
            self.claude_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        # Cache for expert analysis (1 hour TTL)
        self._expert_cache = {
            "analysis": None,
            "cached_at": None,
            "cache_ttl": 3600  # 1 hour in seconds
        }

    def _is_cache_valid(self) -> bool:
        """Check if expert analysis cache is still valid."""
        if not self._expert_cache["cached_at"]:
            return False
        elapsed = (datetime.now(IST) - self._expert_cache["cached_at"]).total_seconds()
        return elapsed < self._expert_cache["cache_ttl"]

    async def get_cached_expert_analysis(self, rates, analysis) -> str:
        """Get expert analysis from cache or generate new one."""
        if self._is_cache_valid() and self._expert_cache["analysis"]:
            logger.info("Using CACHED expert analysis (saves API cost)")
            return self._expert_cache["analysis"]

        # Generate new analysis
        logger.info("Generating NEW expert analysis via Claude API")
        new_analysis = await self.generate_ai_expert_analysis(rates, analysis)

        # Cache it
        self._expert_cache["analysis"] = new_analysis
        self._expert_cache["cached_at"] = datetime.now(IST)
        logger.info(f"Cached expert analysis until {(datetime.now(IST) + timedelta(hours=1)).strftime('%I:%M %p IST')}")

        return new_analysis

    def _extract_rate(self, text: str) -> Optional[float]:
        """Extract numeric rate from text."""
        if not text:
            return None
        cleaned = re.sub(r'[^0-9]', '', text)
        if cleaned:
            try:
                return float(cleaned)
            except ValueError:
                return None
        return None

    def _extract_date(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract rate date from page."""
        # Try title
        title = soup.find('title')
        if title:
            match = re.search(
                r'(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})',
                title.get_text()
            )
            if match:
                return match.group(1)

        # Try headings
        for heading in soup.find_all(['h1', 'h2', 'h3']):
            match = re.search(
                r'(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})',
                heading.get_text()
            )
            if match:
                return match.group(1)

        return None

    def _calculate_all_karats(self, gold_24k: float) -> Dict[str, float]:
        """Calculate all gold karat prices from 24K base price."""
        return {
            "gold_24k": gold_24k,
            "gold_22k": round(gold_24k * GOLD_PURITY["22k"] / GOLD_PURITY["24k"], 0),
            "gold_18k": round(gold_24k * GOLD_PURITY["18k"] / GOLD_PURITY["24k"], 0),
            "gold_14k": round(gold_24k * GOLD_PURITY["14k"] / GOLD_PURITY["24k"], 0),
            "gold_10k": round(gold_24k * GOLD_PURITY["10k"] / GOLD_PURITY["24k"], 0),
            "gold_9k": round(gold_24k * GOLD_PURITY["9k"] / GOLD_PURITY["24k"], 0),
        }

    async def fetch_international_prices(self) -> Dict[str, Optional[float]]:
        """Fetch international gold/silver/platinum prices and USD/INR rate."""
        result = {
            "gold_usd_oz": None,
            "silver_usd_oz": None,
            "platinum_usd_oz": None,
            "usd_inr": None,
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                # Fetch gold price
                try:
                    r = await client.get(GOLD_API_URL)
                    if r.status_code == 200:
                        data = r.json()
                        result["gold_usd_oz"] = data.get("price")
                except:
                    pass

                # Fetch silver price
                try:
                    r = await client.get(SILVER_API_URL)
                    if r.status_code == 200:
                        data = r.json()
                        result["silver_usd_oz"] = data.get("price")
                except:
                    pass

                # Fetch platinum price
                try:
                    r = await client.get(PLATINUM_API_URL)
                    if r.status_code == 200:
                        data = r.json()
                        result["platinum_usd_oz"] = data.get("price")
                except:
                    pass

                # Fetch USD/INR
                try:
                    r = await client.get(FOREX_API_URL)
                    if r.status_code == 200:
                        data = r.json()
                        result["usd_inr"] = data.get("rates", {}).get("INR")
                except:
                    pass

        except Exception as e:
            logger.error(f"Error fetching international prices: {e}")

        return result

    async def scrape_gold_rates(self, city: str = "mumbai") -> Optional[MetalRateData]:
        """Scrape gold rates from GoodReturns.in main page."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                response = await client.get(GOLD_URL, headers=HEADERS)
                response.raise_for_status()

                soup = BeautifulSoup(response.text, "lxml")
                html_text = response.text

                # Check for Cloudflare
                title = soup.find('title')
                if title and 'cloudflare' in title.get_text().lower():
                    logger.error("Blocked by Cloudflare")
                    return None

                # Extract date from title
                rate_date = self._extract_date(soup)

                # Extract 22K price from stock-price span (e.g., "₹ 13,965 /gm")
                gold_22k = None
                gold_24k = None

                # Look for stock-price spans with gold rates
                for span in soup.find_all('span', class_='stock-price'):
                    text = span.get_text()
                    if '/gm' in text or '/g' in text:
                        rate = self._extract_rate(text)
                        if rate and rate > 5000:  # Gold is > 5000/gram
                            if not gold_22k:
                                gold_22k = rate
                                logger.info(f"Found 22K gold: ₹{gold_22k}")

                # If we found 22K, calculate 24K (22K is ~91.6% of 24K)
                if gold_22k:
                    gold_24k = round(gold_22k / 0.916)
                    logger.info(f"Calculated 24K gold: ₹{gold_24k}")

                # Fallback: Try to find from tables
                if not gold_24k:
                    tables = soup.find_all("table")
                    for table in tables[:5]:
                        rows = table.find_all("tr")
                        for row in rows:
                            cells = row.find_all(["td", "th"])
                            if len(cells) >= 2:
                                header = cells[0].get_text().lower()
                                if "24" in header or "24k" in header:
                                    rate = self._extract_rate(cells[1].get_text())
                                    if rate and rate > 5000:
                                        gold_24k = rate
                                elif "22" in header or "22k" in header:
                                    rate = self._extract_rate(cells[1].get_text())
                                    if rate and rate > 5000:
                                        gold_22k = rate

                if not gold_24k and not gold_22k:
                    logger.warning("Could not parse gold rates from page")
                    return None

                # Calculate all karats
                base_24k = gold_24k or round(gold_22k / 0.916)
                karats = self._calculate_all_karats(base_24k)
                if gold_22k:
                    karats["gold_22k"] = gold_22k

                # Estimate yesterday's rate (assume ~0.3% daily change for now)
                yesterday_24k = round(base_24k * 0.997)

                logger.info(f"SCRAPED: 24K=₹{karats['gold_24k']}, 22K=₹{karats['gold_22k']}")

                return MetalRateData(
                    city=city.title(),
                    rate_date=rate_date,
                    gold_24k=karats["gold_24k"],
                    gold_22k=karats["gold_22k"],
                    gold_18k=karats["gold_18k"],
                    gold_14k=karats["gold_14k"],
                    gold_10k=karats["gold_10k"],
                    gold_9k=karats["gold_9k"],
                    yesterday_24k=yesterday_24k,
                    yesterday_22k=round(yesterday_24k * 0.916),
                    source="goodreturns.in"
                )

        except Exception as e:
            logger.error(f"Error scraping gold for {city}: {e}")
            return None

    async def scrape_silver_rate(self, city: str = "mumbai") -> Optional[tuple]:
        """Scrape silver rate from GoodReturns main page."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                response = await client.get(SILVER_URL, headers=HEADERS)
                response.raise_for_status()

                soup = BeautifulSoup(response.text, "lxml")

                if soup.find('title') and 'cloudflare' in soup.find('title').get_text().lower():
                    return None

                silver_per_kg = None
                silver_per_gram = None

                # Look for silver price in stock-price spans (e.g., "₹ 2,75,000/kg")
                for span in soup.find_all('span', class_='stock-price'):
                    text = span.get_text()
                    if '/kg' in text.lower():
                        rate = self._extract_rate(text)
                        if rate and rate > 50000:  # Silver kg is > 50000
                            silver_per_kg = rate
                            silver_per_gram = round(rate / 1000)
                            logger.info(f"Found silver: ₹{silver_per_kg}/kg = ₹{silver_per_gram}/gram")
                            break

                # Fallback: Try tables
                if not silver_per_gram:
                    tables = soup.find_all("table")
                    for table in tables[:5]:
                        rows = table.find_all("tr")
                        for row in rows:
                            cells = row.find_all(["td", "th"])
                            if len(cells) >= 2:
                                header = cells[0].get_text().lower()
                                if "silver" in header or "1 kg" in header:
                                    rate = self._extract_rate(cells[1].get_text())
                                    if rate:
                                        if rate > 50000:  # Per kg
                                            silver_per_gram = round(rate / 1000)
                                        elif rate > 50 and rate < 1000:  # Per gram
                                            silver_per_gram = rate

                if silver_per_gram:
                    yesterday = round(silver_per_gram * 0.997)  # Estimate
                    return silver_per_gram, yesterday

                return None

        except Exception as e:
            logger.error(f"Error scraping silver: {e}")
            return None

    async def scrape_platinum_rate(self) -> Optional[float]:
        """Scrape platinum rate from GoodReturns."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                response = await client.get(PLATINUM_URL, headers=HEADERS)
                response.raise_for_status()

                soup = BeautifulSoup(response.text, "lxml")

                if soup.find('title') and 'cloudflare' in soup.find('title').get_text().lower():
                    return None

                tables = soup.find_all("table")
                if tables:
                    rows = tables[0].find_all("tr")
                    if len(rows) >= 2:
                        cells = rows[1].find_all("td")
                        if len(cells) >= 2:
                            return self._extract_rate(cells[1].get_text())

                return None

        except Exception as e:
            logger.error(f"Error scraping platinum: {e}")
            return None

    async def scrape_mcx_futures(self) -> Dict[str, Any]:
        """Scrape MCX Gold and Silver futures from GoodReturns."""
        result = {
            "gold_futures": None,
            "gold_expiry": None,
            "silver_futures": None,
            "silver_expiry": None,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                response = await client.get(MCX_URL, headers=HEADERS)
                if response.status_code != 200:
                    return result

                soup = BeautifulSoup(response.text, "lxml")

                # Look for MCX gold and silver data in tables
                tables = soup.find_all("table")
                for table in tables:
                    rows = table.find_all("tr")
                    for row in rows:
                        cells = row.find_all(["td", "th"])
                        if len(cells) >= 2:
                            header = cells[0].get_text(strip=True).lower()
                            if "gold" in header and not result["gold_futures"]:
                                rate = self._extract_rate(cells[1].get_text())
                                if rate and rate > 50000:  # MCX gold is typically > 50000
                                    result["gold_futures"] = rate
                                    # Try to extract expiry month
                                    expiry_match = re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)', header, re.I)
                                    if expiry_match:
                                        result["gold_expiry"] = expiry_match.group(1)
                            elif "silver" in header and not result["silver_futures"]:
                                rate = self._extract_rate(cells[1].get_text())
                                if rate and rate > 50000:  # MCX silver is typically > 50000
                                    result["silver_futures"] = rate
                                    expiry_match = re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)', header, re.I)
                                    if expiry_match:
                                        result["silver_expiry"] = expiry_match.group(1)

        except Exception as e:
            logger.error(f"Error scraping MCX: {e}")

        return result

    async def fetch_all_rates(self, city: str = "mumbai") -> Optional[MetalRateData]:
        """Fetch all metal rates including international prices."""
        # Scrape gold rates
        rates = await self.scrape_gold_rates(city)
        if not rates:
            return None

        # Scrape silver
        silver_result = await self.scrape_silver_rate(city)
        if silver_result:
            rates.silver, rates.yesterday_silver = silver_result

        # Fetch international prices first (needed for platinum calculation)
        intl = await self.fetch_international_prices()
        rates.gold_usd_oz = intl.get("gold_usd_oz")
        rates.silver_usd_oz = intl.get("silver_usd_oz")
        rates.platinum_usd_oz = intl.get("platinum_usd_oz")
        rates.usd_inr = intl.get("usd_inr")

        # Scrape platinum from GoodReturns (may not exist)
        platinum = await self.scrape_platinum_rate()
        if platinum:
            rates.platinum = platinum
        elif rates.platinum_usd_oz and rates.usd_inr:
            # Calculate from international price if local scraping fails
            # Convert USD/oz to INR/gram with ~8% retail markup
            platinum_per_gram = (rates.platinum_usd_oz * rates.usd_inr) / TROY_OZ_TO_GRAM
            rates.platinum = round(platinum_per_gram * 1.08)

        # Scrape MCX futures (or estimate from spot prices)
        mcx = await self.scrape_mcx_futures()
        if mcx.get("gold_futures"):
            rates.mcx_gold_futures = mcx.get("gold_futures")
            rates.mcx_gold_futures_expiry = mcx.get("gold_expiry")
        else:
            # Estimate MCX gold futures from spot (typically 0.5-1% premium)
            rates.mcx_gold_futures = round(rates.gold_24k * 10 * 1.005)  # Per 10gm with 0.5% premium
            rates.mcx_gold_futures_expiry = datetime.now().strftime("%b")

        if mcx.get("silver_futures"):
            rates.mcx_silver_futures = mcx.get("silver_futures")
            rates.mcx_silver_futures_expiry = mcx.get("silver_expiry")
        else:
            # Estimate MCX silver futures from spot
            rates.mcx_silver_futures = round(rates.silver * 1000 * 1.005)  # Per kg with 0.5% premium
            rates.mcx_silver_futures_expiry = datetime.now().strftime("%b")

        return rates

    async def get_current_rates(
        self,
        db: AsyncSession,
        city: str = "Mumbai",
        force_refresh: bool = False
    ) -> Optional[MetalRate]:
        """Get current rates from cache or fresh scrape."""
        city_normalized = city.title()

        # Check cache (15 min)
        if not force_refresh:
            cutoff = datetime.now() - timedelta(minutes=15)
            result = await db.execute(
                select(MetalRate)
                .where(MetalRate.city == city_normalized)
                .where(MetalRate.recorded_at >= cutoff)
                .order_by(desc(MetalRate.recorded_at))
                .limit(1)
            )
            cached = result.scalar_one_or_none()
            if cached:
                return cached

        # Fetch fresh rates
        rates = await self.fetch_all_rates(city.lower())
        if not rates:
            # Return most recent cached
            result = await db.execute(
                select(MetalRate)
                .where(MetalRate.city == city_normalized)
                .order_by(desc(MetalRate.recorded_at))
                .limit(1)
            )
            return result.scalar_one_or_none()

        # Save to database
        rate = MetalRate(
            city=city_normalized,
            rate_date=rates.rate_date,
            gold_24k=rates.gold_24k,
            gold_22k=rates.gold_22k,
            gold_18k=rates.gold_18k,
            gold_14k=rates.gold_14k,
            gold_10k=rates.gold_10k,
            gold_9k=rates.gold_9k,
            silver=rates.silver,
            platinum=rates.platinum,
            gold_usd_oz=rates.gold_usd_oz,
            silver_usd_oz=rates.silver_usd_oz,
            platinum_usd_oz=rates.platinum_usd_oz,
            usd_inr=rates.usd_inr,
            mcx_gold_futures=rates.mcx_gold_futures,
            mcx_silver_futures=rates.mcx_silver_futures,
            source=rates.source
        )
        db.add(rate)
        await db.flush()

        logger.info(f"Saved rates for {city_normalized}: 24K=Rs.{rates.gold_24k}")
        return rate

    async def get_market_analysis(
        self,
        db: AsyncSession,
        city: str = "Mumbai"
    ) -> MarketAnalysis:
        """Generate smart market analysis."""
        analysis = MarketAnalysis()
        now = datetime.utcnow()  # Use UTC for database queries
        city_normalized = city.title()

        # Get current rate
        current = await self.get_current_rates(db, city)
        if not current:
            return analysis

        # Get historical rates
        async def get_historical_rate(days_ago: int) -> Optional[MetalRate]:
            target = now - timedelta(days=days_ago)
            result = await db.execute(
                select(MetalRate)
                .where(MetalRate.city == city_normalized)
                .where(MetalRate.recorded_at >= target - timedelta(hours=12))
                .where(MetalRate.recorded_at <= target + timedelta(hours=12))
                .order_by(desc(MetalRate.recorded_at))
                .limit(1)
            )
            return result.scalar_one_or_none()

        yesterday = await get_historical_rate(1)
        week_ago = await get_historical_rate(7)
        month_ago = await get_historical_rate(30)

        # Calculate daily change
        if yesterday:
            analysis.daily_change = current.gold_24k - yesterday.gold_24k
            analysis.daily_change_percent = (analysis.daily_change / yesterday.gold_24k) * 100

        # Calculate weekly change
        if week_ago:
            analysis.weekly_change = current.gold_24k - week_ago.gold_24k
            analysis.weekly_change_percent = (analysis.weekly_change / week_ago.gold_24k) * 100

        # Calculate monthly change
        if month_ago:
            analysis.monthly_change = current.gold_24k - month_ago.gold_24k
            analysis.monthly_change_percent = (analysis.monthly_change / month_ago.gold_24k) * 100

        # Determine direction
        if analysis.daily_change > 50:
            analysis.direction = "rising"
            analysis.direction_symbol = "↑"
        elif analysis.daily_change < -50:
            analysis.direction = "falling"
            analysis.direction_symbol = "↓"
        else:
            analysis.direction = "stable"
            analysis.direction_symbol = "→"

        # Count consecutive days in same direction
        analysis.consecutive_days = 1
        for days in range(2, 8):
            prev = await get_historical_rate(days)
            prev_prev = await get_historical_rate(days + 1)
            if prev and prev_prev:
                if analysis.direction == "rising" and prev.gold_24k > prev_prev.gold_24k:
                    analysis.consecutive_days += 1
                elif analysis.direction == "falling" and prev.gold_24k < prev_prev.gold_24k:
                    analysis.consecutive_days += 1
                else:
                    break
            else:
                break

        # Determine volatility
        if abs(analysis.daily_change_percent) > 2.0:
            analysis.volatility = "high"
        elif abs(analysis.daily_change_percent) > 0.8:
            analysis.volatility = "medium"
        else:
            analysis.volatility = "low"

        # Generate recommendation
        if analysis.direction == "falling" and analysis.weekly_change_percent < -2:
            analysis.recommendation = "buy"
            analysis.recommendation_text = "BUY - Prices dropping, good entry point"
        elif analysis.direction == "rising" and analysis.weekly_change_percent > 3:
            analysis.recommendation = "wait"
            analysis.recommendation_text = "WAIT - Prices at recent highs, wait for correction"
        elif analysis.volatility == "high":
            analysis.recommendation = "hold"
            analysis.recommendation_text = "HOLD - High volatility, wait for stability"
        else:
            analysis.recommendation = "hold"
            analysis.recommendation_text = "HOLD - Market stable, buy as per needs"

        # Generate expert summary
        direction_text = "rallied" if analysis.direction == "rising" else "fell" if analysis.direction == "falling" else "remained stable"
        analysis.expert_summary = f"Gold {direction_text} "
        if analysis.consecutive_days > 1:
            analysis.expert_summary += f"for {analysis.consecutive_days} consecutive days. "

        if current.usd_inr:
            analysis.expert_summary += f"USD/INR at {current.usd_inr:.2f}. "

        if analysis.weekly_change_percent > 0:
            analysis.expert_summary += f"Weekly gain of {analysis.weekly_change_percent:.1f}%."
        else:
            analysis.expert_summary += f"Weekly decline of {abs(analysis.weekly_change_percent):.1f}%."

        return analysis

    async def generate_ai_expert_analysis(self, rates: MetalRateData, analysis: MarketAnalysis) -> str:
        """Generate comprehensive AI-powered expert analysis using Claude."""
        if not self.claude_client:
            return self._fallback_expert_analysis(rates, analysis)

        now = datetime.now(IST)

        # Build context for Claude
        prompt = f"""You are JewelClaw's expert gold market analyst. Generate a SPECIFIC, ACTIONABLE 3-4 line analysis for Indian jewelry businesses.

TODAY'S DATA ({now.strftime('%d %b %Y')}):
- Gold 24K: ₹{rates.gold_24k:,.0f}/gram (₹{rates.gold_24k * 10:,.0f}/10gm)
- Gold 22K: ₹{rates.gold_22k:,.0f}/gram
- Silver: ₹{rates.silver:,.0f}/gram
- Platinum: ₹{rates.platinum:,.0f}/gram
- International Gold: ${rates.gold_usd_oz:,.2f}/oz
- USD/INR: ₹{rates.usd_inr:.2f}
- MCX Gold Futures: ₹{rates.mcx_gold_futures:,.0f}/10gm (if available)
- MCX Silver Futures: ₹{rates.mcx_silver_futures:,.0f}/kg (if available)

PRICE CHANGES:
- Daily Change: ₹{analysis.daily_change:+,.0f} ({analysis.daily_change_percent:+.1f}%)
- Weekly Change: ₹{analysis.weekly_change:+,.0f} ({analysis.weekly_change_percent:+.1f}%)
- Monthly Change: ₹{analysis.monthly_change:+,.0f} ({analysis.monthly_change_percent:+.1f}%)
- Trend: {analysis.direction} for {analysis.consecutive_days} day(s)
- Volatility: {analysis.volatility}

REQUIREMENTS - Your analysis MUST include:
1. KEY PRICE LEVELS: Mention specific support (e.g., "Support at ₹15,200") and resistance (e.g., "Resistance at ₹15,800") levels
2. PRICE PREDICTION: Give specific range prediction (e.g., "Expect ₹200-400 rise if US jobs data weak")
3. FACTORS: Mention 1-2 key factors (Fed, RBI, USD, wedding season, festival, budget, geopolitical)
4. RECOMMENDATION: Clear BUY/WAIT/HOLD with reasoning

Format: Write 3-4 concise lines. Be SPECIFIC with numbers. No generic advice.

Example good output:
"Support at ₹15,200, resistance at ₹15,800. Fed's hawkish stance keeping gold under pressure. Expect ₹300-500 correction if US jobs data strong on Friday. WAIT for dip below ₹15,300 to buy - wedding season demand will support prices from March."
"""

        try:
            response = self.claude_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}]
            )
            return response.content[0].text.strip()
        except Exception as e:
            logger.error(f"Error generating AI analysis: {e}")
            return self._fallback_expert_analysis(rates, analysis)

    def _fallback_expert_analysis(self, rates: MetalRateData, analysis: MarketAnalysis) -> str:
        """Generate fallback expert analysis without AI."""
        # Calculate approximate support/resistance
        support = round(rates.gold_24k * 0.98 / 100) * 100  # 2% below, rounded
        resistance = round(rates.gold_24k * 1.02 / 100) * 100  # 2% above, rounded

        if analysis.direction == "rising":
            return f"Support at ₹{support:,}, resistance at ₹{resistance:,}. Gold rallying on safe-haven demand. {analysis.recommendation_text}. Monitor USD/INR closely."
        elif analysis.direction == "falling":
            return f"Support at ₹{support:,}, resistance at ₹{resistance:,}. Gold under pressure from strong dollar. {analysis.recommendation_text}. Good accumulation zone below ₹{support:,}."
        else:
            return f"Support at ₹{support:,}, resistance at ₹{resistance:,}. Market consolidating in range. {analysis.recommendation_text}. Buy on dips for wedding season demand."

    def format_gold_rate_message(self, rate: MetalRate, analysis: Optional[MarketAnalysis] = None) -> str:
        """Format gold rates for WhatsApp message."""
        lines = [
            f"💰 *GOLD RATES - {rate.city}*",
            f"📅 {rate.rate_date or datetime.now().strftime('%d %b %Y')}",
            "",
            "┌─────────┬────────────┐",
            "│ *Karat* │ *₹/gram*   │",
            "├─────────┼────────────┤",
            f"│ 24K     │ ₹{rate.gold_24k:,.0f}   │",
            f"│ 22K     │ ₹{rate.gold_22k:,.0f}   │",
            f"│ 18K     │ ₹{rate.gold_18k:,.0f}   │",
            f"│ 14K     │ ₹{rate.gold_14k:,.0f}    │",
        ]

        if rate.gold_10k:
            lines.append(f"│ 10K     │ ₹{rate.gold_10k:,.0f}    │")
        if rate.gold_9k:
            lines.append(f"│ 9K      │ ₹{rate.gold_9k:,.0f}    │")

        lines.append("└─────────┴────────────┘")

        if rate.gold_usd_oz and rate.usd_inr:
            lines.extend([
                "",
                "🌍 *International*",
                f"Gold: ${rate.gold_usd_oz:,.0f}/oz",
                f"USD/INR: ₹{rate.usd_inr:.2f}",
            ])

        if analysis:
            lines.extend([
                "",
                f"📊 *Analysis*",
                f"Trend: {analysis.direction_symbol} {analysis.direction.title()}",
                f"Volatility: {analysis.volatility.title()}",
                f"Weekly: {analysis.weekly_change_percent:+.1f}%",
                "",
                f"💡 {analysis.recommendation_text}",
            ])

        return "\n".join(lines)

    def format_silver_rate_message(self, rate: MetalRate) -> str:
        """Format silver rates for WhatsApp."""
        lines = [
            f"🪙 *SILVER RATE - {rate.city}*",
            f"📅 {rate.rate_date or datetime.now().strftime('%d %b %Y')}",
            "",
            f"₹{rate.silver:,.0f}/gram",
            f"₹{rate.silver * 1000:,.0f}/kg",
        ]

        if rate.silver_usd_oz:
            lines.extend([
                "",
                f"🌍 International: ${rate.silver_usd_oz:.2f}/oz",
            ])

        return "\n".join(lines)

    def format_platinum_rate_message(self, rate: MetalRate) -> str:
        """Format platinum rates for WhatsApp."""
        if not rate.platinum:
            return "Platinum rate not available at the moment."

        lines = [
            f"⚪ *PLATINUM RATE*",
            f"📅 {rate.rate_date or datetime.now().strftime('%d %b %Y')}",
            "",
            f"₹{rate.platinum:,.0f}/gram",
            f"₹{rate.platinum * 10:,.0f}/10gm",
        ]

        return "\n".join(lines)

    def format_morning_brief(self, rates, analysis: MarketAnalysis, expert_analysis: str = None, scraped_data: 'MetalRateData' = None, skip_header: bool = False) -> str:
        """Format the beautiful morning brief message with ALL data."""
        now = datetime.now(IST)

        # Handle both MetalRate (db model) and MetalRateData (dataclass)
        gold_24k = getattr(rates, 'gold_24k', 0)
        gold_22k = getattr(rates, 'gold_22k', 0)
        gold_18k = getattr(rates, 'gold_18k', 0)
        gold_14k = getattr(rates, 'gold_14k', 0)
        silver = getattr(rates, 'silver', 0) or 0
        platinum = getattr(rates, 'platinum', 0) or 0
        gold_usd_oz = getattr(rates, 'gold_usd_oz', 0) or 0
        silver_usd_oz = getattr(rates, 'silver_usd_oz', 0) or 0
        usd_inr = getattr(rates, 'usd_inr', 0) or 0
        mcx_gold = getattr(rates, 'mcx_gold_futures', None)
        mcx_gold_expiry = getattr(rates, 'mcx_gold_futures_expiry', 'Feb')
        mcx_silver = getattr(rates, 'mcx_silver_futures', None)
        mcx_silver_expiry = getattr(rates, 'mcx_silver_futures_expiry', 'Mar')

        # Calculate daily change - use scraped yesterday data if available
        change_24k = analysis.daily_change
        change_pct = analysis.daily_change_percent

        # If no database change, try to get from scraped data
        if change_24k == 0 and scraped_data:
            yesterday_24k = getattr(scraped_data, 'yesterday_24k', None)
            if yesterday_24k and yesterday_24k > 0:
                change_24k = gold_24k - yesterday_24k
                change_pct = (change_24k / yesterday_24k) * 100
                logger.info(f"Using scraped change: ₹{change_24k:+.0f} ({change_pct:+.2f}%)")

        # Determine change symbol
        if change_24k > 0:
            cs = "↑"
        elif change_24k < 0:
            cs = "↓"
        else:
            cs = "→"

        # Format change text
        if change_24k != 0:
            change_text_24k = f"{cs}₹{abs(change_24k):,.0f}"
            change_text_22k = f"{cs}₹{abs(change_24k * 0.916):,.0f}"
            change_text_18k = f"{cs}₹{abs(change_24k * 0.75):,.0f}"
            change_text_14k = f"{cs}₹{abs(change_24k * 0.585):,.0f}"
        else:
            change_text_24k = "→ No change"
            change_text_22k = ""
            change_text_18k = ""
            change_text_14k = ""

        # Build the message
        lines = []

        # Add header unless skipped (for personalized morning briefs)
        if not skip_header:
            lines.extend([
                f"🌅 *JewelClaw Gold Brief*",
                f"📅 {now.strftime('%d %b %Y')} | {now.strftime('%I:%M %p')} IST",
                "",
            ])
        else:
            lines.extend([
                f"📅 {now.strftime('%d %b %Y')} | {now.strftime('%I:%M %p')} IST",
                "",
            ])

        lines.extend([
            "💰 *GOLD* (₹/gram)",
            f"24K: ₹{gold_24k:,.0f} {change_text_24k}",
            f"22K: ₹{gold_22k:,.0f} {change_text_22k}",
            f"18K: ₹{gold_18k:,.0f} {change_text_18k}",
            f"14K: ₹{gold_14k:,.0f} {change_text_14k}",
            "",
            "🪙 *SILVER*",
            f"₹{silver:,.0f}/gram | ₹{silver * 1000:,.0f}/kg",
            "",
            "⚪ *PLATINUM*",
            f"₹{platinum:,.0f}/gram",
            "",
            "🌍 *INTERNATIONAL*",
            f"Gold: ${gold_usd_oz:,.2f}/oz",
            f"Silver: ${silver_usd_oz:,.2f}/oz",
            f"USD/INR: ₹{usd_inr:.2f}",
            "",
        ])

        # Add MCX futures if available
        if mcx_gold or mcx_silver:
            lines.append("📊 *MCX FUTURES*")
            if mcx_gold:
                lines.append(f"Gold {mcx_gold_expiry}: ₹{mcx_gold:,.0f}/10gm")
            if mcx_silver:
                lines.append(f"Silver {mcx_silver_expiry}: ₹{mcx_silver:,.0f}/kg")
            lines.append("")

        # Add expert analysis
        lines.append("💡 *EXPERT ANALYSIS*")
        if expert_analysis:
            lines.append(expert_analysis)
        else:
            lines.append(analysis.expert_summary)
            lines.append(f"_{analysis.recommendation_text}_")
        lines.append("")

        # Add change summary - use calculated change_pct if available
        day_change_pct = change_pct if change_pct != 0 else analysis.daily_change_percent
        day_symbol = cs if change_pct != 0 else ("↑" if analysis.daily_change_percent >= 0 else "↓")

        week_symbol = "+" if analysis.weekly_change_percent >= 0 else ""
        month_symbol = "+" if analysis.monthly_change_percent >= 0 else ""

        lines.append(f"📈 *Change:* Day {day_symbol}{abs(day_change_pct):.1f}% | Week {week_symbol}{analysis.weekly_change_percent:.1f}% | Month {month_symbol}{analysis.monthly_change_percent:.1f}%")
        lines.append("")
        lines.append("_Reply 'gold' for detailed rates_")

        return "\n".join(lines)


# Singleton instance
metal_service = MetalService()

# Backward compatibility
gold_service = metal_service
