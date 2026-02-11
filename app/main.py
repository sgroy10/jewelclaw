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
from app.models import User, Conversation, MetalRate, Design, UserDesignPreference, Lookbook, PriceHistory, Alert, TrendReport, BusinessMemory, ConversationSummary, Reminder, FestivalCalendar, IndustryNews, BrandSitemapEntry
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
from app.services.reminder_service import reminder_service
from app.services.pricing_engine_service import pricing_engine
from app.services.background_agent_service import background_agent

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

    # Run schema migrations for new columns on existing tables
    try:
        from sqlalchemy import text
        from app.database import engine
        async with engine.begin() as conn:
            await conn.execute(text(
                "ALTER TABLE designs ADD COLUMN IF NOT EXISTS source_type VARCHAR(30) DEFAULT 'product'"
            ))
            logger.info("Schema migration: source_type column ensured on designs")
    except Exception as e:
        logger.warning(f"Schema migration skipped: {e}")

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

# ==========================================================================
# HELP MENU & FEATURE GUIDES
# ==========================================================================

def get_help_menu(name: str = "there") -> str:
    """Main help menu with numbered features."""
    return f"""Hey *{name}*! Here's everything I can do for you:

*1.* Gold & Market Rates
*2.* Quick Quote (Instant Billing)
*3.* RemindGenie (Birthdays & Festivals)
*4.* Portfolio Tracker (Your Holdings)
*5.* Price Alerts (Buy/Sell Targets)
*6.* Trend Scout (Browse Designs)
*7.* Pricing Engine (Making Charges)
*8.* Morning Brief (Daily 9 AM Update)
*9.* Invite a Friend
*10.* About JewelClaw

_Type any number (1-10) to learn more!_
_Or just talk to me naturally - I understand Hindi & English._"""


FEATURE_GUIDES = {
    "1": """*Gold & Market Rates*

Just type *gold* and I'll show you:
- Live 24K, 22K, 18K, 14K rates per gram
- Silver & platinum rates
- Daily & weekly price changes
- Expert market analysis

You can also ask naturally:
_"What's gold at today?"_
_"Show me silver rate"_
_"Gold kitna hai aaj?"_

I update rates every 15 minutes during market hours.""",

    "2": """*Quick Quote - Instant Jewelry Bill*

Tell me what you're making - I'll generate a full bill in seconds.

*Plain gold:*
_"Quote 10g 22k necklace"_
_"Quote 5g 18k ring x3"_

*Gold + CZ:*
_"Quote 2g 18k ring 30 cz pave"_
_"Quote 5g 22k earring 50 cz bezel"_

*Gold + Diamond:*
_"Quote 3g 18k ring 0.5ct diamond GH-VS"_
_"Quote 5g pendant 20 diamonds sieve 7"_

*Gold + Gemstone:*
_"Quote 4g 18k ring 1.5ct ruby"_

Your bill includes gold + wastage + making + stones + setting + finishing + hallmark + GST.

Works in *₹ INR* or *$ USD* (for exporters).
Shows *cost price vs selling price* if you set a profit margin.

_Set up your rates first: type 'price setup'_""",

    "3": """*RemindGenie - Never Forget!*

I'll remember every birthday, anniversary & festival for you. Just tell me naturally:

_"My wife's birthday is 1 Nov"_
_"Daughter birthday 24 July"_
_"Wedding anniversary 14 Feb"_
_"Tej birthday my friend 27 Aug"_
_"Mom birthday 15 March"_

Or use the format:
*remind add* Mom | Mother | 15 March

What I'll do:
- Send a greeting at *12:01 AM* on the day
- Remind you again at *8:00 AM* with a ready-to-send message
- Suggest gift ideas from trending jewelry!

*More commands:*
- *remind list* - See all your reminders
- *remind festivals* - Load 30+ Indian festivals (Diwali, Holi, Raksha Bandhan...)
- *remind delete [id]* - Remove a reminder""",

    "4": """*Portfolio Tracker - Your Holdings*

Tell me what gold/silver you hold and I'll track its value daily.

Just say:
_"I have 500g 22K gold"_
_"I have 2kg silver"_
_"I hold 100g 24K gold and 5kg silver"_

What you get:
- Live portfolio value updated every 15 min
- Daily profit/loss tracking
- Holdings in your *morning brief* every day
- *Weekly portfolio report* every Sunday

*Commands:*
- *portfolio* - See your current holdings & value
- *clear inventory* - Reset your holdings""",

    "5": """*Price Alerts - Never Miss a Deal*

Set a target price and I'll WhatsApp you the moment gold hits it - even at 2 AM!

Just say:
_"Alert me when gold drops below 7000"_
_"Notify me if gold goes above 8000"_
_"Buy alert at 6500"_

How it works:
- I check gold every 15 minutes
- When price crosses your target → instant WhatsApp message
- Works 24/7, even while you sleep

Perfect for:
- Buying dips automatically
- Selling at your target price
- Tracking market movements""",

    "6": """*Trend Scout - Browse Designs*

I scrape the latest jewelry designs from top brands daily at 6 AM.

*Browse by category:*
- Type *bridal* - Wedding & engagement pieces
- Type *dailywear* - Lightweight everyday jewelry
- Type *temple* - Traditional temple designs
- Type *mens* - Men's rings, chains, bracelets
- Type *trends* - See all categories

When you see a design you like:
- *like [id]* - Save to your lookbook
- *skip [id]* - Pass on it
- *lookbook* - See all your saved designs""",

    "7": """*Pricing Engine - Your Complete Pricing Profile*

I support how YOU price jewelry - percentage, per-gram, CFP, or all-inclusive.

*Easiest way:* Just chat naturally!
_"I charge 14% making on necklaces"_
_"My CZ pave rate is ₹10 per stone"_
_"I work in USD for exports"_

*Or upload a photo* of your pricing chart - I'll read it and save everything!

*What I can store:*
- Making charges (%, per-gram, or per-piece)
- CZ rates by setting type (pave, prong, bezel...)
- Diamond rates by sieve size & quality
- Lab-grown diamond rates
- Gemstone rates (ruby, emerald, sapphire...)
- Setting charges (pave, channel, invisible...)
- Finishing (rhodium, two-tone, enamel...)
- Gold loss / wastage percentages
- Profit margin (shows cost vs selling price)
- Currency (INR or USD for exporters)

*Manual commands:*
price set model percentage
price set necklace 15
price set ring labor 800
price set cz pave 12
price set currency usd
price set margin 15

*View profile:* price profile""",

    "8": """*Morning Brief - 9 AM Daily*

Every morning at 9 AM, I send you a personalized message:

- Live gold & silver rates with change arrows
- Your portfolio value (if you've added holdings)
- Price alert if gold is near your buy target
- Upcoming reminders (birthdays this week)
- Market intelligence from overnight news

It's like getting a text from a smart friend who watches the gold market for you while you sleep.

*Commands:*
- *subscribe* - Start getting morning briefs
- *unsubscribe* - Stop daily updates

_You're auto-subscribed when you join!_""",

    "9": """*Invite a Friend to JewelClaw*

Share these 3 simple steps:

*Step 1:* Save this number in contacts:
*+1 (415) 523-8886* → Save as "JewelClaw"

*Step 2:* Open WhatsApp → New Chat → JewelClaw

*Step 3:* Send this message:
*join third-find*

That's it! They'll get a personalized setup with gold rates, AI chat, and morning briefs.

_Forward this message to any jeweler, wholesaler, or gold trader!_""",

    "10": """*About JewelClaw*

Hi! I'm *JewelClaw* - your personal AI jewelry assistant, right here on WhatsApp.

Built by *Sandeep Roy* for the Indian jewelry trade.

I'm a *multi-agent AI* that works for you *24/7* - even while you sleep! I watch gold prices at 2 AM, gather market news overnight, and have your personalized brief ready by 9 AM.

*What makes me different:*
- I understand natural language (Hindi + English)
- I remember YOUR business - charges, clients, events
- I send price alerts the moment gold hits your target
- I never forget a birthday or festival

*Version 1.0* | Built with multi-agent architecture

Your feedback makes me better! Tell Sandeep what features you'd like or what's not working. We're building this together.

_Powered by advanced AI, designed entirely for WhatsApp_""",
}


