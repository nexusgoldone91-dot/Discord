#!/usr/bin/env python3
"""
Controllo automatico XAU/USD - gira ogni 15 minuti via GitHub Actions.

Due canali Discord separati:
- #info-dal-mondo: notizie generiche (geopolitica, Trump, Fed) da feed RSS,
  testo della notizia, niente link.
- #calendario-news: calendario economico USA (Forex Factory) - ogni lunedi
  7:00 ora Canarie un riepilogo semplice della settimana, e un avviso 15-20
  minuti prima di ogni dato ad alto impatto con i valori attesi/precedenti.

Nessuna chiamata a modelli AI a pagamento: solo parole chiave e dati.
"""

import os
import re
import json
import html
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from collections import defaultdict
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

GIORNI_IT = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
MESI_IT = [
    "", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
    "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre",
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

# Se il titolo parla SOLO di vittime/morti senza un aggancio chiaro a qualcosa
# che muove davvero i mercati, va scartato - un conteggio di morti da solo non
# sposta l'oro o il dollaro, serve un impatto economico concreto (petrolio,
# blocco navale, sanzioni, ecc.)
CASUALTY_TERMS = [
    "kill", "dead", "death toll", "casualt", "wounded", "injur",
    "morti", "vittime", "uccis", "ferit",
]
MARKET_IMPACT_TERMS = [
    "oil", "petrolio", "opec", "hormuz", "blockade", "blocco navale",
    "sanction", "sanzioni", "supply", "shipping", "tanker", "petroliera",
    "dollar", "dollaro", "dxy", "gold", "oro", "xau", "stocks", "market",
    "mercati", "borsa", "fed", "rate",
]


def is_pure_casualty_news(title):
    t = title.lower()
    has_casualty = any(term in t for term in CASUALTY_TERMS)
    has_market_impact = any(term in t for term in MARKET_IMPACT_TERMS)
    return has_casualty and not has_market_impact


# Notizie "retrospettive"/di recap statistico (dati di un periodo gia' passato,
# non un evento fresco) - scartate anche se contengono una parola forte come "gold"
RETROSPECTIVE_TERMS = [
    "first half of", "h1 2026", "year-to-date", "ytd", "so far this year",
    "first six months", "quarterly report", "q1 2026", "q2 2026",
    "prima meta del", "primo semestre",
]


def is_retrospective_news(title):
    t = title.lower()
    return any(term in t for term in RETROSPECTIVE_TERMS)


# Se il titolo parla di un'altra valuta (non USD) e non menziona esplicitamente
# dollaro/oro/Fed, va scartato - "dollar" da solo puo' beccare per sbaglio storie
# su altre valute che lo citano solo di striscio (es. "Canadian dollar")
OTHER_CURRENCY_TERMS = [
    "canadian dollar", "australian dollar", "aussie", "loonie", "kiwi dollar",
    "yen", "euro", "sterling", "pound", "yuan", "renminbi", "franc",
]
USD_GOLD_EXPLICIT_TERMS = [
    "us dollar", "usd", "dxy", "dollar index", "greenback", "gold", "oro", "xau", "fed",
]


def is_other_currency_news(title):
    t = title.lower()
    has_other_currency = any(term in t for term in OTHER_CURRENCY_TERMS)
    has_usd_gold = any(term in t for term in USD_GOLD_EXPLICIT_TERMS)
    return has_other_currency and not has_usd_gold


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


def translate_to_italian(text):
    if not text:
        return text
    try:
        params = urllib.parse.urlencode({
            "client": "gtx", "sl": "en", "tl": "it", "dt": "t", "q": text
        })
        url = f"https://translate.googleapis.com/translate_a/single?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return "".join(seg[0] for seg in data[0])
    except Exception as e:
        print(f"Errore traduzione: {e}")
        return text


def matches_keywords(title):
    if is_pure_casualty_news(title):
        return False
    if is_retrospective_news(title):
        return False
    if is_other_currency_news(title):
        return False
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
        # solo il titolo tradotto: i titoli vanno dritti al punto, le descrizioni
        # di alcune fonti sono lunghe/disordinate e non c'e' AI per riassumerle bene
        title_it = translate_to_italian(it["title"])
        lines.append(f"• {title_it}")
    return lines


# ---------- CALENDARIO ECONOMICO USD (#calendario-news) ----------

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


def parse_event_datetime_canary(ev):
    """Converte data/ora dell'evento (fuso del feed, ET) in ora Canarie."""
    t = ev["time"].lower()
    if "am" not in t and "pm" not in t:
        return None
    try:
        dt = datetime.strptime(f"{ev['date']} {ev['time']}", "%m-%d-%Y %I:%M%p")
        dt = dt.replace(tzinfo=CALENDAR_TZ)
        return dt.astimezone(CANARY_TZ)
    except ValueError:
        return None


def simplify_name(title):
    """'Core CPI m/m' -> 'CPI', 'PPI m/m' -> 'PPI', ecc. per i titoli semplificati."""
    t = title
    for suffix in [" m/m", " q/q", " y/y"]:
        if t.endswith(suffix):
            t = t[: -len(suffix)]
    if t.lower().startswith("core "):
        t = t[5:]
    return t.strip()


def variant_label(title):
    """Recupera la sotto-etichetta (es. 'Core m/m', 'y/y') per il dettaglio numerico."""
    t = title.lower()
    is_core = t.startswith("core ")
    for suffix in ("m/m", "q/q", "y/y"):
        if t.endswith(" " + suffix):
            return ("Core " if is_core else "") + suffix
    return None


def pick_primary_event(items):
    """Tra piu' varianti dello stesso dato (Core CPI m/m, CPI y/y, ecc.) sceglie
    quella principale da mostrare da sola: preferisce non-Core, poi y/y, poi m/m,
    poi q/q, poi la prima disponibile."""
    def score(ev_dt):
        ev, _ = ev_dt
        t = ev["title"].lower()
        is_core = t.startswith("core ")
        if t.endswith(" y/y"):
            suffix_rank = 0
        elif t.endswith(" m/m"):
            suffix_rank = 1
        elif t.endswith(" q/q"):
            suffix_rank = 2
        else:
            suffix_rank = 3
        return (1 if is_core else 0, suffix_rank)

    with_data = [it for it in items if it[0]["forecast"] or it[0]["previous"]]
    pool = with_data if with_data else items
    return min(pool, key=score)


def check_calendar_countdown():
    """Avvisa 15-20 minuti prima di dati USD ad alto impatto, raggruppati per evento/orario."""
    events = fetch_calendar_events()
    now_canary = datetime.now(CANARY_TZ)
    window_end = now_canary + timedelta(minutes=70)  # controllo ora orario, finestra allargata per non perdere eventi tra un giro e l'altro

    already_alerted = load_state(CALENDAR_STATE_FILE)
    still_relevant_ids = set()
    groups = defaultdict(list)  # (date, time, simple_name) -> [eventi]

    for ev in events:
        if ev["country"] != "USD" or ev["impact"] != "High":
            continue
        dt = parse_event_datetime_canary(ev)
        if dt is None:
            continue
        event_id = f"{ev['title']}|{ev['date']}|{ev['time']}"
        if now_canary <= dt <= window_end:
            still_relevant_ids.add(event_id)
            if event_id not in already_alerted:
                simple_name = simplify_name(ev["title"])
                groups[(ev["date"], ev["time"], simple_name)].append((ev, dt))

    save_state(CALENDAR_STATE_FILE, still_relevant_ids | (already_alerted & still_relevant_ids))

    messages = []
    for (date_str, time_str, simple_name), items in groups.items():
        dt = items[0][1]
        minutes_left = int((dt - now_canary).total_seconds() // 60)
        primary_ev, _ = pick_primary_event(items)

        if primary_ev["forecast"] or primary_ev["previous"]:
            forecast_txt = primary_ev["forecast"] if primary_ev["forecast"] else "non disponibile"
            previous_txt = f" (precedente {primary_ev['previous']})" if primary_ev["previous"] else ""
            msg = f"⏰ -{minutes_left} Minuti al {simple_name}\nEcco i dati previsti: {forecast_txt}{previous_txt}"
        else:
            msg = f"⏰ -{minutes_left} Minuti al {simple_name}"
        messages.append(msg)

    return messages


def check_weekly_summary():
    """Ogni lunedi 7:00-7:14 ora Canarie, riepilogo semplice della settimana USD."""
    now_canary = datetime.now(CANARY_TZ)
    if not (now_canary.weekday() == 0 and now_canary.hour == 7 and now_canary.minute < 15):
        return []

    already_sent = load_state(WEEKLY_STATE_FILE)
    week_key = now_canary.strftime("%Y-W%U")
    if week_key in already_sent:
        return []

    events = fetch_calendar_events()
    usd_events = [ev for ev in events if ev["country"] == "USD" and ev["impact"] == "High"]

    # raggruppa per (data, ora, nome semplificato) cosi CPI non compare 4 volte
    grouped = {}
    for ev in usd_events:
        dt = parse_event_datetime_canary(ev)
        if dt is None:
            continue
        simple_name = simplify_name(ev["title"])
        key = (dt.date(), dt.strftime("%H:%M"), simple_name)
        if key not in grouped:
            grouped[key] = dt

    ordered = sorted(grouped.items(), key=lambda kv: kv[1])

    monday = now_canary - timedelta(days=now_canary.weekday())
    sunday = monday + timedelta(days=6)
    date_range = f"dal {monday.strftime('%d-%m-%Y')} al {sunday.strftime('%d-%m-%Y')}"

    lines = [
        f"Settimana {date_range}.",
        "",
        "📅 Calendario News della settimana:",
        "",
    ]

    if ordered:
        for (_, _, simple_name), dt in ordered:
            giorno = GIORNI_IT[dt.weekday()]
            lines.append(f"🔺 {giorno} {dt.day} {simple_name} ore {dt.strftime('%H:%M')}")
    else:
        lines.append("Questa settimana nessuna notizia d'impatto nel calendario.")
        lines.append("Teniamo comunque gli occhi aperti.")

    save_state(WEEKLY_STATE_FILE, already_sent | {week_key})
    return ["\n".join(lines)]


# ---------- DISCORD ----------

def post_to_discord(channel_id, content):
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


def run_news():
    """Controllo notizie generiche - gira ogni 15 minuti (workflow news.yml)."""
    news_lines = check_news()
    if news_lines:
        content = "**Info dal mondo:**\n\n" + "\n\n".join(news_lines)
        post_to_discord(DISCORD_CHANNEL_ID_NEWS, content)
    else:
        print("Nessuna notizia nuova rilevante trovata.")


def run_calendar():
    """Controllo calendario/countdown USD - gira una volta all'ora (workflow calendar.yml)."""
    weekly_lines = check_weekly_summary()
    for msg in weekly_lines:
        post_to_discord(DISCORD_CHANNEL_ID_USD, msg)
    if not weekly_lines:
        print("Nessun riepilogo settimanale da mandare ora.")

    countdown_messages = check_calendar_countdown()
    for msg in countdown_messages:
        post_to_discord(DISCORD_CHANNEL_ID_USD, msg)
    if not countdown_messages:
        print("Nessun dato USD ad alto impatto nella prossima ora.")


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode == "news":
        run_news()
    elif mode == "calendar":
        run_calendar()
    else:
        run_news()
        run_calendar()
