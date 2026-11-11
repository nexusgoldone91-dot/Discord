#!/usr/bin/env python3
"""
Benvenuto automatico membri Discord - controllo periodico (GitHub Actions).
Sostituisce il vecchio bot sempre connesso (Gateway) con un controllo
periodico: lista i membri attuali, confronta con seen_members.json,
e da' il benvenuto a chi e' nuovo. Nessuna connessione persistente richiesta.
"""

import hashlib
import hmac
import json
import os
import random
import urllib.request

DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DISCORD_GUILD_ID = os.environ["DISCORD_GUILD_ID"]
DISCORD_CHANNEL_ID_BENVENUTO = os.environ["DISCORD_CHANNEL_ID_BENVENUTO"]

USER_AGENT = "NexusGoldOne-WelcomeBot/1.0 (https://nexusgoldone.com)"
SEEN_FILE = "seen_members.json"
EMOJI_CHOICES = ["👋", "🚀", "📈", "💛", "⭐"]


def get_all_members():
    members = []
    after = "0"
    while True:
        req = urllib.request.Request(
            f"https://discord.com/api/v10/guilds/{DISCORD_GUILD_ID}/members?limit=1000&after={after}",
            headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(req) as resp:
            batch = json.loads(resp.read())
        if not batch:
            break
        members.extend(batch)
        if len(batch) < 1000:
            break
        after = batch[-1]["user"]["id"]
    return members


def anon(user_id):
    """Impronta anonima dell'ID (HMAC con una chiave segreta): il repo e' pubblico e
    non deve contenere gli ID reali dei membri (decisione di William, 11/11/2026)."""
    key = (DISCORD_GUILD_ID + ":nexus-welcome").encode()
    return hmac.new(key, str(user_id).encode(), hashlib.sha256).hexdigest()


def load_seen():
    """Restituisce (insieme di impronte, True se il file conteneva ancora ID in chiaro)."""
    if not os.path.exists(SEEN_FILE):
        return set(), False
    with open(SEEN_FILE) as f:
        raw = json.load(f)
    migrato = False
    seen = set()
    for x in raw:
        if str(x).isdigit():      # vecchio formato: ID in chiaro, lo converto
            seen.add(anon(x)); migrato = True
        else:
            seen.add(x)
    return seen, migrato


def save_seen(seen_ids):
    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(seen_ids), f, indent=2)


def send_welcome(user_id):
    message = f"Ciao <@{user_id}>, benvenuto sul server ufficiale di NexusGoldOne {random.choice(EMOJI_CHOICES)}"
    payload = json.dumps({"content": message}).encode()
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID_BENVENUTO}/messages",
        data=payload, method="POST",
        headers={
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req) as resp:
        return resp.status


def main():
    members = get_all_members()
    current = {anon(m["user"]["id"]): m["user"]["id"] for m in members if not m["user"].get("bot")}
    seen_ids, migrato = load_seen()

    new_hashes = set(current) - seen_ids
    for h in new_hashes:
        send_welcome(current[h])
        print("Benvenuto inviato a un nuovo membro")   # mai stampare l'ID: i log sono pubblici

    if new_hashes or migrato:
        save_seen(set(current))
        print(f"{len(new_hashes)} nuovo/i membro/i accolto/i, stato aggiornato (migrato: {migrato}).")
    else:
        print("Nessun nuovo membro.")


if __name__ == "__main__":
    main()
