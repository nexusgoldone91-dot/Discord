#!/usr/bin/env python3
"""
Controllo automatico XAU/USD - gira ogni ora via GitHub Actions.
Legge feed RSS gratuiti, cerca parole chiave rilevanti per oro/dollaro
nell'ultima ora, e posta su Discord solo se trova qualcosa di nuovo.
Nessuna chiamata a modelli AI a pagamento: solo parole chiave.
"""

import os
import re
import json
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DISCORD_CHANNEL_ID = os.environ["DISCORD_CHANNEL_ID"]

FEEDS = [
    "https://www.investing.com/rss/news_285.rss",       # Commodities news
    "https://www.investing.com/rss/news_1.rss",          # Economic news
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://www.cnbc.com/id/10000664/device/rss/rss.html",  # CNBC economy
    "https://www.forexlive.com/feed/news",
]

# Parole "forti": da sole bastano per segnalare la notizia (chiaramente legate a oro/geopolitica/Fed)
STRONG_KEYWORDS = [
    "xau", "gold", "oro", "iran", "houthi", "hormuz", "ceasefire", "cessate il fuoco",
    "fomc", "rate hike", "rate cut", "cpi", "ppi", "nfp", "nonfarm payroll",
    "truth social", "missile", "airstrike", "air strike",
]

# Parole "deboli": generiche, contano solo se compaiono insieme ad almeno un'altra
# (forte o debole) nello stesso titolo - evita falsi positivi tipo "Dow ai massimi
# grazie a Trump" o notizie su Bitcoin/lira turca che citano solo "dollar" di striscio
WEAK_KEYWORDS = [
    "trump", "tariff", "tariffs", "dazi", "dazio",
    "fed", "federal reserve", "interest rate", "inflation", "inflazione",
    "israel", "strike", "attack", "war",
    "dollar", "dollaro", "dxy",
    "central bank", "banca centrale", "opec", "oil", "petrolio",
]

STATE_FILE = "seen_ids.json"


def fetch_feed(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
    except Exception as e:
        print(f"Errore su {url}: {e}")
        return []

    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []

    items = []
    for item in root.iter("item"):
        title = item.findtext("title", "")
        link = item.findtext("link", "")
        pub_date = item.findtext("pubDate", "")
        guid = item.findtext("guid", link)
        items.append({"title": title, "link": link, "pub_date": pub_date, "guid": guid})
    return items


def matches_keywords(title):
    t = title.lower()
    strong_hits = [kw for kw in STRONG_KEYWORDS if kw in t]
    if strong_hits:
        return True
    weak_hits = [kw for kw in WEAK_KEYWORDS if kw in t]
    return len(weak_hits) >= 2


def load_seen():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    # tiene solo le ultime 500 per non far crescere il file all'infinito
    with open(STATE_FILE, "w") as f:
        json.dump(list(seen)[-500:], f)


def post_to_discord(lines):
    content = "**Aggiornamenti rilevanti per XAU/USD (ultima ora)**\n\n" + "\n\n".join(lines)
    url = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages"
    body = json.dumps({"content": content}).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "NexusGoldOneBot (https://nexusgoldone.gold, 1.0)",
        },
    )
    with urllib.request.urlopen(req) as resp:
        print("Postato su Discord:", resp.status)


def main():
    seen = load_seen()
    new_relevant = []

    for feed_url in FEEDS:
        for item in fetch_feed(feed_url):
            if item["guid"] in seen:
                continue
            if matches_keywords(item["title"]):
                new_relevant.append(item)
            seen.add(item["guid"])

    if new_relevant:
        lines = [f"• {it['title']} ({it['link']})" for it in new_relevant[:8]]
        post_to_discord(lines)
    else:
        print("Nessuna notizia nuova rilevante trovata.")

    save_seen(seen)


if __name__ == "__main__":
    main()
