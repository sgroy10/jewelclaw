"""
JewelClaw - FastAPI Application
WhatsApp bot for Indian jewelry industry with gold, silver, and platinum rates.
"""

import logging
from datetime import timedelta
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func

from app.config import settings
from app.database import init_db, close_db, get_db, reset_db
# Import models to ensure they're registered with Base.metadata
from app.models import User, Conversation, MetalRate, Design, UserDesignPreference, Lookbook, PriceHistory, Alert, TrendReport, BusinessMemory, ConversationSummary
from app.services.whatsapp_service import whatsapp_service
from app.services.agent_service import agent_service
from app.services.gold_service import metal_service
from app.services.scheduler_service import scheduler_service
from app.services.memory_service import memory_service
from app.services.scraper_service import scraper_service
from app.services.playwright_scraper import playwright_scraper, PLAYWRIGHT_AVAILABLE
from app.services.image_service import image_service
from app.services.api_scraper import api_scraper
from app.services.price_tracker import price_tracker
from app.services.alerts_service import alerts_service
from app.services.lookbook_service import lookbook_service

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

    # Configure Cloudinary if credentials available
    if settings.cloudinary_cloud_name and settings.cloudinary_api_key:
        image_service.configure(
            settings.cloudinary_cloud_name,
            settings.cloudinary_api_key,
            settings.cloudinary_api_secret
        )
        logger.info("Cloudinary configured")

    # Start Playwright browser if available
    if PLAYWRIGHT_AVAILABLE:
        try:
            await playwright_scraper.start()
            logger.info("Playwright browser started")
        except Exception as e:
            logger.warning(f"Playwright start failed: {e}")

    logger.info("Application started successfully")

    yield

    logger.info("Shutting down...")

    # Stop Playwright browser
    if PLAYWRIGHT_AVAILABLE and playwright_scraper.browser:
        await playwright_scraper.stop()

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

*Commands:*
• *gold* - Live gold rates + expert analysis
• *trends* - Trending jewelry designs
• *search [query]* - Live search (e.g. search bridal necklace)
• *like [id]* - Save a design to lookbook
• *lookbook* - View saved designs
• *pdf* - Generate lookbook PDF
• *alerts* - View your price drop alerts
• *subscribe* - Daily 9 AM morning brief
• *setup* - How to join JewelClaw
• *help* - Show this menu

🇮🇳 *Built for Indian Jewelers*

Type *gold* to get started!"""


# Onboarding instructions - shown when user types "setup"
ONBOARDING_GUIDE = """🏆 *JewelClaw Setup Guide*

Share these steps with anyone who wants to join:

*Step 1️⃣ Save this number*
📱 *+1 (415) 523-8886*
Save it as "JewelClaw" in contacts

*Step 2️⃣ Open WhatsApp*
Start a new chat with JewelClaw

*Step 3️⃣ Send join code*
Type and send exactly:
👉 *join third-find*

*Step 4️⃣ You're in!*
• Send *gold* - Get live rates
• Send *subscribe* - Daily 9 AM brief

━━━━━━━━━━━━━━━━━━━━━
_Forward this message to invite others!_"""


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
            # AI Agent routing (with feature flag)
            if settings.enable_ai_agent:
                classification, confidence = agent_service.classify_message(message_body)
                logger.info(f"CLASSIFY: '{classification}' (confidence={confidence})")

                if classification == "ai_conversation":
                    # AI PATH: natural language -> Claude with tools
                    logger.info("AI PATH: routing to agent_service")
                    response = await agent_service.handle_message(db, user, message_body)
                else:
                    # FAST PATH: mapped to existing command handler
                    command = whatsapp_service.parse_command(message_body)
                    if command:
                        logger.info(f"FAST PATH: command={command}")
                        response = await handle_command(db, user, command, phone_number, is_new_user, message_body)
                    else:
                        # Classifier found a fuzzy match but parse_command didn't
                        # For commands needing args (like, skip, search), use normalized message body
                        fuzzy_cmd = classification
                        if classification in ("like", "skip", "search"):
                            fuzzy_cmd = message_body.lower().strip()
                        logger.info(f"FUZZY PATH: classification={classification}, cmd={fuzzy_cmd}")
                        response = await handle_command(db, user, fuzzy_cmd, phone_number, is_new_user, message_body)
            else:
                # Legacy: original command handling
                command = whatsapp_service.parse_command(message_body)
                logger.info(f"COMMAND: {command}")
                response = await handle_command(db, user, command, phone_number, is_new_user, message_body)

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


async def handle_command(db: AsyncSession, user, command: str, phone_number: str, is_new_user: bool = False, message_body: str = "") -> str:
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

    # 6. SETUP → Show onboarding guide
    if command == "setup":
        return ONBOARDING_GUIDE

    # ==========================================================================
    # TREND SCOUT COMMANDS
    # ==========================================================================

    # TRENDS MENU → Show category menu
    if command in ["trends", "trending"]:
        return await handle_trends_command(db, user, phone_number)

    # TREND MENU OPTIONS (1-6)
    if command == "1" or command == "fresh" or command == "today":
        return await handle_fresh_picks_command(db, user, phone_number)

    if command == "2":
        return await handle_category_command(db, user, "bridal", phone_number)

    if command == "3":
        return await handle_category_command(db, user, "dailywear", phone_number)

    if command == "4":
        return await handle_price_drops_command(db, user, phone_number)

    if command == "5":
        return await handle_new_arrivals_command(db, user, phone_number)

    if command == "6" or command == "news":
        return await handle_industry_news_command(db, user, phone_number)

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

    # 14. SEARCH → Live search via Playwright
    if command == "search":
        # Extract search query from the message
        import re
        match = re.search(r'(?:search|find)\s+(.+)', message_body.lower())
        if match:
            query = match.group(1).strip()
            return await handle_search_command(db, user, query, phone_number)
        return "Usage: search [query]\nExample: search bridal necklace"

    # 15. PDF → Generate lookbook PDF
    if command in ["pdf", "lookbook pdf", "create pdf"]:
        return await handle_pdf_command(db, user, phone_number)

    # 16. ALERTS → Show user alerts
    if command == "alerts":
        return await handle_alerts_command(db, user, phone_number)

    # 17. CREATE LOOKBOOK → Create a new lookbook
    if command == "create lookbook" or command.startswith("create lookbook"):
        import re
        match = re.search(r'create lookbook\s*(.*)', message_body.lower())
        name = match.group(1).strip() if match else None
        return await handle_create_lookbook_command(db, user, name)

    # Unknown command → Route through AI if enabled, else show welcome
    if settings.enable_ai_agent:
        try:
            return await agent_service.handle_message(db, user, message_body)
        except Exception as e:
            logger.error(f"AI fallback error: {e}")
            return WELCOME_MESSAGE
    return WELCOME_MESSAGE


async def handle_trends_command(db: AsyncSession, user, phone_number: str) -> str:
    """Handle trends command - show menu for trend categories."""
    return """🔥 *JewelClaw Trend Intelligence*

