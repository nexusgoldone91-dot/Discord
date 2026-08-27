#!/usr/bin/env python3
"""
Controllo automatico XAU/USD via GitHub Actions - due workflow separati:
news.yml (check_news, ogni ~29 minuti) e calendar.yml (check_calendar_countdown
+ check_weekly_summary, ogni 5 minuti - vedi cron-job.org).

Due canali Discord separati:
- #info-dal-mondo: notizie generiche (geopolitica, Trump, Fed) da feed RSS,
  solo titolo tradotto in italiano, niente corpo/link/fonte.
- #calendario-news: calendario economico USA (Forex Factory) - ogni lunedi
  (nel giro dell'ora 7) un riepilogo semplice della settimana, e un avviso
  a ~10 minuti fissi prima di un dato ad alto impatto con i valori attesi/precedenti.

Nessuna chiamata a modelli AI a pagamento: solo parole chiave e dati.
"""

import os
import re
import json
import html
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DISCORD_CHANNEL_ID_NEWS = os.environ["DISCORD_CHANNEL_ID"]
DISCORD_CHANNEL_ID_USD = os.environ["DISCORD_CHANNEL_ID_USD"]

# usati solo dal workflow calendar.yml per creare gli allarmi precisi a 10 minuti
# (assenti su news.yml/alert_event.yml, per questo os.environ.get e non os.environ[])
CRONJOB_API_KEY = os.environ.get("CRONJOB_API_KEY")
GH_TRIGGER_TOKEN = os.environ.get("GH_TRIGGER_TOKEN")
GITHUB_REPO = "nexusgoldone91-dot/Discord"

# usati solo dal workflow newsletter.yml (assenti altrove, per questo os.environ.get)
BREVO_API_KEY = os.environ.get("BREVO_API_KEY")
DISCORD_CHANNEL_ID_NEWSLETTER = os.environ.get("DISCORD_CHANNEL_ID_NEWSLETTER")
GDRIVE_CLIENT_ID = os.environ.get("GDRIVE_CLIENT_ID")
GDRIVE_CLIENT_SECRET = os.environ.get("GDRIVE_CLIENT_SECRET")
GDRIVE_REFRESH_TOKEN = os.environ.get("GDRIVE_REFRESH_TOKEN")

# cartelle Drive newsletter (vedi Nexus Claude/REGISTRO_PUBBLICAZIONI.md)
GDRIVE_FOLDER_NEWSLETTER = "1Ou4eXbMfWo-ifKEHT_NuMgIZ2-77aUz2"  # "recupero email", edizioni numerate #01-#20
GDRIVE_FOLDER_XAUUSD = "1Mhw_RdsAB7ka-IwtWbWP8mPVOh0r9Okv"  # "Analisi XAUUSD", serie ad-hoc
GDRIVE_FOLDER_BONUS = "1Nzu56V9UYEUq3JxOFIFq87CKi53QIgFG"  # "I Pattern della Psicologia nel Trading", serie Bonus del giovedi

NEWS_FEEDS = [
    "https://www.investing.com/rss/news_285.rss",       # Commodities news
    "https://www.investing.com/rss/news_1.rss",          # Economic news
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://www.cnbc.com/id/10000664/device/rss/rss.html",  # CNBC economy
    "https://www.forexlive.com/feed/news",
]

CALENDAR_FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
CALENDAR_TZ = ZoneInfo("UTC")  # verificato il 27/7/2026 con dati reali (FOMC 14:00 ET = 18:00 UTC nel feed): il feed fornisce gli orari in UTC, non in ET come si pensava prima
ITALY_TZ = ZoneInfo("Europe/Rome")

GIORNI_IT = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
MESI_IT = [
    "", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
    "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre",
]

# Parole "forti": da sole bastano per segnalare la notizia (chiaramente legate a oro/geopolitica/Fed)
STRONG_KEYWORDS = [
    "xau", "gold", "oro", "silver", "argento", "platinum", "platino", "palladium", "palladio",
    "iran", "houthi", "hormuz", "ceasefire", "cessate il fuoco",
    "fomc", "rate hike", "rate cut", "cpi", "ppi", "nfp", "nonfarm payroll",
    "truth social", "missile", "airstrike", "air strike",
    "recession", "recessione", "jobs report", "unemployment", "disoccupazione",
    "treasury yield", "bond yield", "gdp",
    "bank collapse", "bank run", "banking crisis", "bank failure",
    "crollo banca", "fallimento banca", "crisi bancaria",
    "gold reserves", "riserve auree", "gold mine", "miniera d'oro", "new gold deposit",
    "de-dollarization", "de-dollarizzazione", "brics",
]

