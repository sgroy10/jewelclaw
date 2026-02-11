"""
RemindGenie - Never forget a birthday, anniversary, or festival.

Features:
- Add/list/delete personal reminders (birthdays, anniversaries, custom)
- Pre-loaded Indian festival calendar
- Claude-powered personalized greeting drafts
- Scheduled checks at 12:01 AM and 8:00 AM IST
"""

import logging
from datetime import datetime, date
from typing import Optional, List, Dict, Tuple

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, delete

from app.config import settings
from app.models import Reminder, User

logger = logging.getLogger(__name__)


# =============================================================================
# INDIAN FESTIVAL CALENDAR (month, day, name, greeting_hint)
# These are approximate fixed dates - some festivals shift yearly
# The scheduler will match on month+day
# =============================================================================

INDIAN_FESTIVALS = [
    # January
    (1, 1, "New Year", "festival", "New Year wishes"),
    (1, 13, "Lohri", "festival", "Lohri ki lakh lakh badhai"),
    (1, 14, "Makar Sankranti", "festival", "Til gul ghya, god god bola"),
    (1, 15, "Pongal", "festival", "Happy Pongal"),
    (1, 26, "Republic Day", "national", "Jai Hind"),
    # February
    (2, 14, "Valentine's Day", "festival", "Love and warmth"),
    # March
    (3, 8, "International Women's Day", "special", "Celebrating women"),
    (3, 14, "Holi", "festival", "Rang barse! Happy Holi"),
    # April
    (4, 1, "Bank Holiday / April Fools", "festival", ""),
    (4, 10, "Ram Navami", "festival", "Jai Shri Ram"),
    (4, 13, "Baisakhi", "festival", "Happy Baisakhi"),
    (4, 14, "Ambedkar Jayanti", "national", "Jai Bhim"),
    # May
    (5, 1, "May Day", "national", "Workers Day"),
    (5, 11, "Mother's Day", "special", "Maa ke liye special"),
    # June
    (6, 15, "Father's Day", "special", "Papa ke liye"),
    # July
    (7, 17, "Muharram", "festival", "Peace and reflection"),
    # August
    (8, 9, "Raksha Bandhan", "festival", "Bhai-behen ka pyaar"),
    (8, 15, "Independence Day", "national", "Vande Mataram"),
    (8, 26, "Janmashtami", "festival", "Nand Ghar Anand Bhayo"),
    # September
    (9, 5, "Teacher's Day", "special", "Guru Brahma Guru Vishnu"),
    (9, 7, "Ganesh Chaturthi", "festival", "Ganpati Bappa Morya"),
    # October
    (10, 2, "Gandhi Jayanti", "national", "Bapu ko naman"),
    (10, 3, "Navratri Begins", "festival", "Navratri ki hardik shubhkamnayein"),
    (10, 12, "Dussehra", "festival", "Burai par acchai ki jeet"),
    (10, 20, "Karwa Chauth", "festival", "Suhagan ko shubhkamnayein"),
    (10, 29, "Dhanteras", "festival", "Dhan teras ki shubhkamnayein - time to buy gold!"),
    (10, 31, "Diwali", "festival", "Shubh Deepawali"),
    # November
    (11, 1, "Govardhan Puja", "festival", "Annakut ki shubhkamnayein"),
    (11, 2, "Bhai Dooj", "festival", "Bhai-behen ka rishta"),
    (11, 15, "Guru Nanak Jayanti", "festival", "Waheguru"),
    (11, 19, "Chhath Puja", "festival", "Chhath Maiya ki jai"),
    # December
    (12, 25, "Christmas", "festival", "Merry Christmas"),
    (12, 31, "New Year's Eve", "festival", "Naye saal ki shubhkamnayein"),
]


# Month name mapping for parsing
MONTH_MAP = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "september": 9, "sept": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