Choose what you want to see:

1️⃣ *Today's Fresh Picks* - 10 new designs
2️⃣ *Bridal Collection* - Wedding jewelry
3️⃣ *Daily Wear* - Lightweight designs
4️⃣ *Price Drops* - Discounted items
5️⃣ *New Arrivals* - Just launched
6️⃣ *Industry News* - Market updates

_Reply with number (1-6) to see_

Or type: *bridal*, *dailywear*, *temple*"""


async def handle_fresh_picks_command(db: AsyncSession, user, phone_number: str) -> str:
    """Handle fresh picks - show today's fresh designs with images."""
    from datetime import datetime, timedelta

    # Get designs added in last 24 hours, or most recent if none
    yesterday = datetime.utcnow() - timedelta(days=1)

    result = await db.execute(
        select(Design)
        .where(Design.image_url.like('%cloudinary%'))  # Only Cloudinary images
        .order_by(desc(Design.id))  # Newest first
        .limit(10)
    )
    designs = result.scalars().all()

    if not designs:
        return """🔥 *Today's Fresh Picks*

No fresh designs yet. Scraping new content...

_Check back in a few minutes!_"""

    # Send header
    await whatsapp_service.send_message(phone_number, f"🔥 *Today's Fresh Picks*\n_{len(designs)} designs_")

    # Send each design with image
    for i, d in enumerate(designs, 1):
        price_text = f"₹{d.price_range_min:,.0f}" if d.price_range_min else "Price on request"
        caption = f"*{i}. {d.title[:50]}*\n{d.category or 'General'} | {price_text}\n_Source: {d.source}_\n\nReply 'like {d.id}' to save"

        if d.image_url:
            await whatsapp_service.send_message(phone_number, caption, media_url=d.image_url)
        else:
            await whatsapp_service.send_message(phone_number, caption)

    return "_Reply 'trends' for more categories | 'lookbook' to see saved_"


async def handle_price_drops_command(db: AsyncSession, user, phone_number: str) -> str:
    """Handle price drops - show designs with price reductions."""
    # Get designs with prices, sorted by price (showing lower priced = value deals)
    result = await db.execute(
        select(Design)
        .where(Design.price_range_min.isnot(None))
        .where(Design.image_url.like('%cloudinary%'))
        .order_by(Design.price_range_min)
        .limit(10)
    )
    designs = result.scalars().all()

    if not designs:
        return """💰 *Price Drops*

No price data available yet.

_We're tracking prices - you'll be notified when items drop!_"""

    await whatsapp_service.send_message(phone_number, "💰 *Best Value Picks*\n_Affordable designs for you_")

    for i, d in enumerate(designs[:5], 1):
        price_text = f"₹{d.price_range_min:,.0f}"
        caption = f"*{i}. {d.title[:50]}*\n💰 {price_text}\n_Source: {d.source}_\n\nReply 'like {d.id}' to save"

        if d.image_url:
            await whatsapp_service.send_message(phone_number, caption, media_url=d.image_url)

    return "_Reply 'trends' for more | 'alerts' to get price drop notifications_"


async def handle_new_arrivals_command(db: AsyncSession, user, phone_number: str) -> str:
    """Handle new arrivals - show recently added designs."""
    result = await db.execute(
        select(Design)
        .where(Design.image_url.like('%cloudinary%'))
        .order_by(desc(Design.created_at))
        .limit(10)
    )
    designs = result.scalars().all()

    if not designs:
        return """✨ *New Arrivals*

No new arrivals yet. Check back soon!"""

    await whatsapp_service.send_message(phone_number, "✨ *New Arrivals*\n_Just added to our collection_")

    for i, d in enumerate(designs[:5], 1):
        price_text = f"₹{d.price_range_min:,.0f}" if d.price_range_min else "Price on request"
        caption = f"*{i}. {d.title[:50]}*\n{d.category or 'New'} | {price_text}\n_Source: {d.source}_\n\nReply 'like {d.id}' to save"

        if d.image_url:
            await whatsapp_service.send_message(phone_number, caption, media_url=d.image_url)

    return "_Reply 'trends' for more categories_"


