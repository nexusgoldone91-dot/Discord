#!/usr/bin/env python3
"""
Security Agent - controllo giornaliero indipendente (GitHub Actions).
Verifica che i token/chiavi principali funzionino ancora, e scansiona
il repository stesso per pattern di chiavi esposte per sbaglio.
Non stampa mai un valore di chiave/token, solo esiti booleani.
Posta su Discord SOLO se trova un problema (silenzioso se tutto ok).
"""

import json
import os
import re
import socket
import ssl
import urllib.request
import urllib.error
from datetime import datetime, timezone

DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
BREVO_API_KEY = os.environ["BREVO_API_KEY"]
NOTION_API_KEY = os.environ["NOTION_API_KEY"]
DISCORD_USER_ID_WILLIAM = os.environ["DISCORD_USER_ID_WILLIAM"]
DISCORD_GUILD_ID = os.environ["DISCORD_GUILD_ID"]

# Permessi pericolosi che il bot NON dovrebbe mai avere (bit flag Discord)
DANGEROUS_PERMISSION_BITS = {
    "Amministratore": 0x8,
    "Bannare membri": 0x4,
    "Espellere membri": 0x2,
    "Gestire il server": 0x20,
    "Gestire i ruoli": 0x10000000,
    "Gestire i webhook": 0x20000000,
    "Silenziare/isolare membri": 0x10000000000,
}

USER_AGENT = "NexusGoldOne-SecurityAgent/1.0 (https://nexusgoldone.com)"
LANDING_DOMAIN = "nexusgoldone.com"
EXPECTED_SECURITY_HEADERS = [
    "strict-transport-security",
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
]
SENSITIVE_PATHS = ["/.env", "/.git/config", "/credentials.json", "/.git/HEAD", "/wp-config.php"]

SECRET_PATTERNS = {
    "Brevo API key": re.compile(r"xkeysib-[a-f0-9]{64}-\w+"),
    "Notion secret": re.compile(r"\b(ntn_|secret_)[A-Za-z0-9]{20,}\b"),
    "Discord bot token": re.compile(r"\b[MN][A-Za-z0-9_-]{23,}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}\b"),
}

SKIP_DIRS = {".git", "node_modules", "venv", ".venv"}


