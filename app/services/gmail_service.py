"""
Gmail Email Intelligence Service.

Connects to Gmail via OAuth2, reads emails, categorizes them with AI,
and provides summaries + suggested replies via WhatsApp.
"""

import logging
import base64
import json
import re
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc

from app.config import settings
from app.models import EmailSummary, EmailConnection

logger = logging.getLogger(__name__)

# Gmail API endpoints
GMAIL_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GMAIL_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"

# OAuth scopes - read-only for safety
GMAIL_SCOPES = "https://www.googleapis.com/auth/gmail.readonly"

# Email categories for jewelry business
EMAIL_CATEGORIES = [
    "customer_inquiry",   # Customer asking about products/prices
    "supplier_quote",     # Supplier sending price quotes
    "gold_alert",         # Gold/diamond price notifications
    "invoice",            # Bills, invoices, payment requests
    "order_update",       # Order status, shipping, delivery
    "newsletter",         # Marketing emails, newsletters
    "spam",               # Junk/promotional
    "other",              # Everything else
]


@dataclass
class EmailData:
    """Parsed email data from Gmail API."""
    message_id: str
    sender: str
    subject: str
    snippet: str
    body_text: str
    received_at: datetime
    labels: List[str]


class GmailService:
    """Service for Gmail integration with AI-powered email intelligence."""

    def __init__(self):
        self.client_id = settings.google_client_id
        self.client_secret = settings.google_client_secret
        self.redirect_uri = settings.google_redirect_uri
        self.configured = bool(self.client_id and self.client_secret)

        # Claude client for AI categorization
        self._claude_client = None

    @property
    def claude_client(self):
        if self._claude_client is None and settings.anthropic_api_key:
            import anthropic
            self._claude_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        return self._claude_client

    # =========================================================================
    # OAUTH FLOW
    # =========================================================================

    def get_auth_url(self, user_id: int) -> Optional[str]:
        """Generate Gmail OAuth authorization URL."""
        if not self.configured:
            return None

        state = base64.urlsafe_b64encode(
            json.dumps({"user_id": user_id}).encode()
        ).decode()

        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": GMAIL_SCOPES,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }

        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{GMAIL_AUTH_URL}?{query}"

    async def exchange_code_for_tokens(self, code: str) -> Optional[Dict]:
        """Exchange OAuth authorization code for access + refresh tokens."""
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                response = await client.post(
                    GMAIL_TOKEN_URL,
                    data={
                        "code": code,
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "redirect_uri": self.redirect_uri,
                        "grant_type": "authorization_code",
                    },
                )
                if response.status_code == 200:
                    return response.json()
                logger.error(f"Token exchange failed: {response.status_code} {response.text}")
                return None
            except Exception as e:
                logger.error(f"Token exchange error: {e}")
                return None

    async def refresh_access_token(self, refresh_token: str) -> Optional[str]:
        """Get new access token using refresh token."""
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                response = await client.post(
                    GMAIL_TOKEN_URL,
                    data={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                )
                if response.status_code == 200:
                    return response.json().get("access_token")
                logger.error(f"Token refresh failed: {response.status_code}")
                return None
            except Exception as e:
                logger.error(f"Token refresh error: {e}")
                return None

    async def save_connection(
        self, db: AsyncSession, user_id: int, refresh_token: str, email: str
    ) -> EmailConnection:
        """Save or update Gmail connection for a user."""
        # Check for existing connection
        result = await db.execute(
            select(EmailConnection).where(
                EmailConnection.user_id == user_id,
                EmailConnection.is_active == True,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.gmail_refresh_token = refresh_token
            existing.gmail_email = email
            existing.connected_at = datetime.utcnow()
            return existing
        else:
            conn = EmailConnection(
                user_id=user_id,
                gmail_refresh_token=refresh_token,
                gmail_email=email,
                is_active=True,
            )
            db.add(conn)
            await db.flush()
            return conn

    async def get_connection(self, db: AsyncSession, user_id: int) -> Optional[EmailConnection]:
        """Get active Gmail connection for a user."""
        result = await db.execute(
            select(EmailConnection).where(
                EmailConnection.user_id == user_id,
                EmailConnection.is_active == True,
            )
        )
        return result.scalar_one_or_none()

    # =========================================================================
    # EMAIL READING
    # =========================================================================

    async def _get_access_token(self, db: AsyncSession, user_id: int) -> Optional[str]:
        """Get a valid access token for a user."""
        conn = await self.get_connection(db, user_id)
        if not conn:
            return None
        return await self.refresh_access_token(conn.gmail_refresh_token)

    async def fetch_recent_emails(
        self, db: AsyncSession, user_id: int, hours: int = 24, max_results: int = 50
    ) -> List[EmailData]:
        """Fetch recent emails from Gmail API."""
        access_token = await self._get_access_token(db, user_id)
        if not access_token:
            return []

        # Calculate time filter
        after_timestamp = int((datetime.utcnow() - timedelta(hours=hours)).timestamp())
        query = f"after:{after_timestamp}"

        headers = {"Authorization": f"Bearer {access_token}"}
        emails = []

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                # List messages
                response = await client.get(
                    f"{GMAIL_API_BASE}/users/me/messages",
                    headers=headers,
                    params={"q": query, "maxResults": max_results},
                )

                if response.status_code != 200:
                    logger.error(f"Gmail list failed: {response.status_code}")
                    return []

                messages = response.json().get("messages", [])

                # Fetch each message's details
                for msg_ref in messages:
                    try:
                        msg_response = await client.get(
                            f"{GMAIL_API_BASE}/users/me/messages/{msg_ref['id']}",
                            headers=headers,
                            params={"format": "full"},
                        )

                        if msg_response.status_code == 200:
                            email_data = self._parse_gmail_message(msg_response.json())
                            if email_data:
                                emails.append(email_data)
                    except Exception as e:
                        logger.warning(f"Error fetching message {msg_ref['id']}: {e}")

            except Exception as e:
                logger.error(f"Gmail fetch error: {e}")

        return emails

    def _parse_gmail_message(self, message: dict) -> Optional[EmailData]:
        """Parse a Gmail API message response into EmailData."""
        try:
            headers_list = message.get("payload", {}).get("headers", [])
            headers_dict = {h["name"].lower(): h["value"] for h in headers_list}

            sender = headers_dict.get("from", "Unknown")
            subject = headers_dict.get("subject", "(No subject)")
            snippet = message.get("snippet", "")

            # Parse date
            date_str = headers_dict.get("date", "")
            received_at = self._parse_email_date(date_str)

            # Extract body text
            body_text = self._extract_body_text(message.get("payload", {}))

            # Get labels
            labels = message.get("labelIds", [])

            return EmailData(
                message_id=message["id"],
                sender=sender,
                subject=subject,
                snippet=snippet,
                body_text=body_text[:2000],  # Limit body size
                received_at=received_at or datetime.utcnow(),
                labels=labels,
            )
        except Exception as e:
            logger.warning(f"Error parsing message: {e}")
            return None

    def _extract_body_text(self, payload: dict) -> str:
        """Extract plain text body from Gmail message payload."""
        # Check for plain text part
        if payload.get("mimeType") == "text/plain":
            data = payload.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        # Check multipart
        parts = payload.get("parts", [])
        for part in parts:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            # Recursive for nested multipart
            if part.get("parts"):
                text = self._extract_body_text(part)
                if text:
                    return text

        # Fallback: try HTML
        for part in parts:
            if part.get("mimeType") == "text/html":
                data = part.get("body", {}).get("data", "")
                if data:
                    html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                    return self._strip_html(html)

        return ""

    def _strip_html(self, html: str) -> str:
        """Basic HTML tag stripping."""
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)
        return text.strip()[:2000]

    def _parse_email_date(self, date_str: str) -> Optional[datetime]:
        """Parse email date header into datetime."""
        if not date_str:
            return None
        # Common email date formats
        formats = [
            "%a, %d %b %Y %H:%M:%S %z",
            "%d %b %Y %H:%M:%S %z",
            "%a, %d %b %Y %H:%M:%S %Z",
        ]
        # Remove extra timezone info in parentheses
        date_str = re.sub(r"\s*\(.*\)\s*$", "", date_str.strip())
        for fmt in formats:
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.replace(tzinfo=None)  # Store as naive UTC
            except ValueError:
                continue
        return None

    # =========================================================================
    # AI CATEGORIZATION
    # =========================================================================

    async def categorize_email(self, email: EmailData) -> Dict:
        """Use Claude to categorize an email and extract key info."""
        if not self.claude_client:
            return self._fallback_categorize(email)

        prompt = f"""Analyze this email for a jewelry business owner. Return JSON only.

From: {email.sender}
Subject: {email.subject}
Body preview: {email.body_text[:500]}

Return this exact JSON format:
{{
    "category": "<one of: customer_inquiry, supplier_quote, gold_alert, invoice, order_update, newsletter, spam, other>",
    "urgency": "<high, medium, or low>",
    "summary": "<1-2 sentence summary>",
    "extracted_amount": <number or null if no amount mentioned>,
    "needs_reply": <true or false>,
    "gold_price_mentioned": <true or false>,
    "opportunity_flag": "<brief note if there's a business opportunity, else null>"
}}

Rules:
- customer_inquiry: Customer asking about products, prices, availability
- supplier_quote: Supplier/vendor sending quotes, rates, offers
- gold_alert: Any email mentioning gold/silver/diamond prices
- invoice: Bills, payment requests, receipts
- order_update: Shipping, delivery, order status
- urgency=high: Customer waiting for reply, time-sensitive quote, payment due
- needs_reply=true: If sender expects a response"""

        try:
            response = self.claude_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()

            # Parse JSON from response (handle markdown code blocks)
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()

            return json.loads(text)
        except Exception as e:
            logger.warning(f"AI categorization failed: {e}")
            return self._fallback_categorize(email)

    def _fallback_categorize(self, email: EmailData) -> Dict:
        """Rule-based fallback categorization."""
        subject_lower = (email.subject or "").lower()
        body_lower = (email.body_text or "").lower()
        combined = f"{subject_lower} {body_lower}"

        category = "other"
        urgency = "medium"
        needs_reply = False

        # Gold/price alerts
        if any(word in combined for word in ["gold rate", "gold price", "silver rate", "bullion", "mcx", "spot gold"]):
            category = "gold_alert"
        # Supplier quotes
        elif any(word in combined for word in ["quote", "quotation", "rate list", "price list", "wholesale"]):
            category = "supplier_quote"
            needs_reply = True
        # Customer inquiries
        elif any(word in combined for word in ["enquiry", "inquiry", "interested in", "price of", "available", "looking for"]):
            category = "customer_inquiry"
            urgency = "high"
            needs_reply = True
        # Invoices
        elif any(word in combined for word in ["invoice", "bill", "payment", "receipt", "due"]):
            category = "invoice"
            urgency = "high"
        # Order updates
        elif any(word in combined for word in ["shipped", "delivered", "order", "tracking", "dispatch"]):
            category = "order_update"
        # Newsletters
        elif any(word in combined for word in ["unsubscribe", "newsletter", "weekly update", "digest"]):
            category = "newsletter"

        # Extract amount
        amount = None
        amount_match = re.search(r'[₹$]\s*([\d,]+(?:\.\d{1,2})?)', combined)
        if amount_match:
            amount = float(amount_match.group(1).replace(",", ""))

        return {
            "category": category,
            "urgency": urgency,
            "summary": email.snippet[:150],
            "extracted_amount": amount,
            "needs_reply": needs_reply,
            "gold_price_mentioned": "gold" in combined and any(w in combined for w in ["rate", "price", "₹", "$"]),
            "opportunity_flag": None,
        }

    async def generate_reply_suggestion(self, email_summary: EmailSummary) -> str:
        """Generate AI-powered reply suggestion for an email."""
        if not self.claude_client:
            return "Sorry, AI reply generation is not available right now."

        prompt = f"""You are a reply assistant for a jewelry business owner in India.
Generate a professional, warm reply for this email.

From: {email_summary.sender}
Subject: {email_summary.subject}
Category: {email_summary.category}
Summary: {email_summary.summary_text}
Amount mentioned: {email_summary.extracted_amount or 'None'}

Write a concise, professional reply (3-5 sentences). Be polite and business-appropriate.
If it's a customer inquiry, be helpful and invite them to visit/call.
If it's a supplier quote, acknowledge receipt and mention you'll review.
Keep it natural and warm - this is Indian business communication style."""

        try:
            response = self.claude_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text.strip()
        except Exception as e:
            logger.error(f"Reply generation error: {e}")
            return "Could not generate reply suggestion. Please compose manually."

    # =========================================================================
    # SYNC & STORE
    # =========================================================================

    async def sync_emails(
        self, db: AsyncSession, user_id: int, hours: int = 24
    ) -> Tuple[int, int]:
        """Fetch, categorize, and store new emails. Returns (new_count, total_fetched)."""
        emails = await self.fetch_recent_emails(db, user_id, hours=hours)

        new_count = 0
        for email in emails:
            # Skip if already processed
            existing = await db.execute(
                select(EmailSummary).where(
                    EmailSummary.gmail_message_id == email.message_id
                )
            )
            if existing.scalar_one_or_none():
                continue

            # Categorize with AI
            analysis = await self.categorize_email(email)

            # Store summary (NOT full email content)
            summary = EmailSummary(
                user_id=user_id,
                gmail_message_id=email.message_id,
                sender=email.sender[:200],
                subject=email.subject[:500] if email.subject else None,
                category=analysis.get("category", "other"),
                extracted_amount=analysis.get("extracted_amount"),
                urgency=analysis.get("urgency", "medium"),
                summary_text=analysis.get("summary", email.snippet[:150]),
                needs_reply=analysis.get("needs_reply", False),
                received_at=email.received_at,
            )
            db.add(summary)
            new_count += 1

        if new_count > 0:
            await db.flush()

        return new_count, len(emails)

    # =========================================================================
    # QUERY HELPERS
    # =========================================================================

    async def get_email_summary_stats(
        self, db: AsyncSession, user_id: int, hours: int = 24
    ) -> Dict:
        """Get email summary statistics for a user."""
        since = datetime.utcnow() - timedelta(hours=hours)

        # Count by category
        result = await db.execute(
            select(
                EmailSummary.category,
                func.count(EmailSummary.id).label("count"),
            )
            .where(
                EmailSummary.user_id == user_id,
                EmailSummary.received_at >= since,
            )
            .group_by(EmailSummary.category)
        )
        category_counts = {row.category: row.count for row in result.all()}

        # Count urgent
        urgent_result = await db.execute(
            select(func.count(EmailSummary.id)).where(
                EmailSummary.user_id == user_id,
                EmailSummary.received_at >= since,
                EmailSummary.urgency == "high",
            )
        )
        urgent_count = urgent_result.scalar() or 0

        # Count needing reply
        reply_result = await db.execute(
            select(func.count(EmailSummary.id)).where(
                EmailSummary.user_id == user_id,
                EmailSummary.received_at >= since,
                EmailSummary.needs_reply == True,
            )
        )
        needs_reply_count = reply_result.scalar() or 0

        # Total unread
        total = sum(category_counts.values())

        return {
            "total": total,
            "by_category": category_counts,
            "urgent": urgent_count,
            "needs_reply": needs_reply_count,
        }

    async def get_emails_by_filter(
        self,
        db: AsyncSession,
        user_id: int,
        category: Optional[str] = None,
        urgency: Optional[str] = None,
        hours: int = 24,
        limit: int = 10,
    ) -> List[EmailSummary]:
        """Get filtered email summaries."""
        since = datetime.utcnow() - timedelta(hours=hours)
        query = (
            select(EmailSummary)
            .where(
                EmailSummary.user_id == user_id,
                EmailSummary.received_at >= since,
            )
            .order_by(desc(EmailSummary.received_at))
            .limit(limit)
        )

        if category:
            query = query.where(EmailSummary.category == category)
        if urgency:
            query = query.where(EmailSummary.urgency == urgency)

        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_email_by_id(
        self, db: AsyncSession, email_id: int, user_id: int
    ) -> Optional[EmailSummary]:
        """Get a specific email summary."""
        result = await db.execute(
            select(EmailSummary).where(
                EmailSummary.id == email_id,
                EmailSummary.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    # =========================================================================
    # FORMATTED OUTPUT
    # =========================================================================

    def format_email_summary(self, stats: Dict) -> str:
        """Format email summary for WhatsApp."""
        total = stats["total"]
        if total == 0:
            return """📧 *Email Summary*

No new emails in the last 24 hours.

_Reply 'connect email' to link your Gmail._"""

        lines = [f"📧 *Email Summary* ({total} new)"]
        lines.append("")

        # Category breakdown
        category_icons = {
            "customer_inquiry": "💬",
            "supplier_quote": "📊",
            "gold_alert": "🥇",
            "invoice": "📋",
            "order_update": "📦",
            "newsletter": "📰",
            "spam": "🚫",
            "other": "📩",
        }
        category_labels = {
            "customer_inquiry": "Customer inquiries",
            "supplier_quote": "Supplier quotes",
            "gold_alert": "Gold/price alerts",
            "invoice": "Invoices",
            "order_update": "Order updates",
            "newsletter": "Newsletters",
            "spam": "Spam",
            "other": "Other",
        }

        for cat, count in stats["by_category"].items():
            if count > 0 and cat != "spam":
                icon = category_icons.get(cat, "📩")
                label = category_labels.get(cat, cat)
                lines.append(f"{icon} {count} {label}")

        if stats["urgent"] > 0:
            lines.append(f"\n⚠️ *{stats['urgent']} URGENT* emails need attention")

        if stats["needs_reply"] > 0:
            lines.append(f"💬 {stats['needs_reply']} emails need reply")

        lines.append("")
        lines.append("_Commands:_")
        lines.append("• *email urgent* - Show urgent emails")
        lines.append("• *email customers* - Customer inquiries")
        lines.append("• *email suppliers* - Supplier quotes")
        lines.append("• *reply [id]* - Get reply suggestion")

        return "\n".join(lines)

    def format_email_list(self, emails: List[EmailSummary], title: str) -> str:
        """Format a list of emails for WhatsApp."""
        if not emails:
            return f"*{title}*\n\nNo emails found in this category."

        lines = [f"*{title}*", ""]
        urgency_icons = {"high": "🔴", "medium": "🟡", "low": "🟢"}

        for email in emails:
            icon = urgency_icons.get(email.urgency, "⚪")
            sender_short = email.sender.split("<")[0].strip()[:25]
            subject_short = (email.subject or "No subject")[:40]
            lines.append(f"{icon} *#{email.id}* {sender_short}")
            lines.append(f"   {subject_short}")
            if email.summary_text:
                lines.append(f"   _{email.summary_text[:60]}_")
            if email.extracted_amount:
                lines.append(f"   💰 ₹{email.extracted_amount:,.0f}")
            lines.append("")

        lines.append("_Reply 'reply [id]' for AI-suggested reply_")
        return "\n".join(lines)

    def format_morning_brief_email_section(self, stats: Dict, urgent_emails: List[EmailSummary], reply_needed: List[EmailSummary]) -> str:
        """Format email section for the 9 AM morning brief."""
        total = stats["total"]
        if total == 0:
            return ""  # Don't add section if no emails

        lines = ["", "━━━━━━━━━━━━━━━━━━━━━"]
        lines.append(f"📧 *EMAIL SUMMARY* ({total} new)")

        # Category summary
        cats = stats["by_category"]
        cat_parts = []
        if cats.get("customer_inquiry"):
            cat_parts.append(f"{cats['customer_inquiry']} customer inquiries")
        if cats.get("supplier_quote"):
            cat_parts.append(f"{cats['supplier_quote']} supplier quotes")
        if cats.get("gold_alert"):
            cat_parts.append(f"{cats['gold_alert']} gold alerts")
        if cats.get("invoice"):
            cat_parts.append(f"{cats['invoice']} invoices")

        if cat_parts:
            lines.append(f"  {', '.join(cat_parts)}")

        # Urgent items
        for email in urgent_emails[:3]:
            sender_short = email.sender.split("<")[0].strip()[:20]
            lines.append(f"  ⚠️ *URGENT:* {sender_short} - {(email.subject or '')[:35]}")

        # Needs reply
        for email in reply_needed[:3]:
            sender_short = email.sender.split("<")[0].strip()[:20]
            lines.append(f"  💬 {sender_short} - needs reply")

        lines.append("  _Reply 'email' for details_")

        return "\n".join(lines)


# Singleton instance
gmail_service = GmailService()
