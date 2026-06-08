#!/usr/bin/env python3
"""
Instagram-Autopilot — postet geplante @gottwald.world-Posts (Single + Karussell)
automatisch zur Zeit über die Meta Graph API (Instagram Content Publishing).
Gedacht für GitHub Actions (Cron alle ~10 Min).

Wichtig (anders als X): die Graph API lädt KEINE lokalen Dateien hoch, sondern zieht
Bilder über ÖFFENTLICHE URLs. Karten müssen als JPEG unter IMG_BASE_URL erreichbar sein.

State: content/ig-cards/ig-posted.json (welche Slots raus sind) — Workflow committet zurück.
Env (lokal aus .env, in CI aus GitHub Secrets):
  IG_USER_ID       — numerische Instagram-Business-Account-ID
  IG_ACCESS_TOKEN  — langlebiger Token (instagram_basic + instagram_content_publish)
  IMG_BASE_URL     — öffentliche Basis-URL der JPEGs, z.B. https://USER.github.io/REPO
Optional: WINDOW_MIN (Std. 90), DRY_RUN=1
"""
import os, csv, json, sys, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Layout-flexibel: dediziertes Autopilot-Repo (Dateien neben dem Skript)
# ODER Projekt-Repo (content/ig-cards/). Bilder kommen via IMG_BASE_URL, nicht lokal.
if os.path.exists(os.path.join(SCRIPT_DIR, "ig-schedule.csv")):
    BASE = SCRIPT_DIR
else:
    BASE = os.path.join(os.path.dirname(SCRIPT_DIR), "content", "ig-cards")
CSV_PATH = os.path.join(BASE, "ig-schedule.csv")
STATE_PATH = os.path.join(BASE, "ig-posted.json")
TZ = ZoneInfo("Europe/Berlin")
WINDOW = timedelta(minutes=int(os.environ.get("WINDOW_MIN", "90")))
DRY = os.environ.get("DRY_RUN") == "1"
# Weg-unabhaengig: Facebook-Login (Standard) ODER Instagram-Login via Secret IG_GRAPH_BASE.
#   Weg A (Facebook): https://graph.facebook.com/v21.0  (Default)
#   Weg B (Instagram): https://graph.instagram.com/v21.0
GRAPH = os.environ.get("IG_GRAPH_BASE", "https://graph.facebook.com/v21.0").rstrip("/")

def log(*a): print("[ig-poster]", *a, flush=True)

def load_state():
    if os.path.exists(STATE_PATH):
        try: return set(json.load(open(STATE_PATH)))
        except Exception: return set()
    return set()

def save_state(done):
    json.dump(sorted(done), open(STATE_PATH, "w"), ensure_ascii=False, indent=2)

def due_rows(now, done):
    out = []
    with open(CSV_PATH, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = f"{r['date']}T{r['time_CET']}"
            if key in done:
                continue
            try:
                sched = datetime.strptime(f"{r['date']} {r['time_CET']}", "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
            except Exception as e:
                log("bad row", key, e); continue
            if sched <= now <= sched + WINDOW:
                out.append((key, sched, r))
            elif now > sched + WINDOW:
                log("SKIP (zu spaet, ausserhalb Fenster):", key)
                done.add(key)  # nicht nachfeuern (kein Spam)
    return out

def _post(url, params, tries=3):
    import requests
    last = None
    for i in range(tries):
        resp = requests.post(url, params=params, timeout=60)
        if resp.status_code < 400:
            return resp.json()
        last = resp.text
        log(f"API {resp.status_code} (try {i+1}):", last[:300])
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"Graph API Fehler: {last}")

def img_url(base, fname):
    return f"{base.rstrip('/')}/{fname}"

def create_container(ig_id, token, base, fname, caption=None, carousel_item=False):
    p = {"image_url": img_url(base, fname), "access_token": token}
    if caption is not None: p["caption"] = caption
    if carousel_item: p["is_carousel_item"] = "true"
    return _post(f"{GRAPH}/{ig_id}/media", p)["id"]

def publish(ig_id, token, creation_id):
    # kleine Wartezeit + Retry, falls Container noch verarbeitet wird
    last = None
    for i in range(4):
        try:
            return _post(f"{GRAPH}/{ig_id}/media_publish",
                         {"creation_id": creation_id, "access_token": token}, tries=1)["id"]
        except Exception as e:
            last = e; log("publish noch nicht bereit, warte…", i + 1); time.sleep(5 * (i + 1))
    raise RuntimeError(f"publish fehlgeschlagen: {last}")

def post_one(ig_id, token, base, r):
    files = [x.strip() for x in r["files"].split(";") if x.strip()]
    caption = r["caption"]
    if r["type"] == "carousel":
        children = [create_container(ig_id, token, base, f, carousel_item=True) for f in files]
        parent = _post(f"{GRAPH}/{ig_id}/media", {
            "media_type": "CAROUSEL", "children": ",".join(children),
            "caption": caption, "access_token": token})["id"]
        return publish(ig_id, token, parent)
    else:
        cid = create_container(ig_id, token, base, files[0], caption=caption)
        return publish(ig_id, token, cid)

def main():
    now = datetime.now(TZ)
    done = load_state()
    rows = due_rows(now, done)
    if not rows:
        log("nichts faellig um", now.strftime("%Y-%m-%d %H:%M %Z")); save_state(done); return 0
    ig_id = os.environ.get("IG_USER_ID", "")
    token = os.environ.get("IG_ACCESS_TOKEN", "")
    base = os.environ.get("IMG_BASE_URL", "")
    if not DRY and not (ig_id and token and base):
        log("WARTET auf Secrets (IG_USER_ID / IG_ACCESS_TOKEN / IMG_BASE_URL) — idle, nichts gepostet.")
        return 0  # sauberes Idle (kein roter Fehllauf), bis Mathias die Secrets setzt
    for key, sched, r in rows:
        if DRY:
            log("DRY would post:", key, r["type"], "->", r["files"], "|", r["caption"][:50].replace("\n", " "), "...")
            done.add(key); continue
        try:
            mid = post_one(ig_id, token, base, r)
            log("GEPOSTET:", key, "media", mid)
            done.add(key)
        except Exception as e:
            log("FEHLER beim Posten", key, ":", e)  # nicht als done -> naechster Lauf versucht erneut
    save_state(done)
    return 0

if __name__ == "__main__":
    sys.exit(main())
