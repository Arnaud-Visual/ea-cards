import os, json, re, time, hashlib
import requests

WATCH_URL = os.getenv("WATCH_URL", "").strip()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
URL_TEMPLATE = os.getenv("URL_TEMPLATE", "").strip()  # ex: https://.../itemBGs/{guid}/cards_bg_s_1_44_0.png

SEEN_PATH = "seen.json"
TIMEOUT = 12
HEADERS = {"User-Agent": "Mozilla/5.0 (ea-cards-watcher)"}

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
PNG_URL_RE = re.compile(r"https?://[^\"'\\s]+?\\.png")

# -------------------------------------------------------------
# ⭐ Fonction utilitaire pour tester plusieurs suffixes (_0, _1, _2, _3)
# -------------------------------------------------------------
def generate_possible_urls(template, guid):
    urls = []
    # formats les plus communs
    suffixes = ["_0.png", "_1.png", "_2.png", "_3.png", "_4.png"]
    for s in suffixes:
        urls.append(template.replace("{guid}", guid).replace("_0.png", s))
    # inclut aussi la version sans suffixe
    urls.append(template.replace("{guid}", guid))
    return urls
# -------------------------------------------------------------

def load_seen():
    try:
        with open(SEEN_PATH, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_seen(seen):
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen)), f, ensure_ascii=False, indent=2)

def fetch_json(url):
    r = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return json.loads(r.text)

def walk_values(obj):
    if isinstance(obj, dict):
        for v in obj.values(): yield from walk_values(v)
    elif isinstance(obj, list):
        for v in obj: yield from walk_values(v)
    else:
        yield obj

def uniq(seq):
    s, out = set(), []
    for x in seq:
        if x not in s:
            s.add(x); out.append(x)
    return out

def test_url_ok(url):
    try:
        r = requests.head(url, timeout=TIMEOUT, allow_redirects=True, headers=HEADERS)
        if r.status_code == 405:
            r = requests.get(url, timeout=TIMEOUT, stream=True, headers=HEADERS)
        return 200 <= r.status_code < 300
    except Exception:
        return False

def discord_embed(image_url, title="Nouvelle carte trouvée"):
    data = {
        "embeds": [{
            "title": title,
            "image": {"url": image_url},
            "url": image_url
        }]
    }
    r = requests.post(DISCORD_WEBHOOK_URL, json=data, timeout=TIMEOUT)
    r.raise_for_status()

def main():
    if not WATCH_URL or not DISCORD_WEBHOOK_URL:
        raise SystemExit("WATCH_URL ou DISCORD_WEBHOOK_URL manquant(s)")

    seen = load_seen()
    root = fetch_json(WATCH_URL)

    # 1️⃣ Récupère les URLs PNG directes
    direct_pngs = []
    for v in walk_values(root):
        if isinstance(v, str):
            direct_pngs.extend(PNG_URL_RE.findall(v))
    direct_pngs = uniq(direct_pngs)

    # 2️⃣ Récupère tous les GUIDs
    guids = []
    for v in walk_values(root):
        if isinstance(v, str):
            for m in UUID_RE.findall(v):
                guids.append(m.lower())
    guids = uniq(guids)

    # 3️⃣ Construit des URLs à partir du template
    templated_pngs = []
    if URL_TEMPLATE and "{guid}" in URL_TEMPLATE:
        for g in guids:
            for u in generate_possible_urls(URL_TEMPLATE, g):  # ⭐ teste plusieurs variantes
                templated_pngs.append(u)

    candidates = uniq(direct_pngs + templated_pngs)

    # 4️⃣ Vérifie et envoie les nouvelles images
    new_sent = 0
    for url in candidates:
        uid = hashlib.sha1(url.encode("utf-8")).hexdigest()
        if uid in seen:
            continue
        if test_url_ok(url):
            try:
                discord_embed(url)
                new_sent += 1
                seen.add(uid)
                time.sleep(0.6)
            except Exception as e:
                print("Discord error:", e)

    save_seen(seen)
    print(f"✅ Terminé. {new_sent} nouvelles cartes envoyées.")

if __name__ == "__main__":
    main()
