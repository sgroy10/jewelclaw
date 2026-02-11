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

logger = logging.getLogger(__name__)

IST = pytz.timezone(settings.timezone)


class SchedulerService:
    """Service for managing scheduled tasks."""

    def __init__(self):
        self.scheduler = None
        self._initialized = False

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
        """Send personalized morning brief to all subscribed users."""
        logger.info("=" * 50)
        logger.info("STARTING 9 AM MORNING BRIEF DISTRIBUTION")
        logger.info("=" * 50)

        try:
            async with get_db_session() as db:
                # Fetch fresh scraped data
                scraped_data = await metal_service.fetch_all_rates("mumbai")
                if not scraped_data:
                    logger.error("Could not scrape rates for morning brief")
                    return

                # Get database rate record
                rate = await metal_service.get_current_rates(db, "Mumbai", force_refresh=True)
                if not rate:
                    logger.error("Could not get rates for morning brief")
                    return

                # Get market analysis
                analysis = await metal_service.get_market_analysis(db, "Mumbai")

                # Get CACHED expert analysis (saves API cost)
                expert_analysis = await metal_service.get_cached_expert_analysis(scraped_data, analysis)

                # Get subscribed users
                users = await whatsapp_service.get_subscribed_users(db)
                logger.info(f"Found {len(users)} subscribed users")

                if not users:
                    logger.info("No subscribers to send morning brief")
                    return

                # Get new designs count for Trend Scout teaser
                new_designs_count = await scraper_service.get_new_designs_count(db, hours=24)

                # Send personalized message to each user
                success_count = 0
                for user in users:
                    try:
                        # Personalized greeting
                        name = user.name or "Friend"
                        greeting = f"🌅 *Good Morning {name}!*\nHere's your JewelClaw Gold Brief...\n\n"

                        # Format brief without the header (we add personalized one)
                        brief_body = metal_service.format_morning_brief(
                            rate, analysis, expert_analysis, scraped_data,
                            skip_header=True
                        )

                        # AI Agent: Personalized buy/sell threshold insight
                        threshold_insight = ""
                        try:
                            thresholds = await business_memory_service.get_buy_thresholds(db, user.id)
                            gold_24k = rate.gold_24k if rate else 0
                            if thresholds.get("buy") and gold_24k:
                                buy_price = thresholds["buy"]
                                diff = gold_24k - buy_price
                                if diff < 0:
                                    threshold_insight = f"\n\n💡 Gold at ₹{gold_24k:,.0f} - ₹{abs(diff):,.0f} *below* your buy price of ₹{buy_price:,.0f}!\n   Good time to stock up!"
                                else:
                                    threshold_insight = f"\n\n📊 Gold at ₹{gold_24k:,.0f} - ₹{diff:,.0f} above your usual ₹{buy_price:,.0f}. Consider waiting."
                        except Exception as e:
                            logger.warning(f"Threshold check failed for {user.phone_number}: {e}")

                        # Add Trend Scout teaser if there are new designs
                        if new_designs_count > 0:
                            trend_teaser = f"\n\n🔥 *{new_designs_count} new designs* added today!\nReply 'trends' to explore."
                        else:
                            trend_teaser = ""

                        personalized_brief = greeting + brief_body + threshold_insight + trend_teaser

                        phone = f"whatsapp:{user.phone_number}"
                        sent = await whatsapp_service.send_message(phone, personalized_brief)

                        if sent:
                            success_count += 1
                            logger.info(f"SENT to {name} ({user.phone_number})")
                        else:
                            logger.error(f"FAILED to send to {name} ({user.phone_number})")

                    except Exception as e:
                        logger.error(f"Error sending to {user.phone_number}: {e}")

                logger.info("=" * 50)
                logger.info(f"MORNING BRIEF COMPLETE: {success_count}/{len(users)} sent")
                logger.info("=" * 50)

        except Exception as e:
            logger.error(f"Error in morning brief job: {e}")

    async def scrape_and_cache_rates(self):
        """Scrape and cache rates for major cities."""
        logger.info("Starting rate scraping job")

        try:
            async with get_db_session() as db:
                cities = ["Mumbai", "Delhi", "Bangalore", "Chennai"]
                cities_scraped = 0

                for city in cities:
                    try:
                        rate = await metal_service.get_current_rates(db, city, force_refresh=True)
                        if rate:
                            cities_scraped += 1
                    except Exception as e:
                        logger.error(f"Error scraping {city}: {e}")

                await db.commit()
                logger.info(f"Scraped rates for {cities_scraped} cities")

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
