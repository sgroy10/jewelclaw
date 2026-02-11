"""
Pricing Engine - Per-user jewelry pricing profiles and instant quote generation.

Uses BusinessMemory to store each user's unique pricing configuration:
- Making charges per jewelry type (necklace, ring, bangle, earring, etc.)
- Wastage percentage per jewelry type
- Stone/diamond markup
- Hallmark charges
- GST preference (inclusive/exclusive)
- Custom labor rates

Generates formatted WhatsApp bills with full breakdown.
"""

import logging
import re
from typing import Optional, Dict, List, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.models import MetalRate, BusinessMemory
from app.services.business_memory_service import business_memory_service

logger = logging.getLogger(__name__)

# Default making charges by jewelry type (industry average %)
DEFAULT_MAKING_CHARGES = {
    "necklace": 14.0,
    "ring": 12.0,
    "bangle": 10.0,
    "earring": 15.0,
    "chain": 8.0,
    "pendant": 13.0,
    "bracelet": 11.0,
    "mangalsutra": 16.0,
    "nosering": 12.0,
    "anklet": 10.0,
    "coin": 3.0,
    "general": 14.0,
}

# Default wastage by jewelry type (%)
DEFAULT_WASTAGE = {
    "necklace": 3.0,
    "ring": 2.5,
    "bangle": 2.0,
    "earring": 3.5,
    "chain": 1.5,
    "pendant": 3.0,
    "bracelet": 2.5,
    "mangalsutra": 3.0,
    "nosering": 2.5,
    "anklet": 2.0,
    "coin": 0.5,
    "general": 2.5,
}

# Karat purity mapping
KARAT_PURITY = {
    "24k": 1.000,
    "22k": 0.9167,
    "18k": 0.750,
    "14k": 0.5833,
}

# Jewelry type aliases
JEWELRY_ALIASES = {
    "necklace": "necklace",
    "haar": "necklace",
    "set": "necklace",
    "ring": "ring",
    "angoothi": "ring",
    "bangle": "bangle",
    "kangan": "bangle",
    "chudi": "bangle",
    "earring": "earring",
    "earrings": "earring",
    "jhumka": "earring",
    "tops": "earring",
    "chain": "chain",
    "pendant": "pendant",
    "locket": "pendant",
    "bracelet": "bracelet",
    "mangalsutra": "mangalsutra",
    "nosering": "nosering",
    "nath": "nosering",
    "anklet": "anklet",
    "payal": "anklet",
    "coin": "coin",
}


