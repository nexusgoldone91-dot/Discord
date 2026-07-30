#!/usr/bin/env python3
"""
Benvenuto automatico membri Discord - controllo periodico (GitHub Actions).
Sostituisce il vecchio bot sempre connesso (Gateway) con un controllo
periodico: lista i membri attuali, confronta con seen_members.json,
e da' il benvenuto a chi e' nuovo. Nessuna connessione persistente richiesta.
"""

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


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    return set()


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
    current_ids = {m["user"]["id"] for m in members if not m["user"].get("bot")}
    seen_ids = load_seen()

    new_ids = current_ids - seen_ids
    for user_id in new_ids:
        send_welcome(user_id)
        print(f"Benvenuto inviato a {user_id}")

    if new_ids:
        save_seen(current_ids)
        print(f"{len(new_ids)} nuovo/i membro/i accolto/i, stato aggiornato.")
    else:
        print("Nessun nuovo membro.")


if __name__ == "__main__":
    main()
