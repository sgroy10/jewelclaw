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

                        # Add Trend Scout teaser if there are new designs
                        if new_designs_count > 0:
                            trend_teaser = f"\n\n🔥 *{new_designs_count} new designs* added today!\nReply 'trends' to explore."
                        else:
                            trend_teaser = ""

                        personalized_brief = greeting + brief_body + trend_teaser

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
