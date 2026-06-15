import asyncio
from agentcore.services.deps import get_db_service
from agentcore.services.manager import service_manager

async def main():
    db = get_db_service()
    print("Running migrations...")
    await db.run_migrations()
    print("Migrations complete!")

if __name__ == "__main__":
    asyncio.run(main())