async def get_quick_rate_text(db: AsyncSession, city: str = "Mumbai") -> str:
    """Get a one-line gold rate for greetings."""
    result = await db.execute(
        select(MetalRate).where(MetalRate.city == city)
        .order_by(desc(MetalRate.recorded_at)).limit(1)
    )
    rate = result.scalar_one_or_none()
    if rate:
        return f"Gold is at *₹{rate.gold_24k:,.0f}/gm* right now."
    return "I'll have fresh gold rates for you shortly."


async def handle_onboarding(db: AsyncSession, user, message_body: str) -> str:
    """
    DB-backed 3-step onboarding. No in-memory state needed.
    Step detection: name is None → step 1, business_type is None → step 2, else → step 3.
    """
    text = message_body.strip()

    # Words that are NOT names - greetings, commands, Twilio sandbox join
    NOT_A_NAME = {
        "hi", "hello", "hey", "hii", "hiii", "namaste", "help", "menu",
        "gold", "silver", "subscribe", "unsubscribe", "setup", "start",
        "onboarding", "trends", "trending", "bridal", "portfolio",
        "1", "2", "3", "yes", "no", "ok", "okay", "thanks", "thank you",
    }

    # STEP 1: We need their name
    if user.name is None:
        normalized = text.lower().strip()
        is_name = (
            len(text) <= 50
            and not any(c.isdigit() for c in text)
            and len(text.split()) <= 5
            and normalized not in NOT_A_NAME
            and not normalized.startswith("join ")  # Twilio sandbox "join xxx-xxx"
        )

        if is_name:
            user.name = text.title()
            await db.flush()
            return (
                f"Great to meet you, *{user.name}*! Quick question -\n\n"
                f"Are you a:\n"
                f"1. Jeweler / Retailer\n"
                f"2. Wholesaler\n"
                f"3. Just tracking gold prices\n\n"
                f"_Reply 1, 2, or 3_"
            )
        else:
            # First contact or greeting - ask for name with a gold rate hook
            rate_text = await get_quick_rate_text(db, user.preferred_city or "Mumbai")
            return (
                f"Hi! I'm *JewelClaw* - your personal gold & jewelry assistant.\n\n"
                f"{rate_text}\n\n"
                f"What's your name?"
            )

    # STEP 2: We need their business type
    if user.business_type is None:
        btype = None
        t = text.lower().strip()
        if t in ("1", "jeweler", "retailer", "jeweller", "retail"):
            btype = "retailer"
        elif t in ("2", "wholesaler", "wholesale"):
            btype = "wholesaler"
        elif t in ("3", "consumer", "tracking", "personal", "just tracking"):
            btype = "consumer"
        else:
            # Try to infer from natural language
            if any(w in t for w in ("shop", "store", "retail", "jewel")):
                btype = "retailer"
            elif any(w in t for w in ("wholesale", "bulk", "supply")):
                btype = "wholesaler"
            else:
                btype = "consumer"

        user.business_type = btype
        await db.flush()

        btype_label = {"retailer": "Jeweler/Retailer", "wholesaler": "Wholesaler", "consumer": "Gold Tracker"}.get(btype, btype)
        return (
            f"Got it - *{btype_label}*!\n\n"
            f"Which city are you in?\n"
            f"Mumbai, Delhi, Bangalore, Chennai - or tell me yours."
        )

    # STEP 3: We need their city (then complete onboarding)
    if not user.onboarding_completed:
        # Parse city from response
        city_map = {
            "mumbai": "Mumbai", "bombay": "Mumbai",
            "delhi": "Delhi", "new delhi": "Delhi",
            "bangalore": "Bangalore", "bengaluru": "Bangalore",
            "chennai": "Chennai", "madras": "Chennai",
            "hyderabad": "Hyderabad", "pune": "Pune",
            "kolkata": "Kolkata", "calcutta": "Kolkata",
            "jaipur": "Jaipur", "ahmedabad": "Ahmedabad",
            "surat": "Surat", "lucknow": "Lucknow",
        }

        city = city_map.get(text.lower().strip(), text.strip().title())
        user.preferred_city = city
        user.onboarding_completed = True
        user.subscribed_to_morning_brief = True
        await db.flush()

        return (
            f"All set, *{user.name}*! Welcome to JewelClaw.\n\n"
            f"📱 *Morning brief at 9 AM* - gold rates for {city}\n"
            f"🚨 *Price alerts* - when gold hits your targets\n"
            f"💬 *AI chat* - ask me anything about gold or jewelry\n"
            f"🔔 *RemindGenie* - never forget a birthday or festival\n\n"
            f"Type *help* to see all my features, or just say *gold* to get started!"
        )


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
async def whatsapp_webhook(request: Request):
    """Handle incoming WhatsApp messages from Twilio."""
    phone_number = None
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

        if not phone_number:
            logger.warning("No phone number")
            return PlainTextResponse("")

        # Allow image-only messages (no body text)
        if not message_body and int(form_dict.get("NumMedia", "0")) == 0:
            logger.warning("No message body and no media")
            return PlainTextResponse("")
        if not message_body:
            message_body = ""  # Will be handled by image upload logic

        logger.info(f"Message from {phone_number}: {message_body[:50]}...")

        # Get DB session — if DB is down, we catch it and send graceful error
        try:
            db_gen = get_db()
            db = await db_gen.__anext__()
        except Exception as db_err:
            logger.error(f"DB CONNECTION FAILED: {db_err}")
            await whatsapp_service.send_message(
                phone_number,
                "We're experiencing a brief server issue. Please try again in a few minutes. Your message was received."
            )
            return PlainTextResponse("")

        # Get or create user
        user, is_new_user = await whatsapp_service.get_or_create_user(db, phone_number, profile_name)
        logger.info(f"USER: {user.phone_number}, new={is_new_user}")

        # Check for image upload (Twilio sends MediaUrl0 for images)
        media_url = form_dict.get("MediaUrl0")
        media_type = form_dict.get("MediaContentType0", "")
        num_media = int(form_dict.get("NumMedia", "0"))

        # Phase 1: Store incoming message with intelligence
        await store_conversation(db, user.id, "user", message_body)

        # MIGRATION: Existing users with name+business_type but not onboarded → auto-complete
        # Requires both name AND business_type to avoid catching mid-onboarding users
        if not user.onboarding_completed and user.name and user.business_type and not is_new_user:
            user.onboarding_completed = True
            if not user.subscribed_to_morning_brief:
                user.subscribed_to_morning_brief = True
            await db.flush()
            logger.info(f"AUTO-MIGRATED existing user {user.phone_number} to onboarded")

        # ONBOARDING: If user hasn't completed onboarding, guide them through it
        if not user.onboarding_completed:
            # But let them use "gold" command even during onboarding
            normalized = message_body.lower().strip()
            if normalized in ("gold", "gold rate", "gold rates", "sona"):
                response = await handle_command(db, user, "gold_rate", phone_number, False, message_body)
            else:
                response = await handle_onboarding(db, user, message_body)
                logger.info(f"ONBOARDING: step completed for {phone_number}")
        # IMAGE UPLOAD: Handle pricing chart / document images
        elif num_media > 0 and media_url and media_type.startswith("image/"):
            logger.info(f"IMAGE UPLOAD: {media_url} type={media_type}")
            response = await handle_image_upload(db, user, media_url, message_body, phone_number)
        else:
            # MAIN ROUTING: Onboarded user
            # Check fast-path commands FIRST (before classifier)
            command = whatsapp_service.parse_command(message_body)
            if command:
                logger.info(f"FAST PATH: command={command}")
                response = await handle_command(db, user, command, phone_number, False, message_body)
            else:
                # No exact command match → classify with AI
                classification, confidence = agent_service.classify_message(message_body)
                logger.info(f"CLASSIFY: '{classification}' (confidence={confidence})")

                if classification == "ai_conversation":
                    # AI PATH: natural language -> Claude with tools
                    logger.info("AI PATH: routing to agent_service")
                    response = await agent_service.handle_message(db, user, message_body)
                else:
                    # Fuzzy match from classifier
                    fuzzy_cmd = classification
                    if classification in ("like", "skip"):
                        fuzzy_cmd = message_body.lower().strip()
                    logger.info(f"FUZZY PATH: classification={classification}, cmd={fuzzy_cmd}")
                    response = await handle_command(db, user, fuzzy_cmd, phone_number, False, message_body)

            logger.info(f"RESPONSE LENGTH: {len(response) if response else 0}")

        # Send response
        if response:
            logger.info(f"SENDING to {phone_number}...")
            sent = await whatsapp_service.send_message(phone_number, response)
            logger.info(f"SENT: {sent}")

            # Phase 1: Store assistant response
            await store_conversation(db, user.id, "assistant", response)

        await db.commit()
        try:
            await db_gen.aclose()
        except Exception:
            pass
        return PlainTextResponse("")

    except Exception as e:
        import traceback
        logger.error(f"WEBHOOK ERROR: {e}")
        logger.error(traceback.format_exc())
        # Send graceful error message if we have the phone number
        if phone_number:
            try:
                await whatsapp_service.send_message(
                    phone_number,
                    "Something went wrong on our end. Please try again in a moment."
                )
            except Exception:
                pass
        return PlainTextResponse("")


