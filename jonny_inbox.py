#!/usr/bin/env python3
"""Posta da Jonny: legge i messaggi che William scrive al bot Jonny e li accoda in una pagina Notion privata.
Gira ogni 5 minuti su GitHub Actions; risponde con una conferma e porta con sé il messaggio a cui William risponde. Nessuno stato nel repo: i messaggi si confermano su Telegram solo dopo averli scritti in Notion.
Env: JONNY_BOT_TOKEN, JONNY_CHAT_ID, NOTION_API_KEY, NOTION_INBOX_PAGE_ID. Non stampa mai chiavi.
"""
import os, sys, json, datetime, urllib.request, urllib.parse, urllib.error
TOK = os.environ["JONNY_BOT_TOKEN"]; CHAT = str(os.environ["JONNY_CHAT_ID"])
NOTION = os.environ["NOTION_API_KEY"]; PAGE = os.environ["NOTION_INBOX_PAGE_ID"]

def tg(method, **params):
    url = f"https://api.telegram.org/bot{TOK}/{method}?" + urllib.parse.urlencode(params)
    return json.load(urllib.request.urlopen(url, timeout=30))

def notion_append(lines):
    children = [{"object": "block", "type": "bulleted_list_item",
                 "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": l[:1900]}}]}} for l in lines]
    req = urllib.request.Request(f"https://api.notion.com/v1/blocks/{PAGE}/children",
        data=json.dumps({"children": children}).encode(), method="PATCH",
        headers={"Authorization": f"Bearer {NOTION}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()

def alert(msg):
    """Avvisa William su Jonny (al massimo una volta all'ora: solo nei primi 5 minuti di ogni ora)."""
    if datetime.datetime.utcnow().minute >= 5:
        return
    try:
        tg("sendMessage", chat_id=CHAT, text=msg)
    except Exception:
        pass

def notion_cleanup(days=30):
    """Cancella (archivia) dalla pagina i messaggi piu' vecchi di `days` giorni. Non tocca la riga introduttiva."""
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=days)
    cursor, n = None, 0
    while True:
        url = f"https://api.notion.com/v1/blocks/{PAGE}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {NOTION}", "Notion-Version": "2022-06-28"})
        d = json.load(urllib.request.urlopen(req, timeout=30))
        for b in d.get("results", []):
            txt = "".join(t.get("plain_text", "") for t in ((b.get(b.get("type"), {}) or {}).get("rich_text") or []))
            if txt.startswith("Qui arrivano da soli"):
                continue
            created = datetime.datetime.strptime(b["created_time"][:19], "%Y-%m-%dT%H:%M:%S")
            if created < cutoff:
                dreq = urllib.request.Request(f"https://api.notion.com/v1/blocks/{b['id']}", method="DELETE",
                    headers={"Authorization": f"Bearer {NOTION}", "Notion-Version": "2022-06-28"})
                urllib.request.urlopen(dreq, timeout=30).read(); n += 1
        if not d.get("has_more"):
            break
        cursor = d.get("next_cursor")
    return n

def main():
    now = datetime.datetime.utcnow()
    if now.hour == 3 and now.minute < 5:   # una volta al giorno
        try:
            print(f"pulizia: {notion_cleanup(30)} blocchi vecchi cancellati")
        except Exception as e:
            alert(f"Jonny: la pulizia dei messaggi vecchi su Notion non è riuscita ({type(e).__name__}).")
    r = tg("getUpdates", timeout=0, limit=100)
    ups = r.get("result", [])
    if not ups:
        print("nessun messaggio nuovo"); return
    lines, last = [], 0
    for u in ups:
        last = max(last, u["update_id"])
        m = u.get("message") or u.get("edited_message") or {}
        if str((m.get("chat") or {}).get("id")) != CHAT:
            continue  # ignora chiunque non sia William
        txt = m.get("text") or m.get("caption")
        if not txt:
            txt = "[allegato senza testo: " + ", ".join(k for k in ("photo", "voice", "document", "video") if m.get(k)) + "]"
        when = datetime.datetime.utcfromtimestamp(m.get("date", 0)).strftime("%d/%m %H:%M UTC")
        rep = m.get("reply_to_message") or {}
        rep_txt = rep.get("text") or rep.get("caption") or ""
        if rep_txt:
            txt += "\n   [in risposta a questo messaggio di Jonny: " + rep_txt[:700].replace("\n", " ") + "]"
        lines.append(f"[{when}] {txt}")
    if lines:
        try:
            notion_append(lines)
        except Exception as e:
            code = getattr(e, "code", "")
            alert(f"Jonny: non riesco a salvare i tuoi messaggi su Notion (errore {type(e).__name__} {code}). Restano in attesa su Telegram fino a 24 ore: va sistemato il collegamento a Notion.")
            print("ERRORE Notion", type(e).__name__, code)
            sys.exit(1)
    tg("getUpdates", offset=last + 1, limit=1)   # conferma solo dopo aver scritto in Notion
    if lines:
        try:
            tg("sendMessage", chat_id=CHAT, text=f"Ricevuto e salvato ({len(lines)}). Lo leggo appena la sessione è aperta.")
        except Exception:
            pass
    print(f"{len(lines)} messaggi salvati")

if __name__ == "__main__":
    main()
