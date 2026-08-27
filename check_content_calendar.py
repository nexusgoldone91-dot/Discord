#!/usr/bin/env python3
"""
Controllo automatico calendario contenuti (email + Instagram) - NexusGoldOne.
Deciso con William il 27/8/2026: dalla settimana del 1/9 in poi, ogni uscita
(email o contenuto Instagram) va segnata sul Google Calendar con un prefisso
fisso nel titolo:
- "Email: <titolo>" per le edizioni newsletter
- "IG: <tipo> <titolo>" per reel/caroselli/post Instagram (es. "IG: Reel FOMO")

Gira ogni ~5 minuti (workflow content_calendar.yml, cron-job.org, indipendente
da sessioni/PC di William). Per ogni evento "Email:"/"IG:" il cui orario di
inizio e' passato da almeno 10 minuti e non ancora controllato, verifica se
il contenuto e' comparso davvero:
- Email: cerca una campagna Brevo "sent" con sentDate vicino all'orario
  dell'evento (finestra +-20 minuti)
- Instagram: cerca un media pubblicato sul profilo (Graph API) con timestamp
  vicino all'orario dell'evento (finestra fino a +30 minuti dopo, i post non
  compaiono mai prima dell'orario previsto)

Se lo trova, segna l'evento come controllato, silenzio. Se non lo trova,
avvisa Jonny su Telegram UNA volta sola per quell'evento (mai piu' di un
avviso per lo stesso evento), poi lo segna comunque come controllato.

QUALSIASI ALTRO evento (chiamate segnate col nome della persona, cose da
fare, senza il prefisso "Email:"/"IG:") riceve invece un promemoria 30
minuti PRIMA dell'orario di inizio, col titolo dell'evento relayato cosi'
com'e' (deciso il 27/8/2026, William: "se e' da fare leggo cosa fare, e se
e' una persona leggo il nome").
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

GCAL_CLIENT_ID = os.environ["GCAL_CLIENT_ID"]
GCAL_CLIENT_SECRET = os.environ["GCAL_CLIENT_SECRET"]
GCAL_REFRESH_TOKEN = os.environ["GCAL_REFRESH_TOKEN"]
GCAL_CALENDAR_ID = os.environ.get("GCAL_CALENDAR_ID", "primary")

BREVO_API_KEY = os.environ.get("BREVO_API_KEY")

IG_ACCESS_TOKEN = os.environ.get("IG_ACCESS_TOKEN")
IG_BUSINESS_ACCOUNT_ID = os.environ.get("IG_BUSINESS_ACCOUNT_ID")

JONNY_BOT_TOKEN = os.environ.get("JONNY_BOT_TOKEN")
JONNY_CHAT_ID = os.environ.get("JONNY_CHAT_ID")

STATE_FILE = "seen_calendar_checks.json"


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return set(json.load(f))
    return set()


def save_state(values):
    with open(STATE_FILE, "w") as f:
        json.dump(list(values)[-1000:], f)


def send_jonny_alert(text):
    if not JONNY_BOT_TOKEN or not JONNY_CHAT_ID:
        return
    payload = json.dumps({"chat_id": JONNY_CHAT_ID, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{JONNY_BOT_TOKEN}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception:
        pass


def get_gcal_access_token():
    data = urllib.parse.urlencode({
        "client_id": GCAL_CLIENT_ID,
        "client_secret": GCAL_CLIENT_SECRET,
        "refresh_token": GCAL_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data, method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)["access_token"]


def get_recent_events(access_token, hours_back=3, hours_forward=1):
    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(hours=hours_back)).isoformat().replace("+00:00", "Z")
    time_max = (now + timedelta(hours=hours_forward)).isoformat().replace("+00:00", "Z")
    cal_id = urllib.parse.quote(GCAL_CALENDAR_ID, safe="")
    url = (
        f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
        f"?timeMin={time_min}&timeMax={time_max}&singleEvents=true&orderBy=startTime"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    with urllib.request.urlopen(req) as resp:
        data = json.load(resp)
    return data.get("items", [])


def check_email_published(event_start):
    """None = non verificabile (credenziali mancanti), True/False = esito reale."""
    if not BREVO_API_KEY:
        return None
    url = "https://api.brevo.com/v3/emailCampaigns?status=sent&limit=20&sort=desc"
    req = urllib.request.Request(url, headers={"api-key": BREVO_API_KEY, "accept": "application/json"})
    with urllib.request.urlopen(req) as resp:
        data = json.load(resp)
    for c in data.get("campaigns", []):
        sent_date_raw = c.get("sentDate")
        if not sent_date_raw:
            continue
        try:
            sent_dt = datetime.fromisoformat(sent_date_raw.replace("Z", "+00:00"))
        except Exception:
            continue
        if abs((sent_dt - event_start).total_seconds()) <= 20 * 60:
            return True
    return False


def check_instagram_published(event_start):
    """None = non verificabile (credenziali mancanti), True/False = esito reale."""
    if not IG_ACCESS_TOKEN or not IG_BUSINESS_ACCOUNT_ID:
        return None
    url = (
        f"https://graph.instagram.com/{IG_BUSINESS_ACCOUNT_ID}/media"
        f"?fields=timestamp&limit=10&access_token={IG_ACCESS_TOKEN}"
    )
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as resp:
        data = json.load(resp)
    for m in data.get("data", []):
        ts_raw = m.get("timestamp")
        if not ts_raw:
            continue
        try:
            ts = datetime.fromisoformat(ts_raw)
        except Exception:
            continue
        if event_start <= ts <= event_start + timedelta(minutes=30):
            return True
    return False


def main():
    try:
        access_token = get_gcal_access_token()
        events = get_recent_events(access_token)
    except Exception as e:
        print(f"Errore leggendo il calendario: {e}")
        return

    checked = load_state()
    now = datetime.now(timezone.utc)
    novita = False

    for ev in events:
        title = ev.get("summary", "") or ""
        event_id = ev.get("id")
        if not event_id or event_id in checked:
            continue

        start_raw = ev.get("start", {}).get("dateTime")
        if not start_raw:
            continue  # evento senza orario preciso (tutto il giorno), salta

        try:
            event_start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
        except Exception:
            continue

        if title.startswith("Email:") or title.startswith("IG:"):
            minuti_passati = (now - event_start).total_seconds() / 60
            if minuti_passati < 10:
                continue  # non ancora il momento di controllare

            if title.startswith("Email:"):
                esito = check_email_published(event_start)
                tipo = "email"
            else:
                esito = check_instagram_published(event_start)
                tipo = "contenuto Instagram"

            if esito is None:
                print(f"Evento '{title}': credenziali mancanti per verificarlo, ritento al prossimo giro.")
                continue  # non segnato come controllato, ritenta al giro dopo

            novita = True
            if esito:
                print(f"Evento '{title}': pubblicato correttamente.")
            else:
                print(f"Evento '{title}': NON risulta pubblicato, avviso Jonny.")
                send_jonny_alert(
                    f"Jonny qui. Non vedo ancora pubblicato: \"{title}\" "
                    f"({tipo}, previsto per le {event_start.astimezone().strftime('%H:%M')})."
                )
            checked.add(event_id)

        else:
            # Promemoria generico (chiamate con nome persona, cose da fare):
            # avviso 30 minuti PRIMA dell'orario del calendario, titolo relayato cosi' com'e'.
            minuti_a_evento = (event_start - now).total_seconds() / 60
            if 25 <= minuti_a_evento <= 35:
                novita = True
                print(f"Promemoria per '{title}' (tra mezz'ora).")
                send_jonny_alert(f"Jonny qui. Ti ricordo: {title}, tra mezz'ora.")
                checked.add(event_id)
            # fuori dalla finestra dei 30 minuti: lasciato non segnato, si ricontrolla al giro dopo

    if not novita:
        print("Nessun evento nuovo da controllare in questo giro.")

    save_state(checked)


if __name__ == "__main__":
    main()
