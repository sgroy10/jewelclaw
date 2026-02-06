"""Test the enhanced JewelClaw features."""

import asyncio
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Import after loading env
import sys
sys.path.insert(0, '.')

from app.services.gold_service import metal_service, MetalRateData, MarketAnalysis


async def test_all_rates():
    """Test fetching all metal rates."""
    print("=" * 70)
    print("Testing Enhanced Metal Rate Fetching")
    print("=" * 70)

    rates = await metal_service.fetch_all_rates("mumbai")

    if rates:
        print(f"\nRate Date: {rates.rate_date}")
        print(f"Fetched at: {rates.recorded_at.strftime('%Y-%m-%d %H:%M:%S')}")

        print("\n--- GOLD RATES (all karats) ---")
        print(f"  24K: Rs.{rates.gold_24k:,.0f}/gram")
        print(f"  22K: Rs.{rates.gold_22k:,.0f}/gram")
        print(f"  18K: Rs.{rates.gold_18k:,.0f}/gram")
        print(f"  14K: Rs.{rates.gold_14k:,.0f}/gram")
        print(f"  10K: Rs.{rates.gold_10k:,.0f}/gram")
        print(f"   9K: Rs.{rates.gold_9k:,.0f}/gram")

        print("\n--- OTHER METALS ---")
        print(f"  Silver: Rs.{rates.silver:,.0f}/gram" if rates.silver else "  Silver: N/A")
        print(f"  Platinum: Rs.{rates.platinum:,.0f}/gram" if rates.platinum else "  Platinum: N/A")

        print("\n--- INTERNATIONAL PRICES ---")
        if rates.gold_usd_oz:
            print(f"  Gold: ${rates.gold_usd_oz:,.2f}/oz")
        if rates.silver_usd_oz:
            print(f"  Silver: ${rates.silver_usd_oz:,.2f}/oz")
        if rates.usd_inr:
            print(f"  USD/INR: {rates.usd_inr:.2f}")

        return rates
    else:
        print("[FAIL] Could not fetch rates")
        return None


def test_message_formatting(rates):
    """Test WhatsApp message formatting."""
    print("\n" + "=" * 70)
    print("Testing WhatsApp Message Formatting")
    print("=" * 70)

    # Create a mock MetalRate-like object for formatting
    class MockRate:
        def __init__(self, data):
            self.city = data.city
            self.rate_date = data.rate_date
            self.gold_24k = data.gold_24k
            self.gold_22k = data.gold_22k
            self.gold_18k = data.gold_18k
            self.gold_14k = data.gold_14k
            self.gold_10k = data.gold_10k
            self.gold_9k = data.gold_9k
            self.silver = data.silver
            self.platinum = data.platinum
            self.gold_usd_oz = data.gold_usd_oz
            self.silver_usd_oz = data.silver_usd_oz
            self.usd_inr = data.usd_inr

    mock_rate = MockRate(rates)

    # Create mock analysis
    analysis = MarketAnalysis(
        direction="falling",
        direction_symbol="v",
        consecutive_days=2,
        volatility="medium",
        recommendation="buy",
        recommendation_text="BUY - Prices dropping, good entry point",
        daily_change=-502,
        daily_change_percent=-3.15,
        weekly_change=-1200,
        weekly_change_percent=-7.2,
        monthly_change=-800,
        monthly_change_percent=-4.9,
        expert_summary="Gold fell for 2 consecutive days. USD/INR at 90.44. Weekly decline of 7.2%."
    )

    print("\n--- Gold Rate Message ---")
    msg = metal_service.format_gold_rate_message(mock_rate, analysis)
    print(msg)

    print("\n--- Silver Rate Message ---")
    msg = metal_service.format_silver_rate_message(mock_rate)
    print(msg)

    print("\n--- Platinum Rate Message ---")
    msg = metal_service.format_platinum_rate_message(mock_rate)
    print(msg)

    print("\n--- Morning Brief ---")
    msg = metal_service.format_morning_brief(mock_rate, analysis)
    print(msg)


async def test_send_message():
    """Test sending WhatsApp message."""
    print("\n" + "=" * 70)
    print("Testing WhatsApp Message Send")
    print("=" * 70)

    test_phone = os.getenv("TEST_PHONE_NUMBER")
    if not test_phone:
        print("[SKIP] TEST_PHONE_NUMBER not set in .env")
        return

    from app.services.whatsapp_service import whatsapp_service

    # Fetch fresh rates
    rates = await metal_service.fetch_all_rates("mumbai")
    if not rates:
        print("[FAIL] Could not fetch rates")
        return

    # Create mock rate object
    class MockRate:
        def __init__(self, data):
            self.city = data.city
            self.rate_date = data.rate_date
            self.gold_24k = data.gold_24k
            self.gold_22k = data.gold_22k
            self.gold_18k = data.gold_18k
            self.gold_14k = data.gold_14k
            self.gold_10k = data.gold_10k
            self.gold_9k = data.gold_9k
            self.silver = data.silver
            self.platinum = data.platinum
            self.gold_usd_oz = data.gold_usd_oz
            self.silver_usd_oz = data.silver_usd_oz
            self.usd_inr = data.usd_inr

    mock_rate = MockRate(rates)

    analysis = MarketAnalysis(
        direction="falling",
        direction_symbol="v",
        consecutive_days=1,
        volatility="medium",
        recommendation="hold",
        recommendation_text="HOLD - Market stable, buy as per needs",
        daily_change=-502,
        daily_change_percent=-3.15,
        weekly_change=0,
        weekly_change_percent=0,
        monthly_change=0,
        monthly_change_percent=0,
        expert_summary="Gold fell today. Market adjusting."
    )

    message = metal_service.format_gold_rate_message(mock_rate, analysis)

    print(f"Sending to: {test_phone}")
    result = await whatsapp_service.send_message(test_phone, message)
    if result:
        print("[OK] Message sent successfully!")
    else:
        print("[FAIL] Failed to send message")


async def main():
    print("JewelClaw Enhanced Features Test")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Test rate fetching
    rates = await test_all_rates()

    # Test message formatting
    if rates:
        test_message_formatting(rates)

    # Test sending (optional)
    send_test = input("\nSend test message to WhatsApp? (y/n): ").strip().lower()
    if send_test == 'y':
        await test_send_message()

    print("\n" + "=" * 70)
    print("Test Complete!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
