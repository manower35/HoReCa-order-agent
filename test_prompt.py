import asyncio
import os
import json
from google import genai
from dotenv import load_dotenv

load_dotenv()

SYSTEM_PERSONA = """You are the "XYZ GROCERY COMPANY TEAM LEAD". RANGE: Spices, Pulses, Rice, Oil, Ghee, Dairy. TASK: Find items in CONTEXT. Respond in JSON: { "agent": "support|sales", "reply": "Quick brief reply", "items_found": [SNo_list] }"""

async def test():
    gemini_key = os.getenv("GEMINI_API_KEY")
    client = genai.Client(api_key=gemini_key)
    context = "SNo,Name,Unit Price\n1038,dal toor - l,125\n1042,dawat royal xxxl basmati rice,225\n1112,vintage malai paneer,360"
    prompt = f"{SYSTEM_PERSONA}\nCONTEXT: {context}\nHISTORY: \nUSER: Daal\nRice\nPaneer"
    
    try:
        res = await client.aio.models.generate_content(
            model="gemini-2.0-flash-lite",
            contents=prompt
        )
        print("Response:", res.text)
    except Exception as e:
        print("Error:", e)

asyncio.run(test())
