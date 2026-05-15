import os
import pandas as pd
from google import genai
import chromadb
from dotenv import load_dotenv
import time

load_dotenv()

# Setup Gemini
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("Error: GEMINI_API_KEY not found in .env")
    exit(1)

client_gemini = genai.Client(api_key=api_key)

# Setup ChromaDB (Local storage)
client = chromadb.PersistentClient(path="vector_db")
collection = client.get_or_create_collection(name="products")

def get_embedding(text):
    """Generate embedding with automatic retry on rate limit."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            result = client_gemini.models.embed_content(
                model="gemini-embedding-001",
                contents=text
            )
            return result.embeddings[0].values
        except Exception as e:
            if "429" in str(e):
                wait_time = 30 * (attempt + 1)
                print(f"Rate limit hit. Waiting {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"Embedding error: {e}")
                return None
    return None

def sync():
    CSV_FILE = 'products.csv'
    if not os.path.exists(CSV_FILE):
        print(f"Error: {CSV_FILE} not found.")
        return

    df = pd.read_csv(CSV_FILE)
    df.columns = df.columns.str.strip()
    
    # Get existing IDs to skip them
    existing_ids = set(collection.get()['ids'])
    print(f"Database already has {len(existing_ids)} items.")

    ids = []
    documents = []
    metadatas = []
    embeddings = []

    print(f"Starting sync for remaining items locally...")

    for idx, row in df.iterrows():
        sku = str(row['SNo'])
        # Always upsert to ensure aliases/price updates are reflected
        name = str(row['Name'])
        category = str(row.get('Category', 'General'))
        aliases = str(row.get('Aliases ', ''))
        visual = str(row.get('Visual Keywords', ''))
        price = str(row.get('Unit Price', '0')).replace('?', '').replace('₹', '').strip()
        
        text_to_embed = f"Product: {name}. Category: {category}. Keywords: {aliases}, {visual}"
        
        print(f"Indexing [{sku}] {name}...", end=" ", flush=True)
        vec = get_embedding(text_to_embed)
        if vec:
            collection.upsert(
                ids=[sku],
                embeddings=[vec],
                metadatas=[{
                    "sku": sku,
                    "category": category,
                    "price": price,
                    "name": name
                }],
                documents=[name]
            )
            print("Done")
        else:
            print("Failed")
        
        # Stay under the 15 RPM limit safely
        time.sleep(4.5)

    print("\nSYNC COMPLETE! Your local vector database is fully updated.")

if __name__ == "__main__":
    sync()
