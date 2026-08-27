#!/usr/bin/env python3
"""
Reparto di controllo unico NexusGoldOne (deciso 27/7/2026: un solo posto che
controlla tutto, invece di più agenti/monitor separati). Gira su GitHub Actions
(workflow control.yml), nessuna dipendenza da Mac/sessione Claude Code aperta.

Controlla in un solo giro:
1. Landing page (link, anchor, sicurezza, PageSpeed best-effort) - sostituisce landing.yml
2. Discord (bot online, canali raggiungibili)
3. Brevo (campagne "queued" il cui scheduledAt e' passato ma non risultano inviate: invio bloccato)
4. cron-job.org (tutti i job ricorrenti attesi sono abilitati e hanno girato di recente)

Scrive l'esito in control_status.json, committato nel repo - letto a inizio
sessione Claude Code (vedi Desktop/CLAUDE.md).
"""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

STATE_FILE = "control_status.json"

# ---------- 1. LANDING PAGE ----------

SITE_URL = "https://nexusgoldone.com/"
ANCHOR_IDS = ["coaching-card", "discord-card", "newsletter-card", "testimonianze"]
EXTERNAL_LINKS = [
    "https://discord.gg/s7FqjtxYmc",
    "https://wa.me/393780116402",
    "https://www.instagram.com/william.bezzon",
]


