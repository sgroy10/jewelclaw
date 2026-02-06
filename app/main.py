"""
JewelClaw - FastAPI Application
WhatsApp bot for Indian jewelry industry with gold, silver, and platinum rates.
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import init_db, close_db, get_db
from app.services.whatsapp_service import whatsapp_service
from app.services.gold_service import metal_service
from app.services.scheduler_service import scheduler_service
from app.utils.language_detector import detect_language

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    logger.info(f"Starting {settings.app_name}...")
    await init_db()
    scheduler_service.start()
    logger.info("Application started successfully")

    yield

    logger.info("Shutting down...")
    scheduler_service.stop()
    await close_db()
    logger.info("Application stopped")


app = FastAPI(
    title=settings.app_name,
    description="AI-powered WhatsApp assistant for the Indian jewelry industry",
    version="1.0.0",
    lifespan=lifespan
)


@app.get("/")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "app": settings.app_name,
        "version": "1.0.0"
    }


# Simple in-memory deduplication for Twilio retries
_processed_message_sids = set()
_max_cached_sids = 1000


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Handle incoming WhatsApp messages from Twilio."""
    try:
        form_data = await request.form()
        form_dict = dict(form_data)

        # Deduplicate Twilio retries using MessageSid
        message_sid = form_dict.get("MessageSid", "")
        if message_sid:
            if message_sid in _processed_message_sids:
                logger.info(f"Skipping duplicate message: {message_sid}")
                return PlainTextResponse("")
            _processed_message_sids.add(message_sid)
            # Limit cache size
            if len(_processed_message_sids) > _max_cached_sids:
                _processed_message_sids.clear()

        phone_number, message_body, profile_name = whatsapp_service.parse_incoming_message(
            form_dict
        )

        if not phone_number or not message_body:
            return PlainTextResponse("")

        logger.info(f"Message from {phone_number}: {message_body[:50]}... (SID: {message_sid})")

        # Get or create user
        user, is_new_user = await whatsapp_service.get_or_create_user(db, phone_number, profile_name)

        # Parse command and respond
        command = whatsapp_service.parse_command(message_body)
        response = await handle_command(db, user, command, phone_number, is_new_user)

        # Send response
        if response:
            await whatsapp_service.send_message(phone_number, response)

        await db.commit()
        return PlainTextResponse("")

    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return PlainTextResponse("")


WELCOME_MESSAGE = """👋 *Welcome to JewelClaw!*
Your AI-powered jewelry industry assistant.

*Commands:*
• *gold* - Get live gold rates
• *subscribe* - Get daily 9 AM brief
• *unsubscribe* - Stop daily briefs
• *help* - Show this menu

_Developed by Sandeep Roy_"""


async def handle_command(db: AsyncSession, user, command: str, phone_number: str, is_new_user: bool = False) -> str:
    """Handle 5 simple commands: hi/hello, gold, subscribe, unsubscribe, help."""
    city = user.preferred_city or "Mumbai"

    # 1. NEW USER or HI/HELLO → Welcome message
    if is_new_user or command == "greeting":
        return WELCOME_MESSAGE

    # 2. GOLD → Show gold rates
    if command == "gold_rate":
        scraped_data = await metal_service.fetch_all_rates(city.lower())
        rate = await metal_service.get_current_rates(db, city, force_refresh=True)

        if rate and scraped_data:
            analysis = await metal_service.get_market_analysis(db, city)
            expert_analysis = await metal_service.generate_ai_expert_analysis(scraped_data, analysis)
            return metal_service.format_morning_brief(rate, analysis, expert_analysis, scraped_data)
        elif rate:
            analysis = await metal_service.get_market_analysis(db, city)
            from app.services.gold_service import MetalRateData
            rate_data = MetalRateData(
                city=rate.city, rate_date=rate.rate_date,
                gold_24k=rate.gold_24k, gold_22k=rate.gold_22k,
                gold_18k=rate.gold_18k, gold_14k=rate.gold_14k,
                silver=rate.silver or 0, platinum=rate.platinum or 0,
                gold_usd_oz=rate.gold_usd_oz, silver_usd_oz=rate.silver_usd_oz,
                usd_inr=rate.usd_inr,
                mcx_gold_futures=getattr(rate, 'mcx_gold_futures', None),
                mcx_silver_futures=getattr(rate, 'mcx_silver_futures', None),
            )
            expert_analysis = await metal_service.generate_ai_expert_analysis(rate_data, analysis)
            return metal_service.format_morning_brief(rate, analysis, expert_analysis)
        return "Unable to fetch gold rates. Please try again."

    # 3. SUBSCRIBE → Save for 9 AM daily brief
    if command == "subscribe":
        user.subscribed_to_morning_brief = True
        await db.flush()
        return "✅ Subscribed to 9 AM daily brief!"

    # 4. UNSUBSCRIBE → Remove from daily brief
    if command == "unsubscribe":
        user.subscribed_to_morning_brief = False
        await db.flush()
        return "❌ Unsubscribed from daily briefs"

    # 5. HELP → Show welcome message
    if command == "help":
        return WELCOME_MESSAGE

    # Unknown command → Show welcome message
    return WELCOME_MESSAGE