class ReminderService:
    """Service for managing user reminders and generating greetings."""

    def __init__(self):
        self._client = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        return self._client

    # =========================================================================
    # CRUD OPERATIONS
    # =========================================================================

    async def add_reminder(
        self,
        db: AsyncSession,
        user_id: int,
        name: str,
        occasion: str,
        month: int,
        day: int,
        relationship: Optional[str] = None,
        year: Optional[int] = None,
        custom_note: Optional[str] = None,
    ) -> Reminder:
        """Add a new reminder for a user."""
        reminder = Reminder(
            user_id=user_id,
            name=name,
            relation=relationship,
            occasion=occasion,
            remind_month=month,
            remind_day=day,
            remind_year=year,
            custom_note=custom_note,
            is_active=True,
        )
        db.add(reminder)
        await db.flush()
        logger.info(f"Added reminder: {name} ({occasion}) on {month}/{day} for user {user_id}")
        return reminder

    async def list_reminders(
        self, db: AsyncSession, user_id: int, include_festivals: bool = False
    ) -> List[Dict]:
        """List all active reminders for a user."""
        result = await db.execute(
            select(Reminder)
            .where(and_(Reminder.user_id == user_id, Reminder.is_active == True))
            .order_by(Reminder.remind_month, Reminder.remind_day)
        )
        reminders = result.scalars().all()

        items = []
        for r in reminders:
            items.append({
                "id": r.id,
                "name": r.name,
                "relationship": r.relation or "",
                "occasion": r.occasion,
                "date": f"{r.remind_day} {self._month_name(r.remind_month)}",
                "month": r.remind_month,
                "day": r.remind_day,
                "custom_note": r.custom_note,
            })

        return items

    async def delete_reminder(
        self, db: AsyncSession, user_id: int, reminder_id: int
    ) -> bool:
        """Delete a reminder by ID (soft delete)."""
        result = await db.execute(
            select(Reminder).where(
                and_(Reminder.id == reminder_id, Reminder.user_id == user_id)
            )
        )
        reminder = result.scalar_one_or_none()
        if reminder:
            reminder.is_active = False
            await db.flush()
            logger.info(f"Deleted reminder {reminder_id} for user {user_id}")
            return True
        return False

    async def get_todays_reminders(
        self, db: AsyncSession, today: Optional[date] = None
    ) -> List[Tuple[User, Reminder]]:
        """Get all reminders for today across all users."""
        if today is None:
            import pytz
            ist = pytz.timezone("Asia/Kolkata")
            today = datetime.now(ist).date()

        month = today.month
        day = today.day

        # Get user reminders matching today
        result = await db.execute(
            select(Reminder, User)
            .join(User, Reminder.user_id == User.id)
            .where(
                and_(
                    Reminder.remind_month == month,
                    Reminder.remind_day == day,
                    Reminder.is_active == True,
                )
            )
        )
        rows = result.all()
        user_reminders = [(user, reminder) for reminder, user in rows]

        return user_reminders

    async def get_todays_festivals(self, today: Optional[date] = None) -> List[Dict]:
        """Get festivals for today from the built-in calendar."""
        if today is None:
            import pytz
            ist = pytz.timezone("Asia/Kolkata")
            today = datetime.now(ist).date()

        month = today.month
        day = today.day

        festivals = []
        for f_month, f_day, f_name, f_type, f_hint in INDIAN_FESTIVALS:
            if f_month == month and f_day == day:
                festivals.append({
                    "name": f_name,
                    "type": f_type,
                    "hint": f_hint,
                })
        return festivals

    async def load_festivals_for_user(
        self, db: AsyncSession, user_id: int
    ) -> int:
        """Pre-load all Indian festivals as reminders for a user. Returns count added."""
        # Check which festivals already exist for this user
        result = await db.execute(
            select(Reminder).where(
                and_(Reminder.user_id == user_id, Reminder.occasion == "festival")
            )
        )
        existing = result.scalars().all()
        existing_keys = {(r.remind_month, r.remind_day, r.name) for r in existing}

        count = 0
        for f_month, f_day, f_name, f_type, f_hint in INDIAN_FESTIVALS:
            if (f_month, f_day, f_name) not in existing_keys:
                reminder = Reminder(
                    user_id=user_id,
                    name=f_name,
                    relation="Festival" if f_type == "festival" else "National Day" if f_type == "national" else "Special Day",
                    occasion="festival",
                    remind_month=f_month,
                    remind_day=f_day,
                    custom_note=f_hint if f_hint else None,
                    is_active=True,
                )
                db.add(reminder)
                count += 1

        if count > 0:
            await db.flush()
            logger.info(f"Loaded {count} festivals for user {user_id}")
        return count

    # =========================================================================
    # GREETING GENERATION
    # =========================================================================

    async def draft_greeting(
        self,
        name: str,
        occasion: str,
        relationship: Optional[str] = None,
        custom_note: Optional[str] = None,
        style: str = "warm",  # "warm", "formal", "funny"
    ) -> str:
        """Use Claude to draft a personalized greeting message."""
        try:
            prompt = f"""Draft a short WhatsApp greeting message (2-3 lines max) for:

Person: {name}
Occasion: {occasion}
Relationship: {relationship or 'Friend'}
{f'Note: {custom_note}' if custom_note else ''}

Rules:
- Keep it SHORT - this is WhatsApp, 2-3 lines
- Make it warm and personal
- Use a mix of Hindi/English if the occasion is Indian (Hinglish)
- Include one relevant emoji
- Don't use "Dear" - too formal for WhatsApp
- End with the greeting, no "Copy and send" type instructions
- For birthdays: include age wishes naturally
- For festivals: include the festival-specific greeting/phrase
- For anniversaries: celebrate the milestone"""

            response = self.client.messages.create(
                model=settings.classifier_model,  # Use Haiku for speed + cost
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )

            greeting = response.content[0].text.strip()
            return greeting

        except Exception as e:
            logger.error(f"Greeting generation failed: {e}")
            # Fallback greetings
            if occasion == "birthday":
                return f"Happy Birthday {name}! Wishing you a wonderful year ahead! 🎂"
            elif occasion == "anniversary":
                return f"Happy Anniversary {name}! Wishing you many more beautiful years together! 💍"
            elif occasion == "festival":
                return f"Happy {name}! Wishing you joy and prosperity! 🪔"
            else:
                return f"Wishing you a wonderful {occasion}, {name}! 🌟"

    async def build_reminder_message(
        self,
        user_name: str,
        reminders: List[Dict],
        festivals: List[Dict],
        is_midnight: bool = True,
    ) -> str:
        """Build the full reminder WhatsApp message for a user."""
        if is_midnight:
            header = "🔔 *RemindGenie - Midnight Alert!*"
            subheader = "Don't miss these today:"
        else:
            header = "☀️ *RemindGenie - Morning Reminder*"
            subheader = "Your reminders for today:"

        lines = [header, f"_{subheader}_", ""]

        # Personal reminders (birthdays, anniversaries, custom)
        personal = [r for r in reminders if r.get("occasion") != "festival"]
        if personal:
            for r in personal:
                emoji = "🎂" if r["occasion"] == "birthday" else "💍" if r["occasion"] == "anniversary" else "📅"
                rel = f" ({r['relationship']})" if r.get("relationship") else ""
                lines.append(f"{emoji} *{r['name']}*{rel} - {r['occasion'].title()}")

                # Generate greeting
                greeting = await self.draft_greeting(
                    r["name"], r["occasion"], r.get("relationship"), r.get("custom_note")
                )
                lines.append(f"   ✉️ _Copy & send:_")
                lines.append(f"   {greeting}")
                lines.append("")

        # Festival reminders
        if festivals:
            lines.append("🪔 *Today's Festivals:*")
            for f in festivals:
                lines.append(f"   • *{f['name']}*")
                if f.get("hint"):
                    lines.append(f"     _{f['hint']}_")

            # Generate one festival greeting to copy
            if festivals:
                main_festival = festivals[0]
                festival_greeting = await self.draft_greeting(
                    main_festival["name"], "festival", "Festival"
                )
                lines.append("")
                lines.append(f"✉️ _Festival greeting to share:_")
                lines.append(f"{festival_greeting}")

        if not personal and not festivals:
            return ""  # Nothing to remind

        lines.append("")
        lines.append("_Powered by JewelClaw RemindGenie_")

        return "\n".join(lines)

    # =========================================================================
    # PARSING HELPERS
    # =========================================================================

    def parse_reminder_input(self, text: str) -> Optional[Dict]:
        """
        Parse natural language reminder input.
        Formats supported:
          remind add Mom | Mother | 15 March
          remind add Mom | Mother | 15 March | birthday
          remind add Diwali | Festival | 31 October
          remind add Priya | Customer | 20 June | anniversary
          remind add Meeting | Work | 15 March 2026
        """
        import re

        # Remove "remind add" prefix
        text = re.sub(r'^remind\s+add\s+', '', text, flags=re.IGNORECASE).strip()

        # Split by pipe
        parts = [p.strip() for p in text.split("|")]

        if len(parts) < 3:
            return None

        name = parts[0]
        relationship = parts[1] if len(parts) > 1 else None
        date_str = parts[2] if len(parts) > 2 else None
        occasion = parts[3].lower() if len(parts) > 3 else None

        if not date_str:
            return None

        # Parse date string (e.g., "15 March", "March 15", "15/3", "15-03-2026")
        parsed = self._parse_date_string(date_str)
        if not parsed:
            return None

        month, day, year = parsed

        # Auto-detect occasion if not provided
        if not occasion:
            rel_lower = (relationship or "").lower()
            if rel_lower in ("festival", "national", "special"):
                occasion = "festival"
            elif "annivers" in name.lower() or "annivers" in rel_lower:
                occasion = "anniversary"
            else:
                occasion = "birthday"  # Default

        return {
            "name": name,
            "relationship": relationship,
            "month": month,
            "day": day,
            "year": year,
            "occasion": occasion,
        }

    def _parse_date_string(self, text: str) -> Optional[Tuple[int, int, Optional[int]]]:
        """Parse various date formats into (month, day, year)."""
        import re
        text = text.strip().lower()

        # Format: "15 March" or "15 march 2026"
        match = re.match(r'(\d{1,2})\s+([a-z]+)(?:\s+(\d{4}))?', text)
        if match:
            day = int(match.group(1))
            month_str = match.group(2)
            year = int(match.group(3)) if match.group(3) else None
            month = MONTH_MAP.get(month_str)
            if month and 1 <= day <= 31:
                return (month, day, year)

        # Format: "March 15" or "March 15 2026"
        match = re.match(r'([a-z]+)\s+(\d{1,2})(?:\s+(\d{4}))?', text)
        if match:
            month_str = match.group(1)
            day = int(match.group(2))
            year = int(match.group(3)) if match.group(3) else None
            month = MONTH_MAP.get(month_str)
            if month and 1 <= day <= 31:
                return (month, day, year)

        # Format: "15/3" or "15/03" or "15/3/2026"
        match = re.match(r'(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{4}))?', text)
        if match:
            day = int(match.group(1))
            month = int(match.group(2))
            year = int(match.group(3)) if match.group(3) else None
            if 1 <= month <= 12 and 1 <= day <= 31:
                return (month, day, year)

        return None

    def _month_name(self, month: int) -> str:
        """Get month name from number."""
        months = [
            "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
        ]
        return months[month] if 1 <= month <= 12 else "?"

    def format_reminder_list(self, reminders: List[Dict]) -> str:
        """Format reminder list for WhatsApp display."""
        if not reminders:
            return """📅 *RemindGenie - Your Reminders*

No reminders yet!

*Add one:*
remind add Mom | Mother | 15 March
remind add Priya | Customer | 20 June | anniversary

*Load festivals:*
remind festivals

_JewelClaw never forgets!_"""

        lines = ["📅 *RemindGenie - Your Reminders*", ""]

        # Group by occasion type
        birthdays = [r for r in reminders if r["occasion"] == "birthday"]
        anniversaries = [r for r in reminders if r["occasion"] == "anniversary"]
        festivals = [r for r in reminders if r["occasion"] == "festival"]
        custom = [r for r in reminders if r["occasion"] not in ("birthday", "anniversary", "festival")]

        if birthdays:
            lines.append("🎂 *Birthdays:*")
            for r in birthdays:
                rel = f" ({r['relationship']})" if r['relationship'] else ""
                lines.append(f"   #{r['id']} {r['name']}{rel} - {r['date']}")
            lines.append("")

        if anniversaries:
            lines.append("💍 *Anniversaries:*")
            for r in anniversaries:
                rel = f" ({r['relationship']})" if r['relationship'] else ""
                lines.append(f"   #{r['id']} {r['name']}{rel} - {r['date']}")
            lines.append("")

        if festivals:
            lines.append(f"🪔 *Festivals:* ({len(festivals)} loaded)")
            # Show next 5 upcoming
            import pytz
            ist = pytz.timezone("Asia/Kolkata")
            now = datetime.now(ist)
            current_month = now.month
            current_day = now.day

            upcoming = sorted(
                festivals,
                key=lambda r: (
                    (r["month"] - current_month) % 12,
                    r["day"]
                )
            )
            for r in upcoming[:5]:
                lines.append(f"   {r['name']} - {r['date']}")
            if len(festivals) > 5:
                lines.append(f"   _...and {len(festivals) - 5} more_")
            lines.append("")

        if custom:
            lines.append("📌 *Custom:*")
            for r in custom:
                rel = f" ({r['relationship']})" if r['relationship'] else ""
                lines.append(f"   #{r['id']} {r['name']}{rel} - {r['date']}")
            lines.append("")

        total = len(reminders)
        lines.append(f"_Total: {total} reminders_")
        lines.append("")
        lines.append("*Commands:*")
        lines.append("remind add [name] | [relationship] | [date]")
        lines.append("remind delete [id]")

        return "\n".join(lines)


# Singleton
reminder_service = ReminderService()
