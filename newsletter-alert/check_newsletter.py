#!/usr/bin/env python3
"""
Annuncio automatico su Discord (#newsletter) quando Brevo manda davvero
un'email (qualsiasi campagna, non solo le edizioni numerate).

Gira via GitHub Actions, triggerato esternamente da cron-job.org
(workflow_dispatch, MAI schedule: interno - vedi il bug del 21/7/2026 sugli
altri due workflow di questo stesso repo).

Stato: seen_campaigns.json, stesso schema di seen_ids.json in
github-xauusd-alert (lista di ID già annunciati, committata su git ad ogni
giro cosi' i riavvii del workflow non perdono la memoria).
"""

import os
import re
import json
import urllib.request

# toglie il suffisso amministrativo Brevo tipo " - Edizione #15" o " - 20",
# lasciando solo il nome pulito dell'argomento (es. "Recap e informazioni")
EDITION_SUFFIX_RE = re.compile(r"\s*-\s*(Edizione\s*#?\d+|\d+)\s*$", re.IGNORECASE)


def clean_title(name):
    return EDITION_SUFFIX_RE.sub("", name).strip()

BREVO_API_KEY = os.environ["BREVO_API_KEY"]
DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DISCORD_CHANNEL_ID_NEWSLETTER = os.environ["DISCORD_CHANNEL_ID_NEWSLETTER"]

STATE_FILE = "seen_campaigns.json"


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return set(json.load(f))
    return set()


def save_state(values):
    with open(STATE_FILE, "w") as f:
        json.dump(list(values)[-500:], f)


def fetch_sent_campaigns():
    url = "https://api.brevo.com/v3/emailCampaigns?status=sent&limit=50&offset=0&sort=desc"
    req = urllib.request.Request(url, headers={
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode())
    return data.get("campaigns", [])


def post_to_discord(content):
    url = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID_NEWSLETTER}/messages"
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


def run():
    seen = load_state()
    campaigns = fetch_sent_campaigns()

    # prima esecuzione in assoluto (nessuno stato salvato ancora): non annuncia
    # nulla, si limita a registrare come "gia' visto" tutto cio' che trova, per
    # non bombardare il canale con lo storico di email gia' mandate in passato
    first_run = not os.path.exists(STATE_FILE)

    new_ones = [c for c in campaigns if c["id"] not in seen]
    seen |= {c["id"] for c in campaigns}
    save_state(seen)

    if first_run:
        print(f"Primo avvio: registrate {len(seen)} campagne esistenti, nessun annuncio.")
        return

    if not new_ones:
        print("Nessuna nuova email inviata da annunciare.")
        return

    # ordine cronologico (dalla piu' vecchia alla piu' nuova tra quelle nuove)
    for c in sorted(new_ones, key=lambda c: c.get("sentDate") or ""):
        title = clean_title(c.get("name") or c.get("subject") or "Nuova email")
        post_to_discord(f'📰 È uscita una nuova email: "{title}"')


if __name__ == "__main__":
    run()
