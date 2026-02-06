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
from app.models import User, Conversation, MetalRate, Design, UserDesignPreference, Lookbook
from app.services.whatsapp_service import whatsapp_service
from app.services.gold_service import metal_service
from app.services.scheduler_service import scheduler_service
from app.services.memory_service import memory_service
from app.services.scraper_service import scraper_service

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
• *gold* - Live gold rates + expert analysis
• *trends* - Trending jewelry designs
• *bridal* - Bridal collection
• *dailywear* - Lightweight designs
• *lookbook* - Your saved designs
• *subscribe* - Daily 9 AM brief
• *help* - Show this menu

🇮🇳 *Built for Indian Jewelers*
_Developed by Sandeep Roy_"""


async def store_conversation(db: AsyncSession, user_id: int, role: str, message: str):
    """Store conversation with intent/entity detection (Phase 1)."""
    try:
        # Analyze message if from user
        if role == "user":
            analysis = memory_service.analyze_message(message)
            intent = analysis["intent"]
            entities = analysis["entities"]
            sentiment = analysis["sentiment"]
        else:
            intent = None
            entities = {}
            sentiment = None

        # Create conversation record
        conv = Conversation(
            user_id=user_id,
            role=role,
            content=message,
            intent=intent,
            entities=entities,
            sentiment=sentiment
        )
        db.add(conv)
        await db.flush()
        logger.info(f"Stored {role} message | intent={intent} | entities={entities}")
    except Exception as e:
        # Non-blocking - don't break webhook if storage fails
        logger.warning(f"Failed to store conversation: {e}")


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

        # Phase 1: Store incoming message with intelligence
        await store_conversation(db, user.id, "user", message_body)

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

            # Phase 1: Store assistant response
            await store_conversation(db, user.id, "assistant", response)

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

    # ==========================================================================
    # TREND SCOUT COMMANDS
    # ==========================================================================

    # 6. TRENDS → Show trending designs
    if command in ["trends", "trending"]:
        return await handle_trends_command(db, user, phone_number)

    # 7. BRIDAL → Show bridal designs
    if command == "bridal":
        return await handle_category_command(db, user, "bridal", phone_number)

    # 8. DAILYWEAR → Show dailywear designs
    if command == "dailywear":
        return await handle_category_command(db, user, "dailywear", phone_number)

    # 9. TEMPLE → Show temple jewelry
    if command == "temple":
        return await handle_category_command(db, user, "temple", phone_number)

    # 10. MENS → Show men's jewelry
    if command == "mens":
        return await handle_category_command(db, user, "mens", phone_number)

    # 11. LIKE/SAVE design
    if command and command.startswith(("like", "save")):
        return await handle_like_command(db, user, command)

    # 12. SKIP design
    if command and command.startswith("skip"):
        return await handle_skip_command(db, user, command)

    # 13. LOOKBOOK → Show saved designs
    if command == "lookbook":
        return await handle_lookbook_command(db, user)

    # Unknown command → Show welcome message
    return WELCOME_MESSAGE


async def handle_trends_command(db: AsyncSession, user, phone_number: str) -> str:
    """Handle trends command - show trending designs with images."""
    designs = await scraper_service.get_trending_designs(db, limit=5)

    if not designs:
        return """🔥 *Trend Scout*

No designs found yet. Scraping in progress...

_New designs will be available soon!_"""

    # Send header
    await whatsapp_service.send_message(phone_number, "🔥 *Trending Designs Today*")

    # Send each design with its image
    for i, d in enumerate(designs, 1):
        price_text = f"₹{d.price_range_min:,.0f}" if d.price_range_min else "Price N/A"
        caption = f"*{i}. {d.title[:50]}*\n{d.category or 'General'} | {price_text}\n_Source: {d.source}_\n\nReply 'like {d.id}' to save"

        # Send with image if available
        if d.image_url:
            await whatsapp_service.send_message(phone_number, caption, media_url=d.image_url)
        else:
            await whatsapp_service.send_message(phone_number, caption)

    # Return final instruction (this will be sent as the last message)
    return "_Reply 'bridal', 'dailywear', 'temple' for categories_"


async def handle_category_command(db: AsyncSession, user, category: str, phone_number: str) -> str:
    """Handle category commands - show designs by category with images."""
    designs = await scraper_service.get_trending_designs(db, category=category, limit=5)

    category_titles = {
        "bridal": "💍 Bridal Collection",
        "dailywear": "✨ Dailywear Designs",
        "temple": "🛕 Temple Jewelry",
        "mens": "👔 Men's Collection",
        "contemporary": "🎨 Contemporary Styles",
    }

    title = category_titles.get(category, f"📿 {category.title()} Designs")

    if not designs:
        return f"""*{title}*

