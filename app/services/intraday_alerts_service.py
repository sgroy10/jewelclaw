"""
Intraday Gold Alerts Service — Standalone real-time gold price monitoring.

Completely separate from morning brief, gold command, and existing price alerts.
Runs its own scheduler jobs and sends WhatsApp alerts for:
- Big moves (>1% in 15 min)
- User buy/sell target hits
- Day high/low
- Multi-day high/low (7-day, 30-day)
- Overnight COMEX signals (6:30 AM)

Anti-spam: max 3 alerts/user/day, 1hr cooldown, threshold alerts fire once per direction.
"""

import logging
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func as sqlfunc

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class AlertTrigger:
    """A detected alert trigger ready to send."""
    user_id: int
    phone_number: str
    user_name: str
    alert_type: str  # big_move, buy_target, sell_target, day_high, day_low, multi_day_high, multi_day_low
    gold_price: float
    message: str


class IntradayAlertsService:
    """Standalone intraday gold price alert engine."""

    def __init__(self):
        # In-memory tracking (reset on restart, but safe — DB is source of truth for anti-spam)
        self._last_price: Optional[float] = None
        self._day_high: Optional[float] = None
        self._day_low: Optional[float] = None
        self._day_high_alerted: bool = False
        self._day_low_alerted: bool = False
        self._current_date: Optional[date] = None
        # Track which users had their buy/sell target fired today
        self._buy_target_fired: set = set()  # user_ids
        self._sell_target_fired: set = set()  # user_ids

    def _reset_daily_state(self):
        """Reset daily tracking at midnight."""
        today = date.today()
        if self._current_date != today:
            self._current_date = today
            self._day_high = None
            self._day_low = None
            self._day_high_alerted = False
            self._day_low_alerted = False
            self._buy_target_fired.clear()
            self._sell_target_fired.clear()
            logger.info("Intraday alerts: daily state reset")

    async def check_and_alert(self, db: AsyncSession, gold_24k: float):
        """
        Main entry point — called every 15 min by scheduler after rate scrape.
        Detects triggers and sends alerts to enabled users.
        """
        if not gold_24k or gold_24k <= 0:
            return

        self._reset_daily_state()

        # Update day high/low
        if self._day_high is None or gold_24k > self._day_high:
            self._day_high = gold_24k
        if self._day_low is None or gold_24k < self._day_low:
            self._day_low = gold_24k

        # Get all users with intraday alerts enabled
        from app.models import User
        result = await db.execute(
            select(User).where(User.intraday_alerts_enabled == True)
        )
        enabled_users = result.scalars().all()

        if not enabled_users:
            self._last_price = gold_24k
            return

        logger.info(f"Intraday check: Gold ₹{gold_24k:,.0f}, {len(enabled_users)} users enabled")

        triggers: List[AlertTrigger] = []

        # --- TRIGGER 1: BIG MOVE (>1% since last check) ---
        if self._last_price and self._last_price > 0:
            pct_change = ((gold_24k - self._last_price) / self._last_price) * 100
            if abs(pct_change) >= 1.0:
                direction = "up" if pct_change > 0 else "down"
                abs_change = abs(gold_24k - self._last_price)
                for user in enabled_users:
                    triggers.append(AlertTrigger(
                        user_id=user.id,
                        phone_number=user.phone_number,
                        user_name=user.name or "Friend",
                        alert_type="big_move",
                        gold_price=gold_24k,
                        message=self._format_big_move(
                            gold_24k, pct_change, abs_change, direction
                        ),
                    ))

        # --- TRIGGER 2: USER BUY TARGET HIT ---
        for user in enabled_users:
            if (user.intraday_buy_target
                    and gold_24k <= user.intraday_buy_target
                    and user.id not in self._buy_target_fired):
                diff = user.intraday_buy_target - gold_24k
                triggers.append(AlertTrigger(
                    user_id=user.id,
                    phone_number=user.phone_number,
                    user_name=user.name or "Friend",
                    alert_type="buy_target",
                    gold_price=gold_24k,
                    message=self._format_buy_target(
                        user.name or "Friend", gold_24k, user.intraday_buy_target, diff
                    ),
                ))
                self._buy_target_fired.add(user.id)

        # --- TRIGGER 3: USER SELL TARGET HIT ---
        for user in enabled_users:
            if (user.intraday_sell_target
                    and gold_24k >= user.intraday_sell_target
                    and user.id not in self._sell_target_fired):
                diff = gold_24k - user.intraday_sell_target
                triggers.append(AlertTrigger(
                    user_id=user.id,
                    phone_number=user.phone_number,
                    user_name=user.name or "Friend",
                    alert_type="sell_target",
                    gold_price=gold_24k,
                    message=self._format_sell_target(
                        user.name or "Friend", gold_24k, user.intraday_sell_target, diff
                    ),
                ))
                self._sell_target_fired.add(user.id)

        # --- TRIGGER 4: DAY HIGH ---
        if gold_24k == self._day_high and not self._day_high_alerted and self._last_price:
            # Only alert if we've moved up to a new high (not the first reading)
            if gold_24k > (self._last_price or 0):
                for user in enabled_users:
                    triggers.append(AlertTrigger(
                        user_id=user.id,
                        phone_number=user.phone_number,
                        user_name=user.name or "Friend",
                        alert_type="day_high",
                        gold_price=gold_24k,
                        message=self._format_day_high(gold_24k),
                    ))
                self._day_high_alerted = True

        # --- TRIGGER 5: DAY LOW ---
        if gold_24k == self._day_low and not self._day_low_alerted and self._last_price:
            if gold_24k < (self._last_price or float('inf')):
                for user in enabled_users:
                    triggers.append(AlertTrigger(
                        user_id=user.id,
                        phone_number=user.phone_number,
                        user_name=user.name or "Friend",
                        alert_type="day_low",
                        gold_price=gold_24k,
                        message=self._format_day_low(gold_24k),
                    ))
                self._day_low_alerted = True

        # --- TRIGGER 6: MULTI-DAY HIGH/LOW (7-day, 30-day) ---
        multi_day_triggers = await self._check_multi_day_extremes(db, gold_24k, enabled_users)
        triggers.extend(multi_day_triggers)

        # --- APPLY ANTI-SPAM + SEND ---
        if triggers:
            await self._send_with_anti_spam(db, triggers, gold_24k)

        self._last_price = gold_24k

    async def _check_multi_day_extremes(
        self, db: AsyncSession, gold_24k: float, users: list
    ) -> List[AlertTrigger]:
        """Check if current price is a 7-day or 30-day high/low."""
        from app.models import MetalRate

        triggers = []

        try:
            # Get 7-day and 30-day price range
            for days, label in [(7, "7-day"), (30, "30-day")]:
                cutoff = datetime.utcnow() - timedelta(days=days)
                result = await db.execute(
                    select(
                        sqlfunc.max(MetalRate.gold_24k),
                        sqlfunc.min(MetalRate.gold_24k),
                    ).where(
                        and_(
                            MetalRate.city == "Mumbai",
                            MetalRate.recorded_at >= cutoff,
                        )
                    )
                )
                row = result.first()
                if not row or row[0] is None:
                    continue

                period_high, period_low = row[0], row[1]

                if gold_24k > period_high:
                    for user in users:
                        triggers.append(AlertTrigger(
                            user_id=user.id,
                            phone_number=user.phone_number,
                            user_name=user.name or "Friend",
                            alert_type=f"multi_day_high",
                            gold_price=gold_24k,
                            message=self._format_multi_day(
                                gold_24k, label, "high", period_high
                            ),
                        ))
                    break  # 30-day high is more important than 7-day, send only the biggest

                if gold_24k < period_low:
                    for user in users:
                        triggers.append(AlertTrigger(
                            user_id=user.id,
                            phone_number=user.phone_number,
                            user_name=user.name or "Friend",
                            alert_type=f"multi_day_low",
                            gold_price=gold_24k,
                            message=self._format_multi_day(
                                gold_24k, label, "low", period_low
                            ),
                        ))
                    break

        except Exception as e:
            logger.error(f"Multi-day extreme check failed: {e}")

        return triggers

    async def send_comex_overnight(self, db: AsyncSession):
        """
        Called at 6:30 AM IST. Checks overnight COMEX gold movement
        and sends a signal to all enabled users.
        """
        from app.models import User, MetalRate

        # Get enabled users
        result = await db.execute(
            select(User).where(User.intraday_alerts_enabled == True)
        )
        enabled_users = result.scalars().all()
        if not enabled_users:
            return

        # Get latest rate (should have international prices)
        result = await db.execute(
            select(MetalRate)
            .where(MetalRate.city == "Mumbai")
            .order_by(MetalRate.recorded_at.desc())
            .limit(1)
        )
        latest = result.scalar_one_or_none()
        if not latest or not latest.gold_usd_oz:
            logger.info("COMEX overnight: No international price data available")
            return

        # Get yesterday's last rate for comparison
        yesterday = datetime.utcnow() - timedelta(hours=12)
        result = await db.execute(
            select(MetalRate)
            .where(
                and_(
                    MetalRate.city == "Mumbai",
                    MetalRate.recorded_at <= yesterday,
                    MetalRate.gold_usd_oz.isnot(None),
                )
            )
            .order_by(MetalRate.recorded_at.desc())
            .limit(1)
        )
        prev = result.scalar_one_or_none()

        if not prev or not prev.gold_usd_oz:
            return

        usd_change = latest.gold_usd_oz - prev.gold_usd_oz
        usd_pct = (usd_change / prev.gold_usd_oz) * 100 if prev.gold_usd_oz > 0 else 0

        # Only send if meaningful movement (>0.3%)
        if abs(usd_pct) < 0.3:
            logger.info(f"COMEX overnight: change {usd_pct:.2f}% too small, skipping")
            return

        message = self._format_comex_overnight(
            latest.gold_usd_oz, usd_change, usd_pct,
            latest.gold_24k, latest.mcx_gold_futures
        )

        from app.services.whatsapp_service import whatsapp_service
        from app.models import IntradayAlertLog

        sent = 0
        for user in enabled_users:
            # Check daily limit
            today_count = await self._get_today_alert_count(db, user.id)
            if today_count >= 3:
                continue

            phone = f"whatsapp:{user.phone_number}"
            success = await whatsapp_service.send_message(phone, message)
            if success:
                sent += 1
                log = IntradayAlertLog(
                    user_id=user.id,
                    alert_type="comex_overnight",
                    gold_price=latest.gold_24k or 0,
                    message=message[:500],
                )
                db.add(log)

        await db.flush()
        logger.info(f"COMEX overnight alert: sent to {sent}/{len(enabled_users)} users")

    async def _send_with_anti_spam(
        self, db: AsyncSession, triggers: List[AlertTrigger], gold_price: float
    ):
        """Apply anti-spam rules and send surviving alerts."""
        from app.services.whatsapp_service import whatsapp_service
        from app.models import IntradayAlertLog

        # Group triggers by user
        user_triggers: Dict[int, List[AlertTrigger]] = {}
        for t in triggers:
            user_triggers.setdefault(t.user_id, []).append(t)

        sent_total = 0

        for user_id, user_trigs in user_triggers.items():
            # Anti-spam check 1: Daily limit (max 3)
            today_count = await self._get_today_alert_count(db, user_id)
            if today_count >= 3:
                logger.info(f"Intraday alert skipped for user {user_id}: daily limit reached ({today_count})")
                continue

            # Anti-spam check 2: Cooldown (1 hour since last alert)
            last_alert_time = await self._get_last_alert_time(db, user_id)
            if last_alert_time:
                elapsed = (datetime.utcnow() - last_alert_time).total_seconds()
                if elapsed < 3600:  # 1 hour
                    logger.info(f"Intraday alert skipped for user {user_id}: cooldown ({elapsed:.0f}s ago)")
                    continue

            # Pick the most important trigger for this user
            # Priority: buy_target/sell_target > big_move > multi_day > day_high/low
            priority_order = {
                "buy_target": 1, "sell_target": 1,
                "big_move": 2,
                "multi_day_high": 3, "multi_day_low": 3,
                "day_high": 4, "day_low": 4,
            }
            best = min(user_trigs, key=lambda t: priority_order.get(t.alert_type, 99))

            # Send
            phone = f"whatsapp:{best.phone_number}"
            success = await whatsapp_service.send_message(phone, best.message)

            if success:
                sent_total += 1
                # Log to DB
                log = IntradayAlertLog(
                    user_id=best.user_id,
                    alert_type=best.alert_type,
                    gold_price=gold_price,
                    message=best.message[:500],
                )
                db.add(log)
                logger.info(
                    f"INTRADAY ALERT sent to {best.user_name}: "
                    f"{best.alert_type} @ ₹{gold_price:,.0f}"
                )

        if sent_total > 0:
            await db.flush()
            logger.info(f"Intraday alerts: {sent_total} sent this cycle")

    async def _get_today_alert_count(self, db: AsyncSession, user_id: int) -> int:
        """Count alerts sent to user today."""
        from app.models import IntradayAlertLog

        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        result = await db.execute(
            select(sqlfunc.count(IntradayAlertLog.id)).where(
                and_(
                    IntradayAlertLog.user_id == user_id,
                    IntradayAlertLog.sent_at >= today_start,
                )
            )
        )
        return result.scalar() or 0

    async def _get_last_alert_time(self, db: AsyncSession, user_id: int) -> Optional[datetime]:
        """Get the most recent alert time for a user."""
        from app.models import IntradayAlertLog

        result = await db.execute(
            select(IntradayAlertLog.sent_at)
            .where(IntradayAlertLog.user_id == user_id)
            .order_by(IntradayAlertLog.sent_at.desc())
            .limit(1)
        )
        row = result.first()
        return row[0] if row else None

    async def get_user_alert_status(self, db: AsyncSession, user_id: int) -> Dict:
        """Get alert settings and recent history for a user."""
        from app.models import User, IntradayAlertLog

        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return {"error": "User not found"}

        today_count = await self._get_today_alert_count(db, user_id)

        # Get last 5 alerts
        result = await db.execute(
            select(IntradayAlertLog)
            .where(IntradayAlertLog.user_id == user_id)
            .order_by(IntradayAlertLog.sent_at.desc())
            .limit(5)
        )
        recent = result.scalars().all()

        return {
            "enabled": user.intraday_alerts_enabled,
            "buy_target": user.intraday_buy_target,
            "sell_target": user.intraday_sell_target,
            "alerts_today": today_count,
            "max_daily": 3,
            "recent_alerts": [
                {
                    "type": a.alert_type,
                    "price": a.gold_price,
                    "time": a.sent_at.strftime("%d %b %H:%M") if a.sent_at else "?",
                }
                for a in recent
            ],
        }

    # =========================================================================
    # MESSAGE FORMATTERS
    # =========================================================================

    def _format_big_move(
        self, price: float, pct: float, abs_change: float, direction: str
    ) -> str:
        arrow = "📈" if direction == "up" else "📉"
        verb = "jumped" if direction == "up" else "dropped"
        return (
            f"{arrow} *GOLD {verb.upper()} {abs(pct):.1f}%!*\n\n"
            f"24K now at *₹{price:,.0f}/gm* — "
            f"{'up' if direction == 'up' else 'down'} ₹{abs_change:,.0f} in the last 15 min.\n\n"
            f"_JewelClaw Intraday Alert_"
        )

    def _format_buy_target(
        self, name: str, price: float, target: float, diff: float
    ) -> str:
        return (
            f"🎯 *BUY TARGET HIT!*\n\n"
            f"Hey {name}, gold just dropped to *₹{price:,.0f}/gm* — "
            f"₹{diff:,.0f} below your ₹{target:,.0f} target!\n\n"
            f"💰 Time to stock up?\n\n"
            f"_Reply 'gold' for full rates | 'alerts off' to pause_"
        )

    def _format_sell_target(
        self, name: str, price: float, target: float, diff: float
    ) -> str:
        return (
            f"📊 *SELL TARGET HIT!*\n\n"
            f"Hey {name}, gold just hit *₹{price:,.0f}/gm* — "
            f"₹{diff:,.0f} above your ₹{target:,.0f} target!\n\n"
            f"📈 Consider booking profits!\n\n"
            f"_Reply 'gold' for full rates | 'alerts off' to pause_"
        )

    def _format_day_high(self, price: float) -> str:
        return (
            f"⬆️ *NEW DAY HIGH*\n\n"
            f"Gold 24K just hit today's high: *₹{price:,.0f}/gm*\n\n"
            f"_JewelClaw Intraday Alert_"
        )

    def _format_day_low(self, price: float) -> str:
        return (
            f"⬇️ *NEW DAY LOW*\n\n"
            f"Gold 24K just hit today's low: *₹{price:,.0f}/gm*\n\n"
            f"_JewelClaw Intraday Alert_"
        )

    def _format_multi_day(
        self, price: float, period: str, direction: str, prev_extreme: float
    ) -> str:
        if direction == "high":
            diff = price - prev_extreme
            return (
                f"🔥 *{period.upper()} HIGH!*\n\n"
                f"Gold 24K just broke through its {period} high!\n"
                f"Now at *₹{price:,.0f}/gm* (prev high was ₹{prev_extreme:,.0f})\n\n"
                f"_Strong bullish signal_\n"
                f"_JewelClaw Intraday Alert_"
            )
        else:
            diff = prev_extreme - price
            return (
                f"💧 *{period.upper()} LOW!*\n\n"
                f"Gold 24K just broke below its {period} low!\n"
                f"Now at *₹{price:,.0f}/gm* (prev low was ₹{prev_extreme:,.0f})\n\n"
                f"_Potential buying opportunity_\n"
                f"_JewelClaw Intraday Alert_"
            )

    def _format_comex_overnight(
        self, usd_price: float, usd_change: float, usd_pct: float,
        inr_price: Optional[float], mcx_futures: Optional[float]
    ) -> str:
        direction = "up" if usd_change > 0 else "down"
        arrow = "📈" if direction == "up" else "📉"

        lines = [
            f"{arrow} *COMEX Overnight Signal*\n",
            f"Gold moved {'up' if usd_change > 0 else 'down'} *${abs(usd_change):.1f}* "
            f"({abs(usd_pct):.1f}%) overnight",
            f"COMEX: *${usd_price:,.1f}/oz*",
        ]

        if mcx_futures:
            lines.append(f"MCX Futures: *₹{mcx_futures:,.0f}*")

        if inr_price:
            lines.append(f"India 24K: *₹{inr_price:,.0f}/gm*")

        if usd_pct > 0.5:
            lines.append(f"\n_Expect Indian prices to open higher today_")
        elif usd_pct < -0.5:
            lines.append(f"\n_Expect Indian prices to open lower today — potential buy window_")

        lines.append(f"\n_JewelClaw Intraday Alert | Reply 'gold' for full rates_")

        return "\n".join(lines)

    def format_alert_status(self, status: Dict) -> str:
        """Format alert status for WhatsApp display."""
        if not status.get("enabled"):
            return (
                "📴 *Intraday Alerts: OFF*\n\n"
                "Turn on real-time gold alerts:\n"
                "• *alerts on* — Enable alerts\n"
                "• *buy alert 6800* — Alert when gold drops below ₹6,800\n"
                "• *sell alert 7200* — Alert when gold rises above ₹7,200\n\n"
                "_Max 3 alerts/day, 1hr cooldown between alerts_"
            )

        lines = ["📊 *Intraday Gold Alerts: ON*\n"]

        if status.get("buy_target"):
            lines.append(f"🎯 Buy target: *₹{status['buy_target']:,.0f}*/gm")
        else:
            lines.append("🎯 Buy target: _not set_ (set with 'buy alert 6800')")

        if status.get("sell_target"):
            lines.append(f"📊 Sell target: *₹{status['sell_target']:,.0f}*/gm")
        else:
            lines.append("📊 Sell target: _not set_ (set with 'sell alert 7200')")

        lines.append(f"\n📬 Alerts today: {status['alerts_today']}/{status['max_daily']}")

        if status.get("recent_alerts"):
            lines.append("\n_Recent alerts:_")
            for a in status["recent_alerts"][:3]:
                lines.append(f"  • {a['type']} @ ₹{a['price']:,.0f} ({a['time']})")

        lines.append("\n*Commands:*")
        lines.append("• *alerts off* — Pause alerts")
        lines.append("• *buy alert [price]* — Set buy target")
        lines.append("• *sell alert [price]* — Set sell target")
        lines.append("• *alerts clear* — Remove all targets")

        return "\n".join(lines)


# Singleton
intraday_alerts_service = IntradayAlertsService()
