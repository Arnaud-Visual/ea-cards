import os, json, re, time, hashlib
import requests

WATCH_URL = os.getenv("WATCH_URL", "").strip()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
URL_TEMPLATE = os.getenv("URL_TEMPLATE", "").strip()  # ex: https://.../itemBGs/{guid}/cards_bg_s_1_44_0.png

SEEN_PATH = "seen.json"
TIMEOUT = 12
HEADERS = {"User-Agent": "Mozilla/5.0 (ea-cards-watcher)"}

# URLs .png / .webp (minuscules ou majuscules)
PNG_URL_RE = re.compile(r"https?://[^\"'\\s]+?\\.(?:png|PNG|webp|WEBP)")

# GUIDs de type ae0f18af-ed41-4e36-af4e-ed10afcf6db0
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


# -------------------------------------------------------------
# ⭐ Fonction utilitaire pour tester plusieurs suffixes (_0, _1, _2, _3, _4)
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
    print(f"[INFO] Récupération du JSON sur : {url}")
    r = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
    print(f"[INFO] Statut HTTP WATCH_URL : {r.status_code}")
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return json.loads(r.text)

def walk_values(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from walk_values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_values(v)
    else:
        yield obj

def uniq(seq):
    s, out = set(), []
    for x in seq:
        if x not in s:
            s.add(x)
            out.append(x)
    return out

def test_url_ok(url):
    try:
        r = requests.head(url, timeout=TIMEOUT, allow_redirects=True, headers=HEADERS)
        code = r.status_code
        if code == 405:
            r = requests.get(url, timeout=TIMEOUT, stream=True, headers=HEADERS)
            code = r.status_code
        return 200 <= code < 300
    except Exception as e:
        print(f"[DEBUG] Erreur sur {url}: {e}")
        return False

def discord_embed(image_url, title="Nouvelle carte trouvée"):
    data = {
        "embeds": [{
            "title": title,
            "image": {"url": image_url},
            "url": image_url
        }]
    }
    print(f"[INFO] Envoi vers Discord : {image_url}")
    r = requests.post(DISCORD_WEBHOOK_URL, json=data, timeout=TIMEOUT)
    print(f"[INFO] Statut HTTP Discord : {r.status_code}")
    r.raise_for_status()

def main():
    print(f"[INFO] WATCH_URL = {WATCH_URL!r}")
    print(f"[INFO] URL_TEMPLATE = {URL_TEMPLATE!r}")
    print(f"[INFO] DISCORD_WEBHOOK_URL défini : {bool(DISCORD_WEBHOOK_URL)}")

    if not WATCH_URL or not DISCORD_WEBHOOK_URL:
        raise SystemExit("WATCH_URL ou DISCORD_WEBHOOK_URL manquant(s)")

    seen = load_seen()
    print(f"[INFO] Entrées déjà vues dans seen.json : {len(seen)}")

    root = fetch_json(WATCH_URL)

    # 1️⃣ Récupère les URLs PNG directes dans le JSON
    direct_pngs = []
    for v in walk_values(root):
        if isinstance(v, str):
            direct_pngs.extend(PNG_URL_RE.findall(v))
    direct_pngs = uniq(direct_pngs)
    print(f"[INFO] PNG directs trouvés dans le JSON : {len(direct_pngs)}")
    if direct_pngs[:3]:
        print("[DEBUG] Exemples PNG directs :", direct_pngs[:3])

    # 2️⃣ Récupère tous les GUIDs
    guids = []
    for v in walk_values(root):
        if isinstance(v, str):
            for m in UUID_RE.findall(v):
                guids.append(m.lower())
    guids = uniq(guids)
    print(f"[INFO] GUIDs trouvés dans le JSON : {len(guids)}")
    if guids[:5]:
        print("[DEBUG] Exemples GUIDs :", guids[:5])

    # 3️⃣ Construit des URLs à partir du template
    templated_pngs = []
    if URL_TEMPLATE and "{guid}" in URL_TEMPLATE:
        for g in guids:
            for u in generate_possible_urls(URL_TEMPLATE, g):
                templated_pngs.append(u)
    templated_pngs = uniq(templated_pngs)
    print(f"[INFO] PNG générés via URL_TEMPLATE : {len(templated_pngs)}")
    if templated_pngs[:3]:
        print("[DEBUG] Exemples URLs générées :", templated_pngs[:3])

    candidates = uniq(direct_pngs + templated_pngs)
    print(f"[INFO] Candidats totaux à tester : {len(candidates)}")

    # 4️⃣ Vérifie et envoie les nouvelles images
    new_sent = 0
    for i, url in enumerate(candidates):
        uid = hashlib.sha1(url.encode("utf-8")).hexdigest()
        if uid in seen:
            continue

        ok = test_url_ok(url)
        if not ok:
            if i < 5:
                print(f"[DEBUG] URL KO (inaccessible) : {url}")
            continue

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