No {category} designs found yet.

_Try 'trends' to see all trending designs_"""

    # Send header
    await whatsapp_service.send_message(phone_number, f"*{title}*")

    # Send each design with its image
    for i, d in enumerate(designs, 1):
        price_text = f"₹{d.price_range_min:,.0f}" if d.price_range_min else "Price N/A"
        caption = f"*{i}. {d.title[:50]}*\n{price_text} | {d.source}\n\nReply 'like {d.id}' to save"

        if d.image_url:
            await whatsapp_service.send_message(phone_number, caption, media_url=d.image_url)
        else:
            await whatsapp_service.send_message(phone_number, caption)

    return "_Reply 'lookbook' to see your saved designs_"


async def handle_like_command(db: AsyncSession, user, command: str) -> str:
    """Handle like/save command."""
    import re
    match = re.search(r'(\d+)', command)
    if not match:
        return "Usage: like [design_id]\nExample: like 5"

    design_id = int(match.group(1))

    # Check if design exists
    design = await db.get(Design, design_id)
    if not design:
        return f"Design #{design_id} not found. Try 'trends' to see available designs."

    await scraper_service.record_preference(db, user.id, design_id, "liked")
    await db.commit()

    return f"""✅ *Saved!*

{design.title[:50]}

_Reply 'lookbook' to see all saved designs_"""


async def handle_skip_command(db: AsyncSession, user, command: str) -> str:
    """Handle skip command."""
    import re
    match = re.search(r'(\d+)', command)
    if not match:
        return "Usage: skip [design_id]\nExample: skip 5"

    design_id = int(match.group(1))

    await scraper_service.record_preference(db, user.id, design_id, "skipped")
    await db.commit()

    return "⏭️ Skipped. Reply 'trends' for more designs."


async def handle_lookbook_command(db: AsyncSession, user) -> str:
    """Handle lookbook command - show saved designs."""
    designs = await scraper_service.get_user_saved_designs(db, user.id)

    if not designs:
        return """📚 *Your Lookbook*

No saved designs yet.

_Browse designs with 'trends', 'bridal', or 'dailywear'
Then reply 'like [id]' to save_"""

    lines = ["📚 *Your Saved Designs*", ""]

    for i, d in enumerate(designs[:10], 1):
        price_text = f"₹{d.price_range_min:,.0f}" if d.price_range_min else ""
        lines.append(f"{i}. {d.title[:35]} {price_text}")

    lines.append("")
    lines.append(f"_Total: {len(designs)} designs saved_")

    return "\n".join(lines)


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


@app.post("/admin/migrate-phase-1")
async def migrate_phase_1():
    """Phase 1: Add conversation intelligence columns."""
    from sqlalchemy import text
    from app.database import engine

    try:
        async with engine.begin() as conn:
            # Add intent column
            await conn.execute(text("""
                ALTER TABLE conversations
                ADD COLUMN IF NOT EXISTS intent VARCHAR(50)
            """))
            # Add entities column
            await conn.execute(text("""
                ALTER TABLE conversations
                ADD COLUMN IF NOT EXISTS entities JSON DEFAULT '{}'
            """))
            # Add sentiment column
            await conn.execute(text("""
                ALTER TABLE conversations
                ADD COLUMN IF NOT EXISTS sentiment VARCHAR(20)
            """))
            logger.info("Phase 1 migration complete")

        return {"status": "success", "message": "Phase 1: Conversation intelligence columns added"}

    except Exception as e:
        import traceback
        return {"status": "error", "error": str(e), "detail": traceback.format_exc()}


@app.post("/admin/migrate-trend-scout")
async def migrate_trend_scout():
    """Create Trend Scout tables (designs, user_design_preferences, lookbooks)."""
    from sqlalchemy import text
    from app.database import engine

    try:
        async with engine.begin() as conn:
            # Create designs table
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS designs (
                    id SERIAL PRIMARY KEY,
                    source VARCHAR(50) NOT NULL,
                    source_url VARCHAR(500),
                    image_url VARCHAR(500),
                    title VARCHAR(200),
                    description TEXT,
                    category VARCHAR(50),
                    metal_type VARCHAR(30),
                    karat VARCHAR(10),
                    price_range_min FLOAT,
                    price_range_max FLOAT,
                    style_tags JSON DEFAULT '[]',
                    trending_score FLOAT DEFAULT 0,
                    scraped_at TIMESTAMP DEFAULT NOW(),
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """))

            # Create indexes
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_design_category_score ON designs(category, trending_score)
            """))
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_design_source ON designs(source)
            """))

            # Create user_design_preferences table
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS user_design_preferences (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    design_id INTEGER REFERENCES designs(id) ON DELETE CASCADE,
                    action VARCHAR(20) NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """))

            # Create lookbooks table
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS lookbooks (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    name VARCHAR(100) NOT NULL,
                    design_ids JSON DEFAULT '[]',
                    pdf_url VARCHAR(500),
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """))

            logger.info("Trend Scout migration complete")

        return {"status": "success", "message": "Trend Scout tables created"}

    except Exception as e:
        import traceback
        return {"status": "error", "error": str(e), "detail": traceback.format_exc()}