async def handle_industry_news_command(db: AsyncSession, user, phone_number: str) -> str:
    """Handle industry news - show market updates and news."""
    # For now, return curated industry insights
    # TODO: Add actual news scraping from Google News, ET, etc.

    from datetime import datetime

    today = datetime.now().strftime("%d %b %Y")

    return f"""📰 *Jewelry Industry News*
_{today}_

*Gold Market:*
Send 'gold' for live rates and analysis

*Trending Styles:*
• Minimalist designs gaining popularity
• Temple jewelry seeing revival
• Layered necklaces trending globally

*Market Updates:*
• Wedding season demand rising
• Lab-grown diamonds market growing
• Sustainable jewelry gaining traction

*Coming Soon:*
• Real-time news alerts
• Competitor collection updates
• Price trend analysis

_Reply 'gold' for rates | 'trends' for designs_"""


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

        # Convert via Cloudinary (webp -> jpg for Twilio)
        if d.image_url:
            cloudinary_url = await image_service.upload_from_url(d.image_url, d.source)
            await whatsapp_service.send_message(phone_number, caption, media_url=cloudinary_url)
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


async def handle_pdf_command(db: AsyncSession, user, phone_number: str) -> str:
    """Handle PDF generation command."""
    await whatsapp_service.send_message(phone_number, "📄 *Generating your lookbook PDF...*")

    try:
        # Generate PDF
        pdf_bytes = await lookbook_service.generate_pdf(db, user.id)

        if not pdf_bytes:
            # Try simple version
            pdf_bytes = await lookbook_service.generate_simple_pdf(db, user.id)

        if not pdf_bytes:
            return """📚 *No saved designs found*

Save designs first with 'like [id]'
Then use 'pdf' to generate your lookbook."""

        # For now, we'll save locally and provide instructions
        # In production, upload to Cloudinary or S3 and send via WhatsApp
        import os
        from datetime import datetime

        # Save to temp file
        filename = f"lookbook_{user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        filepath = os.path.join(os.getcwd(), "temp", filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        with open(filepath, "wb") as f:
            f.write(pdf_bytes)

        logger.info(f"Generated PDF: {filepath}")

        return f"""✅ *Lookbook PDF Generated!*

Your lookbook has been created with all your saved designs.

_PDF generation successful! File ready for download._

Reply 'trends' to discover more designs."""

    except Exception as e:
        logger.error(f"PDF generation error: {e}")
        return f"PDF generation failed: {str(e)[:50]}\n\nTry 'lookbook' to view your saved designs."


async def handle_alerts_command(db: AsyncSession, user, phone_number: str) -> str:
    """Handle alerts command - show user's pending alerts."""
    alerts = await alerts_service.get_pending_alerts(db, user.id, limit=5)

    if not alerts:
        return """🔔 *Your Alerts*

No new alerts!

_You'll be notified when:_
• Prices drop on saved designs
• New arrivals in your favorite categories
• Designs start trending"""

    # Send header
    await whatsapp_service.send_message(phone_number, f"🔔 *You have {len(alerts)} alerts*")

    # Send each alert
    for alert in alerts:
        msg = alerts_service.format_alert_message(alert)

        # Send with image if available
        image_url = None
        if alert.extra_data and alert.extra_data.get("image_url"):
            image_url = await image_service.upload_from_url(
                alert.extra_data["image_url"],
                "alert"
            )

        await whatsapp_service.send_message(phone_number, msg, media_url=image_url)

        # Mark as sent
        await alerts_service.mark_alert_sent(db, alert.id)

    await db.commit()
    return "_Reply 'trends' for more designs_"


async def handle_create_lookbook_command(db: AsyncSession, user, name: str = None) -> str:
    """Handle create lookbook command."""
    try:
        lookbook = await lookbook_service.create_lookbook(db, user.id, name)

        design_count = len(lookbook.design_ids) if lookbook.design_ids else 0

        return f"""✅ *Lookbook Created!*

*{lookbook.name}*
{design_count} designs saved

_Reply 'pdf' to generate a PDF of this lookbook_"""

    except Exception as e:
        logger.error(f"Create lookbook error: {e}")
        return "Failed to create lookbook. Try again later."


async def handle_search_command(db: AsyncSession, user, query: str, phone_number: str) -> str:
    """Handle live search command using API scraper."""
    # Send initial message
    await whatsapp_service.send_message(phone_number, f"🔍 *Searching for '{query}'...*\n\n_Scraping BlueStone, CaratLane, Tanishq..._")

    try:
        # Run live search across all sites using API scraper
        designs = await api_scraper.search(query, limit_per_site=5)

        if not designs:
            return f"No designs found for '{query}'.\n\nTry different keywords like 'bridal necklace' or 'daily wear earrings'."

        # Save designs to database for future reference
        for design in designs[:10]:
            existing = await db.execute(
                select(Design).where(Design.source_url == design.source_url)
            )
            if not existing.scalar_one_or_none():
                db_design = Design(
                    source=design.source,
                    source_url=design.source_url,
                    image_url=design.image_url,
                    title=design.title,
                    category=design.category,
                    metal_type=design.metal_type,
                    price_range_min=design.price,
                    trending_score=70  # High score for live search results
                )
                db.add(db_design)

        await db.commit()

        # Send each design with image
        for i, design in enumerate(designs[:5], 1):
            price_text = f"₹{design.price:,.0f}" if design.price else "Price N/A"
            caption = f"*{i}. {design.title[:50]}*\n{design.category} | {price_text}\n_Source: {design.source}_"

            if design.image_url:
                # Upload to Cloudinary for reliable delivery
                cloudinary_url = await image_service.upload_from_url(design.image_url, design.source)
                await whatsapp_service.send_message(phone_number, caption, media_url=cloudinary_url)
            else:
                await whatsapp_service.send_message(phone_number, caption)

        return f"_Found {len(designs)} designs for '{query}'_"

    except Exception as e:
        logger.error(f"Search error: {e}")
        return f"Search failed: {str(e)[:100]}\n\nTry 'trends' for cached designs."


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


