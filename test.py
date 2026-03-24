import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def main():
    engine = create_async_engine("sqlite+aiosqlite:///backend/test.db")
    async with engine.begin() as conn:
        res = await conn.execute(text("SELECT agent_id FROM interviews WHERE interview_id LIKE '1838fc18%'"))
        row = res.fetchone()
        if row:
            agent_id = row[0]
            print(f"Agent ID: {agent_id}")
            res2 = await conn.execute(text(f"SELECT COUNT(*) FROM agents WHERE agent_id = '{agent_id}'"))
            print(f"Agent count: {res2.fetchone()[0]}")
        else:
            print("Interview not found")

asyncio.run(main())
