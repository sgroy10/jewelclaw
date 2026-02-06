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
from app.database import init_db, close_db, get_db, reset_db
# Import models to ensure they're registered with Base.metadata
from app.models import User, Conversation, MetalRate
from app.services.whatsapp_service import whatsapp_service
from app.services.gold_service import metal_service
from app.services.scheduler_service import scheduler_service

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

# Track users waiting to provide their name for subscription
_pending_subscribe = {}  # phone_number -> True


WELCOME_MESSAGE = """👋 *Welcome to JewelClaw!*
Your AI-powered jewelry industry assistant.

🚀 *First time?* Send: *join third-find*

*Commands:*
• *gold* - Get live gold rates + expert analysis
• *subscribe* - Get daily 9 AM morning brief
• *unsubscribe* - Stop daily briefs
• *help* - Show this menu

🇮🇳 *Built for Indian Jewelers*
_Developed by Sandeep Roy_"""


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Handle incoming WhatsApp messages from Twilio."""
    try:
        form_data = await request.form()
        form_dict = dict(form_data)

        logger.info(f"WEBHOOK RECEIVED: {form_dict}")

        # Deduplicate Twilio retries using MessageSid
        message_sid = form_dict.get("MessageSid", "")
        if message_sid:
            if message_sid in _processed_message_sids:
                logger.info(f"Skipping duplicate message: {message_sid}")
                return PlainTextResponse("")
            _processed_message_sids.add(message_sid)
            if len(_processed_message_sids) > _max_cached_sids:
                _processed_message_sids.clear()

        phone_number, message_body, profile_name = whatsapp_service.parse_incoming_message(
            form_dict
        )

        logger.info(f"PARSED: phone={phone_number}, body={message_body}, profile={profile_name}")

        if not phone_number or not message_body:
            logger.warning("No phone or message body")
            return PlainTextResponse("")

        logger.info(f"Message from {phone_number}: {message_body[:50]}...")

        # Get or create user
        user, is_new_user = await whatsapp_service.get_or_create_user(db, phone_number, profile_name)
        logger.info(f"USER: {user.phone_number}, new={is_new_user}")

        # Check if user is providing their name for subscription
        if phone_number in _pending_subscribe:
            # User is responding with their name
            name = message_body.strip()[:50]  # Limit name length
            user.name = name
            user.subscribed_to_morning_brief = True
            await db.flush()
            del _pending_subscribe[phone_number]
            logger.info(f"SUBSCRIBED: {phone_number} as '{name}'")
            response = f"✅ Welcome {name}! You'll receive the morning brief at 9 AM IST daily."
        else:
            # Normal command handling
            command = whatsapp_service.parse_command(message_body)
            logger.info(f"COMMAND: {command}")
            response = await handle_command(db, user, command, phone_number, is_new_user)
            logger.info(f"RESPONSE LENGTH: {len(response) if response else 0}")

        # Send response
        if response:
            logger.info(f"SENDING to {phone_number}...")
            sent = await whatsapp_service.send_message(phone_number, response)
            logger.info(f"SENT: {sent}")

        await db.commit()
        return PlainTextResponse("")

    except Exception as e:
        import traceback
        logger.error(f"WEBHOOK ERROR: {e}")
        logger.error(traceback.format_exc())
        return PlainTextResponse("")


async def handle_command(db: AsyncSession, user, command: str, phone_number: str, is_new_user: bool = False) -> str:
    """Handle commands: hi/hello, gold, subscribe, unsubscribe, help."""
    city = user.preferred_city or "Mumbai"

    # 1. NEW USER or HI/HELLO → Welcome message
    if is_new_user or command == "greeting":
        return WELCOME_MESSAGE

    # 2. GOLD → Show gold rates
    if command == "gold_rate":
        logger.info(f"Fetching gold rates for {phone_number}")
        scraped_data = await metal_service.fetch_all_rates(city.lower())
        rate = await metal_service.get_current_rates(db, city, force_refresh=True)

        if rate and scraped_data:
            analysis = await metal_service.get_market_analysis(db, city)
            expert_analysis = await metal_service.get_cached_expert_analysis(scraped_data, analysis)
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
            expert_analysis = await metal_service.get_cached_expert_analysis(rate_data, analysis)
            return metal_service.format_morning_brief(rate, analysis, expert_analysis)
        return "Unable to fetch gold rates. Please try again."

    # 3. SUBSCRIBE → Ask for name, then save
    if command == "subscribe":
        if user.subscribed_to_morning_brief and user.name:
            return f"✅ You're already subscribed as {user.name}!"
        # Ask for name
        _pending_subscribe[phone_number] = True
        logger.info(f"Asking name from {phone_number} for subscription")
        return "What's your name?"

    # 4. UNSUBSCRIBE → Remove from daily brief
    if command == "unsubscribe":
        user.subscribed_to_morning_brief = False
        await db.flush()
        logger.info(f"UNSUBSCRIBED: {phone_number}")
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

        return {
            "city": rate.city,
            "rate_date": rate.rate_date,
            "gold": {
                "24k": rate.gold_24k,
                "22k": rate.gold_22k,
                "18k": rate.gold_18k,
                "14k": rate.gold_14k,
            },
            "silver": rate.silver,
            "platinum": rate.platinum,
            "source": rate.source,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting rates: {e}")
        raise HTTPException(status_code=500, detail="Error fetching rates")


@app.get("/subscribers")
async def get_subscribers(db: AsyncSession = Depends(get_db)):
    """Get list of all subscribers."""
    from sqlalchemy import select
    from app.models import User

    result = await db.execute(
        select(User).where(User.subscribed_to_morning_brief == True)
    )
    users = result.scalars().all()

    return {
        "count": len(users),
        "subscribers": [
            {"phone": u.phone_number, "name": u.name, "subscribed_at": str(u.created_at)}
            for u in users
        ]
    }


@app.post("/admin/reset-database")
async def admin_reset_database():
    """DROP ALL TABLES and recreate them. This will delete all data!"""
    from sqlalchemy import text
    from app.database import engine

    logger.warning("DATABASE RESET REQUESTED - Dropping all tables...")

    try:
        async with engine.begin() as conn:
            # Drop all tables with CASCADE
            await conn.execute(text("DROP TABLE IF EXISTS conversations CASCADE"))
            await conn.execute(text("DROP TABLE IF EXISTS metal_rates CASCADE"))
            await conn.execute(text("DROP TABLE IF EXISTS users CASCADE"))
            await conn.execute(text("DROP TYPE IF EXISTS languagepreference CASCADE"))
            logger.info("Tables dropped")

        # Recreate
        await init_db()
        logger.info("Tables recreated")

        return {"status": "success", "message": "Database reset complete"}

    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        logger.error(f"Reset failed: {error_detail}")
        return {"status": "error", "error": str(e), "detail": error_detail}


@app.get("/admin/test-twilio/{phone}")
async def test_twilio(phone: str):
    """Test if Twilio can send a message."""
    try:
        result = await whatsapp_service.send_message(
            f"whatsapp:{phone}",
            "Test from JewelClaw - Twilio is working!"
        )
        return {"status": "sent" if result else "failed", "phone": phone}
    except Exception as e:
        import traceback
        return {"status": "error", "error": str(e), "trace": traceback.format_exc()}


@app.get("/admin/simulate-gold/{phone}")
async def simulate_gold(phone: str, db: AsyncSession = Depends(get_db)):
    """Simulate what happens when someone sends 'gold'."""
    try:
        steps = []

        # Step 1: Parse command
        command = whatsapp_service.parse_command("gold")
        steps.append(f"1. Command parsed: {command}")

        # Step 2: Get user
        user, is_new = await whatsapp_service.get_or_create_user(db, f"whatsapp:{phone}", "Test")
        steps.append(f"2. User: {user.phone_number}, new={is_new}")

        # Step 3: Get response
        response = await handle_command(db, user, command, f"whatsapp:{phone}", is_new)
        steps.append(f"3. Response length: {len(response) if response else 0}")
        steps.append(f"4. Response preview: {response[:200] if response else 'None'}...")

        # Step 4: Send
        if response:
            sent = await whatsapp_service.send_message(f"whatsapp:{phone}", response)
            steps.append(f"5. Sent: {sent}")

        await db.commit()
        return {"steps": steps, "success": True}

    except Exception as e:
        import traceback
        return {"error": str(e), "trace": traceback.format_exc()}


@app.get("/scheduler/status")
async def scheduler_status():
    """Get scheduler job status."""
    return scheduler_service.get_job_status()


@app.post("/scheduler/trigger/morning-brief")
async def trigger_morning_brief():
    """Manually trigger morning brief."""
    await scheduler_service.trigger_morning_brief_now()
    return {"status": "triggered"}


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
