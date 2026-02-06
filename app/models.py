"""
SQLAlchemy models for JewelClaw.

Models:
- User: WhatsApp users and their preferences
- Conversation: Message history for context
- MetalRate: Historical gold/silver/platinum rates
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean,
    DateTime, ForeignKey, Index, Enum as SQLEnum
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

    # Metadata
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    last_message_at = Column(DateTime, nullable=True)
    message_count = Column(Integer, default=0)

    # Relationships
    conversations = relationship("Conversation", back_populates="user", cascade="all, delete-orphan")

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
