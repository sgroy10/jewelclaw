"""
SQLAlchemy models for JewelClaw.

Models:
- User: WhatsApp users and their preferences
- Conversation: Message history for context
- MetalRate: Historical gold/silver/platinum rates
- BusinessMemory: AI agent's learned facts about each user's business
- ConversationSummary: Compressed conversation history for context window
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean,
    DateTime, ForeignKey, Index, Enum as SQLEnum, JSON
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum

from app.database import Base


class LanguagePreference(enum.Enum):
    """User language preference."""
    ENGLISH = "english"
    HINDI = "hindi"
    HINGLISH = "hinglish"
    AUTO = "auto"


class User(Base):
    """WhatsApp user profile and preferences."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    phone_number = Column(String(20), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=True)

    # Preferences
    language = Column(
        SQLEnum(LanguagePreference),
        default=LanguagePreference.AUTO,
        nullable=False
    )
    subscribed_to_morning_brief = Column(Boolean, default=True)
    preferred_city = Column(String(50), default="Mumbai")
    timezone = Column(String(50), default="Asia/Kolkata")  # Auto-detected from phone country code

    # AI Agent: Business profile
    business_type = Column(String(50), nullable=True)  # retailer, wholesaler, manufacturer, designer
    primary_metals = Column(JSON, nullable=True)  # ["gold", "silver"]
    primary_categories = Column(JSON, nullable=True)  # ["bridal", "dailywear"]
    gold_buy_threshold = Column(Float, nullable=True)  # INR per gram - alert when gold drops below
    gold_sell_threshold = Column(Float, nullable=True)  # INR per gram - alert when gold rises above
    ai_personality_notes = Column(Text, nullable=True)  # Free-text notes about user communication style
    onboarding_completed = Column(Boolean, default=False)
    total_ai_interactions = Column(Integer, default=0)

    # Metadata
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    last_message_at = Column(DateTime, nullable=True)
    message_count = Column(Integer, default=0)

    # Relationships
    conversations = relationship("Conversation", back_populates="user", cascade="all, delete-orphan")
    design_preferences = relationship("UserDesignPreference", back_populates="user", cascade="all, delete-orphan")
    lookbooks = relationship("Lookbook", back_populates="user", cascade="all, delete-orphan")
    business_memories = relationship("BusinessMemory", back_populates="user", cascade="all, delete-orphan")
    reminders = relationship("Reminder", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User {self.phone_number}>"


class Conversation(Base):
    """Message history for maintaining context."""

    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    role = Column(String(20), nullable=False)  # "user" or "assistant"
    content = Column(Text, nullable=False)
    detected_language = Column(String(20), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)

    # Phase 1: Conversation Intelligence
    intent = Column(String(50), nullable=True)  # gold_price, subscribe, greeting, etc.
    entities = Column(JSON, default={})  # {"metal": "gold", "city": "mumbai"}
    sentiment = Column(String(20), nullable=True)  # positive, neutral, negative

    user = relationship("User", back_populates="conversations")

    __table_args__ = (
        Index("idx_conversation_user_created", "user_id", "created_at"),
    )

    def __repr__(self):
        return f"<Conversation {self.id} - {self.role}>"


class MetalRate(Base):
    """Historical rates for gold, silver, and platinum."""

    __tablename__ = "metal_rates"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Location
    city = Column(String(50), nullable=False, index=True)
    rate_date = Column(String(50), nullable=True)  # Date from source website

    # Gold rates (INR per gram)
    gold_24k = Column(Float, nullable=False)
    gold_22k = Column(Float, nullable=False)
    gold_18k = Column(Float, nullable=True)
    gold_14k = Column(Float, nullable=True)
    gold_10k = Column(Float, nullable=True)
    gold_9k = Column(Float, nullable=True)

    # Other metals (INR per gram)
    silver = Column(Float, nullable=True)
    platinum = Column(Float, nullable=True)

    # International prices (USD per troy oz)
    gold_usd_oz = Column(Float, nullable=True)
    silver_usd_oz = Column(Float, nullable=True)
    platinum_usd_oz = Column(Float, nullable=True)

    # Exchange rate
    usd_inr = Column(Float, nullable=True)

    # MCX Futures
    mcx_gold_futures = Column(Float, nullable=True)
    mcx_silver_futures = Column(Float, nullable=True)

    # Source and timestamp
    source = Column(String(50), default="goodreturns.in")
    recorded_at = Column(DateTime, server_default=func.now(), index=True)

    __table_args__ = (
        Index("idx_metalrate_city_recorded", "city", "recorded_at"),
    )

    def __repr__(self):
        return f"<MetalRate {self.city} - 24K: Rs.{self.gold_24k}>"


# Keep old name for backward compatibility
GoldRate = MetalRate


# =============================================================================
# TREND SCOUT MODELS
# =============================================================================

class Design(Base):
    """Jewelry designs scraped from competitors and trend sources."""

    __tablename__ = "designs"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Source info
    source = Column(String(50), nullable=False, index=True)  # bluestone, caratlane, tanishq, pinterest, amazon, etsy, vogue
    source_url = Column(String(500), nullable=True)
    image_url = Column(String(500), nullable=True)
    source_type = Column(String(30), default="product")  # product, editorial, marketplace, inspiration

    # Design details
    title = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)
    category = Column(String(50), nullable=True, index=True)  # bridal, dailywear, mens, kids, temple, contemporary
    metal_type = Column(String(30), nullable=True)  # gold, silver, platinum, diamond
    karat = Column(String(10), nullable=True)  # 22K, 18K, etc.

    # Pricing
    price_range_min = Column(Float, nullable=True)
    price_range_max = Column(Float, nullable=True)

    # Tags and scoring
    style_tags = Column(JSON, default=[])  # ["minimal", "traditional", "kundan", "polki"]
    trending_score = Column(Float, default=0)  # 0-100, calculated from likes/views

    # Timestamps
    scraped_at = Column(DateTime, server_default=func.now())
    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    user_preferences = relationship("UserDesignPreference", back_populates="design", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_design_category_score", "category", "trending_score"),
        Index("idx_design_source_scraped", "source", "scraped_at"),
    )

    def __repr__(self):
        return f"<Design {self.id} - {self.title[:30] if self.title else 'Untitled'}>"


