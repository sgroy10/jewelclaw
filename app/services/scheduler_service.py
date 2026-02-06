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
        """Send morning brief to all subscribed users."""
        logger.info("Starting morning brief distribution")

        try:
            async with get_db_session() as db:
                # Get fresh rates
                rate = await metal_service.get_current_rates(db, "Mumbai", force_refresh=True)
                if not rate:
                    logger.error("Could not fetch rates for morning brief")
                    return

                # Get market analysis
                analysis = await metal_service.get_market_analysis(db, "Mumbai")

                # Build rate data for AI analysis
                from app.services.gold_service import MetalRateData
                rate_data = MetalRateData(
                    city=rate.city,
                    rate_date=rate.rate_date,
                    gold_24k=rate.gold_24k,
                    gold_22k=rate.gold_22k,
                    gold_18k=rate.gold_18k,
                    gold_14k=rate.gold_14k,
                    silver=rate.silver or 0,
                    platinum=rate.platinum or 0,
                    gold_usd_oz=rate.gold_usd_oz,
                    silver_usd_oz=rate.silver_usd_oz,
                    usd_inr=rate.usd_inr,
                    mcx_gold_futures=getattr(rate, 'mcx_gold_futures', None),
                    mcx_silver_futures=getattr(rate, 'mcx_silver_futures', None),
                )

                # Generate AI expert analysis
                expert_analysis = await metal_service.generate_ai_expert_analysis(rate_data, analysis)

                # Format morning brief with AI analysis
                brief = metal_service.format_morning_brief(rate, analysis, expert_analysis)

                # Get subscribed users
                users = await whatsapp_service.get_subscribed_users(db)
                logger.info(f"Sending morning brief to {len(users)} users")

                # Send to each user
                success_count = 0
                for user in users:
                    try:
                        phone = f"whatsapp:{user.phone_number}"
                        sent = await whatsapp_service.send_message(phone, brief)
                        if sent:
                            success_count += 1
                    except Exception as e:
                        logger.error(f"Error sending brief to {user.phone_number}: {e}")

                logger.info(f"Morning brief sent to {success_count}/{len(users)} users")

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
