#!/usr/bin/env python3
"""
Pubblica un contenuto (reel o immagine) su Instagram via Graph API diretta
(graph.instagram.com), in due passaggi: crea il media container (video_url/
image_url + cover_url + caption), attende che sia FINISHED, poi lo pubblica
con media_publish.

Pensato per essere lanciato vicino al momento di pubblicazione reale (il
container scade dopo 24h) da un job GitHub Actions attivato da un allarme
cron-job.org creato su misura per data/ora esatte - stesso schema di
schedule_precise_alerts() in check_news.py (Trading Claude/github-xauusd-alert).

Riusabile per ogni reel: legge i parametri da variabili d'ambiente (impostate
come "inputs" nel workflow_dispatch), non hardcoded per un singolo contenuto.

Env richieste:
  IG_ACCESS_TOKEN            token Graph API (meta-agent/graph_api_credentials.json)
  IG_BUSINESS_ACCOUNT_ID     id account business Instagram
  VIDEO_URL                  URL pubblico del video/immagine (Google Drive)
  COVER_URL                  URL pubblico della copertina (opzionale, solo REELS)
  CAPTION                    caption del post
  MEDIA_TYPE                 default REELS (anche IMAGE)
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

GRAPH_BASE = "https://graph.instagram.com/v21.0"
POLL_INTERVAL_SECONDS = 10
POLL_MAX_ATTEMPTS = 30  # ~5 minuti di attesa massima


def _post(url, params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def _get(url):
    with urllib.request.urlopen(url) as resp:
        return json.load(resp)


def create_container(ig_id, token, media_type, video_url, cover_url, caption):
    params = {
        "caption": caption,
        "access_token": token,
    }
    if media_type == "REELS":
        params["media_type"] = "REELS"
        params["video_url"] = video_url
        if cover_url:
            params["cover_url"] = cover_url
    elif media_type == "IMAGE":
        params["image_url"] = video_url
    else:
        raise ValueError(f"media_type non gestito: {media_type}")

    result = _post(f"{GRAPH_BASE}/{ig_id}/media", params)
    if "id" not in result:
        raise RuntimeError(f"Creazione container fallita: {result}")
    return result["id"]


def wait_until_finished(container_id, token):
    for attempt in range(POLL_MAX_ATTEMPTS):
        url = f"{GRAPH_BASE}/{container_id}?fields=status_code,status&access_token={token}"
        d = _get(url)
        status = d.get("status_code")
        print(f"Tentativo {attempt + 1}: status_code={status}")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(f"Container in errore: {d}")
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError("Il container non ha raggiunto FINISHED entro il timeout.")


def publish(ig_id, token, container_id):
    result = _post(f"{GRAPH_BASE}/{ig_id}/media_publish", {
        "creation_id": container_id,
        "access_token": token,
    })
    if "id" not in result:
        raise RuntimeError(f"Pubblicazione fallita: {result}")
    return result["id"]


def main():
    token = os.environ["IG_ACCESS_TOKEN"]
    ig_id = os.environ["IG_BUSINESS_ACCOUNT_ID"]
    video_url = os.environ["VIDEO_URL"]
    cover_url = os.environ.get("COVER_URL", "")
    caption = os.environ["CAPTION"]
    media_type = os.environ.get("MEDIA_TYPE", "REELS")

    print(f"Creo il container ({media_type})...")
    container_id = create_container(ig_id, token, media_type, video_url, cover_url, caption)
    print(f"Container creato: {container_id}")

    print("Attendo che il container sia pronto (status_code=FINISHED)...")
    wait_until_finished(container_id, token)

    print("Pubblico...")
    media_id = publish(ig_id, token, container_id)
    print(f"PUBBLICATO. media_id={media_id}")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        print(f"ERRORE HTTP: {e.code} {e.read().decode()}")
        sys.exit(1)
    except Exception as e:
        print(f"ERRORE: {e}")
        sys.exit(1)
