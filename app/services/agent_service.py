"""
AI Agent Service - The brain of JewelClaw.

Handles:
1. Message classification (exact match -> regex -> Haiku fallback)
2. Claude tool-use orchestration for natural language conversations
3. Tool definitions and execution for jewelry business operations
"""

import logging
import re
import json
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Any

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.config import settings
from app.models import User, Conversation, BusinessMemory, MetalRate
from app.services.business_memory_service import business_memory_service
from app.services.reminder_service import reminder_service
from app.services.pricing_engine_service import pricing_engine
from app.services.background_agent_service import background_agent

logger = logging.getLogger(__name__)

# Commands that should always use the fast path (from whatsapp_service.py COMMANDS)
EXACT_COMMANDS = {
    "gold", "gold rate", "gold rates", "sona",
    "subscribe", "unsubscribe",
    "help", "menu", "setup", "onboarding", "start", "join",
    "trends", "trending", "bridal", "wedding", "dailywear", "daily wear",
    "lightweight", "temple", "traditional", "mens", "men", "gents",
    "lookbook", "saved", "favorites",
    "1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
    "fresh", "today", "about", "about jewelclaw",
    "remind", "remind list", "remind festivals",
    "quote", "price setup", "price profile", "pricing",
    "portfolio", "inventory", "holdings", "my holdings",
}

# Fuzzy patterns that map to existing commands
FUZZY_PATTERNS = [
    (r"^(what.?s|whats|show|get|check).*(gold|sona|rate)", "gold_rate"),
    (r"^gold.*(price|rate|today|now|current)", "gold_rate"),
    (r"^(kya|kitna|aaj).*(gold|sona|rate|bhav)", "gold_rate"),
    (r"^(show|get|check).*(trend|design|new)", "trends"),
    (r"^(bridal|shaadi|wedding).*(design|collection|jewel)", "bridal"),
    (r"^(daily|office|light).*(wear|jewel|design)", "dailywear"),
    (r"^(subscribe|daily brief|morning brief)", "subscribe"),
    (r"^(stop|unsubscribe|no more)", "unsubscribe"),
    (r"^(help|commands|what can you do)", "help"),
    (r"^(like|save)\s+\d+", "like"),
    (r"^(skip)\s+\d+", "skip"),
    (r"^(search|find)\s+.+", "search"),
    (r"^remind", "remind"),
    (r"^(birthday|anniversary|reminder)", "remind"),
    (r"^quote\s+\d", "quote"),
    (r"^price\s+(setup|set|profile|view)", "price setup"),
    (r"^(pricing|my\s*prices)", "pricing"),
    (r"^(portfolio|my\s*holdings|inventory|my\s*stock)", "portfolio"),
    (r"^i\s+have\s+\d+.*(?:gold|silver|platinum|sona|chandi)", "inventory_update"),
    (r"^(clear|remove|delete)\s+inventory", "clear_inventory"),
]

