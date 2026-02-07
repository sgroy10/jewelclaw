"""
Price Tracking Service for JewelClaw.

Tracks price changes for designs and generates price history/alerts.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func
from dataclasses import dataclass

from app.models import Design, PriceHistory, Alert

logger = logging.getLogger(__name__)


@dataclass
class PriceChange:
    """Detected price change."""
    design_id: int
    title: str
    old_price: float
    new_price: float
    change_amount: float
    change_percent: float
    is_drop: bool
    image_url: Optional[str]


class PriceTrackerService:
    """Service for tracking design price changes."""

    def __init__(self):
        self.price_drop_threshold = 5  # Minimum % drop to trigger alert
        self.price_increase_threshold = 10  # Minimum % increase to track

    async def record_price(
        self,
        db: AsyncSession,
        design_id: int,
        price: float
    ) -> Optional[PriceChange]:
        """
        Record current price for a design.
        Returns PriceChange if significant change detected.
        """
        if not price or price <= 0:
            return None

        # Get last recorded price
        result = await db.execute(
            select(PriceHistory)
            .where(PriceHistory.design_id == design_id)
            .order_by(desc(PriceHistory.recorded_at))
            .limit(1)
        )
        last_price_record = result.scalar_one_or_none()

        # Record new price
        new_record = PriceHistory(
            design_id=design_id,
            price=price
        )
        db.add(new_record)

        # Check for significant change
        if last_price_record and last_price_record.price > 0:
            old_price = last_price_record.price
            change_amount = price - old_price
            change_percent = (change_amount / old_price) * 100

            # Significant change detected?
            if abs(change_percent) >= self.price_drop_threshold:
                # Get design details
                design = await db.get(Design, design_id)
                if design:
                    return PriceChange(
                        design_id=design_id,
                        title=design.title or "Unknown",
                        old_price=old_price,
                        new_price=price,
                        change_amount=change_amount,
                        change_percent=change_percent,
                        is_drop=change_amount < 0,
                        image_url=design.image_url
                    )

        return None

    async def record_all_prices(
        self,
        db: AsyncSession,
        designs: List[Design]
    ) -> List[PriceChange]:
        """
        Record prices for all designs and return list of significant changes.
        """
        changes = []

        for design in designs:
            if design.price_range_min and design.price_range_min > 0:
                change = await self.record_price(
                    db,
                    design.id,
                    design.price_range_min
                )
                if change:
                    changes.append(change)
                    logger.info(
                        f"Price change detected: {design.title} "
                        f"({change.change_percent:+.1f}%)"
                    )

        await db.commit()
        return changes

    async def get_price_history(
        self,
        db: AsyncSession,
        design_id: int,
        days: int = 30
    ) -> List[Dict]:
        """Get price history for a design."""
        since = datetime.utcnow() - timedelta(days=days)

        result = await db.execute(
            select(PriceHistory)
            .where(PriceHistory.design_id == design_id)
            .where(PriceHistory.recorded_at >= since)
            .order_by(PriceHistory.recorded_at)
        )
        records = result.scalars().all()

        return [
            {
                "price": r.price,
                "recorded_at": r.recorded_at.isoformat()
            }
            for r in records
        ]

    async def get_price_drops(
        self,
        db: AsyncSession,
        min_drop_percent: float = 5,
        days: int = 7,
        limit: int = 20
    ) -> List[Dict]:
        """Get designs with recent price drops."""
        since = datetime.utcnow() - timedelta(days=days)

        # This is a simplified version - for production, use a more
        # efficient query with window functions
        result = await db.execute(
            select(Design)
            .where(Design.price_range_min.isnot(None))
            .order_by(desc(Design.trending_score))
            .limit(100)
        )
        designs = result.scalars().all()

        drops = []
        for design in designs:
            history = await self.get_price_history(db, design.id, days)
            if len(history) >= 2:
                first_price = history[0]["price"]
                last_price = history[-1]["price"]
                if first_price > 0:
                    change_percent = ((last_price - first_price) / first_price) * 100
                    if change_percent <= -min_drop_percent:
                        drops.append({
                            "design_id": design.id,
                            "title": design.title,
                            "old_price": first_price,
                            "new_price": last_price,
                            "drop_percent": abs(change_percent),
                            "image_url": design.image_url,
                            "source": design.source
                        })

        # Sort by drop percent
        drops.sort(key=lambda x: x["drop_percent"], reverse=True)
        return drops[:limit]

    async def get_price_trends(
        self,
        db: AsyncSession,
        days: int = 7
    ) -> Dict:
        """Get overall price trends across categories."""
        since = datetime.utcnow() - timedelta(days=days)

        # Get average prices by category
        result = await db.execute(
            select(
                Design.category,
                func.avg(Design.price_range_min).label("avg_price"),
                func.count(Design.id).label("count")
            )
            .where(Design.price_range_min.isnot(None))
            .group_by(Design.category)
        )
        category_stats = result.all()

        return {
            "period_days": days,
            "categories": [
                {
                    "category": row.category or "general",
                    "avg_price": round(row.avg_price, 2) if row.avg_price else 0,
                    "design_count": row.count
                }
                for row in category_stats
            ]
        }


# Global instance
price_tracker = PriceTrackerService()