# API Endpoints
@app.get("/rates/gold")
async def get_gold_rates(
    city: str = "Mumbai",
    db: AsyncSession = Depends(get_db)
):
    """Get current gold rates for a city."""
    try:
        rate = await metal_service.get_current_rates(db, city)
        if not rate:
            raise HTTPException(status_code=404, detail=f"No rates found for {city}")

        # Extract all values immediately to avoid lazy loading issues
        return {
            "city": rate.city,
            "rate_date": rate.rate_date,
            "gold": {
                "24k": rate.gold_24k,
                "22k": rate.gold_22k,
                "18k": rate.gold_18k,
                "14k": rate.gold_14k,
                "10k": rate.gold_10k,
                "9k": rate.gold_9k,
            },
            "silver": rate.silver,
            "platinum": rate.platinum,
            "international": {
                "gold_usd_oz": rate.gold_usd_oz,
                "silver_usd_oz": rate.silver_usd_oz,
                "usd_inr": rate.usd_inr,
            },
            "source": rate.source,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting rates: {e}")
        raise HTTPException(status_code=500, detail="Error fetching rates")


@app.get("/rates/silver")
async def get_silver_rates(
    city: str = "Mumbai",
    db: AsyncSession = Depends(get_db)
):
    """Get current silver rates."""
    rate = await metal_service.get_current_rates(db, city)
    if not rate or not rate.silver:
        raise HTTPException(status_code=404, detail="Silver rates not available")

    recorded_at = str(rate.recorded_at) if rate.recorded_at else None
    return {
        "city": rate.city,
        "silver_per_gram": rate.silver,
        "silver_per_kg": rate.silver * 1000,
        "silver_usd_oz": rate.silver_usd_oz,
        "source": rate.source,
        "recorded_at": recorded_at
    }


@app.get("/rates/platinum")
async def get_platinum_rates(db: AsyncSession = Depends(get_db)):
    """Get current platinum rates."""
    rate = await metal_service.get_current_rates(db, "Mumbai")
    if not rate or not rate.platinum:
        raise HTTPException(status_code=404, detail="Platinum rates not available")

    recorded_at = str(rate.recorded_at) if rate.recorded_at else None
    return {
        "platinum_per_gram": rate.platinum,
        "platinum_per_10gram": rate.platinum * 10,
        "source": rate.source,
        "recorded_at": recorded_at
    }


@app.get("/scheduler/status")
async def scheduler_status():
    """Get scheduler job status."""
    return scheduler_service.get_job_status()


@app.post("/scheduler/trigger/morning-brief")
async def trigger_morning_brief():
    """Manually trigger morning brief."""
    await scheduler_service.trigger_morning_brief_now()
    return {"status": "triggered"}


@app.post("/test/send-rates")
async def test_send_rates(
    phone: str,
    db: AsyncSession = Depends(get_db)
):
    """Test endpoint to send rates to a phone number."""
    rate = await metal_service.get_current_rates(db, "Mumbai", force_refresh=True)
    if rate:
        analysis = await metal_service.get_market_analysis(db, "Mumbai")
        message = metal_service.format_gold_rate_message(rate, analysis)
        await whatsapp_service.send_message(phone, message)
        return {"status": "sent", "phone": phone}
    return {"status": "error", "message": "Could not fetch rates"}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler."""
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred"}
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug
    )
