"""Send the FINAL comprehensive JewelClaw morning brief via WhatsApp."""

import asyncio
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

import sys
sys.path.insert(0, '.')

from twilio.rest import Client
from app.services.gold_service import metal_service, MarketAnalysis, MetalRateData


async def main():
    print("=" * 65)
    print("JewelClaw - FINAL Comprehensive Brief")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)

    # Fetch all current rates
    print("\nFetching all metal rates...")
    rates = await metal_service.fetch_all_rates("mumbai")

    if not rates:
        print("[FAIL] Could not fetch rates")
        return

    # Update MCX futures if not available
    if not rates.mcx_gold_futures:
        rates.mcx_gold_futures = round(rates.gold_24k * 10 * 1.005)
        rates.mcx_gold_futures_expiry = "Feb"
    if not rates.mcx_silver_futures:
        rates.mcx_silver_futures = round(rates.silver * 1000 * 1.005)
        rates.mcx_silver_futures_expiry = "Mar"

    print(f"[OK] Rates fetched:")
    print(f"  Gold 24K: Rs.{rates.gold_24k:,.0f}/gram")
    print(f"  Silver: Rs.{rates.silver:,.0f}/gram")
    print(f"  Platinum: Rs.{rates.platinum:,.0f}/gram")
    print(f"  MCX Gold: Rs.{rates.mcx_gold_futures:,.0f}/10gm")
    print(f"  MCX Silver: Rs.{rates.mcx_silver_futures:,.0f}/kg")

    # Create realistic sample analysis (simulating 2 days of rising prices)
    analysis = MarketAnalysis(
        direction="rising",
        direction_symbol="↑",
        consecutive_days=2,
        volatility="medium",
        recommendation="hold",
        recommendation_text="HOLD - Market stable, buy as per needs",
        daily_change=285,  # Rs.285 rise today
        daily_change_percent=1.88,
        weekly_change=890,  # Rs.890 rise this week
        weekly_change_percent=6.1,
        monthly_change=1450,  # Rs.1450 rise this month
        monthly_change_percent=10.4,
        expert_summary="Gold rallied for 2 consecutive days."
    )

    # Generate expert analysis (fallback will be used since Claude credits are low)
    print("\nGenerating expert analysis...")
    expert = await metal_service.generate_ai_expert_analysis(rates, analysis)
    print("[OK] Expert analysis generated")

    # Format the complete morning brief
    brief = metal_service.format_morning_brief(rates, analysis, expert)

    # Save to file
    with open("final_brief.txt", "w", encoding="utf-8") as f:
        f.write(brief)
    print("\n[OK] Brief saved to final_brief.txt")

    # Print the message
    print("\n" + "-" * 65)
    print("MESSAGE CONTENT:")
    print("-" * 65)
    # Can't print to console due to encoding, but saved to file

    # Send via WhatsApp
    print("\n" + "-" * 65)
    print("Sending WhatsApp message...")
    print("-" * 65)

    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_WHATSAPP_NUMBER")
    to_number = os.getenv("TEST_PHONE_NUMBER")

    try:
        client = Client(sid, token)
        msg = client.messages.create(
            body=brief,
            from_=from_number,
            to=f"whatsapp:{to_number}"
        )
        print(f"\n[OK] Message sent successfully!")
        print(f"[OK] SID: {msg.sid}")
        print(f"[OK] Check your WhatsApp at {to_number}")
    except Exception as e:
        print(f"[FAIL] Error sending message: {e}")

    print("\n" + "=" * 65)
    print("FINAL TEST COMPLETE!")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())
