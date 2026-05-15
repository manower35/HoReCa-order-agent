import logging
import os
import io
import json
import re
import difflib
import html
from datetime import datetime
import time
import httpx
import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai import types
from PIL import Image
from fpdf import FPDF
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import chromadb
import asyncio
import sqlite3
from aiohttp import web

# LangChain Imports for Memory
from langchain_community.chat_message_histories import FileChatMessageHistory
from langchain_core.messages import HumanMessage, AIMessage

# Load environment variables from .env file
load_dotenv()

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Owner Telegram ID for order notifications
OWNER_CHAT_ID = os.getenv("OWNER_CHAT_ID")

# Cloud Run Compatibility: Use /tmp for writable storage
IS_CLOUD = os.getenv("K_SERVICE") or os.getenv("K_REVISION")
BASE_DIR = "/tmp" if IS_CLOUD else "."
DB_FILE = os.path.join(BASE_DIR, 'grocery.db')

def is_owner(user_id):
    """Check if the user is the authorized business owner."""
    return str(user_id) == str(OWNER_CHAT_ID)


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS carts (
            user_id INTEGER,
            sno INTEGER,
            name TEXT,
            qty INTEGER,
            price REAL,
            PRIMARY KEY (user_id, sno)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            timestamp TEXT,
            user_id INTEGER,
            first_name TEXT,
            username TEXT,
            total REAL,
            items_json TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            sno INTEGER PRIMARY KEY,
            name TEXT,
            stock_qty INTEGER,
            unit_price REAL
        )
    ''')
    conn.commit()
    conn.close()

def sync_products_to_db():
    """Sync products.csv to SQLite on startup."""
    if df_products.empty: return
    conn = get_db()
    cursor = conn.cursor()
    for _, row in df_products.iterrows():
        cursor.execute('''
            INSERT OR REPLACE INTO products (sno, name, stock_qty, unit_price)
            VALUES (?, ?, ?, ?)
        ''', (int(row['SNo']), str(row['Name']), int(row.get('StockQty', 0)), float(row.get('Unit Price', 0))))
    conn.commit()
    conn.close()
    logger.info("Products synced to SQLite.")

init_db()

# Load the products CSV
CSV_FILE = 'products.csv'
try:
    df_products = pd.read_csv(CSV_FILE)
    df_products.columns = df_products.columns.str.strip()
    
    # Smart Column Mapping
    cols = {c.lower(): c for c in df_products.columns}
    sno_col = next((cols[c] for c in ['sno', 'sku_id', 'id', 's.no'] if c in cols), None)
    price_col = next((cols[c] for c in ['unit price', 'unitprice', 'rate', 'price'] if c in cols), None)
    stock_col = next((cols[c] for c in ['stockqty', 'stock', 'qty', 'stock qty', 'quantity'] if c in cols), None)
    aliases_col = next((cols[c] for c in ['aliases', 'aliases ', 'tags'] if c in cols), None)
    image_col = next((cols[c] for c in ['image url', 'imageurl', 'photo', 'image', 'picture'] if c in cols), None)

    rename_map = {}
    if sno_col: rename_map[sno_col] = 'SNo'
    if price_col: rename_map[price_col] = 'Unit Price'
    if stock_col: rename_map[stock_col] = 'StockQty'
    if aliases_col: rename_map[aliases_col] = 'Aliases'
    if image_col: rename_map[image_col] = 'Image URL'
    
    df_products = df_products.rename(columns=rename_map)
    df_products['Name'] = df_products['Name'].fillna('').astype(str).str.lower()
    if 'Aliases' in df_products:
        df_products['Aliases'] = df_products['Aliases'].fillna('').astype(str).str.lower()
    
    if 'Unit Price' in df_products:
        df_products['Unit Price'] = df_products['Unit Price'].astype(str).str.replace('?', '', regex=False).str.replace('₹', '', regex=False).str.replace(',', '', regex=False).str.strip()
        df_products['Unit Price'] = pd.to_numeric(df_products['Unit Price'], errors='coerce').fillna(0)
    
    if 'StockQty' in df_products:
        df_products['StockQty'] = pd.to_numeric(df_products['StockQty'], errors='coerce').fillna(0).astype(int)
    else:
        df_products['StockQty'] = 0
    
    logger.info(f"Loaded {len(df_products)} products. Columns found: {list(df_products.columns)}")
    sync_products_to_db() # NEW: Sync to SQLite
except Exception as e:
    logger.error(f"Error loading CSV: {e}")
    df_products = pd.DataFrame()

# Ensure directories exist (Cloud-safe)
MEMORY_DIR = os.path.join(BASE_DIR, "user_memories")
INVOICE_DIR = os.path.join(BASE_DIR, "invoices")
CACHE_DIR = os.path.join(BASE_DIR, "cache")

os.makedirs(INVOICE_DIR, exist_ok=True)
os.makedirs(MEMORY_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
if not os.path.exists("image_cache"): os.makedirs("image_cache", exist_ok=True)
if not os.path.exists("invoices"): os.makedirs("invoices", exist_ok=True)

# AI Response Cache
CACHE_FILE = os.path.join(CACHE_DIR, 'ai_cache.json')

def load_ai_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
        except: return {}
    return {}

def save_ai_cache(cache):
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache, f)
    except: pass

ai_cache = load_ai_cache()

def load_user_cart(user_id):
    """Load a specific user's cart from SQLite."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT sno, name, qty, price FROM carts WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return {row['sno']: {'name': row['name'], 'qty': row['qty'], 'price': row['price']} for row in rows}

def update_cart_item(user_id, sno, name, qty, price, increment=False):
    """Add or update an item in the SQLite cart."""
    conn = get_db()
    cursor = conn.cursor()
    if increment:
        cursor.execute('''
            INSERT INTO carts (user_id, sno, name, qty, price)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, sno) DO UPDATE SET qty = qty + excluded.qty
        ''', (user_id, sno, name, qty, price))
    else:
        cursor.execute('''
            INSERT OR REPLACE INTO carts (user_id, sno, name, qty, price)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, sno, name, qty, price))
    conn.commit()
    conn.close()