@app.post("/admin/fix-designs-schema")
async def fix_designs_schema():
    """Fix designs table schema - add missing columns."""
    from sqlalchemy import text
    from app.database import engine

    try:
        async with engine.begin() as conn:
            # Add created_at if missing
            await conn.execute(text("""
                ALTER TABLE designs ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW()
            """))
            logger.info("Fixed designs schema")

        return {"status": "success", "message": "Designs schema fixed"}

    except Exception as e:
        import traceback
        return {"status": "error", "error": str(e), "detail": traceback.format_exc()}


@app.post("/admin/scrape-designs")
async def admin_scrape_designs(db: AsyncSession = Depends(get_db)):
    """Manually trigger design scraping."""
    try:
        # scrape_all handles its own commits/rollbacks per scraper
        results = await scraper_service.scrape_all(db)
        return {"status": "success", "results": results}
    except Exception as e:
        import traceback
        return {"status": "error", "error": str(e), "detail": traceback.format_exc()}


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


@app.post("/admin/test-conversation/{phone}")
async def test_conversation(phone: str, db: AsyncSession = Depends(get_db)):
    """Test conversation storage (Phase 1 debug)."""
    from sqlalchemy import select

    try:
        # Find user
        result = await db.execute(select(User).where(User.phone_number == phone))
        user = result.scalar_one_or_none()
        if not user:
            return {"error": "User not found", "phone": phone}

        # Try to store a test conversation
        await store_conversation(db, user.id, "user", "test message for debugging")
        await db.commit()

        # Verify it was stored
        result = await db.execute(
            select(Conversation).where(Conversation.user_id == user.id).order_by(Conversation.id.desc()).limit(1)
        )
        conv = result.scalar_one_or_none()

        if conv:
            return {
                "status": "success",
                "conversation": {
                    "id": conv.id,
                    "content": conv.content,
                    "intent": conv.intent,
                    "entities": conv.entities,
                    "sentiment": conv.sentiment
                }
            }
        else:
            return {"status": "failed", "message": "Conversation not found after insert"}

    except Exception as e:
        import traceback
        return {"status": "error", "error": str(e), "trace": traceback.format_exc()}


@app.get("/admin/conversations/{phone}")
async def get_conversations(phone: str, limit: int = 10, db: AsyncSession = Depends(get_db)):
    """View recent conversations with intelligence data (Phase 1)."""
    from sqlalchemy import select, desc

    # Find user
    result = await db.execute(select(User).where(User.phone_number == phone))
    user = result.scalar_one_or_none()
    if not user:
        return {"error": "User not found"}

    # Get conversations
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(desc(Conversation.created_at))
        .limit(limit)
    )
    convs = result.scalars().all()

    return {
        "user": {"phone": user.phone_number, "name": user.name},
        "count": len(convs),
        "conversations": [
            {
                "role": c.role,
                "content": c.content[:100] + "..." if len(c.content) > 100 else c.content,
                "intent": c.intent,
                "entities": c.entities,
                "sentiment": c.sentiment,
                "created_at": str(c.created_at)
            }
            for c in reversed(convs)
        ]
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