def check_discord_token():
    req = urllib.request.Request(
        "https://discord.com/api/v10/users/@me",
        headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status == 200
    except urllib.error.HTTPError:
        return False


def check_brevo_key():
    req = urllib.request.Request(
        "https://api.brevo.com/v3/account",
        headers={"api-key": BREVO_API_KEY, "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status == 200
    except urllib.error.HTTPError:
        return False


def check_notion_key():
    req = urllib.request.Request(
        "https://api.notion.com/v1/users/me",
        headers={"Authorization": f"Bearer {NOTION_API_KEY}", "Notion-Version": "2022-06-28", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status == 200
    except urllib.error.HTTPError:
        return False


def scan_repo_for_exposed_secrets(root="."):
    findings = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            if not fname.endswith((".py", ".md", ".html", ".js", ".yml", ".yaml", ".json", ".txt")):
                continue
            path = os.path.join(dirpath, fname)
            try:
                with open(path, "r", errors="ignore") as f:
                    content = f.read()
            except OSError:
                continue
            for label, pattern in SECRET_PATTERNS.items():
                if pattern.search(content):
                    findings.append(f"{label} trovata in `{path}`")
    return findings


def check_landing_security_headers():
    """Controlla header di sicurezza HTTP sulla landing page (cybersecurity, non funzionalità)."""
    problems = []
    req = urllib.request.Request(f"https://{LANDING_DOMAIN}", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            headers = {k.lower(): v for k, v in resp.getheaders()}
    except Exception as e:
        return [f"Landing page non raggiungibile per il controllo header di sicurezza ({e})."]

    for h in EXPECTED_SECURITY_HEADERS:
        if h not in headers:
            problems.append(f"Header di sicurezza mancante sulla landing: `{h}`.")
    return problems


def check_landing_sensitive_paths():
    """Controlla che file/percorsi sensibili non siano accidentalmente pubblicati sulla landing."""
    problems = []
    for path in SENSITIVE_PATHS:
        req = urllib.request.Request(f"https://{LANDING_DOMAIN}{path}", headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    problems.append(f"Percorso sensibile risulta PUBBLICO e raggiungibile: `{path}` (dovrebbe dare 404).")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                problems.append(f"Percorso sensibile `{path}` risponde {e.code} invece di 404 (verificare).")
        except Exception:
            pass
    return problems


def check_ssl_certificate_expiry(days_warning=21):
    """Avvisa se il certificato SSL della landing scade a breve."""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((LANDING_DOMAIN, 443), timeout=15) as sock:
            with ctx.wrap_socket(sock, server_hostname=LANDING_DOMAIN) as ssock:
                cert = ssock.getpeercert()
        expires = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        days_left = (expires - datetime.now(timezone.utc)).days
        if days_left < days_warning:
            return [f"Certificato SSL della landing scade tra {days_left} giorni ({expires.date()})."]
        return []
    except Exception as e:
        return [f"Impossibile verificare la scadenza del certificato SSL ({e})."]


def check_discord_bot_permissions():
    """Controlla che il bot non abbia permessi pericolosi (Amministratore, bannare, ecc.)."""
    try:
        req_me = urllib.request.Request("https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "User-Agent": USER_AGENT})
        with urllib.request.urlopen(req_me) as resp:
            bot_id = json.loads(resp.read())["id"]

        req_member = urllib.request.Request(f"https://discord.com/api/v10/guilds/{DISCORD_GUILD_ID}/members/{bot_id}",
            headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "User-Agent": USER_AGENT})
        with urllib.request.urlopen(req_member) as resp:
            member = json.loads(resp.read())
        role_ids = set(member.get("roles", []))

        req_roles = urllib.request.Request(f"https://discord.com/api/v10/guilds/{DISCORD_GUILD_ID}/roles",
            headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "User-Agent": USER_AGENT})
        with urllib.request.urlopen(req_roles) as resp:
            roles = json.loads(resp.read())

        combined_permissions = 0
        for role in roles:
            if role["id"] in role_ids or role["id"] == DISCORD_GUILD_ID:  # @everyone id == guild id
                combined_permissions |= int(role["permissions"])

        problems = []
        for name, bit in DANGEROUS_PERMISSION_BITS.items():
            if combined_permissions & bit:
                problems.append(f"Il bot ha ancora il permesso pericoloso \"{name}\" — da togliere.")
        return problems
    except Exception as e:
        return [f"Impossibile verificare i permessi del bot Discord ({e})."]


def dns_over_https(name, record_type):
    """Query DNS via Google DoH, nessuna dipendenza esterna necessaria."""
    url = f"https://dns.google/resolve?name={name}&type={record_type}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    answers = data.get("Answer", [])
    return [a["data"] for a in answers]


def check_domain_email_auth():
    """Controlla SPF, DKIM e DMARC su nexusgoldone.com, per accorgersi se qualcosa si rompe nel tempo."""
    problems = []
    try:
        spf_records = dns_over_https(LANDING_DOMAIN, "TXT")
        spf = [r for r in spf_records if "v=spf1" in r]
        if not spf:
            problems.append("Record SPF non trovato su nexusgoldone.com (dovrebbe esserci).")
        elif "spf.brevo.com" not in spf[0]:
            problems.append("Record SPF presente ma non include più Brevo (spf.brevo.com) — controllare se qualcuno l'ha modificato.")
    except Exception as e:
        problems.append(f"Impossibile verificare l'SPF ({e}).")

    try:
        dmarc_records = dns_over_https(f"_dmarc.{LANDING_DOMAIN}", "TXT")
        if not any("v=DMARC1" in r for r in dmarc_records):
            problems.append("Record DMARC non trovato o non valido su _dmarc.nexusgoldone.com.")
    except Exception as e:
        problems.append(f"Impossibile verificare il DMARC ({e}).")

    for host in ["brevo1._domainkey", "brevo2._domainkey"]:
        try:
            cname_records = dns_over_https(f"{host}.{LANDING_DOMAIN}", "CNAME")
            if not cname_records:
                problems.append(f"Record DKIM mancante: {host}.{LANDING_DOMAIN}.")
        except Exception as e:
            problems.append(f"Impossibile verificare il DKIM {host} ({e}).")

    return problems


def post_discord_alert(message):
    """Manda l'alert in DM privato a William, mai in un canale del server."""
    dm_payload = json.dumps({"recipient_id": DISCORD_USER_ID_WILLIAM}).encode()
    dm_req = urllib.request.Request(
        "https://discord.com/api/v10/users/@me/channels",
        data=dm_payload, method="POST",
        headers={
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(dm_req) as resp:
        dm_channel_id = json.loads(resp.read())["id"]

    payload = json.dumps({"content": message}).encode()
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{dm_channel_id}/messages",
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
    problems = []

    if not check_discord_token():
        problems.append("Token bot Discord non risponde correttamente (revocato o rotto?).")
    if not check_brevo_key():
        problems.append("Chiave API Brevo non risponde correttamente (revocata o rotta?).")
    if not check_notion_key():
        problems.append("Chiave API Notion non risponde correttamente (revocata o rotta?).")

    exposed = scan_repo_for_exposed_secrets(".")
    if exposed:
        problems.append("Chiavi esposte trovate nel repository:\n- " + "\n- ".join(exposed))

    problems.extend(check_landing_security_headers())
    problems.extend(check_landing_sensitive_paths())
    problems.extend(check_ssl_certificate_expiry())
    problems.extend(check_discord_bot_permissions())
    problems.extend(check_domain_email_auth())

    if problems:
        message = "🔐 **Security Agent — problema trovato**\n\n" + "\n\n".join(problems)
        post_discord_alert(message)
        print("PROBLEMI TROVATI:\n" + message)
    else:
        print("Tutto ok: nessun problema di sicurezza trovato.")


if __name__ == "__main__":
    main()
