"""
Alerts Service for JewelClaw.

Manages user alerts for price drops, new arrivals, and trending designs.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, update

from app.models import Alert, Design, User, UserDesignPreference
from app.services.price_tracker import PriceChange

logger = logging.getLogger(__name__)


class AlertsService:
    """Service for managing user alerts."""

    def __init__(self):
        self.max_alerts_per_user = 50  # Max pending alerts per user

    async def create_price_drop_alert(
        self,
        db: AsyncSession,
        user_id: int,
        price_change: PriceChange
    ) -> Alert:
        """Create an alert for a price drop."""
        drop_percent = abs(price_change.change_percent)

        alert = Alert(
            user_id=user_id,
            alert_type="price_drop",
            title=f"Price Drop: {price_change.title[:50]}",
            message=f"Price dropped by {drop_percent:.1f}%! "
                   f"Was Rs.{price_change.old_price:,.0f}, "
                   f"now Rs.{price_change.new_price:,.0f}",
            design_id=price_change.design_id,
            metadata={
                "old_price": price_change.old_price,
                "new_price": price_change.new_price,
                "drop_percent": drop_percent,
                "image_url": price_change.image_url
            }
        )
        db.add(alert)
        return alert

    async def create_new_arrival_alert(
        self,
        db: AsyncSession,
        user_id: int,
        design: Design
    ) -> Alert:
        """Create an alert for a new arrival."""
        price_text = f"Rs.{design.price_range_min:,.0f}" if design.price_range_min else "Price TBD"

        alert = Alert(
            user_id=user_id,
            alert_type="new_arrival",
            title=f"New Arrival: {design.title[:50]}",
            message=f"New {design.category or 'design'} from {design.source}. {price_text}",
            design_id=design.id,
            metadata={
                "source": design.source,
                "category": design.category,
                "price": design.price_range_min,
                "image_url": design.image_url
            }
        )
        db.add(alert)
        return alert

    async def create_trending_alert(
        self,
        db: AsyncSession,
        user_id: int,
        designs: List[Design],
        category: str = None
    ) -> Alert:
        """Create an alert for trending designs."""
        if category:
            title = f"Trending in {category.title()}"
        else:
            title = "Today's Trending Designs"

        design_names = ", ".join([d.title[:30] for d in designs[:3]])

        alert = Alert(
            user_id=user_id,
            alert_type="trending",
            title=title,
            message=f"Check out: {design_names}...",
            metadata={
                "design_ids": [d.id for d in designs],
                "category": category,
                "count": len(designs)
            }
        )
        db.add(alert)
        return alert

    async def get_pending_alerts(
        self,
        db: AsyncSession,
        user_id: int,
        limit: int = 10
    ) -> List[Alert]:
        """Get unsent alerts for a user."""
        result = await db.execute(
            select(Alert)
            .where(Alert.user_id == user_id)
            .where(Alert.is_sent == False)
            .order_by(desc(Alert.created_at))
            .limit(limit)
        )
        return result.scalars().all()

    async def mark_alert_sent(
        self,
        db: AsyncSession,
        alert_id: int
    ):
        """Mark an alert as sent."""
        await db.execute(
            update(Alert)
            .where(Alert.id == alert_id)
            .values(is_sent=True, sent_at=datetime.utcnow())
        )

    async def mark_all_sent(
        self,
        db: AsyncSession,
        user_id: int
    ):
        """Mark all alerts as sent for a user."""
        await db.execute(
            update(Alert)
            .where(Alert.user_id == user_id)
            .where(Alert.is_sent == False)
            .values(is_sent=True, sent_at=datetime.utcnow())
        )

    async def get_alert_summary(
        self,
        db: AsyncSession,
        user_id: int
    ) -> Dict:
        """Get summary of user's alerts."""
        result = await db.execute(
            select(Alert)
            .where(Alert.user_id == user_id)
            .where(Alert.is_sent == False)
        )
        pending = result.scalars().all()

        # Count by type
        by_type = {}
        for alert in pending:
            by_type[alert.alert_type] = by_type.get(alert.alert_type, 0) + 1

        return {
            "pending_count": len(pending),
            "by_type": by_type
        }

    async def generate_price_drop_alerts(
        self,
        db: AsyncSession,
        price_changes: List[PriceChange]
    ) -> int:
        """
        Generate alerts for all users who liked/saved designs with price drops.
        Returns count of alerts created.
        """
        alerts_created = 0

        for change in price_changes:
            if not change.is_drop:
                continue

            # Find users who liked/saved this design
            result = await db.execute(
                select(UserDesignPreference.user_id)
                .where(UserDesignPreference.design_id == change.design_id)
                .where(UserDesignPreference.action.in_(["liked", "saved"]))
            )
            user_ids = [row[0] for row in result.all()]

            # Create alert for each user
            for user_id in user_ids:
                await self.create_price_drop_alert(db, user_id, change)
                alerts_created += 1

        await db.commit()
        logger.info(f"Created {alerts_created} price drop alerts")
        return alerts_created

    async def generate_new_arrival_alerts(
        self,
        db: AsyncSession,
        designs: List[Design],
        subscribed_users: List[User]
    ) -> int:
        """
        Generate new arrival alerts for subscribed users.
        Returns count of alerts created.
        """
        alerts_created = 0

        # Only alert for top 5 new arrivals
        top_designs = sorted(
            designs,
            key=lambda d: d.trending_score or 0,
            reverse=True
        )[:5]

        for user in subscribed_users:
            # Check if user has category preferences
            result = await db.execute(
                select(UserDesignPreference)
                .where(UserDesignPreference.user_id == user.id)
                .where(UserDesignPreference.action == "liked")
            )
            preferences = result.scalars().all()

            # Get preferred categories
            preferred_categories = set()
            for pref in preferences:
                design = await db.get(Design, pref.design_id)
                if design and design.category:
                    preferred_categories.add(design.category)

            # Filter designs by preference (or all if no preference)
            relevant_designs = top_designs
            if preferred_categories:
                relevant_designs = [
                    d for d in top_designs
                    if d.category in preferred_categories
                ][:3]

            if relevant_designs:
                await self.create_trending_alert(db, user.id, relevant_designs)
                alerts_created += 1

        await db.commit()
        logger.info(f"Created {alerts_created} new arrival alerts")
        return alerts_created

    def format_alert_message(self, alert: Alert) -> str:
        """Format an alert for WhatsApp delivery."""
        if alert.alert_type == "price_drop":
            meta = alert.metadata or {}
            return f"""🏷️ *Price Drop Alert!*

{alert.title}

Was: Rs.{meta.get('old_price', 0):,.0f}
Now: Rs.{meta.get('new_price', 0):,.0f}
*Save {meta.get('drop_percent', 0):.1f}%!*

_Reply 'like {alert.design_id}' to save this design_"""

        elif alert.alert_type == "new_arrival":
            meta = alert.metadata or {}
            return f"""✨ *New Arrival!*

{alert.title}

{alert.message}
Source: {meta.get('source', 'N/A')}

_Reply 'like {alert.design_id}' to save_"""

        elif alert.alert_type == "trending":
            return f"""🔥 *{alert.title}*

{alert.message}

_Reply 'trends' to see all trending designs_"""

        else:
            return f"""📢 *{alert.title}*

{alert.message}"""


# Global instance
alerts_service = AlertsService()