async def handle_command(db: AsyncSession, user, command: str, phone_number: str, is_new_user: bool = False, message_body: str = "") -> str:
    """Handle fast-path commands for onboarded users."""
    city = user.preferred_city or "Mumbai"

    # GREETING → Smart response with live rate + nudge
    if command == "greeting":
        name = user.name or "there"
        rate_text = await get_quick_rate_text(db, city)
        greeting = f"Hey {name}! {rate_text}\n\nWhat do you need? Just ask naturally, or type *help* to see everything I can do."

        # Nudge retailers/wholesalers who haven't set up pricing
        if user.business_type in ("retailer", "wholesaler"):
            profile = await pricing_engine.get_user_pricing_profile(db, user.id)
            has_custom = (
                profile["making_charges"]
                or profile["labor_per_gram"]
                or profile["cfp_rates"]
            )
            if not has_custom:
                greeting += (
                    "\n\n💡 _Tip: Set up your pricing chart and I'll generate instant quotes for you!"
                    " Just tell me your making charges or upload a photo of your rate card._"
                )

        return greeting

    # HELP → Interactive numbered feature menu
    if command == "help":
        return get_help_menu(user.name or "there")

    # FEATURE GUIDES (1-10) → Expand each feature
    if command in FEATURE_GUIDES:
        return FEATURE_GUIDES[command]

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

    # SUBSCRIBE
    if command == "subscribe":
        if user.subscribed_to_morning_brief:
            return f"You're already subscribed, {user.name or 'friend'}! You'll get the morning brief at 9 AM."
        user.subscribed_to_morning_brief = True
        await db.flush()
        return f"Done! You'll get a personalized gold brief every morning at 9 AM."

    # UNSUBSCRIBE
    if command == "unsubscribe":
        user.subscribed_to_morning_brief = False
        await db.flush()
        return "Unsubscribed from morning briefs. You can still ask me for gold rates anytime."

    # SETUP → Invite guide
    if command == "setup":
        return FEATURE_GUIDES["9"]

    # ABOUT
    if command == "about":
        return FEATURE_GUIDES["10"]

    # ==========================================================================
    # TREND SCOUT COMMANDS
    # ==========================================================================

    # TRENDS MENU → Show category menu
    if command in ["trends", "trending"]:
        return await handle_trends_command(db, user, phone_number)

    # TREND SHORTCUTS
    if command in ("fresh", "today"):
        return await handle_fresh_picks_command(db, user, phone_number)

    # BRIDAL → Show bridal designs
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

    # LUXURY → Global luxury designs
    if command == "luxury":
        return await handle_category_command(db, user, "luxury", phone_number)

    # CONTEMPORARY → Modern minimalist
    if command == "contemporary":
        return await handle_category_command(db, user, "contemporary", phone_number)

    # NEWS → Jewelry industry news
    if command == "news":
        return await handle_industry_news_command(db, user, phone_number)

    # 11. LIKE/SAVE design
    if command and command.startswith(("like", "save")):
        return await handle_like_command(db, user, command)

    # 12. SKIP design
    if command and command.startswith("skip"):
        return await handle_skip_command(db, user, command)

    # 13. LOOKBOOK → Show saved designs
    if command == "lookbook":
        return await handle_lookbook_command(db, user)

    # Search, PDF, Alerts, Create Lookbook removed - dependencies not available in production

    # ==========================================================================
    # PRICING ENGINE COMMANDS
    # ==========================================================================

    if command == "quote" or command.startswith("quote"):
        return await handle_quote_command(db, user, message_body)

    if command in ("price setup", "price profile", "pricing") or command.startswith("price set") or command.startswith("price "):
        return await handle_price_command(db, user, message_body)

    # ==========================================================================
    # PORTFOLIO / INVENTORY COMMANDS
    # ==========================================================================

    if command in ("portfolio", "holdings", "my holdings", "inventory"):
        return await handle_portfolio_command(db, user)

    if command == "inventory_update":
        return await handle_inventory_update_command(db, user, message_body)

    if command == "clear_inventory":
        return await handle_clear_inventory_command(db, user)

    # ==========================================================================
    # REMINDGENIE COMMANDS
    # ==========================================================================

    if command == "remind" or command.startswith("remind"):
        return await handle_remind_command(db, user, message_body)

    # Unknown command → Route to AI agent
    try:
        return await agent_service.handle_message(db, user, message_body)
    except Exception as e:
        logger.error(f"AI fallback error: {e}")
        return "Something went wrong. Try asking me again, or type *gold* for rates."


