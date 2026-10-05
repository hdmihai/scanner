# -*- coding: utf-8 -*-
"""adapters.notify.telegram - rezumatul scanarii trimis pe Telegram (portul ports.notifier)."""

import requests


def send_telegram(token, chat_id, text):
    if "PUNE_AICI" in token or "PUNE_AICI" in chat_id:
        print("[!] Configureaza telegram_bot_token si telegram_chat_id in CONFIG "
              "ca sa primesti rezultatele pe telefon.")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        requests.post(
            url,
            data={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        print(f"[!] Eroare trimitere Telegram: {e}")
