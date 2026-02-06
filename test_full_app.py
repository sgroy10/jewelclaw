"""Test the full JewelClaw application with database integration."""

import asyncio
import sys
sys.path.insert(0, '.')

from dotenv import load_dotenv
load_dotenv()

from datetime import datetime
from app.database import get_db_session, init_db
from app.services.gold_service import metal_service


async def main():
    print("=" * 65)
    print("JewelClaw - Full Application Test")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)

    # Initialize database
    print("\n1. Initializing database...")
    await init_db()
    print("[OK] Database initialized")

    # Test fetching and saving rates
    print("\n2. Fetching and saving rates to database...")
    async with get_db_session() as db:
        rate = await metal_service.get_current_rates(db, "Mumbai", force_refresh=True)
        if rate:
            print(f"[OK] Rates saved to database:")
            print(f"  ID: {rate.id}")
            print(f"  City: {rate.city}")
            print(f"  Gold 24K: Rs.{rate.gold_24k:,.0f}/gram")
            print(f"  Gold 22K: Rs.{rate.gold_22k:,.0f}/gram")
            print(f"  Silver: Rs.{rate.silver:,.0f}/gram" if rate.silver else "  Silver: N/A")
            print(f"  Platinum: Rs.{rate.platinum:,.0f}/gram" if rate.platinum else "  Platinum: N/A")
        else:
            print("[FAIL] Could not fetch rates")
            return

    # Test market analysis
    print("\n3. Generating market analysis...")
    async with get_db_session() as db:
        analysis = await metal_service.get_market_analysis(db, "Mumbai")
        print(f"[OK] Market analysis generated:")
        print(f"  Direction: {analysis.direction}")
        print(f"  Volatility: {analysis.volatility}")
        print(f"  Recommendation: {analysis.recommendation}")
        print(f"  Daily Change: Rs.{analysis.daily_change:+,.0f} ({analysis.daily_change_percent:+.1f}%)")

    # Test message formatting
    print("\n4. Formatting morning brief...")
    async with get_db_session() as db:
        rate = await metal_service.get_current_rates(db, "Mumbai")
        analysis = await metal_service.get_market_analysis(db, "Mumbai")

        # Get rates data for AI analysis
        rates_data = await metal_service.fetch_all_rates("mumbai")
        expert = await metal_service.generate_ai_expert_analysis(rates_data, analysis)

        brief = metal_service.format_morning_brief(rate, analysis, expert)
        print("[OK] Morning brief formatted")

        # Save to file
        with open("test_brief_output.txt", "w", encoding="utf-8") as f:
            f.write(brief)
        print("[OK] Brief saved to test_brief_output.txt")

    print("\n" + "=" * 65)
    print("All tests passed!")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())