async def handle_trends_command(db: AsyncSession, user, phone_number: str) -> str:
    """Handle trends command — weekly intelligence report with real trend data."""
    try:
        from app.services.trends_service import trend_intelligence

        # Try cached report first (generated Monday)
        cached = await trend_intelligence.get_cached_report(db)
        if cached:
            return cached

        # No cached report — generate on the fly
        report = await trend_intelligence.generate_trend_report(db)
        await db.commit()
        return report

    except Exception as e:
        logger.error(f"Trend report error: {e}")
        # Fallback to simple menu if trends service fails
        return """*Trend Scout — Market Intelligence*

*Category Reports:*
- *bridal* - Wedding jewelry intel
- *dailywear* - Everyday jewelry trends
- *temple* - Traditional South Indian
- *mens* - Men's collection data
- *contemporary* - Modern minimalist

*Market Data:*
- *fresh* - Today's market intel
- *news* - Industry headlines

_Reply any category for deep dive._"""


async def handle_fresh_picks_command(db: AsyncSession, user, phone_number: str) -> str:
    """Handle fresh/today command — today's market intelligence summary."""
    from datetime import datetime, timedelta

    parts = []
    today_str = datetime.now().strftime("%d %b")
    parts.append(f"*Today's Intel — {today_str}*")

    # --- Industry News ---
    try:
        from app.services.industry_news_service import industry_news_service
        news = await industry_news_service.get_recent(db, limit=5)
        if news:
            parts.append("\n*Headlines:*")
            for item in news[:3]:
                summary = item.summary or item.headline[:60]
                parts.append(f"  {summary}")
    except Exception:
        pass

    # --- Gold Price ---
    try:
        rate = await metal_service.get_current_rates(db, "Mumbai")
        if rate:
            analysis = await metal_service.get_market_analysis(db, "Mumbai")
            change = analysis.daily_change
            arrow = f"↑₹{abs(change):,.0f}" if change > 0 else f"↓₹{abs(change):,.0f}" if change < 0 else "→"
            parts.append(f"\n*Price Watch:*")
            parts.append(f"  Gold 24K: ₹{rate.gold_24k:,.0f} ({arrow})")
            if rate.silver:
                parts.append(f"  Silver: ₹{rate.silver:,.0f}/gm")
    except Exception:
        pass

    # --- Amazon Bestseller highlight ---
    try:
        from app.services.editorial_scraper import editorial_scraper
        benchmarks = await editorial_scraper.get_price_benchmarks("necklaces")
        if benchmarks and benchmarks.get("count", 0) >= 3:
            avg = benchmarks["avg_price"]
            if avg >= 100000:
                parts.append(f"  Amazon necklace avg: ₹{avg/100000:.1f}L")
            else:
                parts.append(f"  Amazon necklace avg: ₹{avg:,.0f}")
    except Exception:
        pass

    # --- Brand Activity (if available) ---
    try:
        from app.services.brand_monitor_service import brand_monitor
        brand_summary = await brand_monitor.get_brand_activity_summary(db)
        if brand_summary:
            parts.append(f"\n*Brand Watch:*\n{brand_summary}")
    except Exception:
        pass

    # --- 2-3 BlueStone images as bonus ---
    try:
        result = await db.execute(
            select(Design)
            .where(Design.source == "bluestone")
            .where(Design.image_url.isnot(None))
            .order_by(desc(Design.scraped_at))
            .limit(3)
        )
        designs = result.scalars().all()
        if designs:
            parts.append(f"\n*Latest BlueStone:*")
            # Send text first
            await whatsapp_service.send_message(phone_number, "\n".join(parts))
            # Then send images
            for d in designs:
                price_str = f"₹{d.price_range_min:,.0f}" if d.price_range_min else ""
                caption = f"*{d.title[:50]}*\n{price_str} | {d.source}"
                if d.image_url:
                    await whatsapp_service.send_message(phone_number, caption, media_url=d.image_url)
            return "_Reply 'trends' for weekly report | 'news' for all headlines_"
    except Exception:
        pass

    parts.append(f"\n_Reply 'trends' for weekly report | 'news' for all headlines_")
    return "\n".join(parts)


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
    """Handle industry news - show real-time jewelry industry news from RSS feeds."""
    from app.services.industry_news_service import industry_news_service

    news_items = await industry_news_service.get_recent(db, limit=8)
    return industry_news_service.format_news_message(news_items)


async def handle_category_command(db: AsyncSession, user, category: str, phone_number: str) -> str:
    """Handle category commands — intelligence-first deep dive with optional images."""
    try:
        from app.services.trends_service import trend_intelligence
        report = await trend_intelligence.get_category_deep_dive(db, category)

        # Send text report first
        await whatsapp_service.send_message(phone_number, report)

        # Then send 2-3 BlueStone images if available
        try:
            result = await db.execute(
                select(Design)
                .where(Design.source == "bluestone")
                .where(Design.category == category)
                .where(Design.image_url.isnot(None))
                .order_by(desc(Design.scraped_at))
                .limit(3)
            )
            designs = result.scalars().all()

            # If no designs in exact category, try broader search
            if not designs:
                result = await db.execute(
                    select(Design)
                    .where(Design.image_url.isnot(None))
                    .where(Design.category == category)
                    .order_by(desc(Design.scraped_at))
                    .limit(3)
                )
                designs = result.scalars().all()

            for d in designs:
                price_str = f"₹{d.price_range_min:,.0f}" if d.price_range_min else ""
                caption = f"*{d.title[:50]}*\n{price_str} | {d.source}\n_Reply 'like {d.id}' to save_"
                if d.image_url:
                    await whatsapp_service.send_message(phone_number, caption, media_url=d.image_url)
        except Exception:
            pass

        return "_Reply 'trends' for full market report_"

    except Exception as e:
        logger.error(f"Category deep dive error: {e}")
        # Fallback to basic design display
        designs = await scraper_service.get_trending_designs(db, category=category, limit=5)
        if not designs:
            return f"*{category.title()} Intelligence*\n\nNo data available yet for {category}.\n\n_Reply 'trends' for market overview._"

        parts = [f"*{category.title()} — Top Designs*"]
        for i, d in enumerate(designs, 1):
            price_str = f"₹{d.price_range_min:,.0f}" if d.price_range_min else "N/A"
            parts.append(f"{i}. {d.title[:50]} — {price_str}")
        parts.append("\n_Reply 'trends' for full report_")
        return "\n".join(parts)


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


