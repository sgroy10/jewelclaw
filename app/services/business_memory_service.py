"""
Business Memory Service - Stores and retrieves learned facts about each jeweler's business.
Used by the AI agent to personalize responses and provide contextual advice.
"""

import logging
from datetime import datetime
from typing import Optional, List, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, update

from app.models import BusinessMemory, User

logger = logging.getLogger(__name__)


class BusinessMemoryService:
    """CRUD operations for business memory facts."""

    async def store_fact(
        self,
        db: AsyncSession,
        user_id: int,
        category: str,
        key: str,
        value: str,
        value_numeric: Optional[float] = None,
        metal_type: Optional[str] = None,
        jewelry_category: Optional[str] = None,
        confidence: float = 1.0,
        source_message_id: Optional[int] = None,
    ) -> BusinessMemory:
        """Store or update a business fact. Upserts by user_id + key."""
        # Check if fact already exists
        result = await db.execute(
            select(BusinessMemory).where(
                and_(
                    BusinessMemory.user_id == user_id,
                    BusinessMemory.key == key,
                    BusinessMemory.is_active == True,
                )
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.value = value
            existing.value_numeric = value_numeric
            existing.metal_type = metal_type or existing.metal_type
            existing.jewelry_category = jewelry_category or existing.jewelry_category
            existing.confidence = confidence
            existing.source_message_id = source_message_id
            existing.extracted_at = datetime.utcnow()
            logger.info(f"Updated memory for user {user_id}: {key}={value}")
            return existing
        else:
            memory = BusinessMemory(
                user_id=user_id,
                category=category,
                key=key,
                value=value,
                value_numeric=value_numeric,
                metal_type=metal_type,
                jewelry_category=jewelry_category,
                confidence=confidence,
                source_message_id=source_message_id,
            )
            db.add(memory)
            await db.flush()
            logger.info(f"Stored new memory for user {user_id}: {key}={value}")
            return memory

    async def get_user_memory(
        self,
        db: AsyncSession,
        user_id: int,
        category: Optional[str] = None,
    ) -> List[BusinessMemory]:
        """Get all active business facts for a user, optionally filtered by category."""
        query = select(BusinessMemory).where(
            and_(
                BusinessMemory.user_id == user_id,
                BusinessMemory.is_active == True,
            )
        )
        if category:
            query = query.where(BusinessMemory.category == category)

        query = query.order_by(BusinessMemory.extracted_at.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_buy_thresholds(
        self, db: AsyncSession, user_id: int
    ) -> Dict[str, Optional[float]]:
        """Get numeric buy/sell thresholds for alert comparison."""
        result = await db.execute(
            select(BusinessMemory).where(
                and_(
                    BusinessMemory.user_id == user_id,
                    BusinessMemory.category.in_(["buy_threshold", "sell_threshold"]),
                    BusinessMemory.is_active == True,
                )
            )
        )
        memories = result.scalars().all()

        thresholds = {"buy": None, "sell": None}
        for m in memories:
            if m.category == "buy_threshold" and m.value_numeric:
                thresholds["buy"] = m.value_numeric
            elif m.category == "sell_threshold" and m.value_numeric:
                thresholds["sell"] = m.value_numeric

        # Also check User model columns as fallback
        user_result = await db.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one_or_none()
        if user:
            if not thresholds["buy"] and user.gold_buy_threshold:
                thresholds["buy"] = user.gold_buy_threshold
            if not thresholds["sell"] and user.gold_sell_threshold:
                thresholds["sell"] = user.gold_sell_threshold

        return thresholds

    def format_memory_for_prompt(self, memories: List[BusinessMemory]) -> str:
        """Render business memory as a text block for Claude's system prompt."""
        if not memories:
            return "No business information stored yet."

        # Group by category
        grouped: Dict[str, List[BusinessMemory]] = {}
        for m in memories:
            grouped.setdefault(m.category, []).append(m)

        lines = []
        category_labels = {
            "making_charges": "Making Charges",
            "buy_threshold": "Buy Price Thresholds",
            "sell_threshold": "Sell Price Thresholds",
            "supplier": "Suppliers",
            "customer_preference": "Customer Preferences",
            "business_fact": "Business Facts",
            "inventory": "Inventory Notes",
            "interest": "Interests",
            "pricing_rule": "Pricing Rules",
        }

        for cat, items in grouped.items():
            label = category_labels.get(cat, cat.replace("_", " ").title())
            lines.append(f"[{label}]")
            for item in items:
                detail = f"  - {item.key}: {item.value}"
                if item.metal_type:
                    detail += f" ({item.metal_type})"
                if item.jewelry_category:
                    detail += f" [{item.jewelry_category}]"
                lines.append(detail)

        return "\n".join(lines)

    async def delete_fact(
        self, db: AsyncSession, user_id: int, key: str
    ) -> bool:
        """Soft-delete a business fact."""
        result = await db.execute(
            select(BusinessMemory).where(
                and_(
                    BusinessMemory.user_id == user_id,
                    BusinessMemory.key == key,
                    BusinessMemory.is_active == True,
                )
            )
        )
        memory = result.scalar_one_or_none()
        if memory:
            memory.is_active = False
            return True
        return False


# Singleton
business_memory_service = BusinessMemoryService()
