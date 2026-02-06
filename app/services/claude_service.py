"""
Claude AI integration for intelligent conversations.
Handles context management, system prompts, and jewelry-specific knowledge.
"""

import logging
from typing import Optional
from datetime import datetime
import anthropic
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.config import settings
from app.models import User, Conversation

logger = logging.getLogger(__name__)

# System prompt with jewelry industry context
SYSTEM_PROMPT = """You are JewelClaw, an AI assistant specialized in the Indian jewelry industry. You help jewelry manufacturers, retailers, and enthusiasts with:

1. **Gold & Silver Rates**: Provide current rates, explain price movements, and offer market insights
2. **Market Analysis**: Trend analysis, buying/selling recommendations, and market sentiment
3. **Industry Knowledge**: Jewelry manufacturing, hallmarking, purity standards, and trade practices
4. **Business Advice**: Inventory management, pricing strategies, and customer handling

Key knowledge:
- Gold purity: 24K (99.9%), 22K (91.6%), 18K (75%), 14K (58.3%)
- IBJA (India Bullion and Jewellers Association) sets benchmark rates
- Making charges typically range from 8-25% depending on design complexity
- GST on gold jewelry is 3%
- Hallmarking is mandatory in India (BIS standard)

Communication style:
- Be friendly, professional, and knowledgeable
- Use the user's preferred language (English, Hindi, or Hinglish)
- Provide actionable insights, not just data
- Keep responses concise for WhatsApp readability
- Use relevant emojis sparingly for visual appeal

When discussing rates:
- Always mention both per gram and per 10 gram prices
- Compare with yesterday's rates when available
- Explain the "why" behind price movements
- Give clear buy/wait recommendations when asked

Current date: {current_date}
Current time (IST): {current_time}
"""


class ClaudeService:
    """Service for Claude AI interactions."""

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model = "claude-sonnet-4-20250514"
        self.max_tokens = 1024

    async def get_conversation_context(
        self,
        db: AsyncSession,
        user: User,
        limit: int = 10
    ) -> list[dict]:
        """Retrieve recent conversation history for context."""
        result = await db.execute(
            select(Conversation)
            .where(Conversation.user_id == user.id)
            .order_by(desc(Conversation.created_at))
            .limit(limit)
        )
        conversations = result.scalars().all()

        # Reverse to get chronological order
        messages = []
        for conv in reversed(conversations):
            messages.append({
                "role": conv.role,
                "content": conv.content
            })

        return messages

    async def save_message(
        self,
        db: AsyncSession,
        user: User,
        role: str,
        content: str,
        detected_language: Optional[str] = None
    ):
        """Save a message to conversation history."""
        conversation = Conversation(
            user_id=user.id,
            role=role,
            content=content,
            detected_language=detected_language
        )
        db.add(conversation)
        await db.flush()

    def _get_system_prompt(self, gold_context: Optional[str] = None) -> str:
        """Generate system prompt with current date/time and optional gold data."""
        now = datetime.now()
        prompt = SYSTEM_PROMPT.format(
            current_date=now.strftime("%d %B %Y"),
            current_time=now.strftime("%I:%M %p")
        )

        if gold_context:
            prompt += f"\n\nCurrent Gold Rate Data:\n{gold_context}"

        return prompt

    async def chat(
        self,
        db: AsyncSession,
        user: User,
        message: str,
        gold_context: Optional[str] = None,
        language_hint: Optional[str] = None
    ) -> str:
        """
        Process a chat message and generate response.

        Args:
            db: Database session
            user: User object
            message: User's message
            gold_context: Current gold rate data for context
            language_hint: Detected language of user's message

        Returns:
            Assistant's response
        """
        try:
            # Get conversation history
            history = await self.get_conversation_context(db, user)

            # Add current message to history
            history.append({"role": "user", "content": message})

            # Build system prompt
            system_prompt = self._get_system_prompt(gold_context)

            # Add language instruction if detected
            if language_hint:
                if language_hint == "hindi":
                    system_prompt += "\n\nThe user is writing in Hindi. Respond in Hindi (Devanagari script)."
                elif language_hint == "hinglish":
                    system_prompt += "\n\nThe user is writing in Hinglish. Respond in Hinglish (Hindi words in Roman script mixed with English)."

            # Call Claude API
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system_prompt,
                messages=history
            )

            assistant_message = response.content[0].text

            # Save both messages to history
            await self.save_message(db, user, "user", message, language_hint)
            await self.save_message(db, user, "assistant", assistant_message)

            logger.info(f"Generated response for user {user.phone_number}")
            return assistant_message

        except anthropic.APIError as e:
            logger.error(f"Claude API error: {e}")
            return "Sorry, I'm having trouble processing your request. Please try again in a moment."

        except Exception as e:
            logger.error(f"Error in chat: {e}")
            return "Something went wrong. Please try again."

    async def generate_morning_brief(
        self,
        gold_data: dict,
        language: str = "english"
    ) -> str:
        """
        Generate the morning brief message.

        Args:
            gold_data: Dictionary with current rates and analysis
            language: Target language for the brief

        Returns:
            Formatted morning brief message
        """
        prompt = f"""Generate a morning brief message for a jewelry business WhatsApp group.

Gold Data:
{gold_data}

Requirements:
1. Start with a greeting appropriate for 8 AM
2. Show 24K and 22K gold rates prominently
3. Show silver rate
4. Include daily change (up/down arrow emoji)
5. Include weekly trend percentage
6. Add a brief market insight or recommendation
7. Keep it under 500 characters for WhatsApp readability
8. Use emojis appropriately

Language: {language}
{"Use Hindi (Devanagari script)" if language == "hindi" else ""}
{"Use Hinglish (Roman script with Hindi words)" if language == "hinglish" else ""}

Format it nicely with line breaks and emojis."""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}]
            )
            return response.content[0].text

        except Exception as e:
            logger.error(f"Error generating morning brief: {e}")
            # Return a basic fallback brief
            return self._fallback_morning_brief(gold_data)

    def _fallback_morning_brief(self, gold_data: dict) -> str:
        """Generate a simple fallback brief if AI generation fails."""
        return f"""🌅 Good Morning!

💰 GOLD: ₹{gold_data.get('gold_24k', 'N/A')}/gm (24K) | ₹{gold_data.get('gold_22k', 'N/A')}/gm (22K)
💎 SILVER: ₹{gold_data.get('silver', 'N/A')}/gm

Have a great day! 🙏"""


# Singleton instance
claude_service = ClaudeService()
