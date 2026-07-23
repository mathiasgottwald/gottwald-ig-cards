#!/usr/bin/env python3
"""
Instagram-Messung — liest ECHTE Zahlen direkt bei Meta (nicht Buffer, kein Lag, kein Login).

Warum (23.07.2026): Buffer zeigt Posts erst mit >24 h Verzoegerung und liefert keine
Format-Aufschluesselung. Der Reel-vs-Karussell-Test (Discovery-Hebel) braucht aber
per-Post-Reach am Tag danach. Diese Messung ist unsere eigene, unabhaengige Quelle.

Schreibt ig-insights.json (Historie: ein Eintrag je Lauf) + gibt alles ins Actions-Log.
Faellt einzeln fehlertolerant aus: fehlt ein Scope, wird das benannt statt zu crashen.

Env (GitHub Secrets):
  IG_USER_ID, IG_ACCESS_TOKEN, optional IG_GRAPH_BASE (Default graph.instagram.com)
"""
import os, json, sys, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone

TOKEN = os.environ.get("IG_ACCESS_TOKEN", "")
USER = os.environ.get("IG_USER_ID", "me")
GRAPH = os.environ.get("IG_GRAPH_BASE", "https://graph.instagram.com/v21.0").rstrip("/")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ig-insights.json")
MEDIA_LIMIT = int(os.environ.get("MEDIA_LIMIT", "12"))

def log(*a): print("[ig-insights]", *a, flush=True)

def get(path, **params):
    """GET auf die Graph API. Gibt (data, fehler_string) zurueck — wirft nie."""
    params["access_token"] = TOKEN
    url = f"{GRAPH}/{path}?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r), None
    except urllib.error.HTTPError as e:
        try:
            body = json.load(e)
            msg = body.get("error", {}).get("message", str(body))
        except Exception:
            msg = f"HTTP {e.code}"
        return None, msg
    except Exception as e:
        return None, str(e)

if not TOKEN:
    log("FEHLER: IG_ACCESS_TOKEN fehlt."); sys.exit(1)

run = {"gemessen_am": datetime.now(timezone.utc).isoformat(timespec="seconds"), "account": {}, "posts": [], "fehler": []}

# --- 1) Konto-Ebene: echte Followerzahl (Buffer rundet auf '4.8k') ---
acc, err = get(USER, fields="username,followers_count,follows_count,media_count")
if acc:
    run["account"] = acc
    log("Konto:", json.dumps(acc, ensure_ascii=False))
else:
    run["fehler"].append(f"account: {err}")
    log("Konto-Abruf fehlgeschlagen:", err)

# --- 2) Letzte Medien ---
med, err = get(f"{USER}/media", fields="id,media_type,media_product_type,timestamp,permalink,like_count,comments_count", limit=MEDIA_LIMIT)
items = (med or {}).get("data", [])
if err:
    run["fehler"].append(f"media: {err}")
    log("Media-Abruf fehlgeschlagen:", err)
log(f"{len(items)} Medien geholt.")

# --- 3) Insights je Medium. Metriken unterscheiden sich je Format -> gestaffelt versuchen. ---
SETS = {
    "REELS":    "reach,saved,shares,total_interactions,views,ig_reels_avg_watch_time,ig_reels_video_view_total_time",
    "CAROUSEL": "reach,saved,shares,total_interactions,views,profile_visits",
    "DEFAULT":  "reach,saved,shares,total_interactions,views",
}
for m in items:
    prod = (m.get("media_product_type") or "").upper()
    mt = (m.get("media_type") or "").upper()
    key = "REELS" if prod == "REELS" else ("CAROUSEL" if mt == "CAROUSEL_ALBUM" else "DEFAULT")
    row = {
        "id": m.get("id"), "zeit": m.get("timestamp"), "format": prod or mt,
        "permalink": m.get("permalink"), "likes": m.get("like_count"), "kommentare": m.get("comments_count"),
    }
    ins, e1 = get(f"{m['id']}/insights", metric=SETS[key])
    if ins is None and key != "DEFAULT":          # Fallback: schmaler Metrik-Satz
        ins, e1 = get(f"{m['id']}/insights", metric=SETS["DEFAULT"])
    if ins:
        for d in ins.get("data", []):
            vals = d.get("values") or [{}]
            row[d.get("name")] = vals[0].get("value")
    else:
        row["insights_fehler"] = e1
        run["fehler"].append(f"insights {m.get('id')}: {e1}")
    run["posts"].append(row)
    log(json.dumps(row, ensure_ascii=False))

# --- 4) Historie fortschreiben (nie ueberschreiben — Verlauf ist der Wert) ---
hist = []
if os.path.exists(OUT):
    try: hist = json.load(open(OUT))
    except Exception: hist = []
if not isinstance(hist, list): hist = [hist]
hist.append(run)
json.dump(hist[-60:], open(OUT, "w"), ensure_ascii=False, indent=2)
log(f"geschrieben: {OUT} ({len(hist)} Laeufe)")

# --- 5) Klartext-Auswertung: Reel vs. Karussell ---
def avg(fmt):
    v = [p.get("reach") for p in run["posts"] if p.get("format") == fmt and isinstance(p.get("reach"), int)]
    return (sum(v) / len(v), len(v)) if v else (None, 0)
r_reel, n_reel = avg("REELS")
r_car, n_car = avg("CAROUSEL_ALBUM")
if r_car is None: r_car, n_car = avg("CAROUSEL")
log("--- FORMAT-TEST ---")
log(f"Reel:     Reach-Schnitt {r_reel} (n={n_reel})")
log(f"Karussell: Reach-Schnitt {r_car} (n={n_car})")
if r_reel and r_car:
    log(f"Faktor Reel/Karussell: {r_reel / r_car:.2f}x")
else:
    log("Noch kein Vergleich moeglich (zu wenig Daten oder Insights-Scope fehlt).")
if run["fehler"]:
    log("FEHLER aufgetreten:", len(run["fehler"]), "-> siehe ig-insights.json")
