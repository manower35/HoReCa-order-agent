"""Quick script to test if Telegram and Gemini API keys are valid."""
import os
import requests
import sys
from dotenv import load_dotenv

# Force UTF-8 for Windows terminal emojis
if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())

load_dotenv()

print("=" * 50)
print("  API KEY VALIDATION TEST")
print("=" * 50)

# --- Test 1: Telegram Bot Token ---
print("\n[1] Testing Telegram Bot Token...")
token = os.getenv("TELEGRAM_BOT_TOKEN")
if not token:
    print("   ❌ TELEGRAM_BOT_TOKEN is empty in .env")
else:
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if data.get("ok"):
            bot_info = data["result"]
            print(f"   ✅ Telegram Bot Token is VALID!")
            print(f"   Bot Name: {bot_info.get('first_name')}")
            print(f"   Username: @{bot_info.get('username')}")
        else:
            print(f"   ❌ Telegram Bot Token is INVALID!")
            print(f"   Error: {data.get('description')}")
    except Exception as e:
        print(f"   ❌ Connection error: {e}")

# --- Test 2: Gemini API Key ---
print("\n[2] Testing Gemini API Key...")
gemini_key = os.getenv("GEMINI_API_KEY")
if not gemini_key:
    print("   ❌ GEMINI_API_KEY is empty in .env")
else:
    try:
        from google import genai
        client = genai.Client(api_key=gemini_key)
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents="Say 'API key works!' in exactly 3 words."
        )
        print(f"   ✅ Gemini API Key is VALID (Modern SDK)!")
        print(f"   Test response: {response.text.strip()}")
    except Exception as e:
        print(f"   ❌ Gemini API Key failed!")
        print(f"   Error: {e}")

print("\n" + "=" * 50)
print("  TEST COMPLETE")
print("=" * 50)
