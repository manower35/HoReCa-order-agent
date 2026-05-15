# 🛒 HoReCa Order Agent

The **HoReCa Order Agent** is an intelligent AI-powered Telegram bot designed specifically for Hotel, Restaurant, and Catering (HoReCa) businesses. It acts as an automated 24/7 digital concierge, assisting business clients with B2B grocery ordering, inventory search, and seamless invoice generation.

Built with Python, the Telegram Bot API, and Google's Gemini models, the agent provides both text-based intelligent search and photo-based product recognition capabilities.

## 🌟 Key Features

* **🤖 AI Concierge:** Powered by Google's Gemini API (`gemini-1.5-flash`), the bot handles natural language queries, acting as a virtual assistant for sales, support, and billing inquiries.
* **🔍 Semantic & Dish-Based Search (RAG):** Uses a local Vector Database to intelligently map user queries (like "I need biryani ingredients") to raw inventory items (like "Basmati Rice" and "Spices") through custom synonym mapping and catalog aliases.
* **📸 Image/Photo Search:** Clients can simply send a photo of a product (e.g., a bag of rice or a spice bottle) and the bot will use Gemini Vision (`gemini-2.0-flash-lite`) to identify the item and locate it in the catalog.
* **🛒 Cart & Order Management:** Fully functioning cart system allowing users to add/remove items and adjust quantities.
* **🧾 Automated PDF Invoicing:** Automatically generates and sends a professional PDF invoice directly in Telegram upon checkout.
* **📦 Inventory Management:** Admin commands for stock keeping, ledger updates, and dynamic catalog syncing via `products.csv` and an SQLite database (`grocery.db`).

## 🛠️ Technology Stack

* **Language:** Python 3
* **Interface:** Telegram Bot API (`python-telegram-bot`)
* **AI Models:** Google Generative AI (Gemini 1.5 Flash / Gemini 2.0 Flash-Lite Vision)
* **Search Engine:** Retrieval-Augmented Generation (RAG) with local vector databases
* **Database:** SQLite (`grocery.db`) + CSV Catalog System

## 🚀 Getting Started

### Prerequisites
* Python 3.9+
* A Telegram Bot Token (from [BotFather](https://t.me/botfather))
* A Google Gemini API Key

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/manower35/HoReCa-order-agent.git
   cd HoReCa-order-agent
   ```

2. **Set up a virtual environment (Optional but recommended):**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install the dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Environment Variables:**
   Create a `.env` file in the root directory and add your keys:
   ```env
   TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
   GEMINI_API_KEY=your_gemini_api_key_here
   ```

5. **Initialize Database & Vectors:**
   Ensure `products.csv` is populated with your initial inventory, then run:
   ```bash
   python migrate_to_sqlite.py
   python sync_vectors.py
   ```

6. **Run the Bot:**
   ```bash
   python bot.py
   ```

## 📊 Analytics & Reporting

The bot maintains logs of API success rates, average latency, and order volumes. The system automatically shifts AI models to ensure stability if daily API quotas are reached. System administrators can find logged performance in `agent_performance.csv`.

## 🤝 Contributing

Contributions, issues, and feature requests are welcome! Feel free to check the [issues page](https://github.com/manower35/HoReCa-order-agent/issues).

## 📝 License

This project is open-source and available under the MIT License.