async def handle_image_upload(db: AsyncSession, user, media_url: str, message_body: str, phone_number: str) -> str:
    """Handle image uploads - detect pricing charts and process them."""
    body_lower = message_body.lower().strip() if message_body else ""

    # Check if this looks like a pricing chart upload
    is_pricing = any(w in body_lower for w in (
        "price", "pricing", "chart", "rate", "labor", "making",
        "charge", "cfp", "cost", "quote", "diamond", "cz",
        "setting", "finishing", "karigari",
    ))

    if not is_pricing and body_lower:
        # If there's text with the image but it doesn't seem pricing-related,
        # route to AI agent with image context
        return await agent_service.handle_message(
            db, user,
            f"[User sent an image: {media_url}] {message_body}"
        )

    # Process as pricing chart
    await whatsapp_service.send_message(
        phone_number,
        "📸 Analyzing your pricing chart... give me a moment."
    )

    # Use Twilio auth to access media URL
    from urllib.parse import urlparse
    from app.config import settings as app_settings
    parsed_url = urlparse(media_url)
    if parsed_url.hostname and "twilio.com" in parsed_url.hostname:
        auth_media_url = media_url.replace(
            f"https://{parsed_url.hostname}",
            f"https://{app_settings.twilio_account_sid}:{app_settings.twilio_auth_token}@{parsed_url.hostname}"
        )
    else:
        # Non-Twilio URL, use as-is
        auth_media_url = media_url

    result = await pricing_engine.parse_pricing_chart_image(
        auth_media_url,
        user_context=f"User message: {message_body}" if message_body else ""
    )

    if "error" in result:
        logger.error(f"Image parsing error: {result['error']}")
        return (
            f"I couldn't read that image clearly. Try:\n"
            f"- Better lighting / clearer photo\n"
            f"- Or just tell me your rates in chat!\n\n"
            f"_Example: \"I charge 14% making on necklaces, CZ pave ₹10/stone\"_"
        )

    # Successfully parsed - save and show what we found
    parsed_data = result.get("data", {})
    saved_items = await pricing_engine.apply_parsed_pricing(db, user.id, parsed_data)

    if not saved_items:
        return "I could see the image but couldn't extract specific pricing data. Try telling me your rates in chat instead."

    lines = ["✅ *Pricing chart saved!* Here's what I found:\n"]
    for item in saved_items[:20]:  # Cap at 20 items
        lines.append(f"  • {item}")

    notes = parsed_data.get("notes")
    if notes:
        lines.append(f"\n📝 _{notes}_")

    lines.append(f"\n_Total: {len(saved_items)} rates saved to your profile._")
    lines.append("_Type 'price profile' to see your full setup._")

    return "\n".join(lines)


async def handle_quote_command(db: AsyncSession, user, message_body: str) -> str:
    """Handle quote command - generate instant jewelry bill."""
    text = message_body.strip().lower()

    # Just "quote" with no args -> show usage
    if text in ("quote", "quote help"):
        return """💎 *Quick Quote - Instant Jewelry Bill*

*Plain gold:*
quote 10g 22k necklace
quote 5g 18k ring x3

*With CZ stones:*
quote 2g 18k ring 30 cz pave
quote 5g 22k earring 50 cz bezel

*With diamonds:*
quote 3g 18k ring 0.5ct diamond GH-VS
quote 5g 18k pendant 20 diamonds sieve 7

*With gemstones:*
quote 4g 18k ring 1.5ct ruby
quote 3g 18k pendant 2ct emerald

*With finishing:*
quote 2g 18k ring rhodium

_Uses YOUR pricing profile. Type 'price setup' to configure._"""

    parsed = pricing_engine.parse_quote_input(text)
    if not parsed:
        return "Could not parse quote. Try: quote 10g 22k necklace"

    quote = await pricing_engine.generate_quote(
        db=db,
        user_id=user.id,
        weight_grams=parsed["weight_grams"],
        karat=parsed["karat"],
        jewelry_type=parsed["jewelry_type"],
        stone_cost=parsed.get("stone_cost", 0),
        quantity=parsed.get("quantity", 1),
        city=user.preferred_city,
        cz_count=parsed.get("cz_count", 0),
        cz_setting=parsed.get("cz_setting", "pave"),
        diamonds=parsed.get("diamonds"),
        gemstones=parsed.get("gemstones"),
        finishing=parsed.get("finishing"),
    )

    bill = pricing_engine.format_quote_message(quote)

    # Smart nudge: if using all default rates, remind user to set up pricing
    if "error" not in quote and not quote.get("is_custom_making"):
        profile = await pricing_engine.get_user_pricing_profile(db, user.id)
        has_any_custom = (
            profile["making_charges"]
            or profile["labor_per_gram"]
            or profile["cfp_rates"]
            or profile["cz_rates"]
            or profile["diamond_rates"]
        )
        if not has_any_custom:
            bill += (
                "\n\n⚠️ *This quote uses industry default rates, not yours.*"
                "\n\nTell me your rates and I'll remember forever:"
                "\n_\"I charge 18% making on necklaces\"_"
                "\n_\"My CZ pave rate is ₹10 per stone\"_"
                "\n\nOr just *upload a photo* of your pricing chart!"
                "\n\nType *7* for full pricing setup guide."
            )

    return bill


