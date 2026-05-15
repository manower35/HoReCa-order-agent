# 🚀 XYZ GROCERY COMPANY BOT: Migration & Setup Guide

This guide explains how to move the **Telegram Order Agent** to a new laptop or another account.

---

## 📂 1. How to Transfer (Google Drive / USB)

1.  **Locate the Folder**: Go to the project folder (`Grocery Order Agent`).
2.  **Zip the Folder**: Right-click the folder and select **Compress to ZIP file**.
3.  **Transfer**: Upload the ZIP file to Google Drive or copy it to a USB drive.
4.  **Download & Extract**: On the new laptop, download the ZIP and **Extract All**.

> [!IMPORTANT]
> Ensure the `.env` file is included in the transfer. This file contains your API keys. If it's missing, the bot will not work.

---

## 🛠️ 2. New Laptop Setup

Once the files are on the new machine:

1.  **Install Python**: Download from [python.org](https://www.python.org/).
2.  **Open Terminal**: Open PowerShell or Command Prompt in the extracted folder.
3.  **Install Tools**: Run the following command:
    ```powershell
    pip install -r requirements.txt
    ```
4.  **Run the Bot**:
    ```powershell
    python bot.py
    ```

---

## 📧 3. How to Use a New Email (Google AI Studio)

If you need to change the Gemini AI account:

1.  Log into [Google AI Studio](https://aistudio.google.com/) with the new email.
2.  Create a new **API Key**.
3.  Open the `.env` file in Notepad.
4.  Replace the `GEMINI_API_KEY` value with your new key:
    ```env
    GEMINI_API_KEY=your_new_key_here
    ```

---

## 🤖 4. How to Change the Bot (Telegram)

If you want the bot to run on a different Telegram name:

1.  Message [@BotFather](https://t.me/BotFather) on Telegram and create a new bot.
2.  Copy the **HTTP API Token**.
3.  In your `.env` file, replace the `TELEGRAM_BOT_TOKEN`:
    ```env
    TELEGRAM_BOT_TOKEN=your_new_token_here
    ```

---

**Guide Prepared by:** Antigravity AI Assistant
**Date:** April 11, 2026
