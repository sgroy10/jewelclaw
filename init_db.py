"""Initialize the database and create all tables."""

import asyncio
import sys
sys.path.insert(0, '.')

from dotenv import load_dotenv
load_dotenv()

from app.database import init_db, engine
from app.models import Base


async def main():
    print("Initializing database...")
    print(f"Database URL: {engine.url}")

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    print("[OK] Database tables created successfully!")
    print("\nTables created:")
    for table in Base.metadata.tables:
        print(f"  - {table}")


if __name__ == "__main__":
    asyncio.run(main())
