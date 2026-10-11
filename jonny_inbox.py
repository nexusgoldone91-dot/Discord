#!/usr/bin/env python3
"""Posta da Jonny: legge i messaggi che William scrive al bot Jonny e li accoda in una pagina Notion privata.
Gira ogni 15 minuti su GitHub Actions. Nessuno stato nel repo: i messaggi si confermano su Telegram solo dopo averli scritti in Notion.
Env: JONNY_BOT_TOKEN, JONNY_CHAT_ID, NOTION_API_KEY, NOTION_INBOX_PAGE_ID. Non stampa mai chiavi.
"""
import os, sys, json, datetime, urllib.request, urllib.parse
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

def main():
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
        lines.append(f"[{when}] {txt}")
    if lines:
        notion_append(lines)
    tg("getUpdates", offset=last + 1, limit=1)   # conferma solo dopo aver scritto in Notion
    print(f"{len(lines)} messaggi salvati")

if __name__ == "__main__":
    main()
