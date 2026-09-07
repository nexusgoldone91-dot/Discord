#!/usr/bin/env python3
"""
Controllo automatico calendario contenuti Instagram - NexusGoldOne.

Ristretto il 28/8/2026 su richiesta esplicita di William: SOLO i contenuti
Instagram, NIENTE PIU' email/newsletter (quelle le controlla lui) e NIENTE
PIU' promemoria generico per gli altri eventi del calendario (chiamate, cose
da fare, Live Session, ecc.) - comportamento vecchio, disattivato apposta,
non riattivarlo senza una richiesta nuova di William.

Esteso il 7/9/2026 su richiesta di William: oltre a "Reel Instagram" ora
gestisce anche gli eventi con "Post Instagram" nel titolo. Gli eventi reali
hanno titolo tipo "(emoji) Reel Instagram - <nome>" e
"(emoji) Post Instagram - <nome>"; il match e' su quelle due stringhe
ovunque nel titolo, non un prefisso esatto.

Gira ogni ~5 minuti (workflow content_calendar.yml, cron-job.org).

Cosa fa per ciascun tipo:

- "Reel Instagram" (programmati su TikTok via Buffer): SOLO controllo dopo.
  Se l'orario di inizio e' passato da almeno 10 minuti e l'evento non e'
  ancora stato controllato, verifica via Instagram Graph API se il reel e'
  comparso davvero (media con timestamp tra l'orario evento e +30 minuti).
  Se non lo trova, avvisa Jonny su Telegram UNA volta sola per quell'evento.
  Nessun promemoria "pre".

- "Post Instagram" (post-foto pubblicati a mano da William, da mettere a mano
  sia su Instagram sia su TikTok):
  a) Promemoria PRE: quando mancano tra 30 e 25 minuti all'orario di inizio
     (finestra -30..-25 min, larga abbastanza da non perdersi tra un giro e
     l'altro dello scheduler), manda UNA volta a Jonny un promemoria di
     pubblicare a mano. Se nella "description" dell'evento c'e' una riga che
     inizia con "NOTA JONNY:", il testo dopo i due punti viene aggiunto in
     fondo al messaggio.
  b) Controllo DOPO: identico a quello dei reel.

Stato "gia' visto" in seen_calendar_checks.json. Le chiavi distinguono i due
trigger cosi' non si sovrascrivono: "<event_id>:pre" per il promemoria,
"<event_id>:post" per il controllo. Le vecchie voci sono id nudi (erano solo
controlli-dopo di reel) e vengono ancora riconosciute come "<event_id>:post".
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

IG_ACCESS_TOKEN = os.environ.get("IG_ACCESS_TOKEN")
IG_BUSINESS_ACCOUNT_ID = os.environ.get("IG_BUSINESS_ACCOUNT_ID")

JONNY_BOT_TOKEN = os.environ.get("JONNY_BOT_TOKEN")
JONNY_CHAT_ID = os.environ.get("JONNY_CHAT_ID")

STATE_FILE = "seen_calendar_checks.json"

REEL_MATCH = "Reel Instagram"
POST_MATCH = "Post Instagram"

NOTA_JONNY_PREFIX = "NOTA JONNY:"


def extract_nota_jonny(description):
    """Cerca nella descrizione dell'evento una riga 'NOTA JONNY: ...' e ne
    ritorna il testo dopo i due punti (None se assente/vuota)."""
    if not description:
        return None
    for line in description.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith(NOTA_JONNY_PREFIX):
            nota = stripped[len(NOTA_JONNY_PREFIX):].strip()
            return nota or None
    return None


def build_pre_reminder(title, nota=None):
    msg = (
        f'Jonny qui. Tra mezz\'ora tocca il post: "{title}". '
        f'Pubblica a mano su Instagram e TikTok.'
    )
    if nota:
        msg += f" {nota}"
    return msg


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


def parse_event_start(ev):
    start_raw = ev.get("start", {}).get("dateTime")
    if not start_raw:
        return None  # evento senza orario preciso (tutto il giorno), salta
    try:
        return datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
    except Exception:
        return None


def plan_actions(ev, now, checked):
    """Logica pura, senza effetti collaterali: decide cosa fare per un evento.

    Ritorna una lista di tuple:
      ("pre_reminder", event_id, title, nota)   # nota = testo NOTA JONNY o None
      ("post_check", event_id, title, event_start)
    Lista vuota = niente da fare per questo evento in questo giro.
    """
    actions = []
    title = ev.get("summary", "") or ""
    event_id = ev.get("id")
    if not event_id:
        return actions

    is_reel = REEL_MATCH in title
    is_post = POST_MATCH in title
    if not (is_reel or is_post):
        return actions  # SOLO "Reel Instagram" e "Post Instagram", il resto ignorato di proposito

    event_start = parse_event_start(ev)
    if event_start is None:
        return actions

    minuti_delta = (now - event_start).total_seconds() / 60  # negativo = evento ancora nel futuro

    # Promemoria PRE: solo "Post Instagram", finestra -30..-25 min rispetto allo start
    if is_post:
        pre_key = f"{event_id}:pre"
        if pre_key not in checked and -30 <= minuti_delta <= -25:
            nota = extract_nota_jonny(ev.get("description", ""))
            actions.append(("pre_reminder", event_id, title, nota))

    # Controllo DOPO: reel e post, uguale per entrambi
    post_key = f"{event_id}:post"
    already_post_checked = post_key in checked or event_id in checked  # event_id nudo = stato legacy dei reel
    if not already_post_checked and minuti_delta >= 10:
        actions.append(("post_check", event_id, title, event_start))

    return actions


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
        for action in plan_actions(ev, now, checked):
            kind = action[0]

            if kind == "pre_reminder":
                _, event_id, title, nota = action
                send_jonny_alert(build_pre_reminder(title, nota))
                print(f"Evento '{title}': promemoria pre-pubblicazione inviato a Jonny.")
                checked.add(f"{event_id}:pre")
                novita = True

            elif kind == "post_check":
                _, event_id, title, event_start = action
                esito = check_instagram_published(event_start)
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
                        f"(previsto per le {event_start.astimezone().strftime('%H:%M')})."
                    )
                checked.add(f"{event_id}:post")

    if not novita:
        print("Nessun evento nuovo da controllare in questo giro.")

    save_state(checked)


if __name__ == "__main__":
    main()
