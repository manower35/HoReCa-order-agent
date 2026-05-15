import sqlite3
import json
import os
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_FILE = 'grocery.db'
CARTS_JSON = 'carts.json'
ORDERS_JSON = 'orders.json'

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Create Carts Table
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
    
    # Create Orders Table
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
    
    conn.commit()
    return conn

def migrate_data():
    conn = init_db()
    cursor = conn.cursor()
    
    # 1. Migrate Carts
    if os.path.exists(CARTS_JSON):
        try:
            with open(CARTS_JSON, 'r') as f:
                carts = json.load(f)
            
            count = 0
            for user_id_str, items in carts.items():
                user_id = int(user_id_str)
                for sno_str, item in items.items():
                    sno = int(sno_str)
                    cursor.execute('''
                        INSERT OR REPLACE INTO carts (user_id, sno, name, qty, price)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (user_id, sno, item['name'], item['qty'], item['price']))
                    count += 1
            logger.info(f"Successfully migrated {count} cart items.")
        except Exception as e:
            logger.error(f"Error migrating carts: {e}")

    # 2. Migrate Orders
    if os.path.exists(ORDERS_JSON):
        try:
            with open(ORDERS_JSON, 'r') as f:
                orders = json.load(f)
            
            count = 0
            for order in orders:
                # Store items as JSON string in the DB for simplicity
                items_json = json.dumps(order.get('items', []))
                cursor.execute('''
                    INSERT OR REPLACE INTO orders (order_id, timestamp, user_id, first_name, username, total, items_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    order.get('order_id'),
                    order.get('timestamp'),
                    order.get('user_id'),
                    order.get('first_name'),
                    order.get('username'),
                    order.get('total'),
                    items_json
                ))
                count += 1
            logger.info(f"Successfully migrated {count} orders.")
        except Exception as e:
            logger.error(f"Error migrating orders: {e}")

    conn.commit()
    conn.close()
    logger.info("Migration complete. Your data is now in 'grocery.db'.")

if __name__ == "__main__":
    migrate_data()