@app.post("/admin/migrate-openclaw")
async def migrate_openclaw():
    """Create OpenClaw tables (price_history, alerts, trend_reports)."""
    from sqlalchemy import text
    from app.database import engine

    try:
        async with engine.begin() as conn:
            # Create price_history table
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS price_history (
                    id SERIAL PRIMARY KEY,
                    design_id INTEGER REFERENCES designs(id) ON DELETE CASCADE,
                    price FLOAT NOT NULL,
                    recorded_at TIMESTAMP DEFAULT NOW()
                )
            """))

            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_price_history_design_time
                ON price_history(design_id, recorded_at)
            """))

            # Create alerts table
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS alerts (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    alert_type VARCHAR(50) NOT NULL,
                    title VARCHAR(200) NOT NULL,
                    message TEXT,
                    design_id INTEGER REFERENCES designs(id) ON DELETE SET NULL,
                    extra_data JSON DEFAULT '{}',
                    is_sent BOOLEAN DEFAULT FALSE,
                    sent_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """))

            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_alert_user_sent
                ON alerts(user_id, is_sent)
            """))

            # Create trend_reports table
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS trend_reports (
                    id SERIAL PRIMARY KEY,
                    report_type VARCHAR(50) NOT NULL,
                    report_date TIMESTAMP NOT NULL,
                    top_categories JSON DEFAULT '[]',
                    top_designs JSON DEFAULT '[]',
                    price_trends JSON DEFAULT '{}',
                    new_arrivals_count INTEGER DEFAULT 0,
                    source_stats JSON DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """))

            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_trend_report_date
                ON trend_reports(report_date)
            """))

            logger.info("OpenClaw migration complete")

        return {"status": "success", "message": "OpenClaw tables created (price_history, alerts, trend_reports)"}

    except Exception as e:
        import traceback
        return {"status": "error", "error": str(e), "detail": traceback.format_exc()}


