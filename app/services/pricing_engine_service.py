"""
Pricing Engine - Comprehensive jewelry pricing for the Indian industry.

Supports:
- Multiple pricing models: percentage, per-gram, per-piece (CFP), all-inclusive
- Plain gold, gold+CZ, gold+diamond (natural & lab-grown), gold+gemstone
- Setting charges by type (pave, prong, bezel, channel, invisible, micro-pave)
- Finishing charges (rhodium, two-tone, sandblast, enamel, antique)
- Gold loss/wastage per jewelry type
- INR and USD currency
- Cost price vs selling price with profit margin
- Per-user pricing profiles stored in BusinessMemory
"""

import logging
import re
import json
from typing import Optional, Dict, List, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.models import MetalRate, BusinessMemory
from app.services.business_memory_service import business_memory_service

logger = logging.getLogger(__name__)

# =============================================================================
# REFERENCE DATA
# =============================================================================

KARAT_PURITY = {
    "24k": 1.000,
    "22k": 0.9167,
    "18k": 0.750,
    "14k": 0.5833,
    "10k": 0.4167,
    "9k": 0.375,
}

# Jewelry type aliases (Hindi + English)
JEWELRY_ALIASES = {
    "necklace": "necklace", "haar": "necklace", "set": "necklace",
    "ring": "ring", "angoothi": "ring",
    "bangle": "bangle", "kangan": "bangle", "chudi": "bangle",
    "earring": "earring", "earrings": "earring", "jhumka": "earring", "tops": "earring",
    "chain": "chain",
    "pendant": "pendant", "locket": "pendant",
    "bracelet": "bracelet",
    "mangalsutra": "mangalsutra",
    "nosering": "nosering", "nath": "nosering",
    "anklet": "anklet", "payal": "anklet",
    "coin": "coin",
    "brooch": "brooch",
    "tikka": "tikka", "maangtikka": "tikka",
    "kamarband": "kamarband", "waistband": "kamarband",
}

# Default making charges by jewelry type (industry average %)
DEFAULT_MAKING_CHARGES = {
    "necklace": 14.0, "ring": 12.0, "bangle": 10.0, "earring": 15.0,
    "chain": 8.0, "pendant": 13.0, "bracelet": 11.0, "mangalsutra": 16.0,
    "nosering": 12.0, "anklet": 10.0, "coin": 3.0, "general": 14.0,
}

# Default wastage by jewelry type (%)
DEFAULT_WASTAGE = {
    "necklace": 3.0, "ring": 2.5, "bangle": 2.0, "earring": 3.5,
    "chain": 1.5, "pendant": 3.0, "bracelet": 2.5, "mangalsutra": 3.0,
    "nosering": 2.5, "anklet": 2.0, "coin": 0.5, "general": 2.5,
}

# =============================================================================
# PRICING MODELS
# =============================================================================

PRICING_MODELS = {
    "percentage": "Making charge as % of gold cost (e.g. 14%)",
    "per_gram": "Labor charge per gram in ₹ or $ (e.g. ₹800/gm)",
    "per_piece": "Fixed cost per piece / CFP (e.g. $3.25 per ring)",
    "all_inclusive": "All-in rate per gram including labor (e.g. ₹8,500/gm 22K)",
}

# =============================================================================
# STONE REFERENCE DATA
# =============================================================================

# CZ (Cubic Zirconia) rates per stone by setting type (USD)
CZ_RATES_USD = {
    "pave": 0.11,
    "prong": 0.14,
    "bezel": 0.22,
    "channel": 0.22,
    "micro_pave": 0.18,
    "wax_set": 0.12,
}

# CZ rates in INR per stone (approximate, for domestic market)
CZ_RATES_INR = {
    "pave": 10.0,
    "prong": 12.0,
    "bezel": 18.0,
    "channel": 18.0,
    "micro_pave": 15.0,
    "wax_set": 10.0,
}

# Diamond sieve size reference: sieve_range -> (mm_range, approx_carat_each)
DIAMOND_SIEVE_SIZES = {
    "000": {"mm": "0.8-1.0", "cts_each": 0.005, "label": "Micro melee"},
    "00": {"mm": "1.0-1.15", "cts_each": 0.007, "label": "Micro melee"},
    "0": {"mm": "1.15-1.25", "cts_each": 0.01, "label": "Small melee"},
    "1": {"mm": "1.25-1.35", "cts_each": 0.012, "label": "Small melee"},
    "2": {"mm": "1.35-1.50", "cts_each": 0.015, "label": "Melee"},
    "3": {"mm": "1.50-1.70", "cts_each": 0.02, "label": "Melee"},
    "4": {"mm": "1.70-1.80", "cts_each": 0.025, "label": "Melee"},
    "5": {"mm": "1.80-2.00", "cts_each": 0.03, "label": "Small round"},
    "6": {"mm": "2.00-2.20", "cts_each": 0.04, "label": "Round"},
    "7": {"mm": "2.20-2.40", "cts_each": 0.05, "label": "Round"},
    "8": {"mm": "2.40-2.70", "cts_each": 0.07, "label": "Round"},
    "9": {"mm": "2.70-3.00", "cts_each": 0.10, "label": "Round"},
    "10": {"mm": "3.00-3.30", "cts_each": 0.12, "label": "Large round"},
    "11": {"mm": "3.30-3.50", "cts_each": 0.15, "label": "Large round"},
    "12": {"mm": "3.50-3.80", "cts_each": 0.20, "label": "Large round"},
    "13": {"mm": "3.80-4.10", "cts_each": 0.25, "label": "Center stone"},
    "14": {"mm": "4.10-4.40", "cts_each": 0.30, "label": "Center stone"},
    "15": {"mm": "4.40-4.80", "cts_each": 0.40, "label": "Center stone"},
    "16+": {"mm": "4.80+", "cts_each": 0.50, "label": "Large center"},
}

# Default natural diamond rates (USD per carat) by sieve size range and quality
# Quality grades: D-F/VVS (top), G-H/VS (good), I-J/SI (commercial)
DEFAULT_DIAMOND_RATES_USD = {
    "melee_small": {  # Sieve 000-3 (under 1.7mm)
        "DEF_VVS": 900, "GH_VS": 600, "IJ_SI": 350,
    },
    "melee": {  # Sieve 4-7 (1.7-2.4mm)
        "DEF_VVS": 1400, "GH_VS": 900, "IJ_SI": 550,
    },
    "round_small": {  # Sieve 8-10 (2.4-3.3mm)
        "DEF_VVS": 2200, "GH_VS": 1500, "IJ_SI": 800,
    },
    "round_large": {  # Sieve 11-13 (3.3-4.1mm)
        "DEF_VVS": 4000, "GH_VS": 2800, "IJ_SI": 1500,
    },
    "center": {  # Sieve 14+ (4.1mm+)
        "DEF_VVS": 7000, "GH_VS": 5000, "IJ_SI": 2800,
    },
}

