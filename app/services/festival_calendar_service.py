"""
Festival Calendar Service - Auto-updating Indian festival dates using Claude.

Lunar festivals shift yearly. Instead of hardcoding, we ask Claude once per year
to generate correct dates, store in DB, and use as the source of truth.
"""

import json
import logging
from typing import List, Dict, Optional

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.config import settings

logger = logging.getLogger(__name__)


# Fallback festivals (2026 approximate) - used ONLY if DB is empty AND Claude fails
FALLBACK_FESTIVALS = [
    (1, 1, "New Year", "festival", "New Year wishes"),
    (1, 13, "Lohri", "festival", "Lohri ki lakh lakh badhai"),
    (1, 14, "Makar Sankranti", "festival", "Til gul ghya, god god bola"),
    (1, 15, "Pongal", "festival", "Happy Pongal"),
    (1, 26, "Republic Day", "national", "Jai Hind"),
    (2, 14, "Valentine's Day", "festival", "Love and warmth"),
    (3, 14, "Holi", "festival", "Rang barse! Happy Holi"),
    (3, 8, "International Women's Day", "special", "Celebrating women"),
    (3, 26, "Ram Navami", "festival", "Jai Shri Ram"),
    (4, 13, "Baisakhi", "festival", "Happy Baisakhi"),
    (4, 14, "Ambedkar Jayanti", "national", "Jai Bhim"),
    (5, 1, "May Day", "national", "Workers Day"),
    (5, 10, "Mother's Day", "special", "Maa ke liye special"),
    (6, 21, "Father's Day", "special", "Papa ke liye"),
    (8, 15, "Independence Day", "national", "Vande Mataram"),
    (8, 28, "Raksha Bandhan", "festival", "Bhai-behen ka pyaar"),
    (9, 5, "Teacher's Day", "special", "Guru Brahma Guru Vishnu"),
    (9, 14, "Ganesh Chaturthi", "festival", "Ganpati Bappa Morya"),
    (10, 2, "Gandhi Jayanti", "national", "Bapu ko naman"),
    (10, 11, "Navratri Begins", "festival", "Navratri ki hardik shubhkamnayein"),
    (10, 20, "Dussehra", "festival", "Burai par acchai ki jeet"),
    (10, 29, "Karwa Chauth", "festival", "Suhagan ko shubhkamnayein"),
    (11, 6, "Dhanteras", "festival", "Dhan teras ki shubhkamnayein - time to buy gold!"),
    (11, 8, "Diwali", "festival", "Shubh Deepawali"),
    (11, 9, "Govardhan Puja", "festival", "Annakut ki shubhkamnayein"),
    (11, 10, "Bhai Dooj", "festival", "Bhai-behen ka rishta"),
    (12, 25, "Christmas", "festival", "Merry Christmas"),
    (12, 31, "New Year's Eve", "festival", "Naye saal ki shubhkamnayein"),
]