@app.post("/admin/migrate-ai-agent")
async def migrate_ai_agent():
    """Create AI Agent tables (business_memories, conversation_summaries) and extend users."""
    from sqlalchemy import text
    from app.database import engine

    try:
        async with engine.begin() as conn:
            # Extend users table with AI agent columns
            for col_sql in [
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS business_type VARCHAR(50)",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS primary_metals JSON",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS primary_categories JSON",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS gold_buy_threshold FLOAT",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS gold_sell_threshold FLOAT",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS ai_personality_notes TEXT",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_completed BOOLEAN DEFAULT FALSE",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS total_ai_interactions INTEGER DEFAULT 0",
            ]:
                await conn.execute(text(col_sql))

            # Create business_memories table
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS business_memories (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    category VARCHAR(50) NOT NULL,
                    key VARCHAR(200) NOT NULL,
                    value TEXT NOT NULL,
                    value_numeric FLOAT,
                    metal_type VARCHAR(30),
                    jewelry_category VARCHAR(50),
                    confidence FLOAT DEFAULT 1.0,
                    source_message_id INTEGER,
                    extracted_at TIMESTAMP DEFAULT NOW(),
                    last_referenced_at TIMESTAMP,
                    is_active BOOLEAN DEFAULT TRUE
                )
            """))

            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_business_memory_user_category
                ON business_memories(user_id, category)
            """))
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_business_memory_user_key
                ON business_memories(user_id, key)
            """))

            # Create conversation_summaries table
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS conversation_summaries (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    summary_text TEXT NOT NULL,
                    messages_covered INTEGER DEFAULT 0,
                    oldest_message_id INTEGER,
                    newest_message_id INTEGER,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """))

            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_convsummary_user
                ON conversation_summaries(user_id)
            """))

            logger.info("AI Agent migration complete")

        return {"status": "success", "message": "AI Agent tables created (business_memories, conversation_summaries, user columns extended)"}

    except Exception as e:
        import traceback
        return {"status": "error", "error": str(e), "detail": traceback.format_exc()}


@app.get("/admin/designs")
async def get_designs(limit: int = 10, db: AsyncSession = Depends(get_db)):
    """View designs in database."""
    from sqlalchemy import select, desc
    result = await db.execute(
        select(Design).order_by(desc(Design.trending_score)).limit(limit)
    )
    designs = result.scalars().all()
    return {
        "count": len(designs),
        "designs": [
            {
                "id": d.id,
                "title": d.title,
                "source": d.source,
                "category": d.category,
                "price": d.price_range_min,
                "image_url": d.image_url,
                "has_image": bool(d.image_url)
            }
            for d in designs
        ]
    }


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


@app.post("/admin/boost-images")
async def boost_images():
    """Boost trending_score for designs with Cloudinary images (JPG) so they show first."""
    from sqlalchemy import text
    from app.database import engine

    try:
        async with engine.begin() as conn:
            # Boost designs with Cloudinary URLs (JPG format) to 80
            await conn.execute(text("""
                UPDATE designs SET trending_score = 80 WHERE image_url LIKE '%cloudinary%'
            """))
            # Designs with original URLs (webp) get 50
            await conn.execute(text("""
                UPDATE designs SET trending_score = 50 WHERE image_url NOT LIKE '%cloudinary%' AND image_url IS NOT NULL
            """))
            # Designs without images get 30
            await conn.execute(text("""
                UPDATE designs SET trending_score = 30 WHERE image_url IS NULL
            """))
            logger.info("Boosted Cloudinary designs to top")

        return {"status": "success", "message": "Cloudinary (JPG) designs boosted to top"}

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


@app.get("/onboarding")
async def get_onboarding():
    """Get the onboarding guide text for sharing."""
    return {
        "guide": ONBOARDING_GUIDE,
        "phone": "+1 (415) 523-8886",
        "join_code": "join third-find",
        "steps": [
            "1. Save +1 (415) 523-8886 as 'JewelClaw' in contacts",
            "2. Open WhatsApp and start chat with JewelClaw",
            "3. Send: join third-find",
            "4. Send: gold (to test)",
            "5. Send: subscribe (for daily 9 AM brief)"
        ]
    }


@app.get("/admin/send-onboarding/{phone}")
async def send_onboarding(phone: str):
    """Send onboarding guide to a phone number."""
    try:
        result = await whatsapp_service.send_message(
            f"whatsapp:{phone}",
            ONBOARDING_GUIDE
        )
        return {"status": "sent" if result else "failed", "phone": phone}
    except Exception as e:
        return {"status": "error", "error": str(e)}


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


@app.get("/admin/test-image/{phone}")
async def test_image(phone: str, source: str = "unsplash"):
    """Test sending an image via Twilio with Cloudinary conversion."""
    try:
        from twilio.rest import Client
        client = Client(settings.twilio_account_sid, settings.twilio_auth_token)

        # Test different image sources
        if source == "bluestone":
            original_url = "https://kinclimg0.bluestone.com/f_webp,c_scale,w_418,b_rgb:f0f0f0/giproduct/BISN0672N04_YAA18DIG6XXXXXXXX_ABCD00-PICS-00003-1024-49416.png"
            caption = "🔥 BlueStone Test\n\nThe Cursive A Necklace\n₹50,989"
        else:
            original_url = "https://images.unsplash.com/photo-1515562141207-7a88fb7ce338?w=400"
            caption = "🔥 Unsplash Test\n\nGold Jewelry Test"

        # Convert via Cloudinary (webp -> jpg for Twilio compatibility)
        cloudinary_url = await image_service.upload_from_url(original_url, source)

        msg = client.messages.create(
            body=caption,
            from_=settings.twilio_whatsapp_number,
            to=f"whatsapp:{phone}",
            media_url=[cloudinary_url]
        )

        return {
            "status": "sent",
            "phone": phone,
            "source": source,
            "original_url": original_url,
            "cloudinary_url": cloudinary_url,
            "twilio_sid": msg.sid,
            "twilio_status": msg.status,
            "num_media": msg.num_media
        }
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
        response = await handle_command(db, user, command, f"whatsapp:{phone}", is_new, "gold")
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


@app.get("/admin/scraper/status")
async def scraper_status():
    """Check all scraper statuses."""
    import os
    all_env_keys = [k for k in os.environ.keys() if 'SCRAPER' in k or 'API' in k]
    return {
        "api_scraper_configured": api_scraper.configured,
        "api_key_from_settings": bool(settings.scraper_api_key),
        "api_key_from_env": bool(os.environ.get("SCRAPER_API_KEY")),
        "env_keys_with_api_or_scraper": all_env_keys,
        "cloudinary_configured": image_service.configured,
        "playwright_available": PLAYWRIGHT_AVAILABLE,
    }


@app.post("/admin/scraper/test/{source}")
async def test_api_scraper(source: str, category: str = "necklaces", limit: int = 10, api_key: str = None):
    """Test API scraper for a specific source. Pass api_key param to override."""
    try:
        # Allow API key override for testing
        if api_key:
            import os
            os.environ["SCRAPER_API_KEY"] = api_key

        if source == "bluestone":
            designs = await api_scraper.scrape_bluestone(category=category, limit=limit)
        elif source == "caratlane":
            designs = await api_scraper.scrape_caratlane(category=category, limit=limit)
        elif source == "tanishq":
            designs = await api_scraper.scrape_tanishq(category=category, limit=limit)
        elif source == "all":
            designs = await api_scraper.scrape_all(category=category, limit_per_site=limit)
        else:
            return {"error": f"Unknown source: {source}. Use: bluestone, caratlane, tanishq, all"}

        return {
            "source": source,
            "api_configured": api_scraper.configured,
            "api_key_length": len(api_scraper.api_key) if api_scraper.api_key else 0,
            "count": len(designs),
            "designs": [
                {
                    "title": d.title,
                    "price": d.price,
                    "image_url": d.image_url[:100] + "..." if d.image_url and len(d.image_url) > 100 else d.image_url,
                    "source": d.source,
                    "category": d.category
                }
                for d in designs[:10]  # Limit output
            ]
        }
    except Exception as e:
        import traceback
        return {"error": str(e), "trace": traceback.format_exc()}


@app.post("/admin/scraper/save-designs")
async def scraper_save_designs(category: str = "necklaces", db: AsyncSession = Depends(get_db)):
    """Scrape designs and save to database with Cloudinary images."""
    try:
        designs = await api_scraper.scrape_all(category=category, limit_per_site=10)

        saved_count = 0
        skipped_count = 0

        for design in designs:
            # Check if exists
            existing = await db.execute(
                select(Design).where(Design.source_url == design.source_url)
            )
            if existing.scalar_one_or_none():
                skipped_count += 1
                continue

            # Upload image to Cloudinary
            cloudinary_url = design.image_url
            if image_service.configured and design.image_url:
                cloudinary_url = await image_service.upload_from_url(design.image_url, design.source)

            # Save to database
            db_design = Design(
                source=design.source,
                source_url=design.source_url,
                image_url=cloudinary_url,
                title=design.title,
                category=design.category,
                metal_type=design.metal_type,
                price_range_min=design.price,
                trending_score=70
            )
            db.add(db_design)
            saved_count += 1

        await db.commit()

        return {
            "status": "success",
            "scraped": len(designs),
            "saved": saved_count,
            "skipped_duplicates": skipped_count
        }

    except Exception as e:
        import traceback
        return {"error": str(e), "trace": traceback.format_exc()}


@app.post("/admin/scraper/debug")
async def debug_scraper(url: str = "https://www.bluestone.com/jewellery/gold-necklaces.html", api_key: str = None):
    """Debug: See raw ScraperAPI response."""
    import os
    import re
    if api_key:
        os.environ["SCRAPER_API_KEY"] = api_key

    try:
        html = await api_scraper.fetch_rendered_page(url, render_js=True)
        if html:
            # Count LD+JSON blocks
            ld_json_count = html.lower().count('application/ld+json')
            # Find product cards
            product_card_count = len(re.findall(r'data-product-id', html, re.I))
            plp_card_count = len(re.findall(r'plp-card|plp-prod', html, re.I))
            # Look for image URLs
            image_urls = re.findall(r'https://[^"]*bluestone[^"]*\.(jpg|png|webp)', html, re.I)

            # Find a product section
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'lxml')

            # Find LD+JSON content
            ld_json_content = None
            for script in soup.find_all('script', type='application/ld+json'):
                try:
                    ld_json_content = script.string[:500] if script.string else None
                    break
                except:
                    pass

            # Search for actual product elements with images
            # BlueStone uses kinclimg for product images
            img_tags = soup.select('img[src*="kinclimg"], img[data-src*="kinclimg"]')[:5]
            img_parents = []
            for img in img_tags:
                parent = img.find_parent('div')
                if parent:
                    img_parents.append(str(parent)[:400])

            # Look for anchor links to product pages
            product_links = soup.select('a[href*="/jewellery/"][href*=".html"]')[:5]
            link_samples = [(a.get('href', ''), a.get('title', ''), a.get_text(strip=True)[:50]) for a in product_links]

            return {
                "status": "success",
                "html_length": len(html),
                "ld_json_preview": ld_json_content[:200] if ld_json_content else None,
                "product_images": len(soup.select('img[src*="kinclimg"]')),
                "img_parent_samples": img_parents[:2],
                "product_links": link_samples,
            }
        else:
            return {"status": "failed", "html": None}
    except Exception as e:
        import traceback
        return {"error": str(e), "trace": traceback.format_exc()}


@app.get("/admin/playwright/status")
async def playwright_status():
    """Check Playwright scraper status (legacy endpoint)."""
    return {
        "playwright_available": PLAYWRIGHT_AVAILABLE,
        "browser_running": playwright_scraper.browser is not None if PLAYWRIGHT_AVAILABLE else False,
        "cloudinary_configured": image_service.configured
    }


@app.post("/admin/playwright/scrape/{source}")
async def playwright_scrape(source: str, query: str = None, category: str = None, limit: int = 10):
    """Test Playwright scraper for a specific source."""
    if not PLAYWRIGHT_AVAILABLE:
        return {"error": "Playwright not installed"}

    if not playwright_scraper.browser:
        try:
            await playwright_scraper.start()
        except Exception as e:
            return {"error": f"Failed to start browser: {e}"}

    try:
        if source == "bluestone":
            designs = await playwright_scraper.scrape_bluestone(query=query, category=category, limit=limit)
        elif source == "caratlane":
            designs = await playwright_scraper.scrape_caratlane(query=query, category=category, limit=limit)
        elif source == "tanishq":
            designs = await playwright_scraper.scrape_tanishq(query=query, category=category, limit=limit)
        elif source == "all":
            if query:
                designs = await playwright_scraper.search_all(query, limit_per_site=limit)
            else:
                designs = await playwright_scraper.scrape_category(category or "necklaces", limit_per_site=limit)
        else:
            return {"error": f"Unknown source: {source}. Use: bluestone, caratlane, tanishq, all"}

        return {
            "source": source,
            "count": len(designs),
            "designs": [
                {
                    "title": d.title,
                    "price": d.price,
                    "image_url": d.image_url,
                    "source_url": d.source_url,
                    "category": d.category
                }
                for d in designs
            ]
        }

    except Exception as e:
        import traceback
        return {"error": str(e), "trace": traceback.format_exc()}


@app.post("/admin/playwright/save-to-db")
async def playwright_save_to_db(query: str = None, category: str = "necklaces", db: AsyncSession = Depends(get_db)):
    """Scrape with Playwright and save to database."""
    if not PLAYWRIGHT_AVAILABLE:
        return {"error": "Playwright not installed"}

    if not playwright_scraper.browser:
        try:
            await playwright_scraper.start()
        except Exception as e:
            return {"error": f"Failed to start browser: {e}"}

    try:
        # Scrape designs
        if query:
            designs = await playwright_scraper.search_all(query, limit_per_site=10)
        else:
            designs = await playwright_scraper.scrape_category(category, limit_per_site=10)

        saved_count = 0
        skipped_count = 0

        for design in designs:
            # Check if already exists
            existing = await db.execute(
                select(Design).where(Design.source_url == design.source_url)
            )
            if existing.scalar_one_or_none():
                skipped_count += 1
                continue

            # Upload image to Cloudinary if configured
            cloudinary_url = None
            if image_service.configured and design.image_url:
                cloudinary_url = await image_service.upload_from_url(design.image_url, design.source)

            # Save to database
            db_design = Design(
                source=design.source,
                source_url=design.source_url,
                image_url=cloudinary_url or design.image_url,
                title=design.title,
                category=design.category,
                metal_type=design.metal_type,
                price_range_min=design.price,
                trending_score=70  # High score for Playwright scraped designs
            )
            db.add(db_design)
            saved_count += 1

        await db.commit()

        return {
            "status": "success",
            "scraped": len(designs),
            "saved": saved_count,
            "skipped_duplicates": skipped_count
        }

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


# =============================================================================
# OPENCLAW ENDPOINTS - Price Tracking, Alerts, Intelligence
# =============================================================================

@app.post("/admin/track-prices")
async def track_prices(db: AsyncSession = Depends(get_db)):
    """Record current prices for all designs and detect changes."""
    try:
        # Get all designs with prices
        result = await db.execute(
            select(Design).where(Design.price_range_min.isnot(None))
        )
        designs = result.scalars().all()

        # Record prices and detect changes
        changes = await price_tracker.record_all_prices(db, designs)

        # Generate alerts for price drops
        if changes:
            drop_changes = [c for c in changes if c.is_drop]
            if drop_changes:
                alert_count = await alerts_service.generate_price_drop_alerts(db, drop_changes)
            else:
                alert_count = 0
        else:
            alert_count = 0

        return {
            "status": "success",
            "designs_tracked": len(designs),
            "price_changes_detected": len(changes),
            "alerts_created": alert_count,
            "changes": [
                {
                    "design_id": c.design_id,
                    "title": c.title,
                    "old_price": c.old_price,
                    "new_price": c.new_price,
                    "change_percent": round(c.change_percent, 1),
                    "is_drop": c.is_drop
                }
                for c in changes
            ]
        }

    except Exception as e:
        import traceback
        return {"status": "error", "error": str(e), "detail": traceback.format_exc()}


@app.get("/admin/price-drops")
async def get_price_drops(
    min_drop: float = 5,
    days: int = 7,
    limit: int = 20,
    db: AsyncSession = Depends(get_db)
):
    """Get designs with recent price drops."""
    try:
        drops = await price_tracker.get_price_drops(
            db,
            min_drop_percent=min_drop,
            days=days,
            limit=limit
        )
        return {
            "count": len(drops),
            "min_drop_percent": min_drop,
            "period_days": days,
            "drops": drops
        }
    except Exception as e:
        import traceback
        return {"status": "error", "error": str(e), "detail": traceback.format_exc()}


@app.get("/admin/price-trends")
async def get_price_trends(days: int = 7, db: AsyncSession = Depends(get_db)):
    """Get overall price trends by category."""
    try:
        trends = await price_tracker.get_price_trends(db, days=days)
        return trends
    except Exception as e:
        import traceback
        return {"status": "error", "error": str(e), "detail": traceback.format_exc()}


@app.get("/admin/alerts/{phone}")
async def get_user_alerts(phone: str, db: AsyncSession = Depends(get_db)):
    """Get pending alerts for a user."""
    try:
        # Find user
        result = await db.execute(select(User).where(User.phone_number == phone))
        user = result.scalar_one_or_none()
        if not user:
            return {"error": "User not found"}

        # Get alerts
        alerts = await alerts_service.get_pending_alerts(db, user.id, limit=20)
        summary = await alerts_service.get_alert_summary(db, user.id)

        return {
            "user": {"phone": user.phone_number, "name": user.name},
            "summary": summary,
            "alerts": [
                {
                    "id": a.id,
                    "type": a.alert_type,
                    "title": a.title,
                    "message": a.message,
                    "design_id": a.design_id,
                    "is_sent": a.is_sent,
                    "created_at": str(a.created_at)
                }
                for a in alerts
            ]
        }
    except Exception as e:
        import traceback
        return {"status": "error", "error": str(e), "detail": traceback.format_exc()}


@app.post("/admin/send-alerts/{phone}")
async def send_user_alerts(phone: str, db: AsyncSession = Depends(get_db)):
    """Send all pending alerts to a user via WhatsApp."""
    try:
        # Find user
        result = await db.execute(select(User).where(User.phone_number == phone))
        user = result.scalar_one_or_none()
        if not user:
            return {"error": "User not found"}

        # Get pending alerts
        alerts = await alerts_service.get_pending_alerts(db, user.id, limit=10)

        sent_count = 0
        for alert in alerts:
            msg = alerts_service.format_alert_message(alert)

            # Send with image if available
            image_url = None
            if alert.extra_data and alert.extra_data.get("image_url"):
                image_url = await image_service.upload_from_url(
                    alert.extra_data["image_url"],
                    "alert"
                )

            sent = await whatsapp_service.send_message(phone, msg, media_url=image_url)
            if sent:
                await alerts_service.mark_alert_sent(db, alert.id)
                sent_count += 1

        await db.commit()

        return {
            "status": "success",
            "alerts_sent": sent_count,
            "phone": phone
        }

    except Exception as e:
        import traceback
        return {"status": "error", "error": str(e), "detail": traceback.format_exc()}


@app.post("/admin/scrape-all-sources")
async def scrape_all_sources(
    category: str = "necklaces",
    include_pinterest: bool = True,
    db: AsyncSession = Depends(get_db)
):
    """Scrape from all sources including Pinterest."""
    try:
        if include_pinterest:
            designs = await api_scraper.scrape_all_with_pinterest(
                category=category,
                limit_per_site=10
            )
        else:
            designs = await api_scraper.scrape_all(
                category=category,
                limit_per_site=10
            )

        saved_count = 0
        skipped_count = 0

        for design in designs:
            # Check if exists
            existing = await db.execute(
                select(Design).where(Design.source_url == design.source_url)
            )
            if existing.scalar_one_or_none():
                skipped_count += 1
                continue

            # Upload image to Cloudinary
            cloudinary_url = design.image_url
            if image_service.configured and design.image_url:
                cloudinary_url = await image_service.upload_from_url(design.image_url, design.source)

            # Save to database
            db_design = Design(
                source=design.source,
                source_url=design.source_url,
                image_url=cloudinary_url,
                title=design.title,
                category=design.category,
                metal_type=design.metal_type,
                price_range_min=design.price,
                trending_score=75 if design.source == "pinterest" else 70
            )
            db.add(db_design)
            saved_count += 1

        await db.commit()

        # Record prices for new designs
        new_designs = await db.execute(
            select(Design)
            .where(Design.price_range_min.isnot(None))
            .order_by(desc(Design.id))
            .limit(saved_count)
        )
        await price_tracker.record_all_prices(db, new_designs.scalars().all())

        return {
            "status": "success",
            "category": category,
            "include_pinterest": include_pinterest,
            "scraped": len(designs),
            "saved": saved_count,
            "skipped_duplicates": skipped_count,
            "sources": list(set(d.source for d in designs))
        }

    except Exception as e:
        import traceback
        return {"status": "error", "error": str(e), "detail": traceback.format_exc()}


@app.get("/admin/generate-pdf/{phone}")
async def generate_pdf_for_user(phone: str, db: AsyncSession = Depends(get_db)):
    """Generate PDF lookbook for a user."""
    try:
        # Find user
        result = await db.execute(select(User).where(User.phone_number == phone))
        user = result.scalar_one_or_none()
        if not user:
            return {"error": "User not found"}

        # Generate PDF
        pdf_bytes = await lookbook_service.generate_pdf(db, user.id)

        if not pdf_bytes:
            pdf_bytes = await lookbook_service.generate_simple_pdf(db, user.id)

        if not pdf_bytes:
            return {"error": "No saved designs found for this user"}

        # Save to file
        import os
        from datetime import datetime
        filename = f"lookbook_{user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        filepath = os.path.join(os.getcwd(), "temp", filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        with open(filepath, "wb") as f:
            f.write(pdf_bytes)

        return {
            "status": "success",
            "user": {"phone": user.phone_number, "name": user.name},
            "pdf_path": filepath,
            "pdf_size_kb": round(len(pdf_bytes) / 1024, 1)
        }

    except Exception as e:
        import traceback
        return {"status": "error", "error": str(e), "detail": traceback.format_exc()}


@app.post("/admin/generate-trend-report")
async def generate_trend_report(db: AsyncSession = Depends(get_db)):
    """Generate daily trend report."""
    from datetime import datetime

    try:
        # Get category stats
        category_result = await db.execute(
            select(
                Design.category,
                func.count(Design.id).label("count"),
                func.avg(Design.price_range_min).label("avg_price")
            )
            .where(Design.price_range_min.isnot(None))
            .group_by(Design.category)
            .order_by(desc(func.count(Design.id)))
        )
        category_stats = category_result.all()

        top_categories = [
            {
                "category": row.category or "general",
                "count": row.count,
                "avg_price": round(row.avg_price, 0) if row.avg_price else 0
            }
            for row in category_stats[:5]
        ]

        # Get top designs
        top_result = await db.execute(
            select(Design)
            .order_by(desc(Design.trending_score))
            .limit(10)
        )
        top_designs = [
            {"design_id": d.id, "title": d.title, "score": d.trending_score}
            for d in top_result.scalars().all()
        ]

        # Get source stats
        source_result = await db.execute(
            select(
                Design.source,
                func.count(Design.id).label("count")
            )
            .group_by(Design.source)
        )
        source_stats = {row.source: row.count for row in source_result.all()}

        # Get price trends
        price_trends = await price_tracker.get_price_trends(db, days=7)

        # Count new arrivals (last 24 hours)
        yesterday = datetime.utcnow() - timedelta(days=1)
        new_result = await db.execute(
            select(func.count(Design.id))
            .where(Design.created_at >= yesterday)
        )
        new_arrivals = new_result.scalar() or 0

        # Save report
        report = TrendReport(
            report_type="daily",
            report_date=datetime.utcnow(),
            top_categories=top_categories,
            top_designs=top_designs,
            price_trends=price_trends,
            new_arrivals_count=new_arrivals,
            source_stats=source_stats
        )
        db.add(report)
        await db.commit()

        return {
            "status": "success",
            "report_id": report.id,
            "report_date": str(report.report_date),
            "top_categories": top_categories,
            "top_designs": top_designs[:5],
            "new_arrivals": new_arrivals,
            "source_stats": source_stats
        }

    except Exception as e:
        import traceback
        return {"status": "error", "error": str(e), "detail": traceback.format_exc()}


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
