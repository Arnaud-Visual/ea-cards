import os, json, re, time, hashlib
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

WATCH_URL = os.getenv("WATCH_URL", "").strip()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
URL_TEMPLATE = os.getenv("URL_TEMPLATE", "").strip()  # ex: https://.../{guid}/cards_bg_s_1_{id}_0.png

SEEN_PATH = "seen.json"
TIMEOUT = 12

HEADERS = {
    "User-Agent": "Mozilla/5.0 (ea-cards-watcher)"
}

# URLs .png / .webp (minuscule ou majuscule)
PNG_URL_RE = re.compile(r"https?://[^\"'\\s]+?\\.(?:png|PNG|webp|WEBP)")

# GUIDs de type ae0f18af-ed41-4e36-af4e-ed10afcf6db0
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

# -------------------------------------------------------------
# ✅ Session HTTP robuste (retries + backoff) pour EA + Discord
# -------------------------------------------------------------
def make_session():
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=1.2,  # 1.2s, 2.4s, 4.8s...
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD", "POST"],
    )
    s = requests.Session()
    s.headers.update(HEADERS)
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s

SESSION = make_session()

# -------------------------------------------------------------
def generate_possible_urls(template, guid, id_value=None):
    """
    Construit une liste d'URLs possibles à partir du template,
    en remplaçant {guid} et éventuellement {id}, puis en testant plusieurs suffixes.
    """
    base = template.replace("{guid}", guid)
    if id_value is not None:
        base = base.replace("{id}", str(id_value))

    urls = []
    # si le template contient déjà "_0.png", on décline _0.._4
    if "_0.png" in base:
        suffixes = ["_0.png", "_1.png", "_2.png", "_3.png", "_4.png"]
        for s in suffixes:
            urls.append(base.replace("_0.png", s))
    else:
        urls.append(base)
    return urls


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
    r = SESSION.get(url, timeout=TIMEOUT)
    print(f"[INFO] Statut HTTP WATCH_URL : {r.status_code}")
    r.raise_for_status()

    # Debug utile si jamais EA renvoie HTML
    ctype = (r.headers.get("content-type") or "").lower()
    print(f"[INFO] Content-Type : {ctype}")

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
    """
    EA peut refuser HEAD (403/405) mais accepter GET.
    On teste HEAD puis fallback GET léger.
    """
    try:
        r = SESSION.head(url, timeout=TIMEOUT, allow_redirects=True)
        code = r.status_code

        if code in (403, 405):
            r = SESSION.get(url, timeout=TIMEOUT, stream=True)
            code = r.status_code

        return 200 <= code < 300
    except Exception as e:
        print(f"[DEBUG] Erreur sur {url}: {e}")
        return False


def discord_embed(image_url, title="Nouvelle carte trouvée"):
    if not DISCORD_WEBHOOK_URL:
        raise SystemExit("DISCORD_WEBHOOK_URL manquant")

    data = {
        "embeds": [{
            "title": title,
            "image": {"url": image_url},
            "url": image_url
        }]
    }
    print(f"[INFO] Envoi vers Discord : {image_url}")
    r = SESSION.post(DISCORD_WEBHOOK_URL, json=data, timeout=TIMEOUT)
    print(f"[INFO] Statut HTTP Discord : {r.status_code}")
    r.raise_for_status()


# -------------------------------------------------------------
# 🔍 Trouver des paires (GUID, ID) dans le JSON
# -------------------------------------------------------------
def find_guid_id_pairs(obj, pairs):
    """
    Parcourt récursivement le JSON.
    Dès qu'un dict contient :
      - une valeur string qui matche un GUID
      - ET une ou plusieurs valeurs entières
    on ajoute des paires (guid, id) dans le set `pairs`.
    """
    if isinstance(obj, dict):
        guid = None
        ids = []

        for _, v in obj.items():
            if isinstance(v, str) and UUID_RE.fullmatch(v):
                guid = v.lower()

            # ⚠️ EA peut sortir des IDs > 200 selon les promos
            if isinstance(v, int) and 0 < v < 5000:
                ids.append(v)

        if guid and ids:
            for id_value in ids:
                pairs.add((guid, id_value))

        for v in obj.values():
            find_guid_id_pairs(v, pairs)

    elif isinstance(obj, list):
        for v in obj:
            find_guid_id_pairs(v, pairs)


