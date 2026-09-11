import os, sys, json, datetime, requests

ACC, DB, TOK = os.environ["CF_ACCOUNT"], os.environ["CF_DB"], os.environ["CF_TOKEN"]
URL = f"https://api.cloudflare.com/client/v4/accounts/{ACC}/d1/database/{DB}/query"
H = {"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"}


def sql(q, params=None):
    r = requests.post(URL, headers=H, json={"sql": q, "params": params or []}, timeout=60)
    j = r.json()
    if not j.get("success"):
        print("ÉCHEC D1 :", json.dumps(j.get("errors"), ensure_ascii=False))
        sys.exit(1)
    return j["result"][0]


def main():
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")

    # 1. le schéma est-il en place ? (question posée à la base, pas supposée)
    t = sql("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r["name"] for r in t["results"]]
    print("Tables présentes :", tables)
    for attendue in ("societe", "comptes", "metriques", "presets", "runs"):
        if attendue not in tables:
            print(f"🔴 table `{attendue}` absente — appliquer sql/001_schema.sql d'abord")
            sys.exit(1)

    # 2. écriture
    sql("INSERT INTO runs (debut, etape, statut, message) VALUES (?,?,?,?)",
        [now, "incr1", "test", "écriture de test depuis GitHub Actions"])

    # 3. RELECTURE — c'est elle qui prouve, pas le code 200 de l'INSERT
    v = sql("SELECT id, debut, etape, statut FROM runs ORDER BY id DESC LIMIT 1")
    lignes = v["results"]
    if not lignes or lignes[0]["etape"] != "incr1":
        print("🔴 écriture non relue : la chaîne répond mais n'écrit pas")
        sys.exit(1)

    print("✅ chaîne Actions → D1 vérifiée par relecture :", lignes[0])
    print("   méta :", v.get("meta", {}))


if __name__ == "__main__":
    main()
