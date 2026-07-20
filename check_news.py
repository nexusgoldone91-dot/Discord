#!/usr/bin/env python3
"""
Controllo automatico XAU/USD - gira ogni 15 minuti via GitHub Actions.

Due canali Discord separati:
- #info-dal-mondo: notizie generiche (geopolitica, Trump, Fed) da feed RSS,
  testo della notizia, niente link.
- #news-di-mercato-usd: dati economici USA dal calendario Forex Factory -
  riepilogo settimanale ogni lunedi 7:00 ora Canarie, e avviso 15-20 minuti
  prima di un dato ad alto impatto con il valore atteso.

Nessuna chiamata a modelli AI a pagamento: solo parole chiave e dati.
"""

import os
import re
import json
import html
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DISCORD_CHANNEL_ID_NEWS = os.environ["DISCORD_CHANNEL_ID"]
DISCORD_CHANNEL_ID_USD = os.environ["DISCORD_CHANNEL_ID_USD"]

NEWS_FEEDS = [
    "https://www.investing.com/rss/news_285.rss",       # Commodities news
    "https://www.investing.com/rss/news_1.rss",          # Economic news
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://www.cnbc.com/id/10000664/device/rss/rss.html",  # CNBC economy
    "https://www.forexlive.com/feed/news",
]

CALENDAR_FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
CALENDAR_TZ = ZoneInfo("America/New_York")  # convenzione documentata di questo feed
CANARY_TZ = ZoneInfo("Atlantic/Canary")

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

NEWS_STATE_FILE = "seen_ids.json"
CALENDAR_STATE_FILE = "seen_calendar.json"
WEEKLY_STATE_FILE = "seen_weekly.json"

HTML_TAG_RE = re.compile(r"<[^>]+>")


def clean_text(raw):
    if not raw:
        return ""
    text = HTML_TAG_RE.sub("", raw)
    text = html.unescape(text)
    return text.strip()


def fetch_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()


# ---------- NOTIZIE GENERICHE (#info-dal-mondo) ----------

def fetch_news_feed(url):
    try:
        data = fetch_url(url)
        root = ET.fromstring(data)
    except Exception as e:
        print(f"Errore feed notizie {url}: {e}")
        return []

    items = []
    for item in root.iter("item"):
        title = clean_text(item.findtext("title", ""))
        description = clean_text(item.findtext("description", ""))
        link = item.findtext("link", "")
        guid = item.findtext("guid", link)
        items.append({"title": title, "description": description, "guid": guid})
    return items


def matches_keywords(title):
    t = title.lower()
    if any(kw in t for kw in STRONG_KEYWORDS):
        return True
    weak_hits = [kw for kw in WEAK_KEYWORDS if kw in t]
    return len(weak_hits) >= 2


def load_state(path):
    if os.path.exists(path):
        with open(path) as f:
            return set(json.load(f))
    return set()


def save_state(path, values):
    with open(path, "w") as f:
        json.dump(list(values)[-500:], f)


def check_news():
    seen = load_state(NEWS_STATE_FILE)
    new_relevant = []

    for feed_url in NEWS_FEEDS:
        for item in fetch_news_feed(feed_url):
            if item["guid"] in seen:
                continue
            seen.add(item["guid"])
            if matches_keywords(item["title"]):
                new_relevant.append(item)

    save_state(NEWS_STATE_FILE, seen)

    lines = []
    for it in new_relevant[:8]:
        body = it["description"] if it["description"] else it["title"]
        if it["title"].lower() not in body.lower():
            lines.append(f"• **{it['title']}** — {body}")
        else:
            lines.append(f"• {body}")
    return lines


# ---------- CALENDARIO ECONOMICO USD (#news-di-mercato-usd) ----------

def fetch_calendar_events():
    try:
        data = fetch_url(CALENDAR_FEED)
        root = ET.fromstring(data)
    except Exception as e:
        print(f"Errore calendario economico: {e}")
        return []

    events = []
    for event in root.iter("event"):
        events.append({
            "title": clean_text(event.findtext("title", "")),
            "country": clean_text(event.findtext("country", "")),
            "date": clean_text(event.findtext("date", "")),
            "time": clean_text(event.findtext("time", "")),
            "impact": clean_text(event.findtext("impact", "")),
            "forecast": clean_text(event.findtext("forecast", "")),
            "previous": clean_text(event.findtext("previous", "")),
        })
    return events


