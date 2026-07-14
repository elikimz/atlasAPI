import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

async def update_db():
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        print("Updating users table...")
        # Add new columns
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS username VARCHAR"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_number VARCHAR"))
        
        # Make email nullable
        await conn.execute(text("ALTER TABLE users ALTER COLUMN email DROP NOT NULL"))
        
        # Set temporary username for existing users to avoid null constraint
        await conn.execute(text("UPDATE users SET username = email WHERE username IS NULL"))
        
        # Now set username to NOT NULL
        await conn.execute(text("ALTER TABLE users ALTER COLUMN username SET NOT NULL"))
        
        # Add unique constraint to username
        try:
            await conn.execute(text("ALTER TABLE users ADD CONSTRAINT uq_users_username UNIQUE (username)"))
        except Exception as e:
            print(f"Constraint might already exist: {e}")

        print("Done.")

if __name__ == "__main__":
    asyncio.run(update_db())
