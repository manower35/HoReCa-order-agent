import asyncio
import os
import json
import re
from google import genai
from dotenv import load_dotenv

load_dotenv()

SYSTEM_PERSONA = """You are the "XYZ GROCERY COMPANY TEAM LEAD". RANGE: Spices, Pulses, Rice, Oil, Ghee, Dairy. TASK: Find items in CONTEXT. Respond in JSON: { "agent": "support|sales", "reply": "Quick brief reply", "items_found": [SNo_list] }"""

async def test():
    gemini_key = os.getenv("GEMINI_API_KEY")
    client = genai.Client(api_key=gemini_key)
    context = "SNo,Name,Unit Price\n1038,dal toor - l,125\n1075,milky mist paneer 1kg,390\n1102,tata salt,28"
    prompt = f"{SYSTEM_PERSONA}\nCONTEXT: {context}\nHISTORY: \nUSER: dal\npaneer\nsalt"
    
    try:
        res = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        ai_res = res.text.strip()
        print("Raw AI Res:", ai_res)
        match = re.search(r'\{.*\}', ai_res, re.DOTALL)
        if match: 
            ai_res = match.group()
            print("Regex matched:", ai_res)
        data = json.loads(ai_res)
        print("Parsed JSON:", data)
    except Exception as e:
        print("Error:", e)

asyncio.run(test())
