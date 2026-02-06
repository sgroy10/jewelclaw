"""
Memory Service - Conversation intelligence with intent/entity detection.

Phase 1 of OpenClaw architecture.
- Detects intent from user messages
- Extracts entities (metal, price, phone, name, city)
- Analyzes sentiment
- Stores conversations with metadata
"""

import logging
import re
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


# Intent patterns - maps regex patterns to intents
INTENT_PATTERNS = {
    "gold_price": [
        r"\bgold\b", r"\bsona\b", r"\b24k\b", r"\b22k\b", r"\b18k\b",
        r"\brate\b", r"\bprice\b", r"\bbhav\b"
    ],
    "silver_price": [
        r"\bsilver\b", r"\bchandi\b"
    ],
    "platinum_price": [
        r"\bplatinum\b"
    ],
    "subscribe": [
        r"\bsubscribe\b", r"\bmorning\s*brief\b", r"\bdaily\b"
    ],
    "unsubscribe": [
        r"\bunsubscribe\b", r"\bstop\b", r"\bcancel\s*subscription\b"
    ],
    "greeting": [
        r"\bhi\b", r"\bhello\b", r"\bhey\b", r"\bnamaste\b", r"\bhii+\b"
    ],
    "help": [
        r"\bhelp\b", r"\bcommand\b", r"\bmenu\b", r"\bwhat\s*can\b"
    ],
    "thanks": [
        r"\bthanks?\b", r"\bthank\s*you\b", r"\bdhanyawad\b"
    ],
    # Trend Scout intents
    "trends": [
        r"\btrends?\b", r"\btrending\b", r"\bnew\s*designs?\b", r"\bwhat.s\s*hot\b"
    ],
    "bridal": [
        r"\bbridal\b", r"\bwedding\b", r"\bengagement\b", r"\bmangalsutra\b"
    ],
    "dailywear": [
        r"\bdailywear\b", r"\bdaily\s*wear\b", r"\blightweight\b", r"\boffice\s*wear\b"
    ],
    "temple": [
        r"\btemple\b", r"\btraditional\b", r"\bantique\b"
    ],
    "mens": [
        r"\bmens?\b", r"\bmen.s\b", r"\bgents?\b", r"\bkada\b"
    ],
    "like_design": [
        r"\blike\s*(\d+)\b", r"\blove\s*(\d+)\b", r"\bsave\s*(\d+)\b"
    ],
    "skip_design": [
        r"\bskip\s*(\d+)\b", r"\bpass\s*(\d+)\b", r"\bnext\s*(\d+)\b"
    ],
    "lookbook": [
        r"\blookbook\b", r"\bsaved\b", r"\bmy\s*designs?\b", r"\bfavorites?\b"
    ],
}

# Entity extraction patterns
ENTITY_PATTERNS = {
    "metal": r"\b(gold|silver|platinum|sona|chandi)\b",
    "karat": r"\b(24k|22k|18k|14k|10k|9k)\b",
    "price": r"(?:rs\.?|₹|inr)\s*(\d+(?:,\d+)*(?:\.\d+)?)",
    "weight": r"(\d+(?:\.\d+)?)\s*(?:gram|gm|g)\b",
    "phone": r"\b(\d{10})\b",
    "name": r"(?:name\s*(?:is)?|i\s*am|called?)\s*([A-Za-z]+(?:\s+[A-Za-z]+)?)",
    "city": r"\b(mumbai|delhi|bangalore|chennai|kolkata|hyderabad|pune|ahmedabad|jaipur)\b",
}

# Sentiment words
POSITIVE_WORDS = ["thanks", "great", "good", "nice", "helpful", "awesome", "love", "perfect", "excellent"]
NEGATIVE_WORDS = ["bad", "wrong", "error", "problem", "issue", "not working", "hate", "worst", "terrible"]


class MemoryService:
    """Service for conversation intelligence."""

    def detect_intent(self, message: str) -> str:
        """Detect the primary intent from a message."""
        message_lower = message.lower().strip()

        for intent, patterns in INTENT_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, message_lower, re.IGNORECASE):
                    return intent

        return "unknown"

    def extract_entities(self, message: str) -> Dict[str, Any]:
        """Extract entities from a message."""
        entities = {}
        message_lower = message.lower()

        for entity_type, pattern in ENTITY_PATTERNS.items():
            match = re.search(pattern, message_lower, re.IGNORECASE)
            if match:
                value = match.group(1) if match.groups() else match.group(0)
                # Clean up values
                if entity_type == "price":
                    value = float(value.replace(",", ""))
                elif entity_type == "weight":
                    value = float(value)
                elif entity_type == "metal":
                    # Normalize Hindi to English
                    value = value.replace("sona", "gold").replace("chandi", "silver")
                entities[entity_type] = value

        return entities

    def detect_sentiment(self, message: str) -> str:
        """Simple sentiment detection."""
        message_lower = message.lower()

        positive_count = sum(1 for word in POSITIVE_WORDS if word in message_lower)
        negative_count = sum(1 for word in NEGATIVE_WORDS if word in message_lower)

        if positive_count > negative_count:
            return "positive"
        elif negative_count > positive_count:
            return "negative"
        return "neutral"

    def analyze_message(self, message: str) -> Dict[str, Any]:
        """Full analysis of a message - intent, entities, sentiment."""
        return {
            "intent": self.detect_intent(message),
            "entities": self.extract_entities(message),
            "sentiment": self.detect_sentiment(message),
            "analyzed_at": datetime.utcnow().isoformat()
        }


# Singleton instance
memory_service = MemoryService()
