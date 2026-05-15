import sqlite3
import json
import os
from datetime import datetime

DB_FILE = 'grocery.db'

def view_history():
    if not os.path.exists(DB_FILE):
        print("\n❌ No database found! Please run the bot first.")
        return

    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Fetch all orders
        cursor.execute("SELECT order_id, timestamp, first_name, total, items_json FROM orders ORDER BY timestamp DESC")
        orders = cursor.fetchall()
        
        if not orders:
            print("\n📭 Order history is empty.")
            return

        print("\n" + "="*85)
        print(f"{'XYZ GROCERY COMPANY - SALES HISTORY REPORT':^85}")
        print("="*85)
        print(f"{'Order ID':<15} | {'Date':<12} | {'Customer':<15} | {'Items':<5} | {'Total':<10}")
        print("-" * 85)

        grand_total_sales = 0
        for order in orders:
            oid = order['order_id']
            date = order['timestamp'].split(' ')[0]
            name = order['first_name']
            items = json.loads(order['items_json'])
            item_count = len(items)
            total = order['total']
            grand_total_sales += total
            
            print(f"{oid:<15} | {date:<12} | {name:<15} | {item_count:<5} | Rs.{total:<10.2f}")

        print("="*85)
        print(f"{'GRAND TOTAL REVENUE:':<52} Rs.{grand_total_sales:,.2f}")
        print("="*85 + "\n")
        
        conn.close()

    except Exception as e:
        print(f"❌ Error reading history: {e}")

if __name__ == "__main__":
    view_history()
