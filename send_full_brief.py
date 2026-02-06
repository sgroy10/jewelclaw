"""Send a complete JewelClaw morning brief via WhatsApp."""

import asyncio
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

import sys
sys.path.insert(0, '.')

from twilio.rest import Client
from app.services.gold_service import metal_service, MarketAnalysis


async def main():
    print("=" * 65)
    print("JewelClaw - Complete Morning Brief Test")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)

    # Fetch all rates
    print("\nFetching all metal rates...")
    rates = await metal_service.fetch_all_rates("mumbai")

    if not rates:
        print("[FAIL] Could not fetch rates")
        return

    print(f"[OK] Rates fetched successfully")
    print(f"  Gold 24K: Rs.{rates.gold_24k:,.0f}/gram")
    print(f"  Silver: Rs.{rates.silver:,.0f}/gram")
    print(f"  Platinum: Rs.{rates.platinum:,.0f}/gram")
    print(f"  MCX Gold: Rs.{rates.mcx_gold_futures:,.0f}/10gm")
    print(f"  MCX Silver: Rs.{rates.mcx_silver_futures:,.0f}/kg")

    # Create sample analysis (simulating historical data)
    # In production, this would come from database historical rates
    analysis = MarketAnalysis(
        direction="rising",
        direction_symbol="↑",
        consecutive_days=2,
        volatility="medium",
        recommendation="hold",
        recommendation_text="HOLD - Market stable, buy as per needs",
        daily_change=502,
        daily_change_percent=3.36,
        weekly_change=1200,
        weekly_change_percent=8.4,
        monthly_change=2100,
        monthly_change_percent=15.8,
        expert_summary="Gold rallying on safe-haven demand."
    )

    # Generate Claude AI expert analysis
    print("\nGenerating expert analysis...")
    expert_analysis = await metal_service.generate_ai_expert_analysis(rates, analysis)
    print("[OK] Expert analysis generated")

    # Format the complete morning brief
    print("\nFormatting morning brief...")
    brief = metal_service.format_morning_brief(rates, analysis, expert_analysis)

    # Save to file for review
    with open("morning_brief.txt", "w", encoding="utf-8") as f:
        f.write(brief)
    print("[OK] Brief saved to morning_brief.txt")

    # Send via WhatsApp
    print("\nSending WhatsApp message...")

    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_WHATSAPP_NUMBER")
    to_number = os.getenv("TEST_PHONE_NUMBER")

    if not all([sid, token, from_number, to_number]):
        print("[FAIL] Missing Twilio credentials")
        return

    try:
        client = Client(sid, token)
        msg = client.messages.create(
            body=brief,
            from_=from_number,
            to=f"whatsapp:{to_number}"
        )
        print(f"[OK] Message sent! SID: {msg.sid}")
        print(f"[OK] Check your WhatsApp at {to_number}")
    except Exception as e:
        print(f"[FAIL] Error sending message: {e}")

    print("\n" + "=" * 65)
    print("Test Complete!")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())