def clear_user_cart(user_id):
    """Clear a user's cart from SQLite."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM carts WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def delete_cart_item(user_id, sno):
    """Remove a specific item from a user's SQLite cart."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM carts WHERE user_id = ? AND sno = ?", (user_id, sno))
    conn.commit()
    conn.close()

# --- LEAD MANAGEMENT FUNCTIONS ---
async def search_leads_web(segment: str, area: str):
    """Search for business leads using Nominatim (OpenStreetMap)."""
    query = f"{segment} in {area}"
    url = f"https://nominatim.openstreetmap.org/search?q={query}&format=json&addressdetails=1&extratags=1&limit=8"
    headers = {'User-Agent': 'SriCompanyOrderAgent/1.0'}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            return response.json()
    except Exception as e:
        logger.error(f"Lead search error: {e}")
        return []

def save_lead_to_db(name, b_type, area, contact, source="Web Search"):
    """Save a found lead to the database."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO leads (name, business_type, area, contact, source)
        VALUES (?, ?, ?, ?, ?)
    ''', (name, b_type, area, contact, source))
    conn.commit()
    conn.close()

def search_existing_customers(term: str):
    """Search the existing_customers table."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT customer_name, business_type, area, contact, segment
        FROM existing_customers
        WHERE customer_name LIKE ? OR area LIKE ? OR segment LIKE ?
        LIMIT 10
    ''', (f"%{term}%", f"%{term}%", f"%{term}%"))
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_leads_from_db(limit=10):
    """Get saved leads from the database."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, area, contact, status FROM leads ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return rows


# Initialize ChromaDB for Vector Search
try:
    vector_client = chromadb.PersistentClient(path="vector_db")
    vector_collection = vector_client.get_collection(name="products")
    logger.info("Local Vector Database (RAG) connected.")
except Exception as e:
    logger.error(f"Vector Database Error: {e}")
    vector_collection = None

def log_performance(user_id, input_type, agent_selected, latency, status):
    """Silent logging metric for tracking Agent Performance & API speeds."""
    try:
        file_exists = os.path.isfile('agent_performance.csv')
        with open('agent_performance.csv', 'a', encoding='utf-8') as f:
            if not file_exists:
                f.write("Timestamp,User_ID,Input_Type,Agent_Selected,Latency_Seconds,Status\n")
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')},{user_id},{input_type},{agent_selected},{latency:.2f},{status}\n")
    except Exception as e:
        logger.error(f"Failed to log performance: {e}")

async def get_embedding(text):
    """Generate embedding for search queries (Async-ready)."""
    try:
        result = await gemini_client.aio.models.embed_content(
            model="gemini-embedding-001",
            contents=text
        )
        return result.embeddings[0].values
    except Exception as e:
        logger.error(f"Embedding error: {e}")
        return None

async def vector_search(query: str, limit=5):
    """Semantic search using local ChromaDB."""
    if not vector_collection:
        return pd.DataFrame()
    vec = await get_embedding(query)
    if not vec: return pd.DataFrame()
    try:
        results = vector_collection.query(
            query_embeddings=[vec],
            n_results=limit
        )
        skus = [int(s) for s in results['ids'][0]]
        matches = df_products[df_products['SNo'].isin(skus)]
        return matches
    except Exception as e:
        logger.error(f"Vector search fail: {e}")
        return pd.DataFrame()

def get_welcome_message(name):
    """User-focused welcome message for XYZ GROCERY COMPANY."""
    return (
        "✨ <b>Welcome to XYZ GROCERY COMPANY</b> ✨\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "✅ <b>We Sell:</b> Spices, Dals, Rice & Dairy\n"
        "🕓 <b>Open:</b> 10:30 AM to 09:30 PM\n"
        "📍 <b>Location:</b> Begum Bazar\n"
        "💡 <i>(Note: No fresh vegetables)</i>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔎 <b>Search Tips:</b>\n"
        "• Type item names (e.g. <i>Poha, Mirch</i>)\n"
        "• Type quantity (e.g. <i>2kg Ghee, 1kg Salt</i>)\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Type your order here 👇"
    )

def get_chat_history(user_id):
    """Get the persistent chat history for a user."""
    history_file = os.path.join(MEMORY_DIR, f"history_{user_id}.json")
    return FileChatMessageHistory(history_file)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    name = user.first_name or "Partner"
    await update.message.reply_html(get_welcome_message(name))
    if is_owner(user.id):
        await update.message.reply_html("👋 <b>Admin recognized!</b> Use /admin to see lead management tools.")

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only Admin Menu."""
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Unauthorized access.")
        return
    
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Existing Customers", callback_data="admin_customers"),
         InlineKeyboardButton("🔍 Find New Leads", callback_data="admin_find_leads")],
        [InlineKeyboardButton("📈 Sales Report", callback_data="admin_sales"),
         InlineKeyboardButton("📥 Export Leads", callback_data="admin_export")],
        [InlineKeyboardButton("🔙 Close Menu", callback_data="close_admin")]
    ])
    await update.message.reply_html("🛠 <b>Sri Company Admin Dashboard</b>\nWhat would you like to do?", reply_markup=markup)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check the health and status of the bot remotely."""
    me = await context.bot.get_me()
    status_msg = (
        "🖥️ <b>XYZ GROCERY COMPANY ENGINE STATUS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ <b>Status:</b> Online & Connected\n"
        f"🤖 <b>Bot:</b> @{me.username}\n"
        f"📦 <b>Inventory:</b> {len(df_products)} Products Loaded\n"
        f"🕒 <b>Local Time:</b> {datetime.now().strftime('%H:%M:%S')}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Everything is running smoothly! 🛒"
    )
    await update.message.reply_html(status_msg)

async def stock_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate a low-stock/out-of-stock report."""
    user_id = update.effective_user.id
    try:
        low_stock = df_products[df_products['StockQty'] < 10].sort_values('StockQty')
        out_of_stock = df_products[df_products['StockQty'] <= 0]
        msg = [
            "📊 <b>XYZ GROCERY COMPANY: STOCK SUMMARY</b>",
            f"📅 <b>Date:</b> {datetime.now().strftime('%d-%b-%Y')}",
            f"🕒 <b>Time:</b> {datetime.now().strftime('%I:%M %p')}",
            "━━━━━━━━━━━━━━━━━━━━",
            f"✅ <b>Total Items:</b> {len(df_products)}",
            f"🔴 <b>Out of Stock:</b> {len(out_of_stock)}",
            f"⚠️ <b>Low Stock (<10):</b> {len(low_stock)}",
            "━━━━━━━━━━━━━━━━━━━━\n",
            "🛑 <b>OUT OF STOCK (TOP 10):</b>"
        ]
        if out_of_stock.empty:
            msg.append("<i>- All items currently in stock!</i>")
        else:
            for _, row in out_of_stock.head(10).iterrows():
                msg.append(f"• {str(row['Name']).title()}")
        msg.append("\n⚠️ <b>REORDER SOON:</b>")
        if low_stock.empty:
            msg.append("<i>- All stock levels are healthy!</i>")
        else:
            for _, row in low_stock.head(15).iterrows():
                if row['StockQty'] > 0:
                    msg.append(f"• {str(row['Name']).title()} ➞ <b>{row['StockQty']} left</b>")
        msg.append("\n━━━━━━━━━━━━━━━━━━━━")
        msg.append("\n📍 *XYZ GROCERY COMPANY, BEGUM BAZAR*")
        await update.message.reply_html("\n".join(msg))
    except Exception as e:
        logger.error(f"Stock report error: {e}")

async def stock_in(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stock Entry Command."""
    if len(context.args) == 0:
        await update.message.reply_html("💡 <b>Usage:</b> /in [Name or ID]")
        return
    query = context.args[0]
    target_row = None
    if query.isdigit():
        matches = df_products[df_products['SNo'] == int(query)]
        if not matches.empty: target_row = matches.iloc[0]
    if target_row is None:
        name_matches = search_product(query)
        if name_matches.empty:
            await update.message.reply_html(f"❌ Product '<b>{query}</b>' not found.")
            return
        if len(name_matches) == 1:
            target_row = name_matches.iloc[0]
        else:
            msg = [f"🔍 <b>Found matches for '{query}':</b>"]
            for _, row in name_matches.head(10).iterrows():
                msg.append(f"🆔 <code>{row['SNo']}</code> | {str(row['Name']).title()}")
            await update.message.reply_html("\n".join(msg))
            return
    sno = target_row['SNo']
    if len(context.args) >= 2 and context.args[1].isdigit():
        await process_stock_entry(update, sno, int(context.args[1]))
    else:
        buttons = [[InlineKeyboardButton(f"+{q}", callback_data=f"in_{sno}_{q}") for q in [1, 2, 5, 10, 50]]]
        await update.message.reply_html(f"📦 <b>Stock Entry: {str(target_row['Name']).title()}</b>\nHow much are you adding?", reply_markup=InlineKeyboardMarkup(buttons))

async def process_stock_entry(update, sno, qty_added, is_query=False):
    try:
        idx = df_products[df_products['SNo'] == sno].index[0]
        df_products.at[idx, 'StockQty'] += qty_added
        df_products.to_csv(CSV_FILE, index=False)
        msg = f"✅ Stock Updated! <b>{df_products.at[idx, 'Name'].title()}</b> now has <b>{df_products.at[idx, 'StockQty']}</b> units."
        if is_query: await update.callback_query.edit_message_text(msg, parse_mode='HTML')
        else: await update.message.reply_html(msg)
    except Exception as e:
        logger.error(f"Entry process fail: {e}")

COMMON_SYNONYMS = {
    # Basic Staples
    "chawal": "rice", "biyam": "rice", "rice": "chawal",
    "aloo": "potato", "pyaz": "onion", "uulli": "onion",
    "lahsun": "garlic", "vellulli": "garlic", "mirchi": "chilly pepper", "kaaram": "chilly pepper",
    "haldi": "turmeric", "pasupu": "turmeric", "namak": "salt", "tel": "oil", "noone": "oil",
    "poha": "rice pressed", "atukulu": "rice pressed", "chini": "sugar", "panchadara": "sugar",
    "atta": "flour", "maida": "flour", "dal": "dall", "dall": "dal", "pappu": "dal", "ghee": "ghee", "neyyi": "ghee",
    "paneer": "panner", "panner": "paneer",
    
    # Recipe to Ingredient Mapping
    "pulao": "basmati rice ghee jeera cloves",
    "biryani": "basmati rice shahjeera cardamom clove cinnamon",
    "biriyani": "basmati rice shahjeera cardamom clove cinnamon",
    "sambar": "dal toor sambar masala tamarind mustard seeds",
    "sambhar": "dal toor sambar masala tamarind mustard seeds",
    "rasam": "dal toor pepper coriander turmeric",
    "kheer": "basmati rice milk powder sugar dry fruits",
    "payasam": "vermicelli sugar milk powder cardamom",
    "halwa": "suji rawa ghee sugar dry fruits",
    "sheera": "suji rawa ghee sugar",
    "upma": "suji rawa mustard seeds jeera oil",
    "idli": "idly rawa udad dall salt",
    "dosa": "rice udad dall fenugreek methi",
    "chai": "tea sugar milk powder",
    "coffee": "coffee sugar milk powder",
    "tadka": "jeera mustard seeds chilly powder",
    "khichdi": "rice dall moong yellow turmeric ghee",
    "poori": "wheat flour atta oil",
    "puri": "wheat flour atta oil",
    "bhaji": "besan flour turmeric chilly powder oil",
    "pakora": "besan flour onion chilly powder oil",
    
    # Regional/Other Synonyms
    "rajma": "rajama", "kabuli": "chana", "chole": "kabuli chana", "batana": "peas",
    "sabudana": "sago", "rava": "suji rawa", "sooji": "suji rawa", "samver": "sambar", "sambhar": "sambar",
    "badam": "almond", "kaju": "cashew", "kismis": "raisin", "kishmish": "raisin",
    "chini": "sugar", "namak": "salt", "aloo": "potato", "pappu": "dal", "biyam": "rice",
    "chawal": "rice", "mirchi": "chilly", "oil": "noone", "tel": "oil"
}

def search_product(query: str):
    """Diverse keyword search: ensures results for each part of the query."""
    query = query.lower()
    # Replace common filler words with space
    query = re.sub(r'\b(i|need|want|get|give|me|some|please|show|buy|can|you|have|any|a|an|the|am|are|is|going|to|for|del|in|at|on|with|from|my|your|his|her|they|them|we|us)\b', ' ', query)
    query = re.sub(r'\d+(?:kg|gm|ltr|g|ml|unit|units|packet|pcs|l|tin|kg)\b', ' ', query)
    
    raw_parts = [p.strip() for p in re.split(r'[\n,]+', query) if p.strip()]
    
    words = []
    for p in raw_parts:
        p = re.sub(r'[^a-zA-Z0-9\s]', ' ', p)
        for w in p.split():
            if w not in words and not w.isdigit():
                words.append(w)
                
    if not words: return pd.DataFrame()

    final_results = []
    seen_snos = set()
    
    # Pass 1: Combined score
    expanded_words = set()
    for w in words:
        expanded_words.add(w)
        if w in COMMON_SYNONYMS: expanded_words.add(COMMON_SYNONYMS[w])
        
    combined_scores = []
    for _, row in df_products.iterrows():
        name_text = (str(row['Name']).strip() + " " + str(row.get('Aliases', '')).strip()).lower()
        score = 0
        matches = 0
        for w in expanded_words:
            w = w.strip()
            if re.search(rf'\b{re.escape(w)}\b', name_text): 
                score += 50
                matches += 1
            elif w in name_text: 
                score += 20
                matches += 1
            else:
                # Fuzzy Match
                name_words = name_text.split()
                close_matches = difflib.get_close_matches(w, name_words, n=1, cutoff=0.7)
                if close_matches: 
                    score += 10
                    matches += 1
        if score > 0:
            score += (matches * 20) 
            combined_scores.append((row, score))
            
    combined_scores.sort(key=lambda x: x[1], reverse=True)
    
    # Threshold: Only include if the best match has at least one exact word match (score >= 50)
    if combined_scores and combined_scores[0][1] < 50:
        return pd.DataFrame()
    
    for row, score in combined_scores[:10]:
        final_results.append(row)
        seen_snos.add(row['SNo'])
        
    # Pass 2: Ensure diversity (top 3 per word)
    for w in words:
        w_expanded = {w}
        if w in COMMON_SYNONYMS: w_expanded.add(COMMON_SYNONYMS[w])
        
        word_results = []
        for _, row in df_products.iterrows():
            name_text = (str(row['Name']).strip() + " " + str(row.get('Aliases', '')).strip()).lower()
            score = 0
            for ew in w_expanded:
                if re.search(rf'\b{re.escape(ew)}\b', name_text): score += 50
                elif ew in name_text: score += 20
            if score > 0:
                word_results.append((row, score))
                
        word_results.sort(key=lambda x: x[1], reverse=True)
        added = 0
        for row, score in word_results:
            if row['SNo'] not in seen_snos:
                final_results.append(row)
                seen_snos.add(row['SNo'])
                added += 1
            if added >= 3: break
            
    return pd.DataFrame(final_results).head(25)

SYSTEM_PERSONA = """You are the "SRI COMPANY BUSINESS MANAGER". 
RANGE: Spices, Pulses, Rice, Oil, Ghee, Dairy, Dry Fruits. 
OUT OF SCOPE: Fresh Fruits, Vegetables, Meat, Fish.

MODES:
1. CUSTOMER MODE: Help users find products, check prices, and add items to cart.
2. ADMIN MODE (Owner Only): Help find new business leads, analyze existing customers, and manage inventory.

CRITICAL RULES:
1. Respond ONLY in JSON format.
2. INTENT DETECTION: If user says "add [item]" or "order [item]", set agent to "sales".
3. LEAD GENERATION: If the owner asks to "find leads" or "analyze customers", set agent to "lead_manager".
4. Reply field: Short and friendly. DO NOT list products/leads in "reply".

JSON Format: { "agent": "support|sales|lead_manager", "reply": "Message", "items_found": [ID_list] }"""


def extract_quantity(text):
    match = re.search(r'(?:^|\s)(\d+)\s*(?:kg|gm|ltr|g|ml|unit|units|packet|pcs)?(?:\s|$)', text.lower())
    return int(match.group(1)) if match and 1 <= int(match.group(1)) <= 500 else None

async def auto_add_to_cart(update, user_id, row, qty):
    sno, name, price = int(row['SNo']), str(row['Name']).title(), float(row.get('Unit Price', 0))
    update_cart_item(user_id, sno, name, qty, price, increment=True)
    cart = load_user_cart(user_id)
    cost = cart[sno]['qty'] * price
    await update.message.reply_html(f"✅ <b>Added!</b> {name} × {qty}\n💰 Subtotal: ₹{cost}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pulse_msg = None
    try:
        if df_products.empty: 
            await update.message.reply_text("⚠️ Database is empty. Please check products.csv.")
            return
            
        text, user_id, start_time = update.message.text, update.effective_user.id, time.time()
        user_name = update.effective_user.first_name or "Partner"
        clean_text = text.lower().strip()

        # 0. GREETING CHECK — DO THIS FIRST (No pulse message for greetings)
        GREETINGS = {'hi', 'hello', 'hey', 'hii', 'hiii', 'helo', 'heyy', 'heyyy', 'namaste', 'salam', 'yo', 'sup'}
        if clean_text in GREETINGS:
            safe_name = html.escape(user_name)
            await update.message.reply_html(get_welcome_message(safe_name))
            return

        # 1. IMMEDIATE PULSE (Shows the bot is alive)
        print(f"[DEBUG] >>> Received message: '{text}' from user {user_id}")
        try: pulse_msg = await update.message.reply_html("🔍 <i>Searching...</i>")
        except: pass

        detected_qty = extract_quantity(text)
        try: await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
        except: pass

        # 2. FAST-TRACK: Skip AI for simple or recipe-based queries
        clean_words = clean_text.split()
        if len(clean_words) <= 2:
            print(f"[DEBUG] Fast-track for '{clean_text}'")
            all_matches = search_product(text)
            display_matches = all_matches.head(10)
            
            if not display_matches.empty:
                if pulse_msg:
                    try: await pulse_msg.delete()
                    except: pass
                
                safe_search = html.escape(clean_text.title())
                await update.message.reply_html(f"🔍 Results for <b>{safe_search}</b>:")
                
                for _, row in display_matches.iterrows():
                    await display_product_card(update, row)
                
                if len(all_matches) > 10:
                    # Limit query text for callback safety
                    short_q = clean_text[:30]
                    buttons = [[InlineKeyboardButton("🔍 See More", callback_data=f"more_{short_q}_10")]]
                    await update.message.reply_html(f"<i>Found {len(all_matches)} items. Click below for more:</i>", reply_markup=InlineKeyboardMarkup(buttons))
                return

        # 1. CACHE CHECK
        cached = ai_cache.get(clean_text)
        data = None
        if cached and time.time() - cached.get('timestamp', 0) < 86400:
            logger.info(f"CACHE HIT for '{clean_text}'"); data = cached.get('data')

        if not data:
            # 2. PARALLEL RETRIEVAL
            async def fetch_history():
                try:
                    h = get_chat_history(user_id)
                    fmt = "".join([f"{'User' if getattr(m,'type','')=='human' else 'Team'}: {getattr(m,'content','')}\n" for m in h.messages[-5:]])
                    return h, fmt
                except: return None, "N/A"
            
            # Combine Keyword and Vector searches
            res = await asyncio.gather(fetch_history(), vector_search(text, limit=15))
            history_obj, formatted_history, v_matches = res[0][0], res[0][1], res[1]
            k_matches = search_product(text)
            
            # Merge results: keyword matches prioritized for precision, vector for recall
            if v_matches.empty: context_matches = k_matches
            elif k_matches.empty: context_matches = v_matches
            else:
                context_matches = pd.concat([k_matches, v_matches]).drop_duplicates(subset='SNo').head(25)
            
            inventory_context = context_matches[['SNo','Name','Unit Price']].to_csv(index=False)
            
            # 3. AI CALL - Using Gemini 1.5 Flash with a 8-second Safety Timer
            if not gemini_client: raise Exception("No AI client")
            prompt = f"{SYSTEM_PERSONA}\nCONTEXT: {inventory_context}\nHISTORY: {formatted_history}\nUSER: {text}"
            try: 
                # Safety Timer: If AI takes > 8s, we skip it and use local search
                # Use sync call in a thread because we know sync works from test_apis.py
                ai_res_obj = await asyncio.to_thread(
                    gemini_client.models.generate_content,
                    model='gemini-1.5-flash',
                    contents=prompt
                )
                ai_res = ai_res_obj.text.strip()
            except asyncio.TimeoutError:
                logger.error("AI Timeout - Falling back to support")
                ai_res = '{"agent": "support", "reply": "I am here to help! Could you please clarify if you would like to order something or have a question about our dry groceries?", "items_found": []}'
            except Exception as e:
                logger.error(f"AI call fallback triggered: {e}")
                ai_res = '{"agent": "support", "reply": "I am sorry, I am having trouble connecting. Error: ' + html.escape(str(e)) + '", "items_found": []}'
            
            try:
                match = re.search(r'\{.*\}', ai_res, re.DOTALL)
                if match:
                    data = json.loads(match.group())
                else:
                    # If AI returned a conversational reply without JSON, treat it as support
                    data = {"agent": "support", "reply": ai_res, "items_found": []}
                
                ai_cache[clean_text] = {"data": data, "timestamp": time.time()}
                save_ai_cache(ai_cache)
            except Exception as e:
                logger.error(f"AI result parsing failed: {e}")
                data = {"agent": "support", "reply": ai_res if ai_res else "I am sorry, I am having trouble processing that.", "items_found": []}
        else:
            history_obj = get_chat_history(user_id)

        # 4. EXECUTE
        agent_id, msg = data.get("agent", "sales"), data.get("reply", "I found these matches for you:")
        
        # Remove pulse message before sending real results
        if pulse_msg:
            try: await pulse_msg.delete()
            except: pass
            
        skus = data.get("items_found", [])
        display_matches = df_products[df_products['SNo'].isin(skus)]
        
        # If AI is in sales mode but didn't find specific SNos, fallback to keyword search
        if agent_id == "sales" and display_matches.empty: 
            display_matches = search_product(text).head(15)
            
        if not display_matches.empty:
            await update.message.reply_html(f"🔍 <b>{msg}</b>")
            for _, row in display_matches.iterrows():
                await display_product_card(update, row)
        elif agent_id == "lead_manager":
            # If owner asks for lead info
            if is_owner(user_id):
                # Check for "find [business] in [area]" pattern
                find_match = re.search(r"(?:find|search for)\s+(.*?)\s+in\s+(.*)", text.lower())
                if find_match:
                    segment, area = find_match.group(1).strip(), find_match.group(2).strip()
                    leads = await search_leads_web(segment, area)
                    if leads:
                        resp = [f"🌐 <b>Found {len(leads)} leads for {segment} in {area}:</b>"]
                        for i, l in enumerate(leads[:5]):
                            name = l.get('display_name', 'Business').split(',')[0]
                            resp.append(f"• <b>{name}</b>\n  📍 {l.get('display_name')[:80]}...")
                            # In a real bot, we'd add buttons to save each lead
                        resp.append("\n<i>Note: Use the Admin Menu to save these to your pipeline.</i>")
                        await update.message.reply_html("\n".join(resp))
                    else:
                        await update.message.reply_html(f"❌ No live leads found for <b>{segment}</b> in <b>{area}</b>.")
                
                elif "customer" in text.lower() or "lead" in text.lower():
                    results = search_existing_customers(text)
                    if results:
                        resp = ["📊 <b>Matching Customers:</b>"]
                        for r in results:
                            resp.append(f"• <b>{r[0]}</b> ({r[4]})\n  📍 {r[2]} | 📞 {r[3]}")
                        await update.message.reply_html("\n".join(resp))
                    else:
                        await update.message.reply_html(msg)
                else:
                    await update.message.reply_html(msg)
            else:
                await update.message.reply_html("I'm sorry, I am currently set to 'Catalog Mode' for customers. How can I help you with your order?")

        elif agent_id == "sales":

            await update.message.reply_text("We specialize in Wholesale Dry Groceries (Rice, Dals, Spices, Oils). Try searching for Rice, Ghee, or Dal!")
        else:
            await update.message.reply_html(msg)
        
        if history_obj:
            try:
                history_obj.add_user_message(text)
                history_obj.add_ai_message(f"[{agent_id}] {msg}")
            except: pass # Don't crash if history fails
        logger.info(f"DONE in {time.time()-start_time:.2f}s")
    except Exception as e:
        logger.error(f"Global Error: {e}")
        if pulse_msg: 
            try: await pulse_msg.delete()
            except: pass
        # BULLETPROOF FALLBACK - plain text, no buttons, no HTML
        try:
            matches = search_product(text).head(5)
            if not matches.empty:
                lines = ["Found these matches:"]
                for _, row in matches.iterrows():
                    lines.append(f"- {str(row['Name']).title()} | Rs.{row.get('Unit Price',0)}")
                await update.message.reply_text("\n".join(lines))
            else:
                await update.message.reply_text("We sell Rice, Dals, Spices, Oils & Dairy. Try searching for those!")
        except Exception as e:
            print(f"!!! CRITICAL ERROR in handle_message: {e}")
            logger.error(f"Top-level handle_message fail: {e}")

async def display_product_card(update, row):
    """Text-only card for Low Latency with HTML escaping."""
    name = html.escape(str(row['Name']).title())
    sku, price, stock = row['SNo'], row.get('Unit Price',0), row.get('StockQty',0)
    buttons = [[InlineKeyboardButton(str(q), callback_data=f"add_{sku}_{q}") for q in [1, 2, 5, 10]]]
    msg = f"<b>📦 {name}</b>\n🆔 ID: {sku}\n💰 Price: ₹{price}\n📦 Stock: {stock}\n🛒 Add to Cart:"
    try:
        if update.message:
            await update.message.reply_html(msg, reply_markup=InlineKeyboardMarkup(buttons))
        elif update.callback_query and update.callback_query.message:
            await update.callback_query.message.reply_html(msg, reply_markup=InlineKeyboardMarkup(buttons))
        else:
            # Fallback for other update types
            await update.effective_chat.send_message(msg, reply_markup=InlineKeyboardMarkup(buttons), parse_mode='HTML')
    except Exception as e:
        logger.error(f"Failed to send card for {sku}: {e}")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query; await query.answer()
    data, user_id = query.data, query.from_user.id
    
    if data.startswith("more_"):
        try:
            parts = data.split("_")
            if len(parts) >= 3:
                query_text, offset = parts[1], int(parts[2])
                all_results = search_product(query_text)
                next_batch = all_results.iloc[offset : offset + 10]
                for _, row in next_batch.iterrows():
                    await display_product_card(update, row)
                if len(all_results) > offset + 10:
                    new_offset = offset + 10
                    buttons = [[InlineKeyboardButton("🔍 See More", callback_data=f"more_{query_text}_{new_offset}")]]
                    await query.message.reply_html(f"<i>Showing items {offset+1}-{offset+len(next_batch)} of {len(all_results)}.</i>", reply_markup=InlineKeyboardMarkup(buttons))
                else:
                    await query.message.reply_html("🏁 <i>End of results.</i>")
        except Exception as e:
            logger.error(f"More callback fail: {e}")
    elif data.startswith("in_"):
        await process_stock_entry(update, int(data.split("_")[1]), int(data.split("_")[2]), is_query=True)
    elif data.startswith("del_"):
        try:
            idx = int(data.split("_")[1]) - 1
            cart = load_user_cart(user_id)
            if cart and 0 <= idx < len(cart):
                keys = list(cart.keys())
                if idx < len(keys):
                    sno_to_del = keys[idx]
                    delete_cart_item(user_id, sno_to_del)
            
            # Re-render the cart instantly
            msg, buttons = await get_cart_render(user_id)
            if not msg: await query.edit_message_text("Your cart is now empty. 🗑️")
            else: await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(buttons), parse_mode='HTML')
        except Exception as e:
            logger.error(f"Delete callback fail: {e}")
            await query.message.reply_text(f"❌ Delete Error: {str(e)}")
    elif data == "confirm_checkout":
        try: 
            cart = load_user_cart(user_id)
            if not cart:
                await query.message.reply_text("Your cart is empty!")
                return
            await checkout(update, context)
        except Exception as e:
            logger.error(f"Checkout callback error: {e}")
            await query.message.reply_text(f"❌ Checkout Error: {str(e)}")
    elif data == "clear_cart_confirm":
        clear_user_cart(user_id); await query.edit_message_text("Cart cleared. 🗑️")
    elif data == "add_more_items":
        await query.message.reply_html("💡 <b>Tip:</b> Just type the name of the item (e.g. <i>Rice</i> or <i>Ghee</i>) to find and add more products!")
    elif data == "admin_customers":
        await query.message.reply_html("🔍 <b>Search Existing Customers</b>\nType any area or business name to search.")
    elif data == "admin_find_leads":
        await query.message.reply_html("🌐 <b>Find New Leads</b>\nUsage: Tell me <i>'Find [Business Type] in [Area]'</i>\nExample: <i>'Find Hotels in Begum Bazar'</i>")
    elif data == "admin_export":
        conn = get_db()
        df = pd.read_sql_query("SELECT * FROM leads", conn)
        conn.close()
        if df.empty:
            await query.message.reply_text("No leads in pipeline yet.")
        else:
            path = os.path.join(BASE_DIR, "pipeline_export.csv")
            df.to_csv(path, index=False)
            await context.bot.send_document(chat_id=user_id, document=open(path, 'rb'), caption="📂 Here is your Lead Pipeline!")
    elif data == "close_admin":
        await query.edit_message_text("Admin Dashboard closed. ✅")
    elif data.startswith("add_"):

        try:
            parts = data.split("_"); sno, qty = int(parts[1]), int(parts[2])
            p_matches = df_products[df_products['SNo'] == sno]
            if not p_matches.empty:
                p = p_matches.iloc[0]
                item_name = str(p['Name']).title()
                update_cart_item(user_id, sno, item_name, qty, float(p.get('Unit Price', 0)), increment=True)
                # Force immediate feedback
                await query.edit_message_text(text=f"✅ <b>Added {qty} {item_name}</b> to your cart!\nUse /cart to checkout.", parse_mode='HTML')
            else:
                await query.edit_message_text("❌ Error: Product not found in inventory.")
        except Exception as e:
            logger.error(f"Add callback error: {e}")
            await query.message.reply_text("⚠️ Failed to add item. Please try again.")

async def manual_add_to_cart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manual command to add items: /add [ID] [Qty] or /order [ID] [Qty]"""
    if len(context.args) < 2:
        await update.message.reply_html("💡 <b>Usage:</b> /add [Product_ID] [Quantity]\nExample: <code>/add 1042 2</code>")
        return
    
    try:
        sno = int(context.args[0])
        qty = int(context.args[1])
        user_id = update.effective_user.id
        
        matches = df_products[df_products['SNo'] == sno]
        if matches.empty:
            await update.message.reply_html(f"❌ Product ID <b>{sno}</b> not found.")
            return
        
        row = matches.iloc[0]
        await auto_add_to_cart(update, user_id, row, qty)
    except ValueError:
        await update.message.reply_html("❌ Please use numbers for ID and Quantity.\nExample: <code>/add 1042 2</code>")
    except Exception as e:
        logger.error(f"Manual add error: {e}")
        await update.message.reply_text("❌ Something went wrong while adding to cart.")

async def get_cart_render(user_id):
    cart = load_user_cart(user_id)
    if not cart: return None, None
    msg = ["══════════════════", "📜 <b>PROFORMA QUOTATION</b>", "══════════════════", "🛒 <b>YOUR SHOPPING CART</b>", "══════════════════", ""]
    total = 0
    buttons = []
    
    # Red Delete Buttons (One for each item)
    del_row = []
    for idx, (sno, item) in enumerate(cart.items(), 1):
        cost = item['qty'] * item['price']; total += cost
        safe_name = html.escape(str(item['name']).title())
        msg.append(f"{idx}. 🔹 <b>{safe_name}</b>\n   {item['qty']} units × ₹{item['price']} = <b>₹{cost}</b>\n")
        del_row.append(InlineKeyboardButton(f"❌ {idx}", callback_data=f"del_{idx}"))
        if len(del_row) == 4:
            buttons.append(del_row); del_row = []
    if del_row: buttons.append(del_row)
    
    msg.append("──────────────────")
    msg.append(f"💰 <b>GRAND TOTAL: ₹{total}</b>")
    msg.append("   <i>(Including all GST/Taxes)</i>")
    msg.append("──────────────────")
    msg.append("\n📍 <b>XYZ GROCERY COMPANY, BEGUM BAZAR</b>")
    
    # GUARANTEED ACTION BUTTONS
    # Row 1: Delete buttons (already added in loop above)
    
    # Row 2: Add More
    buttons.append([InlineKeyboardButton("➕ Add More Items", callback_data="add_more_items")])
    
    # Row 3: Clear and Checkout
    buttons.append([
        InlineKeyboardButton("🗑️ Clear All", callback_data="clear_cart_confirm"),
        InlineKeyboardButton("💳 Proceed to Checkout", callback_data="confirm_checkout")
    ])
    
    return "\n".join(msg), buttons

async def view_cart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    msg, buttons = await get_cart_render(user_id)
    if not msg: 
        await update.message.reply_text("Your cart is currently empty.")
        return
    await update.message.reply_html(msg, reply_markup=InlineKeyboardMarkup(buttons))

async def checkout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user; user_id = user.id
    cart = load_user_cart(user_id)
    if not cart: return
    
    order_id = datetime.now().strftime("%Y%m%d%H%M%S")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = sum(item['qty'] * item['price'] for item in cart.values())
    
    # DB Update
    conn = get_db(); cursor = conn.cursor()
    items_list = []
    for sno, item in cart.items():
        cost = item['qty'] * item['price']
        items_list.append({"name": item['name'], "qty": item['qty'], "price": item['price'], "cost": cost})
        # Use lowercase names to match our new SQLite table
        cursor.execute("UPDATE products SET stock_qty = MAX(0, stock_qty - ?) WHERE sno = ?", (item['qty'], sno))
    cursor.execute("INSERT INTO orders (order_id, timestamp, user_id, first_name, username, total, items_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (order_id, timestamp, user_id, user.first_name, user.username, total, json.dumps(items_list)))
    conn.commit(); conn.close()

    # PDF Table
    import warnings
    warnings.filterwarnings("ignore", category=UserWarning, module="fpdf")
    pdf = FPDF(); pdf.add_page()
    
    # Add Logo at Top Center
    if os.path.exists("logo.jpg"):
        pdf.image("logo.jpg", x=85, y=10, w=40)
        pdf.ln(38) # Spacer after logo
    else:
        pdf.ln(10) # Default spacer if logo is missing

    pdf.set_font("helvetica", "B", 18)
    pdf.cell(0, 10, "XYZ GROCERY COMPANY", align="C", ln=True)
    pdf.set_font("helvetica", "", 10)
    pdf.cell(0, 5, "Begum Bazar, Hyderabad | Wholesale Dry Groceries", align="C", ln=True)
    pdf.ln(10)
    
    pdf.set_font("helvetica", "B", 11)
    pdf.cell(0, 8, f"PROFORMA QUOTATION: #{order_id}", ln=True)
    pdf.set_font("helvetica", "", 9)
    pdf.cell(0, 6, f"Date: {timestamp} | Customer: {user.first_name}", ln=True)
    pdf.ln(5)
    
    # Table Header (High-Visibility Tabular Layout)
    pdf.set_fill_color(220, 220, 220)
    pdf.set_font("helvetica", "B", 10)
    pdf.cell(15, 10, "S.No", border=1, align="C", fill=True)
    pdf.cell(90, 10, " Item Name", border=1, align="L", fill=True)
    pdf.cell(20, 10, "Qty", border=1, align="C", fill=True)
    pdf.cell(30, 10, "Rate", border=1, align="C", fill=True)
    pdf.cell(35, 10, "Amount", border=1, align="C", fill=True)
    pdf.ln()
    
    # Table Content (High-Visibility)
    pdf.set_font("helvetica", "", 10)
    for idx, item in enumerate(items_list, 1):
        name = (item['name'][:42] + '..') if len(item['name']) > 44 else item['name']
        pdf.cell(15, 8, str(idx), border=1, align="C")
        pdf.cell(90, 8, f" {name.title()}", border=1, align="L")
        pdf.cell(20, 8, str(item['qty']), border=1, align="C")
        pdf.cell(30, 8, f"Rs. {item['price']}", border=1, align="C")
        pdf.cell(35, 8, f"Rs. {item['cost']}", border=1, align="C")
        pdf.ln()
    
    # Total
    pdf.set_font("helvetica", "B", 11)
    pdf.cell(155, 10, "GRAND TOTAL: ", border=1, align="R")
    pdf.cell(35, 10, f"Rs. {total}", border=1, align="C")
    pdf.ln(15)
    
    pdf.set_font("helvetica", "I", 9)
    pdf.cell(0, 5, "This is a computer-generated proforma quotation.", align="C", ln=True)
    
    path = os.path.join(INVOICE_DIR, f"Order_{order_id}.pdf")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pdf.output(path)
    
    if not os.path.exists(path): raise Exception(f"PDF creation failed at {path}")
    
    with open(path, 'rb') as f: 
        await context.bot.send_document(chat_id=update.effective_chat.id, document=f, caption=f"📝 Your proforma quotation #{order_id} is ready!")
    
    if OWNER_CHAT_ID and str(OWNER_CHAT_ID).strip():
        try:
            owner_id = int(str(OWNER_CHAT_ID).strip())
            await context.bot.send_message(chat_id=owner_id, text=f"🔔 <b>NEW ORDER: #{order_id}</b>\n👤 {user.first_name}\n💰 TOTAL: ₹{int(total)}", parse_mode='HTML')
        except: pass
    
    clear_user_cart(user_id)
    await update.effective_message.reply_text("✅ Checkout successful! Your quotation has been sent above.")

async def handle_photo(update, context): await update.message.reply_text("🚫 Photos disabled for speed.")

async def health_check(request):
    return web.Response(text="OK", status=200)

async def run_bot():
    token = os.getenv("TELEGRAM_BOT_TOKEN"); gemini_key = os.getenv("GEMINI_API_KEY")
    global gemini_client; gemini_client = genai.Client(api_key=gemini_key) if gemini_key else None
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start)); app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("cart", view_cart)); app.add_handler(CommandHandler("checkout", checkout))
    app.add_handler(CommandHandler("stock", stock_report)); app.add_handler(CommandHandler("in", stock_in))
    app.add_handler(CommandHandler("add", manual_add_to_cart))
    app.add_handler(CommandHandler("order", manual_add_to_cart))
    app.add_handler(CommandHandler("admin", admin_menu))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    # Initialize and start polling
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    
    # Keep the bot running
    while True:
        await asyncio.sleep(3600)

async def main_async():
    # Set up the health check server for Cloud Run
    app_web = web.Application()
    app_web.router.add_get('/', health_check)
    runner = web.AppRunner(app_web)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    
    # Start the web server first and FAST to satisfy Cloud Run's health check
    await site.start()
    logger.info(f"Health check server started on port {port}")
    
    # Now start the bot (this takes longer)
    try:
        await run_bot()
    except Exception as e:
        logger.error(f"Bot failed to start: {e}")
        # Keep the web server alive for a bit so we can see the logs
        await asyncio.sleep(60)

def main():
    asyncio.run(main_async())

if __name__ == '__main__':
    main()