class FestivalCalendarService:
    """Auto-updating festival calendar using Claude AI."""

    def __init__(self):
        self._client = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        return self._client

    async def refresh_festival_calendar(self, db: AsyncSession, year: int) -> int:
        """
        Generate festival dates for a given year using Claude.
        Returns count of festivals created. Skips if year already populated.
        """
        from app.models import FestivalCalendar

        # Check if already populated
        result = await db.execute(
            select(FestivalCalendar).where(FestivalCalendar.year == year).limit(1)
        )
        if result.scalar_one_or_none():
            logger.info(f"Festival calendar for {year} already exists")
            return 0

        logger.info(f"Generating festival calendar for {year}...")

        # Ask Claude for accurate dates
        festivals = await self._generate_festival_dates(year)

        if not festivals:
            # Fallback to hardcoded list
            logger.warning(f"Claude failed, using fallback festivals for {year}")
            festivals = [
                {"month": m, "day": d, "name": n, "type": t, "hint": h, "is_lunar": False}
                for m, d, n, t, h in FALLBACK_FESTIVALS
            ]

        # Insert into DB
        count = 0
        for f in festivals:
            try:
                month = int(f["month"])
                day = int(f["day"])
                if not (1 <= month <= 12 and 1 <= day <= 31):
                    continue

                entry = FestivalCalendar(
                    year=year,
                    month=month,
                    day=day,
                    name=f["name"],
                    festival_type=f.get("type", "festival"),
                    greeting_hint=f.get("hint", ""),
                    is_lunar=f.get("is_lunar", False),
                )
                db.add(entry)
                count += 1
            except (ValueError, KeyError) as e:
                logger.warning(f"Skipping invalid festival entry: {f}, error: {e}")

        await db.flush()
        logger.info(f"Festival calendar {year}: {count} festivals saved")
        return count

    async def _generate_festival_dates(self, year: int) -> List[Dict]:
        """Use Claude to generate accurate festival dates for a year."""
        try:
            response = self.client.messages.create(
                model=settings.classifier_model,
                max_tokens=2000,
                messages=[{
                    "role": "user",
                    "content": f"""List all major Indian festivals and special days for the year {year} with EXACT correct dates.

Include these (with correct {year} dates, especially lunar ones that shift yearly):
- Holi, Diwali, Dussehra/Vijayadashami, Navratri start, Ganesh Chaturthi
- Raksha Bandhan, Janmashtami, Krishna Jayanti
- Makar Sankranti, Pongal, Lohri, Baisakhi
- Ram Navami, Hanuman Jayanti, Guru Nanak Jayanti
- Karwa Chauth, Dhanteras, Govardhan Puja, Bhai Dooj
- Eid ul-Fitr, Eid ul-Adha, Muharram
- Christmas, New Year, New Year's Eve
- Republic Day (Jan 26), Independence Day (Aug 15), Gandhi Jayanti (Oct 2)
- Valentine's Day, Mother's Day, Father's Day, Teacher's Day, Women's Day
- Ambedkar Jayanti (Apr 14), May Day (May 1)
- Onam, Ugadi, Gudi Padwa

Return ONLY a JSON array, no explanation:
[{{"month": 1, "day": 26, "name": "Republic Day", "type": "national", "hint": "Jai Hind", "is_lunar": false}}, ...]

type must be one of: "festival", "national", "special"
is_lunar: true for festivals that change date each year (Holi, Diwali, Eid, etc.)
hint: short traditional greeting or phrase (mix Hindi/English)"""
                }]
            )

            text = response.content[0].text.strip()

            # Try to parse JSON directly
            try:
                festivals = json.loads(text)
                if isinstance(festivals, list) and len(festivals) > 10:
                    logger.info(f"Claude generated {len(festivals)} festivals for {year}")
                    return festivals
            except json.JSONDecodeError:
                pass

            # Try extracting JSON from text
            import re
            json_match = re.search(r'\[[\s\S]*\]', text)
            if json_match:
                festivals = json.loads(json_match.group())
                if isinstance(festivals, list) and len(festivals) > 10:
                    logger.info(f"Claude generated {len(festivals)} festivals for {year}")
                    return festivals

            logger.warning(f"Could not parse Claude response for {year} festivals")
            return []

        except Exception as e:
            logger.error(f"Festival generation failed for {year}: {e}")
            return []

    async def get_festivals_for_date(
        self, db: AsyncSession, month: int, day: int, year: int
    ) -> List[Dict]:
        """Get festivals for a specific date from DB."""
        from app.models import FestivalCalendar

        result = await db.execute(
            select(FestivalCalendar).where(
                and_(
                    FestivalCalendar.year == year,
                    FestivalCalendar.month == month,
                    FestivalCalendar.day == day,
                )
            )
        )
        entries = result.scalars().all()

        return [
            {
                "name": e.name,
                "type": e.festival_type,
                "hint": e.greeting_hint,
            }
            for e in entries
        ]

    async def get_all_festivals_for_year(
        self, db: AsyncSession, year: int
    ) -> List[Dict]:
        """Get all festivals for a year (for load_festivals_for_user)."""
        from app.models import FestivalCalendar

        result = await db.execute(
            select(FestivalCalendar)
            .where(FestivalCalendar.year == year)
            .order_by(FestivalCalendar.month, FestivalCalendar.day)
        )
        entries = result.scalars().all()

        return [
            {
                "month": e.month,
                "day": e.day,
                "name": e.name,
                "type": e.festival_type,
                "hint": e.greeting_hint,
            }
            for e in entries
        ]


# Singleton
festival_calendar_service = FestivalCalendarService()