# Tool definitions for Claude
TOOLS = [
    {
        "name": "get_gold_rates",
        "description": "Get current live gold, silver, and platinum rates for a city in India. Returns rates per gram in INR for all karats (24K, 22K, 18K, 14K).",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "Indian city name (e.g. Mumbai, Delhi, Pune, Bangalore). Defaults to user's preferred city.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "store_business_fact",
        "description": "Store a fact about the user's jewelry business that was shared in conversation. Use this whenever the user tells you about their making charges, buy/sell thresholds, suppliers, preferences, inventory, or any business detail. Categories: making_charges, buy_threshold, sell_threshold, supplier, customer_preference, business_fact, inventory, interest, pricing_rule.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": [
                        "making_charges", "buy_threshold", "sell_threshold",
                        "supplier", "customer_preference", "business_fact",
                        "inventory", "interest", "pricing_rule",
                    ],
                    "description": "Category of the business fact.",
                },
                "key": {
                    "type": "string",
                    "description": "Unique key for this fact, e.g. '22k_necklace_making_charge', 'gold_buy_threshold', 'preferred_supplier'.",
                },
                "value": {
                    "type": "string",
                    "description": "Human-readable value, e.g. '18%', '₹7,000/gm', 'Rajesh Jewellers'.",
                },
                "value_numeric": {
                    "type": "number",
                    "description": "Numeric value if applicable (e.g. 18.0 for 18%, 7000.0 for ₹7000). Required for thresholds and charges.",
                },
                "metal_type": {
                    "type": "string",
                    "description": "Metal type if relevant: gold, silver, platinum.",
                },
                "jewelry_category": {
                    "type": "string",
                    "description": "Jewelry category if relevant: necklace, ring, bangle, earring, bracelet, chain, pendant.",
                },
            },
            "required": ["category", "key", "value"],
        },
    },
    {
        "name": "calculate_jewelry_quote",
        "description": "Calculate a full jewelry quote/bill with complete breakdown. Supports plain gold, gold+CZ, gold+diamond (natural & lab-grown), gold+gemstone. Uses user's stored pricing profile (model, making charges, stone rates, setting/finishing charges). Works in INR or USD. Shows cost price vs selling price if profit margin is set.",
        "input_schema": {
            "type": "object",
            "properties": {
                "weight_grams": {
                    "type": "number",
                    "description": "Weight of the jewelry piece in grams.",
                },
                "karat": {
                    "type": "string",
                    "enum": ["24k", "22k", "18k", "14k", "10k", "9k"],
                    "description": "Gold karat purity.",
                },
                "jewelry_type": {
                    "type": "string",
                    "description": "Type: necklace, ring, bangle, earring, chain, pendant, bracelet, mangalsutra, anklet, coin, brooch, tikka.",
                },
                "making_charge_percent": {
                    "type": "number",
                    "description": "Override making charge %. If not provided, uses user's stored rate.",
                },
                "labor_per_gram": {
                    "type": "number",
                    "description": "Override labor rate per gram (for per-gram pricing model).",
                },
                "cfp_rate": {
                    "type": "number",
                    "description": "Override CFP (cost for piece) rate.",
                },
                "cz_count": {
                    "type": "integer",
                    "description": "Number of CZ stones. Default 0.",
                },
                "cz_setting": {
                    "type": "string",
                    "enum": ["pave", "prong", "bezel", "channel", "micro_pave", "wax_set"],
                    "description": "CZ setting type. Default pave.",
                },
                "diamonds": {
                    "type": "array",
                    "description": "Diamond details. Each item: {sieve, count, quality, lab, setting, total_carats}.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "sieve": {"type": "string", "description": "Sieve size (000 to 16+). Default 7."},
                            "count": {"type": "integer", "description": "Number of diamonds."},
                            "total_carats": {"type": "number", "description": "Total carat weight (alternative to count)."},
                            "quality": {"type": "string", "description": "Quality grade like GH-VS, DEF-VVS, IJ-SI. Default GH-VS."},
                            "lab": {"type": "boolean", "description": "True for lab-grown diamonds. Default false."},
                            "setting": {"type": "string", "description": "Setting type: prong, pave, bezel, channel, invisible."},
                        },
                    },
                },
                "gemstones": {
                    "type": "array",
                    "description": "Gemstone details. Each item: {stone, carats, grade}.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "stone": {"type": "string", "description": "Stone name: ruby, emerald, sapphire, amethyst, topaz, etc."},
                            "carats": {"type": "number", "description": "Total carat weight."},
                            "grade": {"type": "string", "enum": ["low", "mid", "high"], "description": "Quality grade. Default mid."},
                        },
                    },
                },
                "finishing": {
                    "type": "array",
                    "description": "Finishing types applied: rhodium, black_rhodium, two_tone, sandblast, enamel, antique, matte.",
                    "items": {"type": "string"},
                },
                "quantity": {
                    "type": "integer",
                    "description": "Number of pieces. Default 1.",
                },
                "currency": {
                    "type": "string",
                    "enum": ["INR", "USD"],
                    "description": "Override currency. If not provided, uses user's preference.",
                },
            },
            "required": ["weight_grams", "karat"],
        },
    },
    {
        "name": "save_pricing_config",
        "description": "Save pricing configuration for the user. Use when user tells you about their making charges, labor rates, CZ rates, diamond rates, setting charges, finishing charges, pricing model, currency preference, profit margin, gold loss, or any pricing-related info. Saves to their permanent pricing profile.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pricing_data": {
                    "type": "object",
                    "description": "Pricing data to save. Can include any of: pricing_model ('percentage'/'per_gram'/'per_piece'/'all_inclusive'), currency ('INR'/'USD'), making_charges ({type: %}), labor_per_gram ({type: rate}), cfp_rates ({type: rate}), wastage ({type: %}), gold_loss_pct, profit_margin_pct, cz_rates ({setting: rate}), setting_rates ({type: rate}), finishing_rates ({type: rate}), diamond_rates ({size_quality: rate_per_ct}), hallmark_charge.",
                },
            },
            "required": ["pricing_data"],
        },
    },
    {
        "name": "search_designs",
        "description": "Search trending jewelry designs in our database by category, style, or keyword.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Category to search: bridal, dailywear, temple, mens, contemporary.",
                },
                "keyword": {
                    "type": "string",
                    "description": "Keyword to search in design titles and descriptions.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 5).",
                },
            },
            "required": [],
        },
    },
    {
        "name": "set_price_alert",
        "description": "Set a gold price alert for the user. They'll be notified when gold reaches their target price.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_price": {
                    "type": "number",
                    "description": "Target gold price per gram in INR (24K rate).",
                },
                "direction": {
                    "type": "string",
                    "enum": ["below", "above"],
                    "description": "Alert when price goes 'below' (for buying) or 'above' (for selling).",
                },
            },
            "required": ["target_price", "direction"],
        },
    },
    {
        "name": "get_business_memory",
        "description": "Retrieve stored business facts about this user. Use this to recall their making charges, thresholds, preferences, etc. before answering questions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Optional category filter: making_charges, buy_threshold, sell_threshold, supplier, customer_preference, business_fact, inventory, interest, pricing_rule.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "add_reminder",
        "description": "Add a birthday, anniversary, festival, or custom reminder for the user. Use this when the user mentions someone's birthday, an anniversary, or any date they want to remember. JewelClaw will send them a greeting at 12:01 AM and a reminder at 8:00 AM on that date every year.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the person or event (e.g. 'Mom', 'Priya Sharma', 'Wedding Anniversary').",
                },
                "occasion": {
                    "type": "string",
                    "enum": ["birthday", "anniversary", "festival", "custom"],
                    "description": "Type of occasion.",
                },
                "month": {
                    "type": "integer",
                    "description": "Month number (1-12).",
                },
                "day": {
                    "type": "integer",
                    "description": "Day of month (1-31).",
                },
                "relationship": {
                    "type": "string",
                    "description": "Relationship to the user (e.g. 'Mother', 'Customer', 'Friend', 'Wife').",
                },
            },
            "required": ["name", "occasion", "month", "day"],
        },
    },
    {
        "name": "list_reminders",
        "description": "List all the user's saved reminders (birthdays, anniversaries, festivals). Use this when the user asks about their reminders or upcoming dates.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "update_inventory",
        "description": "Store or update the user's metal inventory/holdings. Use when user says things like 'I have 500g 22K gold', 'my stock is 2kg silver', 'I hold 100g gold'. JewelClaw tracks their portfolio value and sends weekly reports.",
        "input_schema": {
            "type": "object",
            "properties": {
                "metal": {
                    "type": "string",
                    "enum": ["gold", "silver", "platinum"],
                    "description": "Type of metal.",
                },
                "weight_grams": {
                    "type": "number",
                    "description": "Weight in grams. Convert kg to grams (1kg = 1000g).",
                },
                "karat": {
                    "type": "string",
                    "enum": ["24k", "22k", "18k", "14k", "pure"],
                    "description": "Karat for gold, or 'pure' for silver/platinum.",
                },
            },
            "required": ["metal", "weight_grams"],
        },
    },
    {
        "name": "get_portfolio",
        "description": "Get the user's current inventory portfolio value with daily P&L. Shows each holding's current value and change. Use when user asks about 'portfolio', 'holdings', 'inventory value', 'my stock'.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


class AgentService:
    """AI agent that understands natural language and uses tools."""

    def __init__(self):
        self._client = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        return self._client

    def classify_message(self, message: str) -> Tuple[Optional[str], float]:
        """
        Classify a message into a command or 'ai_conversation'.
        Returns (classification, confidence).

        Priority:
        1. Exact command match -> confidence 1.0
        2. Regex fuzzy match -> confidence 0.9
        3. Fallback -> 'ai_conversation' with confidence 0.5
        """
        normalized = message.lower().strip()

        # 1. Exact match
        if normalized in EXACT_COMMANDS:
            return normalized, 1.0

        # Check prefix matches (like "like 5", "search bridal necklace")
        for cmd in EXACT_COMMANDS:
            if normalized.startswith(cmd + " "):
                return cmd, 1.0

        # 2. Fuzzy regex patterns
        for pattern, command in FUZZY_PATTERNS:
            if re.search(pattern, normalized, re.IGNORECASE):
                return command, 0.9

        # 3. Single word greetings
        if normalized in {"hi", "hello", "hey", "hii", "hiii", "namaste"}:
            return "greeting", 1.0

        # 4. Everything else -> AI conversation
        return "ai_conversation", 0.5

    async def handle_message(
        self,
        db: AsyncSession,
        user: User,
        message: str,
    ) -> str:
        """
        Process a natural language message through Claude with tools.
        Builds context, calls Claude, executes tool calls, returns response.
        """
        try:
            # Increment AI interaction count
            user.total_ai_interactions = (user.total_ai_interactions or 0) + 1

            # Build system prompt with user context
            system_prompt = await self._build_system_prompt(db, user)

            # Get recent conversation history
            chat_history = await self._get_chat_history(db, user.id, limit=10)

            # Add current message
            messages = chat_history + [{"role": "user", "content": message}]

            # Call Claude with tools
            response = await self._call_claude_with_tools(db, user, system_prompt, messages)

            return response

        except anthropic.APIError as e:
            logger.error(f"Claude API error: {e}")
            return "I'm having trouble thinking right now. Try again in a moment, or type 'gold' for quick rates."
        except Exception as e:
            logger.error(f"Agent error: {e}", exc_info=True)
            return "Something went wrong. Type 'help' for available commands."

    async def _build_system_prompt(self, db: AsyncSession, user: User) -> str:
        """Build a personalized system prompt with user context."""
        name = user.name or "Friend"
        city = user.preferred_city or "Mumbai"

        # Get business memory
        memories = await business_memory_service.get_user_memory(db, user.id)
        memory_text = business_memory_service.format_memory_for_prompt(memories)

        # Get current gold rate for context
        rate_text = await self._get_current_rate_text(db, city)

        # Check onboarding status
        onboarding_status = "completed" if user.onboarding_completed else "not yet completed - learn about their business"

        system = f"""You are JewelClaw, a personal AI assistant for the Indian jewelry trade. You chat on WhatsApp like a smart, trusted industry friend - not a bot.

USER:
- Name: {name} | City: {city} | Type: {user.business_type or 'Unknown'}
- Interactions: {user.total_ai_interactions or 0}

THEIR BUSINESS:
{memory_text}

LIVE MARKET:
{rate_text}

HOW TO TALK:
- You're texting a friend, not writing an email. Keep it SHORT (2-3 lines per thought).
- Use *bold* for key numbers. Use ₹ and Indian formatting (₹7,00,000 for lakhs).
- Mix in Hinglish naturally when it fits (sona, chandi, karigari, making charge).
- Give opinions, not just data. "Gold at ₹6,850 - below your buy price, I'd stock up today!"
- Never list features or commands unless asked. Just help naturally.

WHAT TO DO:
- Gold/rate questions → use get_gold_rates tool for live data.
- User shares business info → ALWAYS save with store_business_fact (charges, thresholds, suppliers, preferences).
- Jewelry quote → use calculate_jewelry_quote with their stored charges. Supports CZ, diamonds, gemstones, finishing.
- User mentions a birthday/anniversary/date → save with add_reminder.
- User asks about reminders → use list_reminders.
- User mentions their stock ("I have 500g gold") → save with update_inventory.
- Portfolio/holdings question → use get_portfolio.
- "Should I buy?" → check their buy_threshold vs current rate, give clear advice.
- Price alerts → use set_price_alert. Alerts run every 15 minutes automatically.

PRICING KNOWLEDGE - You understand jewelry pricing deeply:
- Pricing models: percentage (% of gold cost), per-gram (₹/gm labor), per-piece (CFP = cost for piece), all-inclusive (one rate/gm including gold+labor)
- When user talks about pricing → use save_pricing_config to store their rates permanently.
- CZ pricing: per stone by setting type (pave ₹10, prong ₹12, bezel ₹18, channel ₹18/stone)
- Diamond pricing: by sieve size (000-16+) and quality (DEF/VVS, GH/VS, IJ/SI). Lab-grown is 75-85% cheaper.
- Setting charges: pave, prong, bezel, channel, invisible, micro-pave (per stone)
- Finishing: rhodium, black rhodium, two-tone, sandblast, enamel, antique (per piece)
- Gold loss/wastage: varies 2-10% by jewelry complexity
- Export pricing in USD: no GST, gold rate converted at live USD/INR
- Cost vs selling price: user can set profit margin % to see both
- If user uploads a pricing chart image, it will be analyzed automatically and you'll get the extracted data to confirm and save."""

        return system

    async def _get_current_rate_text(self, db: AsyncSession, city: str) -> str:
        """Get a concise text summary of current rates for the system prompt."""
        result = await db.execute(
            select(MetalRate)
            .where(MetalRate.city == city)
            .order_by(desc(MetalRate.recorded_at))
            .limit(1)
        )
        rate = result.scalar_one_or_none()
        if not rate:
            return "Gold rates not yet fetched for today."

        lines = [
            f"Gold 24K: ₹{rate.gold_24k:,.0f}/gm ({city})",
            f"Gold 22K: ₹{rate.gold_22k:,.0f}/gm",
        ]
        if rate.gold_18k:
            lines.append(f"Gold 18K: ₹{rate.gold_18k:,.0f}/gm")
        if rate.silver:
            lines.append(f"Silver: ₹{rate.silver:,.0f}/gm")
        if rate.rate_date:
            lines.append(f"Date: {rate.rate_date}")

        return "\n".join(lines)

    async def _get_chat_history(
        self, db: AsyncSession, user_id: int, limit: int = 10
    ) -> List[Dict[str, str]]:
        """Get recent chat history formatted for Claude messages API."""
        result = await db.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(desc(Conversation.created_at))
            .limit(limit)
        )
        conversations = list(reversed(result.scalars().all()))

        messages = []
        for conv in conversations:
            role = "user" if conv.role == "user" else "assistant"
            messages.append({"role": role, "content": conv.content})

        # Ensure messages alternate and start with user
        # Claude API requires alternating user/assistant messages
        cleaned = []
        last_role = None
        for msg in messages:
            if msg["role"] == last_role:
                # Merge consecutive same-role messages
                if cleaned:
                    cleaned[-1]["content"] += "\n" + msg["content"]
                continue
            cleaned.append(msg)
            last_role = msg["role"]

        # Must start with user message
        if cleaned and cleaned[0]["role"] == "assistant":
            cleaned = cleaned[1:]

        return cleaned

    async def _call_claude_with_tools(
        self,
        db: AsyncSession,
        user: User,
        system: str,
        messages: List[Dict],
        depth: int = 0,
    ) -> str:
        """Call Claude, execute any tool calls, and return final text response."""
        if depth > 5:
            return "I got a bit confused. Can you rephrase that?"

        response = self.client.messages.create(
            model=settings.agent_model,
            max_tokens=settings.agent_max_tokens,
            system=system,
            messages=messages,
            tools=TOOLS,
        )

        # Process response content blocks
        text_parts = []
        tool_results = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                # Execute the tool
                tool_result = await self._execute_tool(
                    db, user, block.name, block.input
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(tool_result) if isinstance(tool_result, dict) else str(tool_result),
                })

        # If there were tool calls, send results back to Claude for final response
        if tool_results and response.stop_reason == "tool_use":
            # Build the assistant message with all content blocks
            assistant_content = []
            for block in response.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })

            messages = messages + [
                {"role": "assistant", "content": assistant_content},
                {"role": "user", "content": tool_results},
            ]
            return await self._call_claude_with_tools(
                db, user, system, messages, depth + 1
            )

        return "\n".join(text_parts) if text_parts else "I'm not sure how to respond to that. Type 'help' for commands."

    async def _execute_tool(
        self,
        db: AsyncSession,
        user: User,
        tool_name: str,
        tool_input: Dict[str, Any],
    ) -> Any:
        """Execute a tool call and return the result."""
        logger.info(f"Executing tool: {tool_name} with input: {tool_input}")

        try:
            if tool_name == "get_gold_rates":
                return await self._tool_get_gold_rates(db, user, tool_input)
            elif tool_name == "store_business_fact":
                return await self._tool_store_business_fact(db, user, tool_input)
            elif tool_name == "calculate_jewelry_quote":
                return await self._tool_calculate_quote(db, user, tool_input)
            elif tool_name == "save_pricing_config":
                return await self._tool_save_pricing_config(db, user, tool_input)
            elif tool_name == "search_designs":
                return await self._tool_search_designs(db, user, tool_input)
            elif tool_name == "set_price_alert":
                return await self._tool_set_price_alert(db, user, tool_input)
            elif tool_name == "get_business_memory":
                return await self._tool_get_business_memory(db, user, tool_input)
            elif tool_name == "add_reminder":
                return await self._tool_add_reminder(db, user, tool_input)
            elif tool_name == "list_reminders":
                return await self._tool_list_reminders(db, user, tool_input)
            elif tool_name == "update_inventory":
                return await self._tool_update_inventory(db, user, tool_input)
            elif tool_name == "get_portfolio":
                return await self._tool_get_portfolio(db, user, tool_input)
            else:
                return {"error": f"Unknown tool: {tool_name}"}
        except Exception as e:
            logger.error(f"Tool execution error ({tool_name}): {e}", exc_info=True)
            return {"error": str(e)}

    async def _tool_get_gold_rates(
        self, db: AsyncSession, user: User, inputs: Dict
    ) -> Dict:
        """Get current gold rates."""
        city = inputs.get("city", user.preferred_city or "Mumbai")

        result = await db.execute(
            select(MetalRate)
            .where(MetalRate.city == city)
            .order_by(desc(MetalRate.recorded_at))
            .limit(1)
        )
        rate = result.scalar_one_or_none()

        if not rate:
            # Try fetching fresh rates
            from app.services.gold_service import metal_service
            rate = await metal_service.get_current_rates(db, city, force_refresh=True)

        if not rate:
            return {"error": f"Could not fetch rates for {city}"}

        data = {
            "city": city,
            "date": rate.rate_date or "Today",
            "gold_24k": rate.gold_24k,
            "gold_22k": rate.gold_22k,
            "gold_18k": rate.gold_18k,
            "gold_14k": rate.gold_14k,
            "silver": rate.silver,
            "platinum": rate.platinum,
        }

        # Add threshold context if available
        thresholds = await business_memory_service.get_buy_thresholds(db, user.id)
        if thresholds["buy"]:
            diff = rate.gold_24k - thresholds["buy"]
            data["user_buy_threshold"] = thresholds["buy"]
            data["vs_buy_threshold"] = round(diff, 0)
            data["buy_signal"] = "below" if diff < 0 else "above"

        return data

    async def _tool_store_business_fact(
        self, db: AsyncSession, user: User, inputs: Dict
    ) -> Dict:
        """Store a business fact from conversation."""
        memory = await business_memory_service.store_fact(
            db=db,
            user_id=user.id,
            category=inputs["category"],
            key=inputs["key"],
            value=inputs["value"],
            value_numeric=inputs.get("value_numeric"),
            metal_type=inputs.get("metal_type"),
            jewelry_category=inputs.get("jewelry_category"),
        )

        # Also update User model thresholds for quick access
        if inputs["category"] == "buy_threshold" and inputs.get("value_numeric"):
            user.gold_buy_threshold = inputs["value_numeric"]
        elif inputs["category"] == "sell_threshold" and inputs.get("value_numeric"):
            user.gold_sell_threshold = inputs["value_numeric"]

        # Update onboarding if we're learning business info
        if inputs["category"] in ("business_fact", "making_charges") and not user.onboarding_completed:
            # Check if we have enough info
            all_memories = await business_memory_service.get_user_memory(db, user.id)
            if len(all_memories) >= 3:
                user.onboarding_completed = True

        return {
            "stored": True,
            "key": inputs["key"],
            "value": inputs["value"],
            "category": inputs["category"],
        }

    async def _tool_calculate_quote(
        self, db: AsyncSession, user: User, inputs: Dict
    ) -> Dict:
        """Calculate a jewelry quote using the pricing engine."""
        quote = await pricing_engine.generate_quote(
            db=db,
            user_id=user.id,
            weight_grams=inputs["weight_grams"],
            karat=inputs["karat"],
            jewelry_type=inputs.get("jewelry_type", "general"),
            making_charge_pct=inputs.get("making_charge_percent"),
            quantity=inputs.get("quantity", 1),
            city=user.preferred_city,
            cz_count=inputs.get("cz_count", 0),
            cz_setting=inputs.get("cz_setting", "pave"),
            diamonds=inputs.get("diamonds"),
            gemstones=inputs.get("gemstones"),
            finishing=inputs.get("finishing"),
            labor_per_gram=inputs.get("labor_per_gram"),
            cfp_rate=inputs.get("cfp_rate"),
            currency=inputs.get("currency"),
        )

        if "error" in quote:
            return quote

        # Return full breakdown for Claude to format naturally
        quote["formatted_bill"] = pricing_engine.format_quote_message(quote)
        return quote

    async def _tool_save_pricing_config(
        self, db: AsyncSession, user: User, inputs: Dict
    ) -> Dict:
        """Save pricing configuration from conversation."""
        pricing_data = inputs.get("pricing_data", {})
        saved = await pricing_engine.apply_parsed_pricing(db, user.id, pricing_data)
        return {
            "saved": True,
            "items_saved": len(saved),
            "details": saved,
            "message": f"Saved {len(saved)} pricing settings. These will be used in all future quotes.",
        }

    async def _tool_search_designs(
        self, db: AsyncSession, user: User, inputs: Dict
    ) -> Dict:
        """Search designs in the database."""
        from app.models import Design

        query = select(Design)

        category = inputs.get("category")
        keyword = inputs.get("keyword")
        limit = min(inputs.get("limit", 5), 10)

        if category:
            query = query.where(Design.category == category.lower())
        if keyword:
            query = query.where(Design.title.ilike(f"%{keyword}%"))

        query = query.order_by(desc(Design.trending_score)).limit(limit)

        result = await db.execute(query)
        designs = result.scalars().all()

        return {
            "count": len(designs),
            "designs": [
                {
                    "id": d.id,
                    "title": d.title or "Untitled",
                    "category": d.category,
                    "price": d.price_range_min,
                    "source": d.source,
                    "has_image": bool(d.image_url),
                }
                for d in designs
            ],
        }

    async def _tool_set_price_alert(
        self, db: AsyncSession, user: User, inputs: Dict
    ) -> Dict:
        """Set a gold price alert."""
        target = inputs["target_price"]
        direction = inputs["direction"]

        # Store as business memory
        if direction == "below":
            await business_memory_service.store_fact(
                db=db,
                user_id=user.id,
                category="buy_threshold",
                key="gold_buy_threshold",
                value=f"₹{target:,.0f}/gm",
                value_numeric=target,
                metal_type="gold",
            )
            user.gold_buy_threshold = target
        else:
            await business_memory_service.store_fact(
                db=db,
                user_id=user.id,
                category="sell_threshold",
                key="gold_sell_threshold",
                value=f"₹{target:,.0f}/gm",
                value_numeric=target,
                metal_type="gold",
            )
            user.gold_sell_threshold = target

        return {
            "alert_set": True,
            "target_price": target,
            "direction": direction,
            "message": f"Alert set: notify when gold goes {direction} ₹{target:,.0f}/gm",
        }

    async def _tool_get_business_memory(
        self, db: AsyncSession, user: User, inputs: Dict
    ) -> Dict:
        """Retrieve stored business facts."""
        category = inputs.get("category")
        memories = await business_memory_service.get_user_memory(
            db, user.id, category=category
        )

        return {
            "count": len(memories),
            "facts": [
                {
                    "category": m.category,
                    "key": m.key,
                    "value": m.value,
                    "value_numeric": m.value_numeric,
                    "metal_type": m.metal_type,
                    "jewelry_category": m.jewelry_category,
                }
                for m in memories
            ],
        }

    async def _tool_add_reminder(
        self, db: AsyncSession, user: User, inputs: Dict
    ) -> Dict:
        """Add a reminder via AI conversation."""
        r = await reminder_service.add_reminder(
            db=db,
            user_id=user.id,
            name=inputs["name"],
            occasion=inputs["occasion"],
            month=inputs["month"],
            day=inputs["day"],
            relationship=inputs.get("relationship"),
        )
        month_name = reminder_service._month_name(inputs["month"])
        return {
            "saved": True,
            "id": r.id,
            "name": inputs["name"],
            "occasion": inputs["occasion"],
            "date": f"{inputs['day']} {month_name}",
            "message": f"Reminder set for {inputs['name']} ({inputs['occasion']}) on {inputs['day']} {month_name}. You'll get a greeting at 12:01 AM and a reminder at 8:00 AM.",
        }

    async def _tool_list_reminders(
        self, db: AsyncSession, user: User, inputs: Dict
    ) -> Dict:
        """List user reminders via AI conversation."""
        reminders = await reminder_service.list_reminders(db, user.id)

        personal = [r for r in reminders if r["occasion"] != "festival"]
        festival_count = len([r for r in reminders if r["occasion"] == "festival"])

        return {
            "total": len(reminders),
            "personal_reminders": personal[:20],
            "festival_count": festival_count,
        }

    async def _tool_update_inventory(
        self, db: AsyncSession, user: User, inputs: Dict
    ) -> Dict:
        """Store/update inventory holding."""
        metal = inputs["metal"]
        weight = inputs["weight_grams"]
        karat = inputs.get("karat", "24k" if metal == "gold" else "pure")

        result = await background_agent.store_inventory(
            db, user.id, metal, weight, karat
        )

        # Get updated portfolio value
        portfolio = await background_agent.get_portfolio_summary(db, user.id)
        if "error" not in portfolio:
            result["total_portfolio_value"] = portfolio["total_value"]
            result["message"] = (
                f"Stored {weight}g {karat} {metal}. "
                f"Total portfolio: ₹{portfolio['total_value']:,.0f}. "
                f"You'll get weekly reports every Sunday."
            )
        else:
            result["message"] = f"Stored {weight}g {karat} {metal}. Portfolio tracking enabled."

        return result

    async def _tool_get_portfolio(
        self, db: AsyncSession, user: User, inputs: Dict
    ) -> Dict:
        """Get portfolio summary."""
        portfolio = await background_agent.get_portfolio_summary(db, user.id)
        if "error" not in portfolio:
            portfolio["formatted"] = background_agent.format_portfolio_message(portfolio)
        return portfolio


# Singleton
agent_service = AgentService()