# Parole "deboli": generiche, contano solo se compaiono insieme ad almeno un'altra
# (forte o debole) nello stesso titolo - evita falsi positivi tipo "Dow ai massimi
# grazie a Trump" o notizie su Bitcoin/lira turca che citano solo "dollar" di striscio
# (allargate il 25/8/2026 su richiesta di William: piu' notizie macro/mercati, banche,
# metalli preziosi in generale, mosse grosse di soldi, non solo oro/Fed strette)
WEAK_KEYWORDS = [
    "trump", "tariff", "tariffs", "dazi", "dazio",
    "fed", "federal reserve", "interest rate", "inflation", "inflazione",
    "israel", "strike", "attack", "war", "conflict", "geopolitical", "geopolitica",
    "dollar", "dollaro", "dxy",
    "central bank", "banca centrale", "opec", "oil", "petrolio", "crude",
    "sanction", "sanzioni", "stocks", "s&p", "nasdaq", "dow jones",
    "ecb", "bce", "boe", "boj",
    "mine", "miniera", "deposit", "giacimento", "reserves", "riserve", "stockpile", "scorte",
    "billionaire", "miliardario", "hedge fund", "insider buying", "insider",
    "bankrupt", "bankruptcy", "collapse", "crollo",
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


# Se il titolo riguarda principalmente un altro paese (non USA/Iran/Israel, i
# soli davvero centrali per XAU/USD) e non parla esplicitamente di oro, va
# scartato - evita notizie tipo "CPI Nuova Zelanda" o "dazi USA-Canada" che
# citano una parola forte/debole (cpi, tariff) senza avere nulla a che fare
# con l'oro. Qui l'unica via di salvezza e' la menzione esplicita dell'oro
# (non basta "US"/"Fed" come per le valute sopra, William ha chiesto un filtro
# piu' stretto: anche notizie che coinvolgono gli USA ma sono centrate su un
# altro paese, tipo dazi USA-Canada, non contano)
OTHER_COUNTRY_TERMS = [
    "canada", "canadian", "new zealand", "australia", "australian",
    "japan", "japanese", "britain", "british", " uk ", "china", "chinese",
    "eurozone", "germany", "german", "france", "french",
]
GOLD_EXPLICIT_TERMS = ["gold", "oro", "xau"]


def is_other_country_news(title):
    t = " " + title.lower() + " "
    has_other_country = any(term in t for term in OTHER_COUNTRY_TERMS)
    has_gold = any(term in t for term in GOLD_EXPLICIT_TERMS)
    return has_other_country and not has_gold


NEWS_STATE_FILE = "seen_ids.json"
WEEKLY_STATE_FILE = "seen_weekly.json"
NEWSLETTER_STATE_FILE = "seen_newsletter.json"

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
        items.append({"title": title, "description": description, "guid": guid, "link": link})
    return items


def translate_to_italian(text):
    if not text:
        return text
    params = urllib.parse.urlencode({
        "client": "gtx", "sl": "en", "tl": "it", "dt": "t", "q": text
    })
    url = f"https://translate.googleapis.com/translate_a/single?{params}"
    last_error = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            return "".join(seg[0] for seg in data[0])
        except Exception as e:
            last_error = e
            if attempt < 2:
                time.sleep(2)
    print(f"Errore traduzione dopo 3 tentativi, mando il titolo originale: {last_error}")
    return text


def is_question_title(title):
    # Titoli-domanda (es. "Will Warsh stop the dollar sell-off?") vengono scartati
    # sempre: il bot manda solo il titolo tradotto, mai il link/corpo dell'articolo,
    # quindi una domanda senza risposta arriva "monca" - William l'ha notato il
    # 25/8/2026 e ha chiesto di toglierle del tutto, senza eccezioni.
    return "?" in title


def matches_keywords(title):
    if is_question_title(title):
        return False
    if is_pure_casualty_news(title):
        return False
    if is_retrospective_news(title):
        return False
    if is_other_currency_news(title):
        return False
    if is_other_country_news(title):
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
        # le fonti inglesi usano spesso il trattino lungo/medio nei titoli
        # (es. "...  and here's what happened"): la traduzione lo porta dietro
        # cosi' com'e', e la regola del progetto lo vieta sempre, ovunque
        title_it = title_it.replace(" — ", ": ").replace(" – ", ": ").replace("—", "-").replace("–", "-")
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


def parse_event_datetime_italy(ev):
    """Converte data/ora dell'evento (fuso del feed, ET) in ora italiana."""
    t = ev["time"].lower()
    if "am" not in t and "pm" not in t:
        return None
    try:
        dt = datetime.strptime(f"{ev['date']} {ev['time']}", "%m-%d-%Y %I:%M%p")
        dt = dt.replace(tzinfo=CALENDAR_TZ)
        return dt.astimezone(ITALY_TZ)
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


def schedule_precise_alerts(grouped_full):
    """Crea, per ogni notizia della settimana, un allarme cron-job.org su misura
    che scatta esattamente 10 minuti prima (non un controllo a ripetizione).
    Chiamata una volta sola, quando esce il riepilogo del lunedi."""
    if not CRONJOB_API_KEY or not GH_TRIGGER_TOKEN:
        print("CRONJOB_API_KEY o GH_TRIGGER_TOKEN mancanti, salto la creazione degli allarmi precisi.")
        return

    headers = {"Authorization": f"Bearer {CRONJOB_API_KEY}", "Content-Type": "application/json"}

    for (_, _, simple_name), items in grouped_full.items():
        dt = items[0][1]
        alert_time = dt - timedelta(minutes=10)
        if alert_time <= datetime.now(ITALY_TZ):
            continue  # evento troppo vicino/gia' passato, non ha senso schedulare

        primary_ev, _ = pick_primary_event(items)
        forecast_txt = primary_ev["forecast"] if primary_ev["forecast"] else "non disponibile"
        previous_txt = primary_ev["previous"] if primary_ev["previous"] else ""

        body = json.dumps({
            "ref": "main",
            "inputs": {
                "event_name": simple_name,
                "forecast": forecast_txt,
                "previous": previous_txt,
            },
        })

        payload = {
            "job": {
                "title": f"Alert 10min - {simple_name} {alert_time.strftime('%d-%m %H:%M')}",
                "url": f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/alert_event.yml/dispatches",
                "enabled": True,
                "saveResponses": True,
                "requestMethod": 1,
                "extendedData": {
                    "headers": {
                        "Accept": "application/vnd.github+json",
                        "Authorization": f"token {GH_TRIGGER_TOKEN}",
                    },
                    "body": body,
                },
                "schedule": {
                    "timezone": "Europe/Rome",
                    "hours": [alert_time.hour],
                    "mdays": [alert_time.day],
                    "minutes": [alert_time.minute],
                    "months": [alert_time.month],
                    "wdays": [-1],
                },
            }
        }

        req = urllib.request.Request(
            "https://api.cron-job.org/jobs",
            data=json.dumps(payload).encode("utf-8"),
            method="PUT",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req) as resp:
                result = json.load(resp)
            print(f"Allarme creato per {simple_name} alle {alert_time.strftime('%d-%m %H:%M')}: job {result.get('jobId')}")
        except urllib.error.HTTPError as e:
            print(f"Errore creando l'allarme per {simple_name}: {e.code} {e.read()}")
        time.sleep(3)  # cron-job.org limita le creazioni troppo ravvicinate (429), pausa di sicurezza tra una e l'altra


def check_weekly_summary():
    """Ogni lunedi 7:00-7:14 ora italiana, riepilogo semplice della settimana USD."""
    now_italy = datetime.now(ITALY_TZ)
    # il workflow gira una volta all'ora al minuto :47 - quindi lunedi' l'unico
    # giro nella fascia 7:xx e' alle 7:47, non serve un controllo sui minuti
    if not (now_italy.weekday() == 0 and now_italy.hour == 7):
        return []

    already_sent = load_state(WEEKLY_STATE_FILE)
    week_key = now_italy.strftime("%Y-W%U")
    if week_key in already_sent:
        return []

    events = fetch_calendar_events()
    usd_events = [ev for ev in events if ev["country"] == "USD" and ev["impact"] == "High"]

    # raggruppa per (data, ora, nome semplificato) cosi CPI non compare 4 volte
    # tiene anche tutte le varianti (ev, dt) per poter recuperare forecast/previous dopo
    grouped_full = defaultdict(list)
    for ev in usd_events:
        dt = parse_event_datetime_italy(ev)
        if dt is None:
            continue
        simple_name = simplify_name(ev["title"])
        key = (dt.date(), dt.strftime("%H:%M"), simple_name)
        grouped_full[key].append((ev, dt))

    grouped = {key: items[0][1] for key, items in grouped_full.items()}
    ordered = sorted(grouped.items(), key=lambda kv: kv[1])

    schedule_precise_alerts(grouped_full)

    monday = now_italy - timedelta(days=now_italy.weekday())
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
            "User-Agent": "NexusGoldOneBot (https://nexusgoldone.com, 1.0)",
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
    """Riepilogo settimanale USD - gira una volta all'ora, di fatto agisce solo
    il lunedi nell'ora 7 (workflow calendar.yml). Il countdown a 10 minuti fissi
    NON passa piu' da qui (vedi check_calendar_countdown, non piu' chiamata):
    e' sostituito da allarmi precisi creati uno per uno da schedule_precise_alerts,
    che scattano da soli via il workflow alert_event.yml esattamente all'orario giusto."""
    weekly_lines = check_weekly_summary()
    for msg in weekly_lines:
        post_to_discord(DISCORD_CHANNEL_ID_USD, msg)
    if not weekly_lines:
        print("Nessun riepilogo settimanale da mandare ora.")


XAU_CAMPAIGN_RE = re.compile(r"^Aggiornamento XAUUSD (.+)$")
EDIZIONE_CAMPAIGN_RE = re.compile(r"^(.+) - (\d+)$")
BONUS_CAMPAIGN_RE = re.compile(r"^Bonus (\d+) - (.+)$")


def get_gdrive_access_token():
    data = urllib.parse.urlencode({
        "client_id": GDRIVE_CLIENT_ID,
        "client_secret": GDRIVE_CLIENT_SECRET,
        "refresh_token": GDRIVE_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data, method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)["access_token"]


def gdrive_list_files(access_token, folder_id):
    q = urllib.parse.quote(f"'{folder_id}' in parents and trashed=false")
    url = f"https://www.googleapis.com/drive/v3/files?q={q}&fields=files(id,name)&pageSize=1000"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    with urllib.request.urlopen(req) as resp:
        return json.load(resp).get("files", [])


def gdrive_upload_pdf(access_token, pdf_bytes, filename, folder_id):
    metadata = {"name": filename, "parents": [folder_id]}
    boundary = "-------nexusgoldone-pdf-boundary"
    body = (
        f"--{boundary}\r\n"
        "Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(metadata)}\r\n"
        f"--{boundary}\r\n"
        "Content-Type: application/pdf\r\n\r\n"
    ).encode() + pdf_bytes + f"\r\n--{boundary}--".encode()
    req = urllib.request.Request(
        "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
        data=body, method="POST",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": f"multipart/related; boundary={boundary}"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def generate_pdf_from_html(html_content):
    """Stessa tecnica di pdf-agent/pdf_agent.py (viewport 600px, altezza dinamica,
    PDF a pagina singola continua) - qui via Playwright installato nel runner GitHub Actions."""
    from playwright.sync_api import sync_playwright

    tmp_path = "_tmp_campaign.html"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    output_path = "_tmp_campaign.pdf"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 600, "height": 1000})
            page.goto(f"file://{os.path.abspath(tmp_path)}")
            page.wait_for_load_state("networkidle")
            content_height = page.evaluate(
                """
                () => {
                    const bh = document.body.scrollHeight;
                    const dh = document.documentElement.scrollHeight;
                    let maxBottom = 0;
                    document.querySelectorAll('*').forEach(el => {
                        const r = el.getBoundingClientRect();
                        if (r.bottom > maxBottom) maxBottom = r.bottom;
                    });
                    return Math.max(bh, dh, maxBottom);
                }
                """
            ) + 2
            page.pdf(
                path=output_path, width="600px", height=f"{content_height}px",
                print_background=True, margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
            )
            browser.close()
        with open(output_path, "rb") as f:
            return f.read()
    finally:
        for p in (tmp_path, output_path):
            if os.path.exists(p):
                os.remove(p)


def genera_e_carica_pdf(campaign_id, campaign_name):
    """Genera il PDF di una campagna Brevo e lo carica nella cartella Drive giusta,
    capendo dal nome campagna se e' un'edizione numerata o la serie ad-hoc XAUUSD."""
    if not (GDRIVE_CLIENT_ID and GDRIVE_CLIENT_SECRET and GDRIVE_REFRESH_TOKEN):
        print("Credenziali Google Drive mancanti, salto la generazione PDF.")
        return

    m_xau = XAU_CAMPAIGN_RE.match(campaign_name)
    m_ed = EDIZIONE_CAMPAIGN_RE.match(campaign_name)
    if not m_xau and not m_ed:
        print(f"Nome campagna '{campaign_name}' non riconosciuto (ne' 'Aggiornamento XAUUSD ...' ne' '... - Edizione #NN'), salto il PDF.")
        return

    req = urllib.request.Request(
        f"https://api.brevo.com/v3/emailCampaigns/{campaign_id}",
        headers={"api-key": BREVO_API_KEY, "accept": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        campaign = json.load(resp)
    html_content = campaign["htmlContent"]

    access_token = get_gdrive_access_token()

    if m_xau:
        data_str = m_xau.group(1)
        folder_id = GDRIVE_FOLDER_XAUUSD
        esistenti = gdrive_list_files(access_token, folder_id)
        numero = len(esistenti) + 1
        filename = f"{numero}. Analisi del {data_str}.pdf"
    else:
        titolo, numero_str = m_ed.group(1), m_ed.group(2)
        folder_id = GDRIVE_FOLDER_NEWSLETTER
        filename = f"{int(numero_str)}. {titolo}.pdf"

    pdf_bytes = generate_pdf_from_html(html_content)
    result = gdrive_upload_pdf(access_token, pdf_bytes, filename, folder_id)
    print(f"PDF caricato su Drive: {filename} (id {result.get('id')})")


def check_newsletter():
    """Controlla via API Brevo le campagne realmente inviate (`status=sent`) e
    ritorna quelle non ancora annunciate su Discord (confrontate con
    NEWSLETTER_STATE_FILE). Gira sul repo GitHub, non su una sessione/PC di
    William, cosi' l'annuncio parte da solo anche a Mac spento."""
    if not BREVO_API_KEY or not DISCORD_CHANNEL_ID_NEWSLETTER:
        print("BREVO_API_KEY o DISCORD_CHANNEL_ID_NEWSLETTER mancanti, salto il controllo newsletter.")
        return []

    url = "https://api.brevo.com/v3/emailCampaigns?status=sent&limit=20&sort=desc"
    req = urllib.request.Request(url, headers={"api-key": BREVO_API_KEY, "accept": "application/json"})
    with urllib.request.urlopen(req) as resp:
        data = json.load(resp)

    already_sent = load_state(NEWSLETTER_STATE_FILE)
    nuove = [c for c in data.get("campaigns", []) if str(c["id"]) not in already_sent]
    if not nuove:
        return []

    messaggi = []
    for c in nuove:
        nome = c["name"]
        oggetto = c.get("subject", "")
        messaggi.append((c["id"], nome, f"📰 Uscita Newsletter: {nome}\n{oggetto}"))

    save_state(NEWSLETTER_STATE_FILE, already_sent | {str(c["id"]) for c in nuove})
    return messaggi


def run_newsletter():
    """Annuncio automatico su #newsletter + generazione/upload PDF su Drive,
    quando una campagna Brevo risulta davvero inviata (workflow newsletter.yml,
    girato via cron-job.org - nessuna dipendenza da Mac/sessione aperta)."""
    messaggi = check_newsletter()
    for campaign_id, campaign_name, msg in messaggi:
        post_to_discord(DISCORD_CHANNEL_ID_NEWSLETTER, msg)
        print(f"Annunciata su Discord campagna {campaign_id}")
        try:
            genera_e_carica_pdf(campaign_id, campaign_name)
        except Exception as e:
            print(f"Errore generando/caricando il PDF per la campagna {campaign_id}: {e}")
    if not messaggi:
        print("Nessuna nuova campagna da annunciare.")


def run_alert_event():
    """Manda l'annuncio a 10 minuti fissi per UNA singola notizia (workflow alert_event.yml,
    attivato da un allarme cron-job.org creato su misura da schedule_precise_alerts)."""
    event_name = os.environ["EVENT_NAME"]
    forecast_txt = os.environ.get("EVENT_FORECAST", "non disponibile")
    previous_raw = os.environ.get("EVENT_PREVIOUS", "")

    if forecast_txt and forecast_txt != "non disponibile":
        previous_txt = f" (precedente {previous_raw})" if previous_raw else ""
        msg = f"⏰ -10 Minuti al {event_name}\nEcco i dati previsti: {forecast_txt}{previous_txt}"
    else:
        msg = f"⏰ -10 Minuti al {event_name}"

    post_to_discord(DISCORD_CHANNEL_ID_USD, msg)


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode == "news":
        run_news()
    elif mode == "calendar":
        run_calendar()
    elif mode == "alert_event":
        run_alert_event()
    elif mode == "newsletter":
        run_newsletter()
    else:
        run_news()
        run_calendar()