class UserDesignPreference(Base):
    """Track user interactions with designs (like/skip/save)."""

    __tablename__ = "user_design_preferences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    design_id = Column(Integer, ForeignKey("designs.id", ondelete="CASCADE"), nullable=False, index=True)

    action = Column(String(20), nullable=False)  # liked, skipped, saved
    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    user = relationship("User", back_populates="design_preferences")
    design = relationship("Design", back_populates="user_preferences")

    __table_args__ = (
        Index("idx_user_design_action", "user_id", "design_id", "action"),
    )


class Lookbook(Base):
    """User-curated collections of designs for PDF export."""

    __tablename__ = "lookbooks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    name = Column(String(100), nullable=False)
    design_ids = Column(JSON, default=[])  # [1, 5, 12, 34, ...]
    pdf_url = Column(String(500), nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    user = relationship("User", back_populates="lookbooks")


# =============================================================================
# PRICE TRACKING & ALERTS
# =============================================================================

class PriceHistory(Base):
    """Track price changes for designs over time."""

    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    design_id = Column(Integer, ForeignKey("designs.id", ondelete="CASCADE"), nullable=False, index=True)

    price = Column(Float, nullable=False)
    recorded_at = Column(DateTime, server_default=func.now(), index=True)

    __table_args__ = (
        Index("idx_price_history_design_time", "design_id", "recorded_at"),
    )


class Alert(Base):
    """User alerts for price drops, new arrivals, etc."""

    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    alert_type = Column(String(50), nullable=False)  # price_drop, new_arrival, trending, competitor_update
    title = Column(String(200), nullable=False)
    message = Column(Text, nullable=True)
    design_id = Column(Integer, ForeignKey("designs.id", ondelete="SET NULL"), nullable=True)

    # Alert extra data
    extra_data = Column(JSON, default={})  # {"old_price": 50000, "new_price": 45000, "drop_percent": 10}

    # Status
    is_sent = Column(Boolean, default=False)
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_alert_user_sent", "user_id", "is_sent"),
    )


class TrendReport(Base):
    """Daily/weekly trend analysis reports."""

    __tablename__ = "trend_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)

    report_type = Column(String(50), nullable=False)  # daily, weekly
    report_date = Column(DateTime, nullable=False, index=True)

    # Trend data
    top_categories = Column(JSON, default=[])  # [{"category": "bridal", "count": 45, "change": "+15%"}]
    top_designs = Column(JSON, default=[])  # [{"design_id": 123, "score": 95}]
    price_trends = Column(JSON, default={})  # {"avg_necklace_price": 75000, "change": "-2%"}
    new_arrivals_count = Column(Integer, default=0)

    # Source breakdown
    source_stats = Column(JSON, default={})  # {"bluestone": 25, "caratlane": 18, "tanishq": 12}

    created_at = Column(DateTime, server_default=func.now())


# =============================================================================
# AI AGENT MODELS
# =============================================================================

