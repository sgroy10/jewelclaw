"""
Scheduler service for morning briefs and periodic rate updates.
"""

import logging
from datetime import datetime
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.database import get_db_session
from app.services.gold_service import metal_service
from app.services.whatsapp_service import whatsapp_service
from app.services.scraper_service import scraper_service
from app.services.api_scraper import api_scraper
from app.services.image_service import image_service
from app.services.business_memory_service import business_memory_service
from app.services.reminder_service import reminder_service
from app.services.background_agent_service import background_agent

logger = logging.getLogger(__name__)

IST = pytz.timezone(settings.timezone)


class SchedulerService:
    """Service for managing scheduled tasks."""

    def __init__(self):
        self.scheduler = None
        self._initialized = False
        self._cached_market_intel = ""  # Gathered at midnight, used in morning brief

    def _ensure_initialized(self):
        """Lazy initialize scheduler when needed."""
        if not self._initialized:
            self.scheduler = AsyncIOScheduler(timezone=IST)
            self._setup_jobs()
            self._initialized = True

    def _setup_jobs(self):
        """Configure scheduled jobs."""
        # Morning brief at 9 AM IST
        self.scheduler.add_job(
            self.send_morning_briefs,
            CronTrigger(
                hour=settings.morning_brief_hour,
                minute=settings.morning_brief_minute,
                timezone=IST
            ),
            id="morning_brief",
            name="Send Morning Briefs",
            replace_existing=True
        )

        # Rate scraping every 15 minutes during market hours (9 AM - 9 PM IST)
        self.scheduler.add_job(
            self.scrape_and_cache_rates,
            CronTrigger(
                minute=f"*/{settings.scrape_interval_minutes}",
                hour="9-21",
                timezone=IST
            ),
            id="rate_scraper",
            name="Scrape Metal Rates",
            replace_existing=True
        )

        # Trend Scout: Design scraping at 6 AM IST daily
        self.scheduler.add_job(
            self.scrape_designs,
            CronTrigger(
                hour=6,
                minute=0,
                timezone=IST
            ),
            id="design_scraper",
            name="Scrape Jewelry Designs",
            replace_existing=True
        )

        # RemindGenie: Check every hour for users whose local time is 00:01 or 08:00
        self.scheduler.add_job(
            self.check_reminders_all_timezones,
            CronTrigger(
                minute=1,
                timezone="UTC"
            ),
            id="remind_check",
            name="RemindGenie Timezone-Aware Check",
            replace_existing=True
        )

        # Market Intelligence: Gather news at midnight IST for morning brief
        self.scheduler.add_job(
            self.gather_overnight_intelligence,
            CronTrigger(
                hour=0,
                minute=30,
                timezone=IST
            ),
            id="market_intelligence",
            name="Overnight Market Intelligence",
            replace_existing=True
        )

        # Weekly Portfolio Report: Sunday 10 AM IST
        self.scheduler.add_job(
            self.send_weekly_portfolio_reports,
            CronTrigger(
                day_of_week="sun",
                hour=10,
                minute=0,
                timezone=IST
            ),
            id="weekly_portfolio",
            name="Weekly Portfolio Report",
            replace_existing=True
        )

        logger.info("Scheduled jobs configured")

    def start(self):
        """Start the scheduler."""
        self._ensure_initialized()
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("Scheduler started")

    def stop(self):
        """Stop the scheduler."""
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Scheduler stopped")

    async def send_morning_briefs(self):
        """Send personalized, flowing morning brief to all subscribed users."""
        logger.info("=" * 50)
        logger.info("STARTING 9 AM MORNING BRIEF")
        logger.info("=" * 50)

        try:
            async with get_db_session() as db:
                # Scrape fresh rates
                scraped_data = await metal_service.fetch_all_rates("mumbai")
                rate = await metal_service.get_current_rates(db, "Mumbai", force_refresh=bool(scraped_data))
                if not rate:
                    logger.error("No rates available for morning brief")
                    return

                # Get analysis for change data
                analysis = await metal_service.get_market_analysis(db, "Mumbai")

                # Calculate gold change
                gold_24k = rate.gold_24k
                change_24k = analysis.daily_change
                if change_24k == 0 and scraped_data:
                    yesterday = getattr(scraped_data, 'yesterday_24k', None)
                    if yesterday and yesterday > 0:
                        change_24k = gold_24k - yesterday

                silver = rate.silver or 0

                # Get overnight intel
                market_intel = self._cached_market_intel or ""

                # Get subscribers
                users = await whatsapp_service.get_subscribed_users(db)
                logger.info(f"Found {len(users)} subscribers")
                if not users:
                    return

                success_count = 0
                for user in users:
                    try:
                        brief = await self._build_flowing_brief(
                            db, user, gold_24k, change_24k, silver,
                            rate, analysis, market_intel
                        )

                        phone = f"whatsapp:{user.phone_number}"
                        sent = await whatsapp_service.send_message(phone, brief)
                        if sent:
                            success_count += 1
                            logger.info(f"SENT to {user.name} ({user.phone_number})")

                    except Exception as e:
                        logger.error(f"Error sending to {user.phone_number}: {e}")

                self._cached_market_intel = ""
                logger.info(f"MORNING BRIEF COMPLETE: {success_count}/{len(users)} sent")

        except Exception as e:
            logger.error(f"Morning brief error: {e}")

    async def _build_flowing_brief(
        self, db, user, gold_24k, change_24k, silver, rate, analysis, market_intel
    ):
        """Build a single flowing message that feels like a smart friend texting you."""
        name = user.name or "Friend"
        parts = []

        # --- LINE 1: Greeting + headline rate ---
        if change_24k > 0:
            parts.append(f"Morning {name}! Gold *₹{gold_24k:,.0f}* (↑₹{abs(change_24k):,.0f})")
        elif change_24k < 0:
            parts.append(f"Morning {name}! Gold *₹{gold_24k:,.0f}* (↓₹{abs(change_24k):,.0f})")
        else:
            parts.append(f"Morning {name}! Gold at *₹{gold_24k:,.0f}*/gm")

        # Silver one-liner
        if silver > 0:
            parts.append(f"Silver ₹{silver:,.0f}/gm | 22K ₹{rate.gold_22k:,.0f}")

        # --- PORTFOLIO (if they have holdings) ---
        try:
            portfolio = await background_agent.get_portfolio_summary(db, user.id)
            if "error" not in portfolio and portfolio.get("holdings"):
                total = portfolio["total_value"]
                change = portfolio["total_change"]
                # Format total nicely (lakhs/crores)
                if total >= 10000000:
                    total_str = f"₹{total/10000000:.1f}Cr"
                elif total >= 100000:
                    total_str = f"₹{total/100000:.1f}L"
                else:
                    total_str = f"₹{total:,.0f}"

                if change > 0:
                    parts.append(f"\n📦 Your holdings: {total_str} (+₹{abs(change):,.0f} today)")
                elif change < 0:
                    parts.append(f"\n📦 Your holdings: {total_str} (-₹{abs(change):,.0f} today)")
                else:
                    parts.append(f"\n📦 Your holdings: {total_str}")
        except Exception:
            pass

        # --- THRESHOLD INSIGHT (contextual, not just data) ---
        try:
            thresholds = await business_memory_service.get_buy_thresholds(db, user.id)
            if thresholds.get("buy") and gold_24k:
                buy_price = thresholds["buy"]
                diff = gold_24k - buy_price
                if diff < 0:
                    parts.append(f"\n💡 Gold is ₹{abs(diff):,.0f} *below* your ₹{buy_price:,.0f} buy target - good time to stock up!")
                elif diff > 0 and diff < 500:
                    parts.append(f"\n📊 Gold just ₹{diff:,.0f} above your buy price. Close to your range.")
        except Exception:
            pass

        # --- UPCOMING REMINDERS (next 7 days) ---
        try:
            upcoming = await reminder_service.get_upcoming_reminders(db, user.id, days=7)
            if upcoming:
                r = upcoming[0]  # Most imminent
                days = r["days_away"]
                day_word = "tomorrow" if days == 1 else f"in {days} days"
                relation = f" ({r['relation']})" if r.get("relation") else ""
                parts.append(f"\n🔔 {r['name']}'s {r['occasion']}{relation} {day_word}")
        except Exception:
            pass

        # --- MARKET INTEL (if available) ---
        if market_intel:
            # Take just the first 2 bullet points to keep it short
            intel_lines = [l.strip() for l in market_intel.split("\n") if l.strip().startswith("•")][:2]
            if intel_lines:
                parts.append(f"\n🌙 Overnight: " + " ".join(intel_lines))

        # --- CHANGE SUMMARY ---
        change_parts = []
        day_pct = analysis.daily_change_percent
        if day_pct != 0:
            change_parts.append(f"Day {'↑' if day_pct > 0 else '↓'}{abs(day_pct):.1f}%")
        if analysis.weekly_change_percent != 0:
            wp = analysis.weekly_change_percent
            change_parts.append(f"Week {'+' if wp > 0 else ''}{wp:.1f}%")
        if change_parts:
            parts.append(f"\n📈 {' | '.join(change_parts)}")

        # --- SIGN OFF ---
        parts.append(f"\n_Reply 'gold' for full rates_")

        return "\n".join(parts)

    async def scrape_and_cache_rates(self):
        """Scrape and cache rates for major cities, then check price alerts."""
        logger.info("Starting rate scraping job")

        try:
            async with get_db_session() as db:
                cities = ["Mumbai", "Delhi", "Bangalore", "Chennai"]
                cities_scraped = 0
                mumbai_rate = None

                for city in cities:
                    try:
                        rate = await metal_service.get_current_rates(db, city, force_refresh=True)
                        if rate:
                            cities_scraped += 1
                            if city == "Mumbai":
                                mumbai_rate = rate
                    except Exception as e:
                        logger.error(f"Error scraping {city}: {e}")

                await db.commit()
                logger.info(f"Scraped rates for {cities_scraped} cities")

                # Check price threshold alerts after scraping
                if mumbai_rate:
                    try:
                        await background_agent.check_price_alerts(
                            db,
                            gold_24k=mumbai_rate.gold_24k,
                            silver=mumbai_rate.silver or 0,
                        )
                    except Exception as e:
                        logger.error(f"Price alert check failed: {e}")

        except Exception as e:
            logger.error(f"Error in rate scraping job: {e}")

    async def scrape_designs(self):
        """Scrape jewelry designs from all sources (6 AM daily)."""
        from sqlalchemy import select
        from app.models import Design

        logger.info("=" * 50)
        logger.info("STARTING 6 AM FRESH SCRAPE - TREND SCOUT")
        logger.info("=" * 50)

        try:
            async with get_db_session() as db:
                total_saved = 0
                sources_results = {}

                # Scrape multiple categories for variety
                categories = ["necklaces", "earrings", "bangles", "rings"]

                for category in categories:
                    try:
                        logger.info(f"Scraping {category}...")

                        # Use API scraper with Pinterest
                        designs = await api_scraper.scrape_all_with_pinterest(
                            category=category,
                            limit_per_site=8
                        )

                        saved_count = 0
                        for design in designs:
                            # Check if already exists
                            existing = await db.execute(
                                select(Design).where(Design.source_url == design.source_url)
                            )
                            if existing.scalar_one_or_none():
                                continue

                            # Upload image to Cloudinary for WhatsApp compatibility
                            cloudinary_url = design.image_url
                            if image_service.configured and design.image_url:
                                try:
                                    cloudinary_url = await image_service.upload_from_url(
                                        design.image_url, design.source
                                    )
                                except Exception as e:
                                    logger.warning(f"Cloudinary upload failed: {e}")

                            # Save to database with high score for fresh content
                            db_design = Design(
                                source=design.source,
                                source_url=design.source_url,
                                image_url=cloudinary_url,
                                title=design.title,
                                category=design.category or category,
                                metal_type=design.metal_type,
                                price_range_min=design.price,
                                trending_score=85  # High score for fresh daily scrape
                            )
                            db.add(db_design)
                            saved_count += 1

                        sources_results[category] = saved_count
                        total_saved += saved_count
                        logger.info(f"  {category}: {saved_count} new designs saved")

                    except Exception as e:
                        logger.error(f"Error scraping {category}: {e}")
                        sources_results[category] = 0

                await db.commit()

                logger.info("=" * 50)
                logger.info(f"6 AM SCRAPE COMPLETE: {total_saved} total new designs")
                for cat, count in sources_results.items():
                    logger.info(f"  {cat}: {count}")
                logger.info("=" * 50)

        except Exception as e:
            logger.error(f"Error in design scraping job: {e}")

    async def check_reminders_all_timezones(self):
        """
        Runs every hour (at :01). Checks which users are at midnight (00:xx)
        or 8 AM (08:xx) in their local timezone, and sends reminders accordingly.
        """
        from datetime import datetime, timezone
        import pytz

        utc_now = datetime.now(timezone.utc)
        logger.info(f"REMINDGENIE: Hourly timezone check at {utc_now.strftime('%H:%M UTC')}")

        try:
            async with get_db_session() as db:
                # Get all users with reminders or subscribed (for festivals)
                from sqlalchemy import select, or_
                from app.models import User, Reminder

                result = await db.execute(
                    select(User).where(
                        or_(
                            User.subscribed_to_morning_brief == True,
                            User.id.in_(
                                select(Reminder.user_id).where(Reminder.is_active == True)
                            )
                        )
                    )
                )
                all_users = result.scalars().all()

                if not all_users:
                    return

                midnight_users = []
                morning_users = []

                for user in all_users:
                    user_tz_str = user.timezone or "Asia/Kolkata"
                    try:
                        user_tz = pytz.timezone(user_tz_str)
                    except pytz.exceptions.UnknownTimeZoneError:
                        user_tz = pytz.timezone("Asia/Kolkata")

                    user_local = utc_now.astimezone(user_tz)
                    local_hour = user_local.hour

                    if local_hour == 0:
                        midnight_users.append((user, user_local))
                    elif local_hour == 8:
                        morning_users.append((user, user_local))

                sent_count = 0

                if midnight_users:
                    logger.info(f"RemindGenie: {len(midnight_users)} users at midnight")
                    sent_count += await self._send_reminders_to_users(db, midnight_users, is_midnight=True)

                if morning_users:
                    logger.info(f"RemindGenie: {len(morning_users)} users at 8 AM")
                    sent_count += await self._send_reminders_to_users(db, morning_users, is_midnight=False)

                if sent_count > 0:
                    logger.info(f"REMINDGENIE: Sent {sent_count} messages this hour")

        except Exception as e:
            logger.error(f"RemindGenie timezone check error: {e}")

    async def _send_reminders_to_users(self, db, user_time_pairs, is_midnight=True):
        """Send reminders to a list of (user, local_datetime) pairs."""
        sent_count = 0

        for user, local_dt in user_time_pairs:
            try:
                today = local_dt.date()

                # Get this user's reminders for today
                user_reminders = await reminder_service.get_todays_reminders(db, today=today)
                # Filter to only this user's reminders
                my_reminders = [
                    {"name": r.name, "occasion": r.occasion, "relationship": r.relation, "custom_note": r.custom_note}
                    for u, r in user_reminders if u.id == user.id
                ]

                # Get festivals for this user's local date
                festivals = await reminder_service.get_todays_festivals(today=today)

                # For festivals, all subscribed users get them
                if not my_reminders and not festivals:
                    continue

                message = await reminder_service.build_reminder_message(
                    user_name=user.name or "Friend",
                    reminders=my_reminders,
                    festivals=festivals if user.subscribed_to_morning_brief else [],
                    is_midnight=is_midnight,
                )

                if message:
                    phone = f"whatsapp:{user.phone_number}"
                    sent = await whatsapp_service.send_message(phone, message)
                    if sent:
                        sent_count += 1
                        tag = "midnight" if is_midnight else "morning"
                        logger.info(f"RemindGenie {tag} sent to {user.name} ({user.phone_number}, tz={user.timezone})")

            except Exception as e:
                logger.error(f"RemindGenie error for {user.phone_number}: {e}")

        return sent_count

    async def gather_overnight_intelligence(self):
        """Gather market news at midnight, cache for morning brief."""
        logger.info("=" * 50)
        logger.info("OVERNIGHT INTELLIGENCE: Gathering market news")
        logger.info("=" * 50)

        try:
            summary = await background_agent.gather_market_intelligence()
            if summary:
                self._cached_market_intel = summary
                logger.info(f"Market intel cached: {len(summary)} chars")
            else:
                self._cached_market_intel = ""
                logger.info("No market intel gathered")
        except Exception as e:
            logger.error(f"Market intelligence error: {e}")
            self._cached_market_intel = ""

    async def send_weekly_portfolio_reports(self):
        """Send weekly portfolio reports to users with inventory (Sunday 10 AM)."""
        logger.info("=" * 50)
        logger.info("WEEKLY PORTFOLIO REPORT - Sunday")
        logger.info("=" * 50)

        try:
            async with get_db_session() as db:
                sent = await background_agent.generate_weekly_portfolio_report(db)
                logger.info(f"Weekly portfolio reports sent: {sent}")
        except Exception as e:
            logger.error(f"Weekly portfolio report error: {e}")

    async def trigger_morning_brief_now(self):
        """Manually trigger morning brief."""
        await self.send_morning_briefs()

    def get_job_status(self) -> dict:
        """Get status of all scheduled jobs."""
        if not self.scheduler:
            return {"status": "not_initialized"}
        jobs = {}
        for job in self.scheduler.get_jobs():
            jobs[job.id] = {
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                "pending": job.pending
            }
        return jobs


# Singleton instance
scheduler_service = SchedulerService()
