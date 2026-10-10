#!/usr/bin/env python3
"""Riepilogo giornaliero da canali Telegram PUBBLICI (pagina web t.me/s/<canale>, nessun account).
Legge le ultime N ore, tiene le notizie che contengono le parole chiave del file di configurazione,
le raggruppa per argomento e le manda a William tramite il bot Jonny.
Variabili: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (solo per l'invio). Opzioni: --dry (stampa, non invia)."""
import json, os, re, sys, html, time, datetime, urllib.request, urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(BASE, "telegram_digest_config.json"), encoding="utf-8"))
UA = {"User-Agent": "Mozilla/5.0 (NexusGoldOneDigest/1.0)"}
DRY = "--dry" in sys.argv

def scarica(url):
    for tentativo in range(3):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read().decode("utf-8", "ignore")
        except Exception:
            time.sleep(1.5)
    return ""

def pulisci(t):
    t = re.sub(r"<br\s*/?>", " ", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s*Leggi l.articolo completo su \w+\s*$", "", t, flags=re.I)
    return t

def messaggi_canale(canale, da_data):
    out, prima = [], None
    for _ in range(CFG.get("pagine_max_per_canale", 8)):
        url = f"https://t.me/s/{canale}" + (f"?before={prima}" if prima else "")
        pagina = scarica(url)
        blocchi = pagina.split("tgme_widget_message_wrap")[1:]
        if not blocchi:
            break
        trovati, piu_vecchio = [], None
        for b in blocchi:
            m_id = re.search(r'data-post="[^/"]+/(\d+)"', b)
            dt = re.search(r'<time[^>]*datetime="([^"]+)"', b)
            tx = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', b, flags=re.S)
            if not (m_id and dt):
                continue
            mid = int(m_id.group(1))
            quando = datetime.datetime.fromisoformat(dt.group(1).replace("Z", "+00:00"))
            piu_vecchio = mid if piu_vecchio is None else min(piu_vecchio, mid)
            if tx and quando >= da_data:
                trovati.append({"canale": canale, "id": mid, "quando": quando, "testo": pulisci(tx.group(1)),
                                "link": f"https://t.me/{canale}/{mid}"})
        out += trovati
        # se il messaggio più vecchio della pagina è già fuori finestra, basta
        date_pagina = [datetime.datetime.fromisoformat(re.search(r'<time[^>]*datetime="([^"]+)"', b).group(1).replace("Z", "+00:00"))
                       for b in blocchi if re.search(r'<time[^>]*datetime="([^"]+)"', b)]
        if not date_pagina or min(date_pagina) < da_data or piu_vecchio is None:
            break
        prima = piu_vecchio
    return out

def cerca(testo):
    t = " " + testo.lower() + " "
    esclusi = [e for e in CFG.get("escludi", []) if e in t]
    if esclusi:
        return []
    trovate = []
    for argomento, parole in CFG["parole_chiave"].items():
        hit = [p for p in parole if (p in t if p.endswith(" ") or " " in p else re.search(r"(?<![a-zàèéìòù])" + re.escape(p), t))]
        if hit:
            trovate.append((argomento, hit))
    return trovate

def main():
    ora = datetime.datetime.now(datetime.timezone.utc)
    da_data = ora - datetime.timedelta(hours=CFG.get("ore", 24))
    tutte, errori = [], []
    for c in CFG["canali"]:
        try:
            msg = messaggi_canale(c, da_data)
            if not msg: errori.append(c)
            tutte += msg
        except Exception:
            errori.append(c)
    visti, scelte = set(), []
    for m in sorted(tutte, key=lambda x: x["quando"], reverse=True):
        chiave = re.sub(r"[^a-z0-9]", "", m["testo"].lower())[:45]
        if len(m["testo"]) < 25 or chiave in visti:
            continue
        match = cerca(m["testo"])
        if not match:
            continue
        visti.add(chiave)
        m["match"] = match
        m["punti"] = sum(len(h) for _, h in match)
        scelte.append(m)
    def parole(t):
        return {w for w in re.findall(r"[a-zàèéìòù0-9]{4,}", t.lower())}
    unite = []
    for m in sorted(scelte, key=lambda x: (-x["punti"], x["quando"])):
        w = parole(m["testo"])
        for u in unite:
            wu = u["_w"]
            if w and wu and len(w & wu) / len(w | wu) >= 0.30:
                u["altri"].append(m); break
        else:
            m["_w"] = w; m["altri"] = []; unite.append(m)
    scelte = unite[:CFG.get("max_notizie", 15)]
    gruppi = {}
    for m in scelte:
        gruppi.setdefault(m["match"][0][0], []).append(m)
    giorno = datetime.datetime.now().strftime("%d/%m")
    righe = [f"Riepilogo ultime {CFG.get('ore',24)} ore dai canali ({giorno})"]
    if not scelte:
        righe.append("Niente di rilevante con le tue parole chiave.")
    for arg, lista in gruppi.items():
        righe.append(f"\n{arg.upper()}")
        for m in sorted(lista, key=lambda x: x["quando"], reverse=True):
            t = m["testo"]
            t = t if len(t) <= 170 else t[:167].rsplit(" ", 1)[0] + "..."
            fonti = [m["canale"]] + [a["canale"] for a in m["altri"]]
            etichetta = ", ".join(dict.fromkeys(fonti))
            righe.append(f"- {t}\n  [{etichetta}] {m['link']}")
    if errori:
        righe.append("\n(canali senza messaggi o non raggiungibili: " + ", ".join(errori) + ")")
    righe.append("\nDa riverificare alla fonte prima di usarle.")
    testo = "\n".join(righe)
    print(testo)
    print(f"\n[{len(tutte)} messaggi letti, {len(scelte)} scelti, {len(testo)} caratteri]")
    if DRY:
        return
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        print("Mancano TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID: non invio."); sys.exit(1)
    pezzi, corrente = [], ""
    for blocco in testo.split("\n\n"):
        if len(corrente) + len(blocco) + 2 > 3800:
            pezzi.append(corrente); corrente = ""
        corrente += ("\n\n" if corrente else "") + blocco
    pezzi.append(corrente)
    for p in pezzi:
        d = urllib.parse.urlencode({"chat_id": chat, "text": p, "disable_web_page_preview": "true"}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", d, timeout=20)
        time.sleep(1)
    print("Inviato a Jonny.")

if __name__ == "__main__":
    main()