async def handle_price_command(db: AsyncSession, user, message_body: str) -> str:
    """Handle price setup/set/profile commands."""
    text = message_body.strip().lower()

    # price profile -> show current settings
    if text in ("price profile", "price view", "my prices", "pricing"):
        return await pricing_engine.get_setup_summary(db, user.id)

    # price setup -> show menu
    if text in ("price setup", "price help", "price"):
        return pricing_engine.get_setup_menu()

    # price set [type] [value] -> update a rate
    if text.startswith("price set"):
        parsed = pricing_engine.parse_setup_input(text)
        if not parsed:
            return """Could not parse. Try:
price set necklace 15
price set ring labor 800
price set cz pave 12
price set model percentage
price set currency usd
price set margin 15"""

        ptype = parsed["type"]

        if ptype == "hallmark":
            await pricing_engine.save_hallmark_charge(db, user.id, parsed["value"])
            return f"✅ Hallmark charge set to *₹{parsed['value']:,.0f}* per piece."

        elif ptype == "wastage":
            jtype = pricing_engine._normalize_jewelry_type(parsed["jewelry_type"])
            await pricing_engine.save_wastage(db, user.id, jtype, parsed["value"])
            return f"✅ Wastage for *{jtype.title()}* set to *{parsed['value']}%*."

        elif ptype == "making":
            jtype = pricing_engine._normalize_jewelry_type(parsed["jewelry_type"])
            await pricing_engine.save_making_charge(db, user.id, jtype, parsed["value"])
            return f"✅ Making charge for *{jtype.title()}* set to *{parsed['value']}%*.\n\n_Try: quote 10g 22k {jtype}_"

        elif ptype == "labor":
            jtype = pricing_engine._normalize_jewelry_type(parsed["jewelry_type"])
            await pricing_engine.save_labor_per_gram(db, user.id, jtype, parsed["value"])
            return f"✅ Labor rate for *{jtype.title()}* set to *₹{parsed['value']:,.0f}/gm*."

        elif ptype == "cfp":
            jtype = pricing_engine._normalize_jewelry_type(parsed["jewelry_type"])
            await pricing_engine.save_cfp_rate(db, user.id, jtype, parsed["value"])
            return f"✅ CFP rate for *{jtype.title()}* set to *{parsed['value']}*."

        elif ptype == "model":
            await pricing_engine.save_pricing_model(db, user.id, parsed["value"])
            from app.services.pricing_engine_service import PRICING_MODELS
            label = PRICING_MODELS.get(parsed["value"], parsed["value"])
            return f"✅ Pricing model set to *{label}*."

        elif ptype == "currency":
            await pricing_engine.save_currency(db, user.id, parsed["value"])
            return f"✅ Currency set to *{parsed['value']}*. {'No GST on exports.' if parsed['value'] == 'USD' else ''}"

        elif ptype == "margin":
            await pricing_engine.save_profit_margin(db, user.id, parsed["value"])
            return f"✅ Profit margin set to *{parsed['value']}%*. Quotes will now show cost + selling price."

        elif ptype == "gold_loss":
            await pricing_engine.save_gold_loss(db, user.id, parsed["value"])
            return f"✅ Gold loss set to *{parsed['value']}%*."

        elif ptype == "cz":
            await pricing_engine.save_cz_rate(db, user.id, parsed["setting"], parsed["value"])
            return f"✅ CZ rate for *{parsed['setting']}* set to *{parsed['value']}* per stone."

        elif ptype == "setting":
            await pricing_engine.save_setting_rate(db, user.id, parsed["setting"], parsed["value"])
            return f"✅ Setting charge for *{parsed['setting']}* set to *{parsed['value']}* per stone."

        elif ptype == "finishing":
            await pricing_engine.save_finishing_rate(db, user.id, parsed["finishing"], parsed["value"])
            return f"✅ Finishing charge for *{parsed['finishing']}* set to *{parsed['value']}* per piece."

    return pricing_engine.get_setup_menu()


async def handle_remind_command(db: AsyncSession, user, message_body: str) -> str:
    """Handle all RemindGenie commands."""
    import re
    text = message_body.strip().lower()

    # remind list
    if text in ("remind", "remind list", "reminders", "my reminders"):
        reminders = await reminder_service.list_reminders(db, user.id)
        return reminder_service.format_reminder_list(reminders)

    # remind festivals - load Indian festival calendar
    if text in ("remind festivals", "remind festival", "load festivals"):
        count = await reminder_service.load_festivals_for_user(db, user.id)
        if count > 0:
            return f"""🪔 *{count} Indian festivals loaded!*

Diwali, Holi, Raksha Bandhan, Dussehra, and more!

You'll get reminders on each festival day at *12:01 AM* and *8:00 AM* with greeting messages ready to share.

_Type 'remind list' to see all your reminders_"""
        else:
            return "🪔 All festivals already loaded! Type 'remind list' to see them."

    # remind delete [id]
    match = re.match(r'remind\s+delete\s+(\d+)', text)
    if match:
        reminder_id = int(match.group(1))
        deleted = await reminder_service.delete_reminder(db, user.id, reminder_id)
        if deleted:
            return f"✅ Reminder #{reminder_id} deleted."
        return f"Reminder #{reminder_id} not found. Type 'remind list' to see your reminders."

    # remind add [name] | [relationship] | [date] | [occasion]
    if text.startswith("remind add"):
        parsed = reminder_service.parse_reminder_input(message_body)
        if not parsed:
            return """📅 *How to add a reminder:*

remind add [name] | [relationship] | [date]
remind add [name] | [relationship] | [date] | [type]

*Examples:*
remind add Mom | Mother | 15 March
remind add Priya | Customer | 20 June | anniversary
remind add Wedding Day | Spouse | 14 Feb | anniversary
remind add Rahul | Friend | 5/8

*Types:* birthday, anniversary, festival, custom
_(Default: birthday)_"""

        reminder = await reminder_service.add_reminder(
            db=db,
            user_id=user.id,
            name=parsed["name"],
            occasion=parsed["occasion"],
            month=parsed["month"],
            day=parsed["day"],
            relationship=parsed["relationship"],
            year=parsed["year"],
            custom_note=parsed.get("custom_note"),
        )

        emoji = "🎂" if parsed["occasion"] == "birthday" else "💍" if parsed["occasion"] == "anniversary" else "📅"
        date_str = f"{parsed['day']} {reminder_service._month_name(parsed['month'])}"
        if parsed.get("year"):
            date_str += f" {parsed['year']}"

        return f"""{emoji} *Reminder saved!*

*{parsed['name']}* ({parsed.get('relationship', '')})
{parsed['occasion'].title()} - {date_str}

You'll get a greeting at *12:01 AM* and a reminder at *8:00 AM* with a ready-to-send message!

_Type 'remind list' to see all reminders_"""

    # remind help
    return """🔔 *RemindGenie - Never Forget!*

*Commands:*
• *remind list* - See all your reminders
• *remind add* [name] | [relation] | [date]
• *remind festivals* - Load 30+ Indian festivals
• *remind delete [id]* - Remove a reminder

*Examples:*
remind add Mom | Mother | 15 March
remind add Priya | Customer | 20 June | anniversary
remind add Papa | Father | 5 August

JewelClaw remembers forever! You'll get greetings at 12:01 AM and 8:00 AM with ready-to-send messages.

_Your customers will love you for never forgetting!_"""


async def handle_portfolio_command(db: AsyncSession, user) -> str:
    """Handle portfolio/holdings/inventory view command."""
    try:
        portfolio = await background_agent.get_portfolio_summary(db, user.id)
        return background_agent.format_portfolio_message(portfolio)
    except Exception as e:
        logger.error(f"Portfolio command error: {e}")
        return "Error loading portfolio. Try again."