class BusinessMemory(Base):
    """AI agent's learned facts about each user's jewelry business."""

    __tablename__ = "business_memories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Categorization
    category = Column(String(50), nullable=False)  # making_charges, buy_threshold, sell_threshold, supplier, customer_preference, business_fact, inventory, interest, pricing_rule
    key = Column(String(200), nullable=False)  # e.g. "22k_necklace_making_charge", "preferred_supplier_gold"
    value = Column(Text, nullable=False)  # Human-readable value: "18%", "Rajesh Jewellers"
    value_numeric = Column(Float, nullable=True)  # Numeric value if applicable: 18.0, 7000.0

    # Context
    metal_type = Column(String(30), nullable=True)  # gold, silver, platinum
    jewelry_category = Column(String(50), nullable=True)  # necklace, ring, bangle, etc.
    confidence = Column(Float, default=1.0)  # 0-1, how confident we are in this fact

    # Tracking
    source_message_id = Column(Integer, nullable=True)  # Conversation.id that generated this
    extracted_at = Column(DateTime, server_default=func.now())
    last_referenced_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)

    # Relationships
    user = relationship("User", back_populates="business_memories")

    __table_args__ = (
        Index("idx_business_memory_user_category", "user_id", "category"),
        Index("idx_business_memory_user_key", "user_id", "key"),
    )

    def __repr__(self):
        return f"<BusinessMemory {self.user_id}: {self.key}={self.value}>"


# =============================================================================
# REMINDGENIE MODELS
# =============================================================================

class Reminder(Base):
    """User reminders for birthdays, anniversaries, festivals, and custom dates."""

    __tablename__ = "reminders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Who/What
    name = Column(String(100), nullable=False)  # "Mom", "Priya Sharma", "Diwali"
    relation = Column(String(50), nullable=True)  # "Mother", "Customer", "Festival"
    occasion = Column(String(50), nullable=False)  # "birthday", "anniversary", "festival", "custom"

    # When (month + day for annual recurring, full date for one-time)
    remind_month = Column(Integer, nullable=False)  # 1-12
    remind_day = Column(Integer, nullable=False)  # 1-31
    remind_year = Column(Integer, nullable=True)  # NULL = every year, set = one-time

    # Greeting
    custom_note = Column(Text, nullable=True)  # User's optional note

    # Status
    is_active = Column(Boolean, default=True)
    last_sent_at = Column(DateTime, nullable=True)  # Last time greeting was sent
    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    user = relationship("User", back_populates="reminders")

    __table_args__ = (
        Index("idx_reminder_user_active", "user_id", "is_active"),
        Index("idx_reminder_month_day", "remind_month", "remind_day"),
    )

    def __repr__(self):
        return f"<Reminder {self.name} - {self.occasion} ({self.remind_month}/{self.remind_day})>"


class FestivalCalendar(Base):
    """Auto-updated Indian festival calendar with correct dates per year."""

    __tablename__ = "festival_calendar"

    id = Column(Integer, primary_key=True, autoincrement=True)
    year = Column(Integer, nullable=False, index=True)
    month = Column(Integer, nullable=False)
    day = Column(Integer, nullable=False)
    name = Column(String(100), nullable=False)
    festival_type = Column(String(30), default="festival")  # festival, national, special
    greeting_hint = Column(String(200), nullable=True)
    is_lunar = Column(Boolean, default=False)  # True = date shifts yearly

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_festival_year_month_day", "year", "month", "day"),
    )


class IndustryNews(Base):
    """Jewelry industry news items scraped from RSS feeds."""

    __tablename__ = "industry_news"

    id = Column(Integer, primary_key=True, autoincrement=True)
    headline = Column(String(500), nullable=False)
    source_url = Column(String(500), nullable=True)
    source = Column(String(100), nullable=True)  # google_news, jck, et, etc.

    # AI categorization
    category = Column(String(50), nullable=True)  # launch, store_opening, collection, regulation, market, trend
    priority = Column(String(20), default="low")  # high, medium, low
    brands = Column(JSON, default=[])  # ["Tanishq", "Cartier"]
    summary = Column(Text, nullable=True)  # Claude one-liner

    # Status
    is_alerted = Column(Boolean, default=False)
    is_briefed = Column(Boolean, default=False)

    scraped_at = Column(DateTime, server_default=func.now(), index=True)

    __table_args__ = (
        Index("idx_industry_news_priority", "priority", "scraped_at"),
    )


class ConversationSummary(Base):
    """Compressed conversation history for AI context window management."""

    __tablename__ = "conversation_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    summary_text = Column(Text, nullable=False)
    messages_covered = Column(Integer, default=0)  # How many messages this summary covers
    oldest_message_id = Column(Integer, nullable=True)
    newest_message_id = Column(Integer, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
