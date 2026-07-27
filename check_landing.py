#!/usr/bin/env python3
"""
Controllo automatico della landing page NexusGoldOne (nexusgoldone.com).
Gira su GitHub Actions (workflow landing.yml), attivato ogni lunedi alle 10:00
Canarie da un job cron-job.org indipendente - nessuna dipendenza da Mac/sessione
Claude Code aperta, stesso principio del workflow newsletter.yml.

Controlla: sito online, link interni/esterni/anchor, header di sicurezza base
(HSTS, mixed content), lang="it", e i Core Web Vitals via PageSpeed Insights
(gratis, nessuna chiave richiesta). Scrive l'esito in landing_status.json,
committato nel repo - lo stato si legge a inizio sessione Claude Code (vedi
Desktop/CLAUDE.md), nessun controllo LLM richiesto qui.
"""

import json
import re
import urllib.request
import urllib.error
import urllib.parse
import time

SITE_URL = "https://nexusgoldone.com/"
STATE_FILE = "landing_status.json"

ANCHOR_IDS = ["coaching-card", "discord-card", "newsletter-card", "testimonianze"]

EXTERNAL_LINKS = [
    "https://discord.gg/s7FqjtxYmc",
    "https://wa.me/393780116402",
    "https://www.instagram.com/william.bezzon",
]


def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (NexusGoldOneMonitor/1.0)"})
    return urllib.request.urlopen(req, timeout=timeout)


def check_homepage(issues):
    start = time.time()
    try:
        with fetch(SITE_URL) as resp:
            elapsed = time.time() - start
            status = resp.status
            headers = dict(resp.getheaders())
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        issues.append(f"Sito irraggiungibile: {e}")
        return None, None, elapsed if 'elapsed' in dir() else None

    if status != 200:
        issues.append(f"Homepage risponde {status} invece di 200")
    if elapsed > 5:
        issues.append(f"Homepage lenta: {elapsed:.1f}s per rispondere (soglia 5s)")

    if "Strict-Transport-Security" not in headers:
        issues.append("Header HSTS mancante (Strict-Transport-Security)")

    if 'lang="it"' not in html and "lang='it'" not in html:
        issues.append('<html lang="it"> non trovato')

    mixed_content = re.findall(r'(?:src|href)="http://[^"]+"', html)
    if mixed_content:
        issues.append(f"Mixed content trovato: {mixed_content[:3]}")

    return html, headers, elapsed


def check_anchors(html, issues):
    if not html:
        return
    for anchor_id in ANCHOR_IDS:
        if f'id="{anchor_id}"' not in html:
            issues.append(f"Anchor #{anchor_id} referenziato ma id non trovato nella pagina")


def check_external_links(issues):
    for url in EXTERNAL_LINKS:
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0 (NexusGoldOneMonitor/1.0)"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status >= 400:
                    issues.append(f"Link esterno {url} risponde {resp.status}")
        except urllib.error.HTTPError as e:
            if e.code >= 400 and e.code != 405:  # 405 = HEAD non supportato, non e' un errore del link
                issues.append(f"Link esterno {url} risponde {e.code}")
        except Exception as e:
            issues.append(f"Link esterno {url} non raggiungibile: {e}")


def check_pagespeed(issues):
    url = (
        "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
        f"?url={urllib.parse.quote(SITE_URL, safe='')}&strategy=mobile&category=performance"
    )
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = json.load(resp)
        audits = data.get("lighthouseResult", {}).get("audits", {})
        metrics = {
            "LCP": audits.get("largest-contentful-paint", {}).get("displayValue"),
            "CLS": audits.get("cumulative-layout-shift", {}).get("displayValue"),
            "TBT": audits.get("total-blocking-time", {}).get("displayValue"),
        }
        score = data.get("lighthouseResult", {}).get("categories", {}).get("performance", {}).get("score")
        if score is not None and score < 0.5:
            issues.append(f"PageSpeed mobile basso: punteggio {score} (LCP {metrics['LCP']}, CLS {metrics['CLS']})")
        return metrics
    except Exception as e:
        # non bloccante: se l'API PageSpeed e' irraggiungibile non e' un problema del sito
        return {"errore": str(e)}


def main():
    issues = []
    html, headers, elapsed = check_homepage(issues)
    check_anchors(html, issues)
    check_external_links(issues)
    metrics = check_pagespeed(issues)

    result = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ok": len(issues) == 0,
        "issues": issues,
        "response_time_s": round(elapsed, 2) if elapsed else None,
        "pagespeed_mobile": metrics,
    }

    with open(STATE_FILE, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