class PricingEngineService:
    """Per-user jewelry pricing and quote generation."""

    # =========================================================================
    # PRICING PROFILE (read from BusinessMemory)
    # =========================================================================

    async def get_user_pricing_profile(
        self, db: AsyncSession, user_id: int
    ) -> Dict:
        """Get the user's full pricing profile from BusinessMemory."""
        memories = await business_memory_service.get_user_memory(
            db, user_id, category="making_charges"
        )
        wastage_memories = await business_memory_service.get_user_memory(
            db, user_id, category="pricing_rule"
        )

        making_charges = {}
        wastage = {}
        extras = {}

        for m in memories:
            # Extract jewelry type from key (e.g., "necklace_making_charge" -> "necklace")
            for jtype in DEFAULT_MAKING_CHARGES:
                if jtype in m.key.lower():
                    making_charges[jtype] = m.value_numeric or DEFAULT_MAKING_CHARGES[jtype]
                    break

        for m in wastage_memories:
            key_lower = m.key.lower()
            if "wastage" in key_lower:
                for jtype in DEFAULT_WASTAGE:
                    if jtype in key_lower:
                        wastage[jtype] = m.value_numeric or DEFAULT_WASTAGE[jtype]
                        break
            elif "hallmark" in key_lower:
                extras["hallmark_charge"] = m.value_numeric or 45.0
            elif "stone" in key_lower or "diamond" in key_lower:
                extras["stone_markup_pct"] = m.value_numeric or 0
            elif "gst" in key_lower:
                extras["gst_pct"] = m.value_numeric or 3.0

        return {
            "making_charges": making_charges,
            "wastage": wastage,
            "extras": extras,
        }

    async def save_making_charge(
        self,
        db: AsyncSession,
        user_id: int,
        jewelry_type: str,
        percentage: float,
    ):
        """Save a making charge for a specific jewelry type."""
        jtype = self._normalize_jewelry_type(jewelry_type)
        await business_memory_service.store_fact(
            db=db,
            user_id=user_id,
            category="making_charges",
            key=f"{jtype}_making_charge",
            value=f"{percentage}%",
            value_numeric=percentage,
            metal_type="gold",
            jewelry_category=jtype,
        )

    async def save_wastage(
        self,
        db: AsyncSession,
        user_id: int,
        jewelry_type: str,
        percentage: float,
    ):
        """Save wastage percentage for a specific jewelry type."""
        jtype = self._normalize_jewelry_type(jewelry_type)
        await business_memory_service.store_fact(
            db=db,
            user_id=user_id,
            category="pricing_rule",
            key=f"{jtype}_wastage",
            value=f"{percentage}%",
            value_numeric=percentage,
            jewelry_category=jtype,
        )

    async def save_hallmark_charge(
        self, db: AsyncSession, user_id: int, amount: float
    ):
        """Save hallmark charge per piece."""
        await business_memory_service.store_fact(
            db=db,
            user_id=user_id,
            category="pricing_rule",
            key="hallmark_charge",
            value=f"₹{amount:,.0f}",
            value_numeric=amount,
        )

    # =========================================================================
    # QUOTE GENERATION
    # =========================================================================

    async def generate_quote(
        self,
        db: AsyncSession,
        user_id: int,
        weight_grams: float,
        karat: str = "22k",
        jewelry_type: str = "general",
        making_charge_pct: Optional[float] = None,
        wastage_pct: Optional[float] = None,
        stone_cost: float = 0,
        quantity: int = 1,
        city: Optional[str] = None,
    ) -> Dict:
        """Generate a full jewelry quote with breakdown."""

        jtype = self._normalize_jewelry_type(jewelry_type)
        karat = karat.lower().replace(" ", "")
        if not karat.endswith("k"):
            karat += "k"

        # Get live gold rate
        rate_city = city or "Mumbai"
        result = await db.execute(
            select(MetalRate)
            .where(MetalRate.city == rate_city)
            .order_by(desc(MetalRate.recorded_at))
            .limit(1)
        )
        rate = result.scalar_one_or_none()
        if not rate:
            return {"error": f"Could not fetch gold rates for {rate_city}. Try 'gold' first."}

        # Get gold rate for this karat
        karat_rates = {
            "24k": rate.gold_24k,
            "22k": rate.gold_22k,
            "18k": rate.gold_18k or rate.gold_24k * KARAT_PURITY["18k"],
            "14k": rate.gold_14k or rate.gold_24k * KARAT_PURITY["14k"],
        }
        gold_rate_per_gram = karat_rates.get(karat, rate.gold_22k)

        # Get user's pricing profile
        profile = await self.get_user_pricing_profile(db, user_id)

        # Making charge: user input > user profile > default
        if making_charge_pct is None:
            making_charge_pct = profile["making_charges"].get(
                jtype, profile["making_charges"].get("general", DEFAULT_MAKING_CHARGES.get(jtype, 14.0))
            )

        # Wastage: user input > user profile > default
        if wastage_pct is None:
            wastage_pct = profile["wastage"].get(
                jtype, DEFAULT_WASTAGE.get(jtype, 2.5)
            )

        # Hallmark charge
        hallmark = profile["extras"].get("hallmark_charge", 45.0)

        # GST
        gst_pct = profile["extras"].get("gst_pct", 3.0)

        # Calculate
        gold_cost = weight_grams * gold_rate_per_gram
        wastage_cost = gold_cost * (wastage_pct / 100)
        making_cost = (gold_cost + wastage_cost) * (making_charge_pct / 100)
        subtotal = gold_cost + wastage_cost + making_cost + stone_cost + hallmark
        gst = subtotal * (gst_pct / 100)
        total = subtotal + gst

        # Per piece if quantity > 1
        grand_total = total * quantity

        return {
            "jewelry_type": jtype,
            "weight_grams": weight_grams,
            "karat": karat.upper(),
            "city": rate_city,
            "gold_rate_per_gram": round(gold_rate_per_gram, 0),
            "gold_cost": round(gold_cost, 0),
            "wastage_pct": wastage_pct,
            "wastage_cost": round(wastage_cost, 0),
            "making_charge_pct": making_charge_pct,
            "making_cost": round(making_cost, 0),
            "stone_cost": round(stone_cost, 0),
            "hallmark_charge": round(hallmark, 0),
            "subtotal": round(subtotal, 0),
            "gst_pct": gst_pct,
            "gst": round(gst, 0),
            "total_per_piece": round(total, 0),
            "quantity": quantity,
            "grand_total": round(grand_total, 0),
            "rate_date": rate.rate_date or "Today",
            "is_custom_making": making_charge_pct != DEFAULT_MAKING_CHARGES.get(jtype, 14.0),
        }

    def format_quote_message(self, quote: Dict) -> str:
        """Format a quote as a WhatsApp bill message."""
        if "error" in quote:
            return f"⚠️ {quote['error']}"

        jtype = quote["jewelry_type"].title()
        karat = quote["karat"]
        custom_tag = " ✓" if quote.get("is_custom_making") else " (default)"

        lines = [
            f"📋 *JewelClaw Quick Quote*",
            f"━━━━━━━━━━━━━━━━━━━━━",
            f"*{jtype}* | {karat} | {quote['weight_grams']}g",
            f"",
            f"💰 Gold Rate: ₹{quote['gold_rate_per_gram']:,.0f}/gm ({quote['city']})",
            f"📅 {quote['rate_date']}",
            f"",
            f"*Breakdown:*",
            f"   Gold ({quote['weight_grams']}g × ₹{quote['gold_rate_per_gram']:,.0f})  ₹{quote['gold_cost']:,.0f}",
            f"   Wastage ({quote['wastage_pct']}%)  ₹{quote['wastage_cost']:,.0f}",
            f"   Making ({quote['making_charge_pct']}%{custom_tag})  ₹{quote['making_cost']:,.0f}",
        ]

        if quote["stone_cost"] > 0:
            lines.append(f"   Stones  ₹{quote['stone_cost']:,.0f}")

        if quote["hallmark_charge"] > 0:
            lines.append(f"   Hallmark  ₹{quote['hallmark_charge']:,.0f}")

        lines.extend([
            f"   ─────────────────",
            f"   Subtotal  ₹{quote['subtotal']:,.0f}",
            f"   GST ({quote['gst_pct']}%)  ₹{quote['gst']:,.0f}",
            f"━━━━━━━━━━━━━━━━━━━━━",
            f"*TOTAL: ₹{quote['total_per_piece']:,.0f}*",
        ])

        if quote["quantity"] > 1:
            lines.append(f"*× {quote['quantity']} pcs = ₹{quote['grand_total']:,.0f}*")

        lines.extend([
            f"",
            f"_Your making charges are saved._",
            f"_Type 'price setup' to update rates._",
        ])

        return "\n".join(lines)

    # =========================================================================
    # INPUT PARSING
    # =========================================================================

    def parse_quote_input(self, text: str) -> Optional[Dict]:
        """
        Parse natural language quote request.
        Formats:
            quote 10g 22k necklace
            quote 5g 18k ring with 0.5ct diamond
            quote 15g bangle
            quote 8g 22k chain x3
        """
        text = re.sub(r'^quote\s+', '', text.strip().lower())

        # Extract weight
        weight_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:g|gm|gram|grams)', text)
        if not weight_match:
            # Try just a number at the start
            weight_match = re.match(r'(\d+(?:\.\d+)?)', text)
        if not weight_match:
            return None
        weight = float(weight_match.group(1))

        # Extract karat
        karat_match = re.search(r'(\d{1,2})\s*k(?:t|arat)?', text)
        karat = f"{karat_match.group(1)}k" if karat_match else "22k"

        # Extract jewelry type
        jewelry_type = "general"
        for alias, jtype in JEWELRY_ALIASES.items():
            if alias in text:
                jewelry_type = jtype
                break

        # Extract quantity
        qty_match = re.search(r'[x×]\s*(\d+)', text)
        quantity = int(qty_match.group(1)) if qty_match else 1

        # Extract stone cost
        stone_cost = 0.0
        stone_match = re.search(r'stone\s*(?:cost)?\s*(\d+(?:\.\d+)?)', text)
        if stone_match:
            stone_cost = float(stone_match.group(1))

        return {
            "weight_grams": weight,
            "karat": karat,
            "jewelry_type": jewelry_type,
            "quantity": quantity,
            "stone_cost": stone_cost,
        }

    def parse_setup_input(self, text: str) -> Optional[Dict]:
        """
        Parse price setup input.
        Formats:
            price set necklace 15%
            price set ring making 12
            price set bangle wastage 2.5
            price set hallmark 50
        """
        text = re.sub(r'^price\s+set\s+', '', text.strip().lower())

        # Hallmark charge
        hallmark_match = re.match(r'hallmark\s+(\d+(?:\.\d+)?)', text)
        if hallmark_match:
            return {"type": "hallmark", "value": float(hallmark_match.group(1))}

        # Wastage: "ring wastage 2.5"
        wastage_match = re.match(r'(\w+)\s+wastage\s+(\d+(?:\.\d+)?)', text)
        if wastage_match:
            return {
                "type": "wastage",
                "jewelry_type": wastage_match.group(1),
                "value": float(wastage_match.group(2)),
            }

        # Making charge: "necklace 15" or "necklace making 15"
        making_match = re.match(r'(\w+)\s+(?:making\s+)?(\d+(?:\.\d+)?)\s*%?', text)
        if making_match:
            return {
                "type": "making",
                "jewelry_type": making_match.group(1),
                "value": float(making_match.group(2)),
            }

        return None

    async def get_setup_summary(self, db: AsyncSession, user_id: int) -> str:
        """Get a formatted summary of the user's pricing setup."""
        profile = await self.get_user_pricing_profile(db, user_id)

        lines = [
            "⚙️ *Your Pricing Profile*",
            "━━━━━━━━━━━━━━━━━━━━━",
            "",
            "*Making Charges:*",
        ]

        # Show all jewelry types with user's rate or default
        for jtype, default_rate in DEFAULT_MAKING_CHARGES.items():
            if jtype == "general":
                continue
            user_rate = profile["making_charges"].get(jtype)
            if user_rate:
                lines.append(f"   {jtype.title()}: *{user_rate}%* ✓")
            else:
                lines.append(f"   {jtype.title()}: {default_rate}% _(default)_")

        lines.append("")
        lines.append("*Wastage:*")
        for jtype, default_rate in DEFAULT_WASTAGE.items():
            if jtype == "general":
                continue
            user_rate = profile["wastage"].get(jtype)
            if user_rate:
                lines.append(f"   {jtype.title()}: *{user_rate}%* ✓")
            else:
                lines.append(f"   {jtype.title()}: {default_rate}% _(default)_")

        hallmark = profile["extras"].get("hallmark_charge", 45.0)
        gst = profile["extras"].get("gst_pct", 3.0)

        lines.extend([
            "",
            f"*Other:*",
            f"   Hallmark: ₹{hallmark:,.0f}/pc",
            f"   GST: {gst}%",
            "",
            "*Update:*",
            "price set necklace 18",
            "price set ring wastage 3",
            "price set hallmark 50",
            "",
            "_✓ = your custom rate_",
        ])

        return "\n".join(lines)

    # =========================================================================
    # GUIDED SETUP FLOW
    # =========================================================================

    def get_setup_menu(self) -> str:
        """Return the setup menu for pricing configuration."""
        return """⚙️ *Price Setup - Configure Your Rates*

Set your making charges per jewelry type:

*Quick set (one at a time):*
price set necklace 15
price set ring 12
price set bangle 10
price set earring 16

*Set wastage:*
price set necklace wastage 3
price set ring wastage 2.5

*Other charges:*
price set hallmark 50

*View your profile:*
price profile

*Generate a quote:*
quote 10g 22k necklace

_Your rates are saved forever & used in all quotes!_"""

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _normalize_jewelry_type(self, text: str) -> str:
        """Normalize jewelry type to standard name."""
        text = text.lower().strip()
        return JEWELRY_ALIASES.get(text, text if text in DEFAULT_MAKING_CHARGES else "general")


# Singleton
pricing_engine = PricingEngineService()
