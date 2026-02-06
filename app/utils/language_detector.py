"""
Language detection for Hindi, English, and Hinglish.
Detects user's language preference from their messages.
"""

import re
from typing import Optional
from langdetect import detect, LangDetectException


# Common Hindi words written in Roman script (Hinglish indicators)
HINGLISH_MARKERS = {
    # Greetings
    "namaste", "namaskar", "dhanyawad", "shukriya", "alvida",
    # Common words
    "kya", "kaise", "kaisa", "kaisi", "kab", "kahan", "kyun", "kyu",
    "hai", "hain", "ho", "tha", "thi", "the",
    "aur", "ya", "par", "lekin", "toh", "bhi",
    "acha", "accha", "theek", "thik", "sahi",
    "nahi", "nahin", "nah", "haan", "ji",
    "karo", "karna", "karenge", "karunga",
    "bolo", "batao", "bataiye", "batana",
    "dekho", "dekhna", "dekhiye",
    "samajh", "samjho", "samajhna",
    # Jewelry/gold related in Hinglish
    "sona", "chandi", "heera", "moti",
    "tola", "ratti", "masha",
    "gehna", "zewar", "haar", "kangan", "bali",
    # Numbers
    "ek", "do", "teen", "char", "paanch",
    # Pronouns
    "mera", "meri", "mere", "tera", "teri", "tere",
    "aap", "aapka", "aapki", "tumhara", "tumhari",
    # Question words
    "kitna", "kitni", "kitne", "kaun", "konsa",
    # Time
    "aaj", "kal", "parso", "abhi", "baad",
    # Business
    "daam", "kimat", "rate", "bhav",
}

# Hindi (Devanagari) Unicode range
DEVANAGARI_PATTERN = re.compile(r'[\u0900-\u097F]')


class LanguageDetector:
    """Detect language from text input."""

    def detect(self, text: str) -> str:
        """
        Detect the language of the given text.

        Args:
            text: Input text to analyze

        Returns:
            "hindi" for Devanagari Hindi
            "hinglish" for Hindi in Roman script
            "english" for English
        """
        if not text or not text.strip():
            return "english"

        text = text.strip().lower()

        # Check for Devanagari script first
        if self._has_devanagari(text):
            return "hindi"

        # Check for Hinglish markers
        if self._is_hinglish(text):
            return "hinglish"

        # Use langdetect for remaining cases
        try:
            detected = detect(text)
            if detected == "hi":
                # langdetect detected Hindi but no Devanagari - likely Hinglish
                return "hinglish"
            return "english"
        except LangDetectException:
            return "english"

    def _has_devanagari(self, text: str) -> bool:
        """Check if text contains Devanagari script."""
        return bool(DEVANAGARI_PATTERN.search(text))

    def _is_hinglish(self, text: str) -> bool:
        """Check if text appears to be Hinglish (Hindi in Roman script)."""
        words = set(re.findall(r'\b[a-z]+\b', text.lower()))

        # Count Hinglish markers
        hinglish_count = len(words.intersection(HINGLISH_MARKERS))

        # If more than 20% of words are Hinglish markers, classify as Hinglish
        if words and hinglish_count / len(words) > 0.2:
            return True

        # If at least 2 Hinglish markers present in short text
        if hinglish_count >= 2:
            return True

        return False

    def get_response_language_hint(self, detected: str) -> Optional[str]:
        """
        Get a hint for Claude about how to respond.

        Args:
            detected: Detected language

        Returns:
            Language instruction string or None
        """
        if detected == "hindi":
            return "Respond in Hindi using Devanagari script (हिंदी में जवाब दें)"
        elif detected == "hinglish":
            return "Respond in Hinglish (Hindi words in Roman/English script)"
        return None


# Singleton instance
language_detector = LanguageDetector()


def detect_language(text: str) -> str:
    """Convenience function for language detection."""
    return language_detector.detect(text)
