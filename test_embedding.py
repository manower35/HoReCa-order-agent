import asyncio
import os
from google import genai
from dotenv import load_dotenv

load_dotenv()

async def test():
    gemini_key = os.getenv("GEMINI_API_KEY")
    client = genai.Client(api_key=gemini_key)
    try:
        result = await client.aio.models.embed_content(
            model="text-embedding-004",
            contents="hello"
        )
        print("text-embedding-004 OK")
    except Exception as e:
        print("text-embedding-004 Error:", e)

    try:
        result = await client.aio.models.embed_content(
            model="gemini-embedding-001",
            contents="hello"
        )
        print("gemini-embedding-001 OK")
    except Exception as e:
        print("gemini-embedding-001 Error:", e)

asyncio.run(test())
