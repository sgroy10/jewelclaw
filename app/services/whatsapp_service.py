"""
Twilio WhatsApp service with command handling.
"""

import logging
from typing import Optional, Tuple
from datetime import datetime, timedelta
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.config import settings
from app.models import User, Conversation

logger = logging.getLogger(__name__)


def detect_timezone_from_phone(phone: str) -> str:
    """Detect timezone from phone number country code."""
    phone = phone.replace("whatsapp:", "").replace("+", "").replace(" ", "").replace("-", "")

    # Country code -> timezone mapping (most common codes)
    PHONE_TIMEZONE_MAP = [
        ("91", "Asia/Kolkata"),       # India
        ("1", "America/New_York"),    # US/Canada (default ET)
        ("44", "Europe/London"),      # UK
        ("971", "Asia/Dubai"),        # UAE
        ("966", "Asia/Riyadh"),       # Saudi Arabia
        ("65", "Asia/Singapore"),     # Singapore
        ("60", "Asia/Kuala_Lumpur"),  # Malaysia
        ("852", "Asia/Hong_Kong"),    # Hong Kong
        ("61", "Australia/Sydney"),   # Australia
        ("64", "Pacific/Auckland"),   # New Zealand
        ("49", "Europe/Berlin"),      # Germany
        ("33", "Europe/Paris"),       # France
        ("39", "Europe/Rome"),        # Italy
        ("81", "Asia/Tokyo"),         # Japan
        ("86", "Asia/Shanghai"),      # China
        ("82", "Asia/Seoul"),         # South Korea
        ("66", "Asia/Bangkok"),       # Thailand
        ("62", "Asia/Jakarta"),       # Indonesia
        ("63", "Asia/Manila"),        # Philippines
        ("92", "Asia/Karachi"),       # Pakistan
        ("94", "Asia/Colombo"),       # Sri Lanka
        ("880", "Asia/Dhaka"),        # Bangladesh
        ("977", "Asia/Kathmandu"),    # Nepal
        ("974", "Asia/Qatar"),        # Qatar
        ("968", "Asia/Muscat"),       # Oman
        ("973", "Asia/Bahrain"),      # Bahrain
        ("965", "Asia/Kuwait"),       # Kuwait
        ("254", "Africa/Nairobi"),    # Kenya
        ("27", "Africa/Johannesburg"),# South Africa
        ("234", "Africa/Lagos"),      # Nigeria
        ("7", "Europe/Moscow"),       # Russia
        ("55", "America/Sao_Paulo"),  # Brazil
        ("52", "America/Mexico_City"),# Mexico
    ]

    # Match longest prefix first (e.g., 880 before 88)
    for prefix, tz in sorted(PHONE_TIMEZONE_MAP, key=lambda x: -len(x[0])):
        if phone.startswith(prefix):
            return tz

    return "Asia/Kolkata"  # Default to IST


# Command definitions
COMMANDS = {
    # Greeting commands
    "hi": "greeting",
    "hello": "greeting",
    "hey": "greeting",
    "hii": "greeting",
    "hiii": "greeting",
    "namaste": "greeting",

    # Subscribe commands
    "subscribe": "subscribe",

    # Unsubscribe commands
    "unsubscribe": "unsubscribe",

    # Gold rate commands
    "gold": "gold_rate",
    "gold rate": "gold_rate",
    "gold rates": "gold_rate",
    "sona": "gold_rate",

    # Help
    "help": "help",
    "menu": "help",

    # Setup/Onboarding
    "setup": "setup",
    "onboarding": "setup",
    "start": "setup",
    "join": "setup",

    # Portfolio / Inventory
    "portfolio": "portfolio",
    "holdings": "portfolio",
    "my holdings": "portfolio",
    "inventory": "portfolio",
    "my stock": "portfolio",
    "clear inventory": "clear_inventory",

    # About
    "about": "about",
    "about jewelclaw": "about",

    # Feature guide numbers (from help menu)
    "1": "1",
    "2": "2",
    "3": "3",
    "4": "4",
    "5": "5",
    "6": "6",
    "7": "7",
    "8": "8",
    "9": "9",
    "10": "10",

    # Pricing commands
    "price set": "price set",
    "price setup": "price setup",
    "price profile": "price profile",
    "pricing": "pricing",
    "my prices": "pricing",

    # Admin
    "stats": "stats",

    # Industry News
    "news": "news",
    "industry": "news",
    "jewelry news": "news",

    # Intraday Gold Alerts
    "alerts": "alerts",
    "alerts on": "alerts_on",
    "alerts off": "alerts_off",
    "alert on": "alerts_on",
    "alert off": "alerts_off",
    "my alerts": "alerts",
    "alerts clear": "alerts_clear",
    "alert clear": "alerts_clear",
    "buy alert": "buy_alert",
    "sell alert": "sell_alert",
}