def check_landing(issues):
    start = time.time()
    try:
        req = urllib.request.Request(SITE_URL, headers={"User-Agent": "Mozilla/5.0 (NexusGoldOneMonitor/1.0)"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            elapsed = time.time() - start
            status = resp.status
            headers = dict(resp.getheaders())
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        issues.append(f"[Landing] Sito irraggiungibile: {e}")
        return

    if status != 200:
        issues.append(f"[Landing] Homepage risponde {status} invece di 200")
    if elapsed > 5:
        issues.append(f"[Landing] Homepage lenta: {elapsed:.1f}s (soglia 5s)")
    if "Strict-Transport-Security" not in headers:
        issues.append("[Landing] Header HSTS mancante")
    if 'lang="it"' not in html and "lang='it'" not in html:
        issues.append('[Landing] <html lang="it"> non trovato')
    mixed_content = re.findall(r'(?:src|href)="http://[^"]+"', html)
    if mixed_content:
        issues.append(f"[Landing] Mixed content trovato: {mixed_content[:3]}")

    for anchor_id in ANCHOR_IDS:
        if f'id="{anchor_id}"' not in html:
            issues.append(f"[Landing] Anchor #{anchor_id} referenziato ma id non trovato")

    for url in EXTERNAL_LINKS:
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0 (NexusGoldOneMonitor/1.0)"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status >= 400:
                    issues.append(f"[Landing] Link esterno {url} risponde {resp.status}")
        except urllib.error.HTTPError as e:
            # 405 = HEAD non supportato, non e' un errore del link
            # 429 = rate limit (Instagram blocca aggressivamente le richieste automatiche, non vuol dire che il link e' rotto)
            if e.code >= 400 and e.code not in (405, 429):
                issues.append(f"[Landing] Link esterno {url} risponde {e.code}")
        except Exception as e:
            issues.append(f"[Landing] Link esterno {url} non raggiungibile: {e}")

    # PageSpeed: best-effort, mai bloccante (quota anonima spesso esaurita)
    try:
        ps_url = (
            "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
            f"?url={urllib.parse.quote(SITE_URL, safe='')}&strategy=mobile&category=performance"
        )
        with urllib.request.urlopen(ps_url, timeout=60) as resp:
            data = json.load(resp)
        score = data.get("lighthouseResult", {}).get("categories", {}).get("performance", {}).get("score")
        if score is not None and score < 0.5:
            issues.append(f"[Landing] PageSpeed mobile basso: punteggio {score}")
    except Exception:
        pass  # non bloccante


# ---------- 2. DISCORD ----------

DISCORD_CHANNELS = {
    "benvenuto": "1528730184423964814",
    "info-contatti": "1528730186818781275",
    "newsletter": "1528730188836507879",
    "info-dal-mondo": "1528730192342679643",
    "analisi-e-confronto": "1528730194008080548",
    "calendario-news": "1528796259366993921",
    "le-basi-del-percorso": "1531320711489261689",
    "pillole-di-willy": "1531382028623417466",
}


def check_discord(issues):
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        issues.append("[Discord] DISCORD_BOT_TOKEN mancante, salto il controllo")
        return
    headers = {"Authorization": f"Bot {token}", "User-Agent": "DiscordBot (https://nexusgoldone.com, 1.0)"}

    try:
        req = urllib.request.Request("https://discord.com/api/v10/users/@me", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                issues.append(f"[Discord] Bot non raggiungibile: status {resp.status}")
    except Exception as e:
        issues.append(f"[Discord] Bot non raggiungibile: {e}")
        return

    for name, channel_id in DISCORD_CHANNELS.items():
        try:
            req = urllib.request.Request(f"https://discord.com/api/v10/channels/{channel_id}", headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    issues.append(f"[Discord] Canale #{name} risponde {resp.status}")
        except Exception as e:
            issues.append(f"[Discord] Canale #{name} non raggiungibile: {e}")


# ---------- 3. BREVO (campagne programmate rimaste bloccate) ----------

def check_brevo(issues):
    api_key = os.environ.get("BREVO_API_KEY")
    if not api_key:
        issues.append("[Brevo] BREVO_API_KEY mancante, salto il controllo")
        return
    try:
        req = urllib.request.Request(
            "https://api.brevo.com/v3/emailCampaigns?status=queued&limit=50",
            headers={"api-key": api_key, "accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
    except Exception as e:
        issues.append(f"[Brevo] Impossibile leggere le campagne: {e}")
        return

    now = datetime.now(timezone.utc)
    grace_minutes = 30  # margine di tolleranza prima di considerarla bloccata
    for c in data.get("campaigns", []):
        scheduled_at = c.get("scheduledAt")
        if not scheduled_at:
            continue
        try:
            scheduled_dt = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
        except Exception:
            continue
        delay_minutes = (now - scheduled_dt).total_seconds() / 60
        if delay_minutes > grace_minutes:
            issues.append(
                f"[Brevo] Campagna '{c.get('name')}' (ID {c.get('id')}) doveva partire alle {scheduled_at} "
                f"ma risulta ancora 'queued' da {int(delay_minutes)} minuti - invio probabilmente bloccato"
            )


# ---------- 4. CRON-JOB.ORG (job ricorrenti attesi) ----------

RECURRING_JOB_TITLES_SUBSTRINGS = [
    "Notizie XAU-USD",
    "Calendario News",
    "Annuncio Newsletter",
]


def check_cronjobs(issues):
    api_key = os.environ.get("CRONJOB_API_KEY")
    if not api_key:
        issues.append("[cron-job.org] CRONJOB_API_KEY mancante, salto il controllo")
        return
    try:
        req = urllib.request.Request("https://api.cron-job.org/jobs", headers={"Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
    except Exception as e:
        issues.append(f"[cron-job.org] Impossibile leggere i job: {e}")
        return

    jobs = data.get("jobs", [])
    now_ts = time.time()
    for expected_substring in RECURRING_JOB_TITLES_SUBSTRINGS:
        matches = [j for j in jobs if expected_substring in j.get("title", "")]
        if not matches:
            issues.append(f"[cron-job.org] Nessun job trovato con titolo contenente '{expected_substring}' - potrebbe essere stato cancellato")
            continue
        for j in matches:
            if not j.get("enabled"):
                issues.append(f"[cron-job.org] Job '{j.get('title')}' e' disabilitato")
            last_exec = j.get("lastExecution", 0)
            if last_exec and (now_ts - last_exec) > 3 * 24 * 3600:
                issues.append(f"[cron-job.org] Job '{j.get('title')}' non gira da oltre 3 giorni")




# ---------- 5. NEWSLETTER SIGNUP (form Brevo sulla landing) ----------

def check_newsletter_signup(issues):
    """Test end-to-end vero: manda un'email di test alla function live di iscrizione
    e legge il campo diagnostico brevoOk per sapere se e' arrivata davvero su Brevo,
    senza doversi fidare della risposta sempre-200 mostrata al visitatore reale."""
    test_email = f"control-check-{int(time.time())}@nexusgoldone-monitor.test"
    payload = json.dumps({"email": test_email}).encode("utf-8")
    try:
        req = urllib.request.Request(
            "https://nexusgoldone.com/.netlify/functions/subscribe",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.load(resp)
    except Exception as e:
        issues.append(f"[Newsletter signup] Impossibile testare il modulo di iscrizione: {e}")
        return

    if not data.get("brevoOk", False):
        issues.append(
            f"[Newsletter signup] Il modulo di iscrizione sulla landing NON arriva a Brevo "
            f"(risposta: {data}) - chi si iscrive dal sito non risulta su Brevo, controllare "
            f"la variabile BREVO_API_KEY su Netlify"
        )


# ---------- 6. AVVISO JONNY (Telegram) - solo per i problemi della landing per ora ----------

def send_jonny_alert(issues):
    """Manda un avviso immediato su Telegram (bot Jonny, chat privata 1:1 con William)
    quando il controllo landing trova un problema. Deciso il 27/8/2026: si parte solo
    dalla landing, gli altri controlli (Discord/Brevo/cron-job.org) restano per ora solo
    in control_status.json, letto a inizio sessione - si aggiungeranno a Jonny uno alla
    volta man mano che si rivedono gli altri reparti."""
    token = os.environ.get("JONNY_BOT_TOKEN")
    chat_id = os.environ.get("JONNY_CHAT_ID")
    if not token or not chat_id:
        return
    testo = "Jonny qui. Il controllo della landing page ha trovato un problema:\n\n" + "\n".join(f"- {i}" for i in issues)
    payload = json.dumps({"chat_id": chat_id, "text": testo}).encode("utf-8")
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=15)
    except Exception:
        pass  # non deve mai far fallire il resto del controllo


def main():
    landing_issues = []
    check_landing(landing_issues)
    check_newsletter_signup(landing_issues)

    altri_issues = []
    check_discord(altri_issues)
    check_brevo(altri_issues)
    check_cronjobs(altri_issues)

    if landing_issues:
        send_jonny_alert(landing_issues)

    issues = landing_issues + altri_issues

    result = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ok": len(issues) == 0,
        "issues": issues,
    }

    with open(STATE_FILE, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