def parse_event_datetime(ev):
    t = ev["time"].lower()
    if "am" not in t and "pm" not in t:
        return None
    try:
        dt = datetime.strptime(f"{ev['date']} {ev['time']}", "%m-%d-%Y %I:%M%p")
        return dt.replace(tzinfo=CALENDAR_TZ)
    except ValueError:
        return None


def check_calendar_countdown():
    """Avvisa 15-20 minuti prima di un dato USD ad alto impatto."""
    events = fetch_calendar_events()
    now = datetime.now(CALENDAR_TZ)
    window_end = now + timedelta(minutes=20)

    already_alerted = load_state(CALENDAR_STATE_FILE)
    new_alerts = []
    still_relevant_ids = set()

    for ev in events:
        if ev["country"] != "USD" or ev["impact"] != "High":
            continue
        dt = parse_event_datetime(ev)
        if dt is None:
            continue
        event_id = f"{ev['title']}|{ev['date']}|{ev['time']}"
        if now <= dt <= window_end:
            still_relevant_ids.add(event_id)
            if event_id not in already_alerted:
                minutes_left = int((dt - now).total_seconds() // 60)
                forecast_txt = ev["forecast"] if ev["forecast"] else "non disponibile"
                new_alerts.append(
                    f"-{minutes_left} Minuti ai {ev['title']}\nEcco i dati previsti: {forecast_txt}"
                )

    save_state(CALENDAR_STATE_FILE, still_relevant_ids | (already_alerted & still_relevant_ids))
    return new_alerts


def check_weekly_summary():
    """Ogni lunedi 7:00-7:14 ora Canarie, riepilogo della settimana USD."""
    now_canary = datetime.now(CANARY_TZ)
    if not (now_canary.weekday() == 0 and now_canary.hour == 7 and now_canary.minute < 15):
        return []

    already_sent = load_state(WEEKLY_STATE_FILE)
    week_key = now_canary.strftime("%Y-W%U")
    if week_key in already_sent:
        return []

    events = fetch_calendar_events()
    usd_events = [
        ev for ev in events
        if ev["country"] == "USD" and ev["impact"] == "High"
    ]
    usd_events.sort(key=lambda ev: (ev["date"], ev["time"]))

    if not usd_events:
        save_state(WEEKLY_STATE_FILE, already_sent | {week_key})
        return []

    dates = [ev["date"] for ev in usd_events if ev["date"]]
    date_range = f"dal {dates[0]} al {dates[-1]}" if dates else ""

    lines = [f"**Settimana {date_range}**\n"]
    for ev in usd_events:
        forecast_txt = f", previsto {ev['forecast']}" if ev["forecast"] else ""
        lines.append(f"🔴 {ev['date']} {ev['time']} — {ev['title']}{forecast_txt}")

    save_state(WEEKLY_STATE_FILE, already_sent | {week_key})
    return ["\n".join(lines)]


# ---------- DISCORD ----------

def post_to_discord(channel_id, lines, header):
    content = f"**{header}**\n\n" + "\n\n".join(lines)
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
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
    news_lines = check_news()
    if news_lines:
        post_to_discord(DISCORD_CHANNEL_ID_NEWS, news_lines, "Aggiornamenti rilevanti per XAU/USD")
    else:
        print("Nessuna notizia nuova rilevante trovata.")

    weekly_lines = check_weekly_summary()
    if weekly_lines:
        post_to_discord(DISCORD_CHANNEL_ID_USD, weekly_lines, "Calendario News della settimana")
    else:
        print("Nessun riepilogo settimanale da mandare ora.")

    calendar_lines = check_calendar_countdown()
    if calendar_lines:
        post_to_discord(DISCORD_CHANNEL_ID_USD, calendar_lines, "Calendario News")
    else:
        print("Nessun dato USD ad alto impatto nei prossimi 20 minuti.")


if __name__ == "__main__":
    main()