class WhatsAppService:
    """Service for WhatsApp messaging via Twilio."""

    def __init__(self):
        self.client = Client(
            settings.twilio_account_sid,
            settings.twilio_auth_token
        )
        self.from_number = settings.twilio_whatsapp_number

    async def send_message(self, to_number: str, message: str, media_url: str = None) -> bool:
        """Send a WhatsApp message, optionally with an image."""
        if not to_number.startswith("whatsapp:"):
            to_number = f"whatsapp:{to_number}"

        try:
            # Split long messages
            if len(message) > 1500:
                chunks = self._split_message(message)
                for i, chunk in enumerate(chunks):
                    # Only attach media to first chunk
                    if i == 0 and media_url:
                        self.client.messages.create(
                            body=chunk,
                            from_=self.from_number,
                            to=to_number,
                            media_url=[media_url]
                        )
                    else:
                        self.client.messages.create(
                            body=chunk,
                            from_=self.from_number,
                            to=to_number
                        )
            else:
                if media_url:
                    logger.info(f"Sending message with media_url: {media_url}")
                    msg = self.client.messages.create(
                        body=message,
                        from_=self.from_number,
                        to=to_number,
                        media_url=[media_url]
                    )
                    logger.info(f"Twilio response SID: {msg.sid}, status: {msg.status}")
                else:
                    self.client.messages.create(
                        body=message,
                        from_=self.from_number,
                        to=to_number
                    )

            logger.info(f"Message sent to {to_number}")
            return True

        except TwilioRestException as e:
            logger.error(f"Twilio error sending to {to_number}: {e}")
            return False
        except Exception as e:
            logger.error(f"Error sending message to {to_number}: {e}")
            return False

    def _split_message(self, message: str, max_length: int = 1500) -> list:
        """Split a long message into chunks."""
        if len(message) <= max_length:
            return [message]

        chunks = []
        paragraphs = message.split("\n\n")
        current_chunk = ""

        for para in paragraphs:
            if len(current_chunk) + len(para) + 2 <= max_length:
                if current_chunk:
                    current_chunk += "\n\n"
                current_chunk += para
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                if len(para) > max_length:
                    # Split by newlines
                    lines = para.split("\n")
                    current_chunk = ""
                    for line in lines:
                        if len(current_chunk) + len(line) + 1 <= max_length:
                            if current_chunk:
                                current_chunk += "\n"
                            current_chunk += line
                        else:
                            if current_chunk:
                                chunks.append(current_chunk)
                            current_chunk = line
                else:
                    current_chunk = para

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    def parse_command(self, message: str) -> Optional[str]:
        """Parse message to identify command."""
        normalized = message.lower().strip()

        # Check exact matches first
        if normalized in COMMANDS:
            return COMMANDS[normalized]

        # Check if message starts with a command (require word boundary)
        for cmd, action in COMMANDS.items():
            if normalized.startswith(cmd + " ") or normalized.startswith(cmd + "\n"):
                return action

        return None

    async def get_or_create_user(
        self,
        db: AsyncSession,
        phone_number: str,
        name: Optional[str] = None
    ) -> Tuple[User, bool]:
        """Get existing user or create new one. Returns (user, is_new)."""
        phone = phone_number.replace("whatsapp:", "")

        result = await db.execute(
            select(User).where(User.phone_number == phone)
        )
        user = result.scalar_one_or_none()

        if user:
            user.last_message_at = datetime.now()
            user.message_count += 1
            if name and not user.name:
                user.name = name
            return user, False
        else:
            user = User(
                phone_number=phone,
                name=name,
                last_message_at=datetime.now(),
                message_count=1,
                subscribed_to_morning_brief=True,
                timezone=detect_timezone_from_phone(phone),
            )
            db.add(user)
            await db.flush()
            logger.info(f"Created new user: {phone}")
            return user, True

    async def subscribe_user(self, db: AsyncSession, user: User) -> str:
        """Subscribe user to morning brief."""
        user.subscribed_to_morning_brief = True
        await db.flush()
        return """✅ *Subscribed to Morning Brief!*

You'll receive daily gold rates at 9 AM IST.

Commands:
• *gold* - Current gold rates
• *silver* - Silver rates
• *platinum* - Platinum rates
• *unsubscribe* - Stop daily updates
• *help* - All commands"""

    async def unsubscribe_user(self, db: AsyncSession, user: User) -> str:
        """Unsubscribe user from morning brief."""
        user.subscribed_to_morning_brief = False
        await db.flush()
        return """🔕 *Unsubscribed from Morning Brief*

You won't receive daily updates anymore.

You can still check rates anytime:
• *gold* - Current gold rates
• *subscribe* - Re-enable daily updates"""

    def get_help_message(self) -> str:
        """Get help message with all commands."""
        return """🙏 *JewelClaw - Help*

*Rate Commands:*
• *gold* - All gold karat rates (24K, 22K, 18K, 14K, 10K, 9K)
• *silver* - Silver rates
• *platinum* - Platinum rates
• *analysis* - Market analysis & trends

*Subscription Commands:*
• *subscribe* - Get daily morning brief at 9 AM
• *unsubscribe* - Stop daily updates

*Other:*
• *help* - Show this menu

_Just type any command to get started!_

📱 Powered by JewelClaw"""

    async def get_subscribed_users(self, db: AsyncSession) -> list:
        """Get all users subscribed to morning brief."""
        result = await db.execute(
            select(User).where(User.subscribed_to_morning_brief == True)
        )
        return list(result.scalars().all())

    async def check_rate_limit(self, db: AsyncSession, user: User) -> bool:
        """Check if user is within rate limits."""
        hour_ago = datetime.now() - timedelta(hours=1)

        result = await db.execute(
            select(func.count(Conversation.id))
            .where(Conversation.user_id == user.id)
            .where(Conversation.role == "user")
            .where(Conversation.created_at >= hour_ago)
        )
        message_count = result.scalar()

        if message_count >= settings.max_messages_per_hour:
            logger.warning(f"Rate limit exceeded for user {user.phone_number}")
            return False

        return True

    async def send_rate_limit_message(self, to_number: str):
        """Send rate limit notification."""
        message = """⚠️ *Rate Limit Reached*

You've sent too many messages in the last hour.
Please wait a bit before sending more.

This helps us serve everyone better! 🙏"""
        await self.send_message(to_number, message)

    async def send_welcome_message(self, to_number: str, name: Optional[str] = None):
        """Send welcome message to new users."""
        greeting = f"Hi {name}!" if name else "Hi there!"
        message = f"""🙏 *{greeting} Welcome to JewelClaw!*

I'm your AI assistant for gold & jewelry rates in India.

*Quick Commands:*
• *gold* - Live gold rates (all karats)
• *silver* - Silver rates
• *platinum* - Platinum rates
• *analysis* - Market trends
• *help* - All commands

You're automatically subscribed to our *Morning Brief* at 9 AM with daily rates and analysis.

_Type "gold" to get started!_"""

        await self.send_message(to_number, message)

    def parse_incoming_message(self, form_data: dict) -> Tuple[str, str, Optional[str]]:
        """Parse incoming Twilio webhook data."""
        phone_number = form_data.get("From", "")
        message_body = form_data.get("Body", "").strip()
        profile_name = form_data.get("ProfileName")

        return phone_number, message_body, profile_name


# Singleton instance
whatsapp_service = WhatsAppService()