# Lab-grown diamond rates (roughly 75-85% cheaper than natural)
DEFAULT_LAB_DIAMOND_RATES_USD = {
    "melee_small": {"DEF_VVS": 180, "GH_VS": 120, "IJ_SI": 70},
    "melee": {"DEF_VVS": 280, "GH_VS": 180, "IJ_SI": 110},
    "round_small": {"DEF_VVS": 440, "GH_VS": 300, "IJ_SI": 160},
    "round_large": {"DEF_VVS": 800, "GH_VS": 560, "IJ_SI": 300},
    "center": {"DEF_VVS": 1400, "GH_VS": 1000, "IJ_SI": 560},
}

# Gemstone rates (USD per carat, approximate ranges)
DEFAULT_GEMSTONE_RATES_USD = {
    # Precious
    "ruby": {"low": 100, "mid": 500, "high": 2000},
    "emerald": {"low": 80, "mid": 400, "high": 1500},
    "sapphire": {"low": 100, "mid": 600, "high": 2500},
    # Semi-precious
    "amethyst": {"low": 5, "mid": 15, "high": 40},
    "topaz": {"low": 8, "mid": 25, "high": 60},
    "garnet": {"low": 5, "mid": 20, "high": 50},
    "peridot": {"low": 10, "mid": 30, "high": 80},
    "citrine": {"low": 5, "mid": 15, "high": 40},
    "tanzanite": {"low": 50, "mid": 200, "high": 800},
    "opal": {"low": 10, "mid": 50, "high": 200},
    "tourmaline": {"low": 20, "mid": 80, "high": 300},
    "aquamarine": {"low": 15, "mid": 60, "high": 200},
}

# Setting charges by type (for diamonds/gemstones)
DEFAULT_SETTING_RATES_USD = {
    "pave": 0.15,
    "prong": 0.18,
    "bezel": 0.22,
    "channel": 0.22,
    "invisible": 0.85,
    "micro_pave": 0.30,
    "wax_set": 0.14,
    "flush": 0.20,
}

DEFAULT_SETTING_RATES_INR = {
    "pave": 12, "prong": 15, "bezel": 18, "channel": 18,
    "invisible": 70, "micro_pave": 25, "wax_set": 12, "flush": 16,
}

# Finishing charges per piece (USD)
DEFAULT_FINISHING_RATES_USD = {
    "rhodium": 1.00,
    "black_rhodium": 1.50,
    "two_tone": 0.75,
    "sandblast": 0.50,
    "enamel": 1.25,
    "antique": 1.00,
    "matte": 0.50,
    "hammered": 0.75,
}

DEFAULT_FINISHING_RATES_INR = {
    "rhodium": 80, "black_rhodium": 125, "two_tone": 60, "sandblast": 40,
    "enamel": 100, "antique": 80, "matte": 40, "hammered": 60,
}

# Color stone setting charges by size (USD per stone)
COLOR_STONE_SETTING_USD = {
    "below_3mm": 0.22,
    "3_5mm": 0.28,
    "5_7mm": 0.60,
    "above_7mm": 0.85,
}

# Center diamond setting charges by carat (USD per stone)
CENTER_DIAMOND_SETTING_USD = {
    "0.10_0.15": 0.60,
    "0.15_0.25": 0.85,
    "0.25_0.49": 1.50,
    "0.50_0.99": 2.50,
    "1.00+": 4.50,
}


def _sieve_to_size_category(sieve: str) -> str:
    """Map sieve size to rate category."""
    try:
        s = sieve.replace("+", "")
        if s in ("000", "00", "0", "1", "2", "3"):
            return "melee_small"
        elif s in ("4", "5", "6", "7"):
            return "melee"
        elif s in ("8", "9", "10"):
            return "round_small"
        elif s in ("11", "12", "13"):
            return "round_large"
        else:
            return "center"
    except Exception:
        return "melee"


def _normalize_quality(quality: str) -> str:
    """Normalize diamond quality to our categories."""
    q = quality.upper().replace(" ", "").replace("-", "_").replace("/", "_")
    if any(x in q for x in ("DEF", "D_F", "D_E_F", "EF")):
        if any(x in q for x in ("VVS", "IF", "FL")):
            return "DEF_VVS"
        return "DEF_VVS"
    if any(x in q for x in ("GH", "G_H")):
        if any(x in q for x in ("VS", "VVS")):
            return "GH_VS"
        return "GH_VS"
    if any(x in q for x in ("IJ", "I_J", "KL")):
        return "IJ_SI"
    # Fallback by clarity alone
    if "VVS" in q:
        return "DEF_VVS"
    if "VS" in q:
        return "GH_VS"
    if "SI" in q:
        return "IJ_SI"
    return "GH_VS"  # Default to mid-grade