async def handle_inventory_update_command(db: AsyncSession, user, message_body: str) -> str:
    """Handle natural language inventory updates like 'I have 500g 22K gold'."""
    try:
        items = background_agent.parse_inventory_input(message_body)
        if not items:
            return """📦 *How to set your inventory:*

Tell me what you hold:
• "I have 500g 22K gold"
• "I have 200g gold and 5kg silver"
• "I hold 1kg 24K gold, 10kg silver, 50g platinum"

_I'll track your portfolio value daily and send weekly reports!_"""

        results = []
        for item in items:
            await background_agent.store_inventory(
                db, user.id,
                metal=item["metal"],
                weight_grams=item["weight_grams"],
                karat=item["karat"],
            )
            if item["weight_grams"] >= 1000:
                wt = f"{item['weight_grams']/1000:.1f}kg"
            else:
                wt = f"{item['weight_grams']:.0f}g"
            karat_str = f" {item['karat'].upper()}" if item["metal"] == "gold" else ""
            results.append(f"• {wt}{karat_str} {item['metal'].title()}")

        # Get portfolio value
        portfolio = await background_agent.get_portfolio_summary(db, user.id)

        response = f"✅ *Inventory Updated!*\n\n" + "\n".join(results)
        if "error" not in portfolio:
            response += f"\n\n💰 *Total Value: ₹{portfolio['total_value']:,.0f}*"
            if portfolio["total_change"] != 0:
                ch = portfolio["total_change"]
                if ch > 0:
                    response += f"\nToday: +₹{abs(ch):,.0f} (+{abs(portfolio['total_change_pct']):.1f}%)"
                else:
                    response += f"\nToday: -₹{abs(ch):,.0f} (-{abs(portfolio['total_change_pct']):.1f}%)"
            response += "\n\n_Weekly report every Sunday. Type 'portfolio' anytime._"

        return response

    except Exception as e:
        logger.error(f"Inventory update error: {e}")
        return "Error updating inventory. Try again."


async def handle_clear_inventory_command(db: AsyncSession, user) -> str:
    """Clear all inventory holdings for a user."""
    try:
        from app.services.business_memory_service import business_memory_service

        memories = await business_memory_service.get_user_memory(db, user.id, category="inventory")
        if not memories:
            return "No inventory to clear."

        for mem in memories:
            mem.is_active = False

        await db.flush()
        return f"🗑️ Cleared {len(memories)} inventory items. Portfolio tracking paused."
    except Exception as e:
        logger.error(f"Clear inventory error: {e}")
        return "Error clearing inventory. Try again."


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