def main():
    print(f"[INFO] WATCH_URL = {WATCH_URL!r}")
    print(f"[INFO] URL_TEMPLATE = {URL_TEMPLATE!r}")
    print(f"[INFO] DISCORD_WEBHOOK_URL défini : {bool(DISCORD_WEBHOOK_URL)}")

    if not WATCH_URL or not DISCORD_WEBHOOK_URL:
        raise SystemExit("WATCH_URL ou DISCORD_WEBHOOK_URL manquant(s)")

    seen = load_seen()
    print(f"[INFO] Entrées déjà vues dans seen.json : {len(seen)}")

    root = fetch_json(WATCH_URL)

    # Debug structure du JSON
    print("[INFO] Type root:", type(root))
    if isinstance(root, dict):
        print("[INFO] Keys root sample:", list(root.keys())[:20])

    # 1️⃣ Récupère les URLs PNG directes dans le JSON (au cas où)
    direct_pngs = []
    for v in walk_values(root):
        if isinstance(v, str):
            direct_pngs.extend(PNG_URL_RE.findall(v))
    direct_pngs = uniq(direct_pngs)
    print(f"[INFO] PNG directs trouvés dans le JSON : {len(direct_pngs)}")
    if direct_pngs[:3]:
        print("[DEBUG] Exemples PNG directs :", direct_pngs[:3])

    # 2️⃣ Trouve des paires (GUID, ID)
    pairs = set()
    find_guid_id_pairs(root, pairs)
    pairs = list(pairs)
    print(f"[INFO] Paires GUID/ID trouvées : {len(pairs)}")
    if pairs[:5]:
        print("[DEBUG] Exemples paires GUID/ID :", pairs[:5])

    # 3️⃣ Construit des URLs à partir du template avec {guid} et {id}
    templated_pngs = []
    if URL_TEMPLATE and "{guid}" in URL_TEMPLATE and "{id}" in URL_TEMPLATE:
        for guid, id_value in pairs:
            templated_pngs.extend(generate_possible_urls(URL_TEMPLATE, guid, id_value))
    elif URL_TEMPLATE and "{guid}" in URL_TEMPLATE:
        # fallback : ancien comportement si jamais {id} n'est pas dans le template
        guids = [g for g, _ in pairs] or []
        guids = uniq(guids)
        print(f"[INFO] Fallback sans {{id}} : {len(guids)} GUIDs utilisés")
        for g in guids:
            templated_pngs.extend(generate_possible_urls(URL_TEMPLATE, g))

    templated_pngs = uniq(templated_pngs)
    print(f"[INFO] PNG générés via URL_TEMPLATE : {len(templated_pngs)}")
    if templated_pngs[:3]:
        print("[DEBUG] Exemples URLs générées :", templated_pngs[:3])

    candidates = uniq(direct_pngs + templated_pngs)
    print(f"[INFO] Candidats totaux à tester : {len(candidates)}")

    # 4️⃣ Vérifie et envoie les nouvelles images
    new_sent = 0
    tested = 0
    for i, url in enumerate(candidates):
        uid = hashlib.sha1(url.encode("utf-8")).hexdigest()
        if uid in seen:
            continue

        tested += 1
        ok = test_url_ok(url)
        if not ok:
            if i < 10:
                print(f"[DEBUG] URL KO : {url}")
            continue

        try:
            discord_embed(url)
            new_sent += 1
            seen.add(uid)
            time.sleep(0.6)
        except Exception as e:
            print("Discord error:", e)

    save_seen(seen)
    print(f"✅ Terminé. {new_sent} nouvelles cartes envoyées. (testées: {tested})")


if __name__ == "__main__":
    main()