class PricingEngineService:
    """Comprehensive jewelry pricing engine."""

    # =========================================================================
    # PRICING PROFILE (stored per-user in BusinessMemory)
    # =========================================================================

    async def get_user_pricing_profile(self, db: AsyncSession, user_id: int) -> Dict:
        """Get the user's full pricing profile from BusinessMemory."""
        memories = await business_memory_service.get_user_memory(
            db, user_id, category="pricing_profile"
        )
        # Also get legacy making_charges and pricing_rule entries
        legacy_mc = await business_memory_service.get_user_memory(
            db, user_id, category="making_charges"
        )
        legacy_rules = await business_memory_service.get_user_memory(
            db, user_id, category="pricing_rule"
        )

        profile = {
            "pricing_model": "percentage",  # default
            "currency": "INR",
            "making_charges": {},
            "labor_per_gram": {},
            "cfp_rates": {},
            "wastage": {},
            "hallmark_charge": 45.0,
            "gst_pct": 3.0,
            "gold_loss_pct": None,
            "profit_margin_pct": None,
            "cz_rates": {},
            "diamond_rates": {},
            "lab_diamond_rates": {},
            "gemstone_rates": {},
            "setting_rates": {},
            "finishing_rates": {},
            "show_cost_price": False,
        }

        for m in memories:
            key = m.key.lower()
            try:
                if key == "pricing_model":
                    profile["pricing_model"] = m.value
                elif key == "currency":
                    profile["currency"] = m.value.upper()
                elif key == "hallmark_charge":
                    profile["hallmark_charge"] = m.value_numeric or 45.0
                elif key == "gst_pct":
                    profile["gst_pct"] = m.value_numeric or 3.0
                elif key == "gold_loss_pct":
                    profile["gold_loss_pct"] = m.value_numeric
                elif key == "profit_margin_pct":
                    profile["profit_margin_pct"] = m.value_numeric
                elif key == "show_cost_price":
                    profile["show_cost_price"] = m.value.lower() in ("true", "yes", "1")
                elif key.startswith("making_") and key != "making_charges":
                    jtype = key.replace("making_", "").replace("_charge", "")
                    if jtype:  # Skip empty jewelry type
                        profile["making_charges"][jtype] = m.value_numeric
                elif key.startswith("labor_pergram_"):
                    jtype = key.replace("labor_pergram_", "")
                    profile["labor_per_gram"][jtype] = m.value_numeric
                elif key.startswith("cfp_"):
                    jtype = key.replace("cfp_", "")
                    profile["cfp_rates"][jtype] = m.value_numeric
                elif key.startswith("wastage_"):
                    jtype = key.replace("wastage_", "")
                    profile["wastage"][jtype] = m.value_numeric
                elif key.startswith("cz_"):
                    setting = key.replace("cz_", "")
                    profile["cz_rates"][setting] = m.value_numeric
                elif key.startswith("diamond_"):
                    # e.g. diamond_melee_GH_VS
                    parts = key.replace("diamond_", "")
                    profile["diamond_rates"][parts] = m.value_numeric
                elif key.startswith("lab_diamond_"):
                    parts = key.replace("lab_diamond_", "")
                    profile["lab_diamond_rates"][parts] = m.value_numeric
                elif key.startswith("gemstone_"):
                    stone = key.replace("gemstone_", "")
                    profile["gemstone_rates"][stone] = m.value_numeric
                elif key.startswith("setting_"):
                    stype = key.replace("setting_", "")
                    profile["setting_rates"][stype] = m.value_numeric
                elif key.startswith("finishing_"):
                    ftype = key.replace("finishing_", "")
                    profile["finishing_rates"][ftype] = m.value_numeric
            except Exception as e:
                logger.warning(f"Error parsing pricing memory {key}: {e}")

        # Merge legacy making_charges entries
        for m in legacy_mc:
            for jtype in DEFAULT_MAKING_CHARGES:
                if jtype in m.key.lower() and jtype not in profile["making_charges"]:
                    profile["making_charges"][jtype] = m.value_numeric or DEFAULT_MAKING_CHARGES[jtype]

        # Merge legacy pricing_rule entries
        for m in legacy_rules:
            key_lower = m.key.lower()
            if "wastage" in key_lower:
                for jtype in DEFAULT_WASTAGE:
                    if jtype in key_lower and jtype not in profile["wastage"]:
                        profile["wastage"][jtype] = m.value_numeric
            elif "hallmark" in key_lower and profile["hallmark_charge"] == 45.0:
                profile["hallmark_charge"] = m.value_numeric or 45.0
            elif "gst" in key_lower and profile["gst_pct"] == 3.0:
                profile["gst_pct"] = m.value_numeric or 3.0

        return profile

    async def save_pricing_field(
        self, db: AsyncSession, user_id: int, key: str, value: str,
        value_numeric: float = None, metal_type: str = None, jewelry_category: str = None,
    ):
        """Save any pricing profile field."""
        await business_memory_service.store_fact(
            db=db, user_id=user_id,
            category="pricing_profile",
            key=key, value=value,
            value_numeric=value_numeric,
            metal_type=metal_type,
            jewelry_category=jewelry_category,
        )

    async def save_pricing_model(self, db: AsyncSession, user_id: int, model: str):
        """Save the user's pricing model preference."""
        await self.save_pricing_field(db, user_id, "pricing_model", model)

    async def save_currency(self, db: AsyncSession, user_id: int, currency: str):
        """Save currency preference (INR or USD)."""
        await self.save_pricing_field(db, user_id, "currency", currency.upper())

    async def save_making_charge(self, db: AsyncSession, user_id: int, jewelry_type: str, percentage: float):
        """Save a percentage-based making charge."""
        jtype = self._normalize_jewelry_type(jewelry_type)
        await self.save_pricing_field(
            db, user_id, f"making_{jtype}",
            f"{percentage}%", percentage, "gold", jtype,
        )

    async def save_labor_per_gram(self, db: AsyncSession, user_id: int, jewelry_type: str, rate: float):
        """Save a per-gram labor rate."""
        jtype = self._normalize_jewelry_type(jewelry_type)
        await self.save_pricing_field(
            db, user_id, f"labor_pergram_{jtype}",
            f"₹{rate:,.0f}/gm", rate, "gold", jtype,
        )

    async def save_cfp_rate(self, db: AsyncSession, user_id: int, jewelry_type: str, rate: float):
        """Save a CFP (cost for piece) rate."""
        jtype = self._normalize_jewelry_type(jewelry_type)
        await self.save_pricing_field(
            db, user_id, f"cfp_{jtype}",
            f"${rate:.2f}" if rate < 1000 else f"₹{rate:,.0f}", rate, "gold", jtype,
        )

    async def save_wastage(self, db: AsyncSession, user_id: int, jewelry_type: str, percentage: float):
        """Save wastage percentage."""
        jtype = self._normalize_jewelry_type(jewelry_type)
        await self.save_pricing_field(
            db, user_id, f"wastage_{jtype}",
            f"{percentage}%", percentage, "gold", jtype,
        )

    async def save_hallmark_charge(self, db: AsyncSession, user_id: int, amount: float):
        """Save hallmark charge per piece."""
        await self.save_pricing_field(db, user_id, "hallmark_charge", f"₹{amount:,.0f}", amount)

    async def save_cz_rate(self, db: AsyncSession, user_id: int, setting_type: str, rate: float):
        """Save CZ rate per stone for a setting type."""
        await self.save_pricing_field(
            db, user_id, f"cz_{setting_type}",
            f"₹{rate:,.0f}/stone" if rate >= 1 else f"${rate:.2f}/stone", rate,
        )

    async def save_diamond_rate(
        self, db: AsyncSession, user_id: int,
        size_category: str, quality: str, rate_per_ct: float, is_lab: bool = False,
    ):
        """Save diamond rate per carat for a size+quality combo."""
        prefix = "lab_diamond" if is_lab else "diamond"
        quality_key = _normalize_quality(quality)
        key = f"{prefix}_{size_category}_{quality_key}"
        await self.save_pricing_field(
            db, user_id, key,
            f"${rate_per_ct:,.0f}/ct", rate_per_ct,
        )

    async def save_setting_rate(self, db: AsyncSession, user_id: int, setting_type: str, rate: float):
        """Save per-stone setting charge."""
        await self.save_pricing_field(
            db, user_id, f"setting_{setting_type}",
            f"₹{rate:,.0f}" if rate >= 1 else f"${rate:.2f}", rate,
        )

    async def save_finishing_rate(self, db: AsyncSession, user_id: int, finishing_type: str, rate: float):
        """Save per-piece finishing charge."""
        await self.save_pricing_field(
            db, user_id, f"finishing_{finishing_type}",
            f"₹{rate:,.0f}" if rate >= 1 else f"${rate:.2f}", rate,
        )

    async def save_profit_margin(self, db: AsyncSession, user_id: int, margin_pct: float):
        """Save profit margin percentage."""
        await self.save_pricing_field(db, user_id, "profit_margin_pct", f"{margin_pct}%", margin_pct)

    async def save_gold_loss(self, db: AsyncSession, user_id: int, loss_pct: float):
        """Save gold loss percentage."""
        await self.save_pricing_field(db, user_id, "gold_loss_pct", f"{loss_pct}%", loss_pct)

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
        # Stone details
        cz_count: int = 0,
        cz_setting: str = "pave",
        diamonds: Optional[List[Dict]] = None,
        # e.g. [{"sieve": "7", "count": 20, "quality": "GH-VS", "lab": False}]
        gemstones: Optional[List[Dict]] = None,
        # e.g. [{"stone": "ruby", "carats": 1.5, "grade": "mid"}]
        # Finishing
        finishing: Optional[List[str]] = None,
        # e.g. ["rhodium", "enamel"]
        # Override pricing model
        labor_per_gram: Optional[float] = None,
        cfp_rate: Optional[float] = None,
        # Currency override
        currency: Optional[str] = None,
    ) -> Dict:
        """Generate a comprehensive jewelry quote with full breakdown."""

        jtype = self._normalize_jewelry_type(jewelry_type)
        karat = karat.lower().replace(" ", "")
        if not karat.endswith("k"):
            karat += "k"

        # Get user's pricing profile
        profile = await self.get_user_pricing_profile(db, user_id)
        cur = (currency or profile["currency"]).upper()

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

        # Gold rate for this karat
        karat_rates = {
            "24k": rate.gold_24k,
            "22k": rate.gold_22k,
            "18k": rate.gold_18k or rate.gold_24k * KARAT_PURITY["18k"],
            "14k": rate.gold_14k or rate.gold_24k * KARAT_PURITY["14k"],
            "10k": rate.gold_24k * KARAT_PURITY["10k"],
            "9k": rate.gold_24k * KARAT_PURITY["9k"],
        }
        gold_rate_per_gram = karat_rates.get(karat, rate.gold_22k)

        # USD/INR for conversions
        usd_inr = rate.usd_inr or 83.5  # fallback

        # Convert gold rate to working currency
        if cur == "USD":
            gold_rate_per_gram_cur = gold_rate_per_gram / usd_inr
        else:
            gold_rate_per_gram_cur = gold_rate_per_gram

        # ---- GOLD COST ----
        gold_cost = weight_grams * gold_rate_per_gram_cur

        # ---- WASTAGE ----
        if wastage_pct is None:
            wastage_pct = profile["wastage"].get(jtype, DEFAULT_WASTAGE.get(jtype, 2.5))
        wastage_cost = gold_cost * (wastage_pct / 100)

        # ---- GOLD LOSS ----
        gold_loss_pct = profile.get("gold_loss_pct")
        gold_loss_cost = gold_cost * (gold_loss_pct / 100) if gold_loss_pct else 0

        # ---- MAKING/LABOR CHARGE ----
        pricing_model = profile["pricing_model"]
        making_cost = 0
        making_detail = ""

        if cfp_rate is not None:
            making_cost = cfp_rate
            making_detail = f"CFP ${cfp_rate:.2f}" if cur == "USD" else f"CFP ₹{cfp_rate:,.0f}"
            pricing_model = "per_piece"
        elif labor_per_gram is not None:
            making_cost = weight_grams * labor_per_gram
            making_detail = f"{_fmt(labor_per_gram, cur)}/gm × {weight_grams}g"
            pricing_model = "per_gram"
        elif pricing_model == "per_gram":
            lpg = profile["labor_per_gram"].get(jtype, profile["labor_per_gram"].get("general", 0))
            if lpg:
                making_cost = weight_grams * lpg
                making_detail = f"{_fmt(lpg, cur)}/gm × {weight_grams}g"
            else:
                # Fallback to percentage
                pct = profile["making_charges"].get(jtype, DEFAULT_MAKING_CHARGES.get(jtype, 14.0))
                making_cost = (gold_cost + wastage_cost) * (pct / 100)
                making_detail = f"{pct}%"
        elif pricing_model == "per_piece":
            cfp = profile["cfp_rates"].get(jtype, 0)
            if cfp:
                making_cost = cfp
                making_detail = f"CFP {_fmt(cfp, cur)}"
            else:
                pct = profile["making_charges"].get(jtype, DEFAULT_MAKING_CHARGES.get(jtype, 14.0))
                making_cost = (gold_cost + wastage_cost) * (pct / 100)
                making_detail = f"{pct}%"
        elif pricing_model == "all_inclusive":
            # All-in rate per gram - gold cost is already included
            aig = profile["labor_per_gram"].get(jtype, 0)
            if aig:
                total_all_in = weight_grams * aig
                making_cost = total_all_in - gold_cost
                making_detail = f"All-in {_fmt(aig, cur)}/gm"
            else:
                pct = profile["making_charges"].get(jtype, DEFAULT_MAKING_CHARGES.get(jtype, 14.0))
                making_cost = (gold_cost + wastage_cost) * (pct / 100)
                making_detail = f"{pct}%"
        else:
            # Default: percentage
            if making_charge_pct is None:
                making_charge_pct = profile["making_charges"].get(
                    jtype, profile["making_charges"].get("general", DEFAULT_MAKING_CHARGES.get(jtype, 14.0))
                )
            making_cost = (gold_cost + wastage_cost) * (making_charge_pct / 100)
            making_detail = f"{making_charge_pct}%"

        # ---- CZ STONES ----
        cz_cost = 0
        cz_detail = ""
        if cz_count > 0:
            cz_setting_norm = cz_setting.lower().replace(" ", "_").replace("-", "_")
            if cur == "USD":
                per_stone = profile["cz_rates"].get(cz_setting_norm, CZ_RATES_USD.get(cz_setting_norm, 0.11))
            else:
                per_stone = profile["cz_rates"].get(cz_setting_norm, CZ_RATES_INR.get(cz_setting_norm, 10.0))
            cz_cost = cz_count * per_stone
            cz_detail = f"{cz_count} stones × {_fmt(per_stone, cur)} ({cz_setting_norm})"

        # ---- DIAMONDS ----
        diamond_cost = 0
        diamond_details = []
        diamond_setting_cost = 0
        if diamonds:
            for d in diamonds:
                sieve = str(d.get("sieve", "7"))
                count = d.get("count", 1)
                quality = d.get("quality", "GH-VS")
                is_lab = d.get("lab", False)
                total_cts = d.get("total_carats", None)

                size_cat = _sieve_to_size_category(sieve)
                quality_key = _normalize_quality(quality)

                # Get rate per carat
                if is_lab:
                    rate_table = profile.get("lab_diamond_rates", {})
                    rate_key = f"{size_cat}_{quality_key}"
                    rate_per_ct = rate_table.get(rate_key, DEFAULT_LAB_DIAMOND_RATES_USD.get(size_cat, {}).get(quality_key, 200))
                else:
                    rate_table = profile.get("diamond_rates", {})
                    rate_key = f"{size_cat}_{quality_key}"
                    rate_per_ct = rate_table.get(rate_key, DEFAULT_DIAMOND_RATES_USD.get(size_cat, {}).get(quality_key, 900))

                # Calculate total carats
                if total_cts is None:
                    cts_each = DIAMOND_SIEVE_SIZES.get(sieve, {}).get("cts_each", 0.03)
                    total_cts = count * cts_each

                # Diamond cost (always in USD first, then convert)
                cost_usd = total_cts * rate_per_ct
                if cur == "USD":
                    d_cost = cost_usd
                else:
                    d_cost = cost_usd * usd_inr

                diamond_cost += d_cost

                lab_tag = " (lab)" if is_lab else ""
                diamond_details.append(
                    f"Sieve {sieve} × {count}{lab_tag}: {total_cts:.2f}ct @ ${rate_per_ct:,.0f}/ct = {_fmt(d_cost, cur)}"
                )

                # Setting charges for diamonds
                setting_type = d.get("setting", "prong")
                setting_type_norm = setting_type.lower().replace(" ", "_").replace("-", "_")
                if cur == "USD":
                    set_rate = profile["setting_rates"].get(setting_type_norm, DEFAULT_SETTING_RATES_USD.get(setting_type_norm, 0.18))
                else:
                    set_rate = profile["setting_rates"].get(setting_type_norm, DEFAULT_SETTING_RATES_INR.get(setting_type_norm, 15))
                diamond_setting_cost += count * set_rate

        # ---- GEMSTONES ----
        gemstone_cost = 0
        gemstone_details = []
        if gemstones:
            for g in gemstones:
                stone = g.get("stone", "ruby").lower()
                carats = g.get("carats", 1.0)
                grade = g.get("grade", "mid")  # low, mid, high

                rate_per_ct = profile["gemstone_rates"].get(
                    stone,
                    DEFAULT_GEMSTONE_RATES_USD.get(stone, {}).get(grade, 100)
                )

                cost_usd = carats * rate_per_ct
                if cur == "USD":
                    g_cost = cost_usd
                else:
                    g_cost = cost_usd * usd_inr

                gemstone_cost += g_cost
                gemstone_details.append(f"{stone.title()} {carats}ct @ ${rate_per_ct:,.0f}/ct = {_fmt(g_cost, cur)}")

        # ---- FINISHING ----
        finishing_cost = 0
        finishing_details = []
        if finishing:
            for f_type in finishing:
                f_norm = f_type.lower().replace(" ", "_").replace("-", "_")
                if cur == "USD":
                    f_rate = profile["finishing_rates"].get(f_norm, DEFAULT_FINISHING_RATES_USD.get(f_norm, 0.75))
                else:
                    f_rate = profile["finishing_rates"].get(f_norm, DEFAULT_FINISHING_RATES_INR.get(f_norm, 60))
                finishing_cost += f_rate
                finishing_details.append(f"{f_type.title()}: {_fmt(f_rate, cur)}")

        # ---- HALLMARK ----
        hallmark = profile["hallmark_charge"]
        if cur == "USD":
            hallmark = hallmark / usd_inr  # Convert ₹45 -> ~$0.54

        # ---- STONE COST (legacy param, added on top) ----
        total_stone_cost = stone_cost + cz_cost + diamond_cost + gemstone_cost
        total_setting_cost = diamond_setting_cost

        # ---- SUBTOTAL & GST ----
        subtotal = (gold_cost + wastage_cost + gold_loss_cost + making_cost
                    + total_stone_cost + total_setting_cost + finishing_cost + hallmark)
        gst_pct = profile["gst_pct"]
        if cur == "USD":
            gst_pct = 0  # No GST on exports
        gst = subtotal * (gst_pct / 100)
        cost_price = subtotal + gst

        # ---- PROFIT MARGIN (selling price) ----
        profit_margin_pct = profile.get("profit_margin_pct")
        selling_price = cost_price
        profit = 0
        if profit_margin_pct:
            profit = cost_price * (profit_margin_pct / 100)
            selling_price = cost_price + profit

        # Per piece and grand total
        total_per_piece = selling_price if profit_margin_pct else cost_price
        grand_total = total_per_piece * quantity

        result_dict = {
            "jewelry_type": jtype,
            "weight_grams": weight_grams,
            "karat": karat.upper(),
            "city": rate_city,
            "currency": cur,
            "usd_inr": usd_inr,
            "pricing_model": pricing_model,
            # Gold
            "gold_rate_per_gram": round(gold_rate_per_gram_cur, 2),
            "gold_cost": round(gold_cost, 2),
            # Wastage
            "wastage_pct": wastage_pct,
            "wastage_cost": round(wastage_cost, 2),
            # Gold loss
            "gold_loss_pct": gold_loss_pct,
            "gold_loss_cost": round(gold_loss_cost, 2),
            # Making
            "making_detail": making_detail,
            "making_cost": round(making_cost, 2),
            # CZ
            "cz_count": cz_count,
            "cz_setting": cz_setting,
            "cz_cost": round(cz_cost, 2),
            "cz_detail": cz_detail,
            # Diamonds
            "diamond_cost": round(diamond_cost, 2),
            "diamond_details": diamond_details,
            "diamond_setting_cost": round(diamond_setting_cost, 2),
            # Gemstones
            "gemstone_cost": round(gemstone_cost, 2),
            "gemstone_details": gemstone_details,
            # Finishing
            "finishing_cost": round(finishing_cost, 2),
            "finishing_details": finishing_details,
            # Hallmark
            "hallmark_charge": round(hallmark, 2),
            # Totals
            "subtotal": round(subtotal, 2),
            "gst_pct": gst_pct,
            "gst": round(gst, 2),
            "cost_price": round(cost_price, 2),
            "profit_margin_pct": profit_margin_pct,
            "profit": round(profit, 2),
            "selling_price": round(selling_price, 2),
            "total_per_piece": round(total_per_piece, 2),
            "quantity": quantity,
            "grand_total": round(grand_total, 2),
            "rate_date": rate.rate_date or "Today",
            "is_custom_making": making_detail != f"{DEFAULT_MAKING_CHARGES.get(jtype, 14.0)}%",
        }

        return result_dict

    def format_quote_message(self, quote: Dict) -> str:
        """Format a quote as a WhatsApp bill message."""
        if "error" in quote:
            return f"⚠️ {quote['error']}"

        cur = quote.get("currency", "INR")
        jtype = quote["jewelry_type"].title()
        karat = quote["karat"]
        sym = "$" if cur == "USD" else "₹"

        lines = [
            f"📋 *JewelClaw Quote*",
            f"━━━━━━━━━━━━━━━━━━━━━",
            f"*{jtype}* | {karat} | {quote['weight_grams']}g",
        ]

        if cur == "USD":
            lines.append(f"💱 Currency: *USD* (₹1 = ${1/quote['usd_inr']:.4f})")

        lines.extend([
            f"",
            f"💰 Gold: {_fmt(quote['gold_rate_per_gram'], cur)}/gm ({quote['city']})",
            f"📅 {quote['rate_date']}",
            f"",
            f"*Breakdown:*",
            f"  Gold ({quote['weight_grams']}g × {_fmt(quote['gold_rate_per_gram'], cur)})  {_fmt(quote['gold_cost'], cur)}",
            f"  Wastage ({quote['wastage_pct']}%)  {_fmt(quote['wastage_cost'], cur)}",
        ])

        if quote.get("gold_loss_pct") and quote["gold_loss_cost"] > 0:
            lines.append(f"  Gold Loss ({quote['gold_loss_pct']}%)  {_fmt(quote['gold_loss_cost'], cur)}")

        # Making charge
        custom_tag = " ✓" if quote.get("is_custom_making") else " _(default)_"
        lines.append(f"  Making ({quote['making_detail']}{custom_tag})  {_fmt(quote['making_cost'], cur)}")

        # CZ stones
        if quote.get("cz_count", 0) > 0:
            lines.append(f"  CZ ({quote['cz_detail']})  {_fmt(quote['cz_cost'], cur)}")

        # Diamonds
        if quote.get("diamond_cost", 0) > 0:
            lines.append(f"  *Diamonds:*")
            for dd in quote.get("diamond_details", []):
                lines.append(f"    {dd}")
            if quote.get("diamond_setting_cost", 0) > 0:
                lines.append(f"  Setting  {_fmt(quote['diamond_setting_cost'], cur)}")

        # Gemstones
        if quote.get("gemstone_cost", 0) > 0:
            lines.append(f"  *Gemstones:*")
            for gd in quote.get("gemstone_details", []):
                lines.append(f"    {gd}")

        # Finishing
        if quote.get("finishing_cost", 0) > 0:
            lines.append(f"  *Finishing:*")
            for fd in quote.get("finishing_details", []):
                lines.append(f"    {fd}")

        # Hallmark
        if quote["hallmark_charge"] > 0.01:
            lines.append(f"  Hallmark  {_fmt(quote['hallmark_charge'], cur)}")

        lines.extend([
            f"  ─────────────────",
            f"  Subtotal  {_fmt(quote['subtotal'], cur)}",
        ])

        if quote["gst_pct"] > 0:
            lines.append(f"  GST ({quote['gst_pct']}%)  {_fmt(quote['gst'], cur)}")

        lines.append(f"━━━━━━━━━━━━━━━━━━━━━")

        # Cost vs Selling price
        if quote.get("profit_margin_pct"):
            lines.append(f"*COST: {_fmt(quote['cost_price'], cur)}*")
            lines.append(f"Margin ({quote['profit_margin_pct']}%): +{_fmt(quote['profit'], cur)}")
            lines.append(f"*SELL: {_fmt(quote['selling_price'], cur)}*")
        else:
            lines.append(f"*TOTAL: {_fmt(quote['total_per_piece'], cur)}*")

        if quote["quantity"] > 1:
            lines.append(f"*× {quote['quantity']} pcs = {_fmt(quote['grand_total'], cur)}*")

        lines.extend([
            f"",
            f"_Your rates are saved. Chat with me to update._",
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
            quote 5g 18k ring 30 CZ pave
            quote 8g 22k pendant 0.5ct diamond GH-VS
            quote 15g bangle x3
            quote 2g 18k ring 20 cz pave 1ct ruby rhodium
        """
        text = re.sub(r'^quote\s+', '', text.strip().lower())

        # Extract weight
        weight_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:g|gm|gram|grams)', text)
        if not weight_match:
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
            if re.search(r'\b' + re.escape(alias) + r'\b', text):
                jewelry_type = jtype
                break

        # Extract quantity
        qty_match = re.search(r'[x×]\s*(\d+)', text)
        quantity = int(qty_match.group(1)) if qty_match else 1

        result = {
            "weight_grams": weight,
            "karat": karat,
            "jewelry_type": jewelry_type,
            "quantity": quantity,
        }

        # Extract CZ count
        cz_match = re.search(r'(\d+)\s*(?:cz|czs|cubic)', text)
        if cz_match:
            result["cz_count"] = int(cz_match.group(1))
            # Check setting type
            for st in ("pave", "prong", "bezel", "channel", "micro_pave", "micro pave", "wax_set", "wax set"):
                if st in text:
                    result["cz_setting"] = st.replace(" ", "_")
                    break
            else:
                result["cz_setting"] = "pave"

        # Extract diamond info (e.g. "0.5ct diamond GH-VS" or "20 diamonds sieve 7")
        diamond_match = re.search(
            r'(\d+(?:\.\d+)?)\s*(?:ct|carat)\s*(?:diamond|dia)', text
        )
        diamond_count_match = re.search(
            r'(\d+)\s*(?:diamond|dia)(?:s)?', text
        )
        if diamond_match or diamond_count_match:
            diamonds = []
            d = {}
            if diamond_match:
                d["total_carats"] = float(diamond_match.group(1))
                d["count"] = 1
            elif diamond_count_match:
                d["count"] = int(diamond_count_match.group(1))

            # Quality
            quality_match = re.search(r'([defghij]{1,3})\s*[-/]?\s*(vvs|vs|si|if)', text, re.IGNORECASE)
            d["quality"] = f"{quality_match.group(1)}-{quality_match.group(2)}" if quality_match else "GH-VS"

            # Lab grown
            d["lab"] = bool(re.search(r'lab\s*(?:grown|created|made)?', text, re.IGNORECASE))

            # Sieve size
            sieve_match = re.search(r'sieve\s*(\d+\+?)', text)
            d["sieve"] = sieve_match.group(1) if sieve_match else "7"

            # Setting
            for st in ("pave", "prong", "bezel", "channel", "invisible", "micro_pave"):
                if st in text:
                    d["setting"] = st
                    break
            else:
                d["setting"] = "prong"

            diamonds.append(d)
            result["diamonds"] = diamonds

        # Extract gemstones
        for stone_name in DEFAULT_GEMSTONE_RATES_USD:
            stone_match = re.search(
                r'(\d+(?:\.\d+)?)\s*(?:ct|carat)?\s*' + stone_name, text, re.IGNORECASE
            )
            if stone_match:
                result.setdefault("gemstones", []).append({
                    "stone": stone_name,
                    "carats": float(stone_match.group(1)),
                    "grade": "mid",
                })

        # Extract finishing
        for f_type in DEFAULT_FINISHING_RATES_USD:
            f_name = f_type.replace("_", " ")
            if f_name in text or f_type in text:
                result.setdefault("finishing", []).append(f_type)

        # Legacy stone_cost support
        stone_match = re.search(r'stone\s*(?:cost)?\s*(\d+(?:\.\d+)?)', text)
        if stone_match:
            result["stone_cost"] = float(stone_match.group(1))

        return result

    def parse_setup_input(self, text: str) -> Optional[Dict]:
        """
        Parse price setup input.

        Formats:
            price set necklace 15%      -> percentage making charge
            price set ring making 12     -> percentage making charge
            price set bangle wastage 2.5 -> wastage
            price set hallmark 50        -> hallmark charge
            price set ring labor 800     -> per-gram labor
            price set ring cfp 3.25      -> CFP rate
            price set cz pave 0.11       -> CZ rate per stone
            price set model percentage   -> pricing model
            price set currency usd       -> currency
            price set margin 15          -> profit margin
            price set gold loss 10       -> gold loss
            price set setting pave 0.15  -> setting charge
            price set finishing rhodium 1 -> finishing charge
        """
        text = re.sub(r'^price\s+set\s+', '', text.strip().lower())

        # Model
        model_match = re.match(r'model\s+(percentage|per.?gram|per.?piece|cfp|all.?inclusive)', text)
        if model_match:
            model_raw = model_match.group(1)
            model_map = {"percentage": "percentage", "cfp": "per_piece"}
            for k in ("per_gram", "pergram", "per gram"):
                model_map[k] = "per_gram"
            for k in ("per_piece", "perpiece", "per piece"):
                model_map[k] = "per_piece"
            for k in ("all_inclusive", "allinclusive", "all inclusive"):
                model_map[k] = "all_inclusive"
            return {"type": "model", "value": model_map.get(model_raw.replace(" ", "_").replace("-", "_"), "percentage")}

        # Currency
        currency_match = re.match(r'currency\s+(inr|usd|₹|\$|rupees?|dollars?)', text)
        if currency_match:
            c = currency_match.group(1)
            return {"type": "currency", "value": "USD" if c in ("usd", "$", "dollar", "dollars") else "INR"}

        # Margin
        margin_match = re.match(r'(?:profit\s+)?margin\s+(\d+(?:\.\d+)?)\s*%?', text)
        if margin_match:
            return {"type": "margin", "value": float(margin_match.group(1))}

        # Gold loss
        loss_match = re.match(r'gold\s*loss\s+(\d+(?:\.\d+)?)\s*%?', text)
        if loss_match:
            return {"type": "gold_loss", "value": float(loss_match.group(1))}

        # Hallmark
        hallmark_match = re.match(r'hallmark\s+(\d+(?:\.\d+)?)', text)
        if hallmark_match:
            return {"type": "hallmark", "value": float(hallmark_match.group(1))}

        # CZ rate: "cz pave 0.11" or "cz prong 12"
        cz_match = re.match(r'cz\s+(\w+)\s+(\d+(?:\.\d+)?)', text)
        if cz_match:
            return {"type": "cz", "setting": cz_match.group(1), "value": float(cz_match.group(2))}

        # Setting charge: "setting pave 0.15"
        setting_match = re.match(r'setting\s+(\w+)\s+(\d+(?:\.\d+)?)', text)
        if setting_match:
            return {"type": "setting", "setting": setting_match.group(1), "value": float(setting_match.group(2))}

        # Finishing: "finishing rhodium 1.00"
        finishing_match = re.match(r'finishing\s+(\w+)\s+(\d+(?:\.\d+)?)', text)
        if finishing_match:
            return {"type": "finishing", "finishing": finishing_match.group(1), "value": float(finishing_match.group(2))}

        # Wastage: "ring wastage 2.5"
        wastage_match = re.match(r'(\w+)\s+wastage\s+(\d+(?:\.\d+)?)', text)
        if wastage_match:
            return {
                "type": "wastage",
                "jewelry_type": wastage_match.group(1),
                "value": float(wastage_match.group(2)),
            }

        # Labor per gram: "ring labor 800"
        labor_match = re.match(r'(\w+)\s+labor\s+(\d+(?:\.\d+)?)', text)
        if labor_match:
            return {
                "type": "labor",
                "jewelry_type": labor_match.group(1),
                "value": float(labor_match.group(2)),
            }

        # CFP: "ring cfp 3.25"
        cfp_match = re.match(r'(\w+)\s+cfp\s+(\d+(?:\.\d+)?)', text)
        if cfp_match:
            return {
                "type": "cfp",
                "jewelry_type": cfp_match.group(1),
                "value": float(cfp_match.group(2)),
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
        cur = profile["currency"]

        lines = [
            "⚙️ *Your Pricing Profile*",
            "━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"*Model:* {PRICING_MODELS.get(profile['pricing_model'], profile['pricing_model'])}",
            f"*Currency:* {cur}",
        ]

        if profile.get("profit_margin_pct"):
            lines.append(f"*Profit Margin:* {profile['profit_margin_pct']}%")
        if profile.get("gold_loss_pct"):
            lines.append(f"*Gold Loss:* {profile['gold_loss_pct']}%")

        lines.append("")

        # Making charges / Labor
        if profile["pricing_model"] == "per_gram" and profile["labor_per_gram"]:
            lines.append("*Labor Rates (per gram):*")
            for jtype, rate in profile["labor_per_gram"].items():
                lines.append(f"  {jtype.title()}: *{_fmt(rate, cur)}/gm* ✓")
        elif profile["pricing_model"] == "per_piece" and profile["cfp_rates"]:
            lines.append("*CFP Rates (per piece):*")
            for jtype, rate in profile["cfp_rates"].items():
                lines.append(f"  {jtype.title()}: *{_fmt(rate, cur)}* ✓")
        else:
            lines.append("*Making Charges:*")
            for jtype, default_rate in DEFAULT_MAKING_CHARGES.items():
                if jtype == "general":
                    continue
                user_rate = profile["making_charges"].get(jtype)
                if user_rate:
                    lines.append(f"  {jtype.title()}: *{user_rate}%* ✓")
                else:
                    lines.append(f"  {jtype.title()}: {default_rate}% _(default)_")

        # Wastage
        has_custom_wastage = any(profile["wastage"].values())
        if has_custom_wastage:
            lines.append("")
            lines.append("*Wastage:*")
            for jtype, rate in profile["wastage"].items():
                lines.append(f"  {jtype.title()}: *{rate}%* ✓")

        # Stone rates
        if profile["cz_rates"]:
            lines.append("")
            lines.append("*CZ Rates:*")
            for setting, rate in profile["cz_rates"].items():
                lines.append(f"  {setting.title()}: *{_fmt(rate, cur)}/stone* ✓")

        if profile["diamond_rates"]:
            lines.append("")
            lines.append("*Diamond Rates (per ct):*")
            for key, rate in profile["diamond_rates"].items():
                lines.append(f"  {key}: *${rate:,.0f}/ct* ✓")

        if profile["lab_diamond_rates"]:
            lines.append("")
            lines.append("*Lab Diamond Rates (per ct):*")
            for key, rate in profile["lab_diamond_rates"].items():
                lines.append(f"  {key}: *${rate:,.0f}/ct* ✓")

        if profile["setting_rates"]:
            lines.append("")
            lines.append("*Setting Charges:*")
            for st, rate in profile["setting_rates"].items():
                lines.append(f"  {st.title()}: *{_fmt(rate, cur)}/stone* ✓")

        if profile["finishing_rates"]:
            lines.append("")
            lines.append("*Finishing Charges:*")
            for ft, rate in profile["finishing_rates"].items():
                lines.append(f"  {ft.title()}: *{_fmt(rate, cur)}/pc* ✓")

        # Other
        lines.extend([
            "",
            f"*Other:*",
            f"  Hallmark: {_fmt(profile['hallmark_charge'], 'INR')}/pc",
            f"  GST: {profile['gst_pct']}%",
            "",
            "_Chat with me to update any rate, or type 'price setup' for commands._",
            "_✓ = your custom rate_",
        ])

        return "\n".join(lines)

    def get_setup_menu(self) -> str:
        """Return the setup menu for pricing configuration."""
        return """⚙️ *Pricing Setup*

Just *chat with me naturally* to set up your pricing! Tell me things like:
_"I charge 14% making on necklaces"_
_"My labor rate is ₹800 per gram"_
_"CZ pave setting is ₹10 per stone"_
_"I work in USD for exports"_

Or *upload a photo* of your pricing chart - I'll read and save it!

*Manual commands:*
price set model percentage
price set necklace 15
price set ring labor 800
price set ring cfp 3.25
price set ring wastage 2.5
price set hallmark 50
price set cz pave 12
price set setting prong 15
price set finishing rhodium 80
price set margin 15
price set gold loss 10
price set currency usd

*View profile:* price profile
*Quick quote:* quote 10g 22k necklace"""

    # =========================================================================
    # PRICING CHART IMAGE PARSER
    # =========================================================================

    async def parse_pricing_chart_image(self, image_url: str, user_context: str = "") -> Dict:
        """
        Use Claude Vision to extract pricing data from an uploaded image.
        Returns structured pricing data that can be saved to the user's profile.
        """
        try:
            import anthropic
            import httpx
            from app.config import settings

            # Download image
            async with httpx.AsyncClient() as http_client:
                resp = await http_client.get(image_url, follow_redirects=True)
                if resp.status_code != 200:
                    return {"error": "Could not download image"}
                image_data = resp.content
                content_type = resp.headers.get("content-type", "image/jpeg")

            import base64
            b64_image = base64.b64encode(image_data).decode("utf-8")

            # Determine media type
            if "png" in content_type:
                media_type = "image/png"
            elif "webp" in content_type:
                media_type = "image/webp"
            else:
                media_type = "image/jpeg"

            client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

            response = client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=2000,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64_image,
                            },
                        },
                        {
                            "type": "text",
                            "text": f"""Extract ALL pricing data from this jewelry pricing chart/table. {user_context}

Return a JSON object with these fields (only include fields that are visible in the image):

{{
    "pricing_model": "percentage" or "per_gram" or "per_piece" or "all_inclusive",
    "currency": "INR" or "USD",
    "making_charges": {{"necklace": 14.0, "ring": 12.0, ...}},
    "labor_per_gram": {{"ring": 800, ...}},
    "cfp_rates": {{"ring_below_3g": 3.25, "ring_3_5g": 3.75, ...}},
    "wastage": {{"necklace": 3.0, ...}},
    "gold_loss_pct": 10.0,
    "cz_rates": {{"pave": 0.11, "prong": 0.14, ...}},
    "setting_rates": {{"pave": 0.15, "channel": 0.22, ...}},
    "finishing_rates": {{"rhodium": 1.00, "two_tone": 0.75, ...}},
    "diamond_rates": {{"melee_small": 600, ...}},
    "notes": "Any other pricing info visible"
}}

Return ONLY valid JSON, nothing else.""",
                        },
                    ],
                }],
            )

            # Parse the response
            text = response.content[0].text.strip()
            # Try direct JSON parse first (most reliable)
            try:
                data = json.loads(text)
                return {"success": True, "data": data}
            except json.JSONDecodeError:
                pass
            # Fallback: extract JSON from markdown code block or surrounding text
            # Use non-greedy match to avoid capturing extra braces
            json_match = re.search(r'\{[\s\S]*?\}(?=\s*$)', text)
            if not json_match:
                # Try greedy as last resort, but validate
                json_match = re.search(r'\{[\s\S]*\}', text)
            if json_match:
                try:
                    data = json.loads(json_match.group())
                    return {"success": True, "data": data}
                except json.JSONDecodeError:
                    return {"error": "Could not parse pricing data from image", "raw": text}
            else:
                return {"error": "Could not parse pricing data from image", "raw": text}

        except Exception as e:
            logger.error(f"Error parsing pricing chart image: {e}", exc_info=True)
            return {"error": f"Failed to analyze image: {str(e)}"}

    async def apply_parsed_pricing(self, db: AsyncSession, user_id: int, parsed_data: Dict) -> List[str]:
        """Apply parsed pricing data (from image or conversation) to user's profile."""
        saved = []

        if "pricing_model" in parsed_data:
            await self.save_pricing_model(db, user_id, parsed_data["pricing_model"])
            saved.append(f"Model: {parsed_data['pricing_model']}")

        if "currency" in parsed_data:
            await self.save_currency(db, user_id, parsed_data["currency"])
            saved.append(f"Currency: {parsed_data['currency']}")

        if "gold_loss_pct" in parsed_data and parsed_data["gold_loss_pct"]:
            await self.save_gold_loss(db, user_id, float(parsed_data["gold_loss_pct"]))
            saved.append(f"Gold loss: {parsed_data['gold_loss_pct']}%")

        for jtype, val in parsed_data.get("making_charges", {}).items():
            await self.save_making_charge(db, user_id, jtype, float(val))
            saved.append(f"Making {jtype}: {val}%")

        for jtype, val in parsed_data.get("labor_per_gram", {}).items():
            await self.save_labor_per_gram(db, user_id, jtype, float(val))
            saved.append(f"Labor {jtype}: {_fmt(float(val), parsed_data.get('currency', 'INR'))}/gm")

        for jtype, val in parsed_data.get("cfp_rates", {}).items():
            await self.save_cfp_rate(db, user_id, jtype, float(val))
            saved.append(f"CFP {jtype}: {val}")

        for jtype, val in parsed_data.get("wastage", {}).items():
            await self.save_wastage(db, user_id, jtype, float(val))
            saved.append(f"Wastage {jtype}: {val}%")

        for setting, val in parsed_data.get("cz_rates", {}).items():
            await self.save_cz_rate(db, user_id, setting, float(val))
            saved.append(f"CZ {setting}: {val}")

        for setting, val in parsed_data.get("setting_rates", {}).items():
            await self.save_setting_rate(db, user_id, setting, float(val))
            saved.append(f"Setting {setting}: {val}")

        for ftype, val in parsed_data.get("finishing_rates", {}).items():
            await self.save_finishing_rate(db, user_id, ftype, float(val))
            saved.append(f"Finishing {ftype}: {val}")

        # Diamond rates - save by size category
        for key, val in parsed_data.get("diamond_rates", {}).items():
            # Key could be "melee_small" with a single rate, or "melee_GH_VS" with quality
            if "_" in key:
                parts = key.split("_", 1)
                # Try to detect if quality is in the key
                for q in ("DEF_VVS", "GH_VS", "IJ_SI"):
                    if q in key.upper():
                        size = key.upper().replace(f"_{q}", "").lower()
                        await self.save_diamond_rate(db, user_id, size, q, float(val))
                        saved.append(f"Diamond {size} {q}: ${val}/ct")
                        break
                else:
                    # Just a size category - save for default quality
                    await self.save_diamond_rate(db, user_id, key, "GH_VS", float(val))
                    saved.append(f"Diamond {key}: ${val}/ct")

        return saved

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _normalize_jewelry_type(self, text: str) -> str:
        """Normalize jewelry type to standard name."""
        text = text.lower().strip()
        return JEWELRY_ALIASES.get(text, text if text in DEFAULT_MAKING_CHARGES else "general")


def _fmt(amount: float, currency: str = "INR") -> str:
    """Format an amount with currency symbol."""
    if currency == "USD":
        if amount >= 1000:
            return f"${amount:,.0f}"
        elif amount >= 1:
            return f"${amount:,.2f}"
        else:
            return f"${amount:.2f}"
    else:
        if amount >= 100000:
            # Indian lakh formatting
            lakhs = amount / 100000
            return f"₹{lakhs:,.2f}L"
        return f"₹{amount:,.0f}"


# Singleton
pricing_engine = PricingEngineService()