@app.post("/admin/migrate-remindgenie")
async def migrate_remindgenie():
    """Create RemindGenie tables (reminders) and add timezone to users."""
    from sqlalchemy import text
    from app.database import engine

    try:
        async with engine.begin() as conn:
            # Add timezone column to users
            await conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS timezone VARCHAR(50) DEFAULT 'Asia/Kolkata'"
            ))

            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    name VARCHAR(100) NOT NULL,
                    relation VARCHAR(50),
                    occasion VARCHAR(50) NOT NULL,
                    remind_month INTEGER NOT NULL,
                    remind_day INTEGER NOT NULL,
                    remind_year INTEGER,
                    custom_note TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    last_sent_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """))

            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_reminder_user_active
                ON reminders(user_id, is_active)
            """))
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_reminder_month_day
                ON reminders(remind_month, remind_day)
            """))

            logger.info("RemindGenie migration complete")

        return {"status": "success", "message": "RemindGenie table created (reminders)"}

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
        "guide": FEATURE_GUIDES["9"],
        "phone": "+1 (415) 523-8886",
        "join_code": "join third-find",
        "steps": [
            "1. Save +1 (415) 523-8886 as 'JewelClaw' in contacts",
            "2. Open WhatsApp and start chat with JewelClaw",
            "3. Send: join third-find",
            "4. Type: help (to see all features)",
        ]
    }


@app.get("/admin/send-onboarding/{phone}")
async def send_onboarding(phone: str):
    """Send onboarding guide to a phone number."""
    try:
        result = await whatsapp_service.send_message(
            f"whatsapp:{phone}",
            FEATURE_GUIDES["9"]
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
                "content": c.content[:100] + "..." if c.content and len(c.content) > 100 else c.content,
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


@app.post("/scheduler/trigger/market-intelligence")
async def trigger_market_intelligence():
    """Manually trigger overnight market intelligence gathering."""
    await scheduler_service.gather_overnight_intelligence()
    intel = scheduler_service._cached_market_intel
    return {
        "status": "gathered",
        "cached_intel_length": len(intel) if intel else 0,
        "cached_intel": intel or "(empty)",
    }


@app.post("/scheduler/trigger/festival-refresh")
async def trigger_festival_refresh():
    """Manually trigger festival calendar refresh."""
    await scheduler_service.refresh_festival_calendar()
    return {"status": "triggered"}


@app.post("/scheduler/trigger/industry-news")
async def trigger_industry_news():
    """Manually trigger industry news scrape + categorize + alert."""
    await scheduler_service.scrape_industry_news()
    return {"status": "triggered"}


@app.post("/scheduler/trigger/editorial-scrape")
async def trigger_editorial_scrape():
    """Manually trigger editorial design scrape."""
    await scheduler_service.scrape_editorial_designs()
    return {"status": "triggered"}


@app.post("/scheduler/trigger/brand-scan")
async def trigger_brand_scan():
    """Manually trigger brand sitemap scan."""
    await scheduler_service.scan_brand_sitemaps()
    return {"status": "triggered"}


@app.post("/scheduler/trigger/trend-report")
async def trigger_trend_report():
    """Manually trigger weekly trend report generation."""
    await scheduler_service.generate_weekly_trend_report()
    return {"status": "triggered"}


@app.post("/admin/purge-fake-designs")
async def purge_fake_designs(db: AsyncSession = Depends(get_db)):
    """Delete fake/hardcoded designs from DB (CaratLane, Tanishq samples)."""
    from sqlalchemy import delete
    count = 0
    for source in ["caratlane", "tanishq"]:
        result = await db.execute(
            delete(Design).where(Design.source == source)
        )
        count += result.rowcount
    await db.commit()
    return {"purged": count, "sources": ["caratlane", "tanishq"]}


@app.get("/admin/preview/morning-brief/{phone}")
async def preview_morning_brief(phone: str, db: AsyncSession = Depends(get_db)):
    """Preview morning brief for a specific user WITHOUT sending."""
    import traceback
    try:
        # Find user
        result = await db.execute(select(User).where(User.phone_number == phone))
        user = result.scalar_one_or_none()
        if not user:
            return {"error": f"User not found: {phone}"}

        # Get rates
        scraped_data = await metal_service.fetch_all_rates("mumbai")
        rate = await metal_service.get_current_rates(db, "Mumbai", force_refresh=bool(scraped_data))
        if not rate:
            return {"error": "No rates available"}

        analysis = await metal_service.get_market_analysis(db, "Mumbai")
        gold_24k = rate.gold_24k
        change_24k = analysis.daily_change
        if change_24k == 0 and scraped_data:
            yesterday = getattr(scraped_data, 'yesterday_24k', None)
            if yesterday and yesterday > 0:
                change_24k = gold_24k - yesterday

        silver = rate.silver or 0
        market_intel = scheduler_service._cached_market_intel or ""

        # Build the brief
        brief = await scheduler_service._build_flowing_brief(
            db, user, gold_24k, change_24k, silver, rate, analysis, market_intel
        )

        return {
            "user": {"name": user.name, "phone": user.phone_number, "business_type": user.business_type},
            "gold_24k": gold_24k,
            "change_24k": change_24k,
            "market_intel_available": bool(market_intel),
            "brief_length": len(brief),
            "brief": brief,
        }
    except Exception as e:
        return {"error": str(e), "trace": traceback.format_exc()}


@app.get("/admin/debug/morning-brief")
async def debug_morning_brief(db: AsyncSession = Depends(get_db)):
    """Debug morning brief - check each step without sending."""
    debug = {}

    # Step 1: Check subscribers
    users = await whatsapp_service.get_subscribed_users(db)
    debug["subscribers"] = [
        {"id": u.id, "phone": u.phone_number, "name": u.name, "subscribed": u.subscribed_to_morning_brief}
        for u in users
    ]
    debug["subscriber_count"] = len(users)

    # Step 2: Check rate scraping
    try:
        scraped_data = await metal_service.fetch_all_rates("mumbai")
        debug["rate_scrape"] = "OK" if scraped_data else "FAILED - returned None"
        if scraped_data:
            debug["gold_24k"] = getattr(scraped_data, 'gold_24k', 'N/A')
    except Exception as e:
        debug["rate_scrape"] = f"ERROR: {str(e)}"

    # Step 3: Check DB rate
    try:
        rate = await metal_service.get_current_rates(db, "Mumbai")
        debug["db_rate"] = "OK" if rate else "No rate in DB"
        if rate:
            debug["db_gold_24k"] = rate.gold_24k
            debug["db_rate_date"] = rate.rate_date
    except Exception as e:
        debug["db_rate"] = f"ERROR: {str(e)}"

    # Step 4: Check Twilio config
    debug["twilio_sid"] = settings.twilio_account_sid[:10] + "..." if settings.twilio_account_sid else "NOT SET"
    debug["twilio_token"] = "SET" if settings.twilio_auth_token else "NOT SET"
    debug["twilio_number"] = settings.twilio_whatsapp_number

    return debug


@app.get("/admin/debug/send-test/{phone}")
async def debug_send_test(phone: str):
    """Send a test WhatsApp message to verify Twilio works."""
    try:
        result = await whatsapp_service.send_message(
            f"whatsapp:{phone}",
            "✅ *JewelClaw Test*\nIf you see this, WhatsApp messaging works!"
        )
        return {"sent": result, "to": phone}
    except Exception as e:
        import traceback
        return {"error": str(e), "detail": traceback.format_exc()}


@app.get("/admin/debug/remind-preview/{phone}")
async def debug_remind_preview(phone: str, send: bool = False, db: AsyncSession = Depends(get_db)):
    """
    Preview (and optionally send) what a user receives when their reminders fire.
    Simulates both midnight and morning messages using the user's ACTUAL reminders.
    If no reminders match today, simulates with sample birthday + anniversary.
    """
    import traceback
    from datetime import datetime as dt_now

    try:
        # Find user
        result = await db.execute(select(User).where(User.phone_number == phone))
        user = result.scalar_one_or_none()
        if not user:
            return {"error": f"User not found: {phone}"}

        user_name = user.name or "Friend"

        # Check if user has any reminders matching today
        import pytz
        ist = pytz.timezone("Asia/Kolkata")
        today = dt_now.now(ist).date()

        # Get today's actual reminders
        today_reminders = await reminder_service.get_todays_reminders(db, today=today)
        my_today = [
            {"name": r.name, "occasion": r.occasion, "relationship": r.relation, "custom_note": r.custom_note}
            for u, r in today_reminders if u.id == user.id
        ]
        festivals_today = await reminder_service.get_todays_festivals(today=today)

        # If nothing today, simulate with sample data from user's actual reminders
        sample_reminders = []
        if not my_today:
            all_reminders = await reminder_service.list_reminders(db, user.id)
            # Pick first birthday and first anniversary
            for r in all_reminders:
                if r["occasion"] == "birthday" and not any(s["occasion"] == "birthday" for s in sample_reminders):
                    sample_reminders.append({
                        "name": r["name"], "occasion": "birthday",
                        "relationship": r["relationship"], "custom_note": r.get("custom_note")
                    })
                elif r["occasion"] == "anniversary" and not any(s["occasion"] == "anniversary" for s in sample_reminders):
                    sample_reminders.append({
                        "name": r["name"], "occasion": "anniversary",
                        "relationship": r["relationship"], "custom_note": r.get("custom_note")
                    })
                if len(sample_reminders) >= 2:
                    break

            # Fallback if no reminders at all
            if not sample_reminders:
                sample_reminders = [
                    {"name": "Mom", "occasion": "birthday", "relationship": "Mother", "custom_note": None},
                    {"name": "Priya", "occasion": "anniversary", "relationship": "Wife", "custom_note": None},
                ]

        reminders_to_use = my_today if my_today else sample_reminders
        is_simulated = not bool(my_today)

        # Build midnight message (12:01 AM)
        midnight_msg = await reminder_service.build_reminder_message(
            user_name=user_name,
            reminders=reminders_to_use,
            festivals=festivals_today,
            is_midnight=True,
        )

        # Build morning message (8:00 AM)
        morning_msg = await reminder_service.build_reminder_message(
            user_name=user_name,
            reminders=reminders_to_use,
            festivals=festivals_today,
            is_midnight=False,
        )

        result_data = {
            "user": user_name,
            "simulated": is_simulated,
            "reminders_used": reminders_to_use,
            "festivals_today": festivals_today,
            "midnight_message": midnight_msg,
            "morning_message": morning_msg,
        }

        # Optionally send to WhatsApp
        if send and midnight_msg:
            sent = await whatsapp_service.send_message(f"whatsapp:{phone}", midnight_msg)
            result_data["sent_midnight"] = sent
        if send and morning_msg:
            sent = await whatsapp_service.send_message(f"whatsapp:{phone}", morning_msg)
            result_data["sent_morning"] = sent

        return result_data

    except Exception as e:
        return {"error": str(e), "trace": traceback.format_exc()}


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
