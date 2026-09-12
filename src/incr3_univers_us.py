#!/usr/bin/env python3
"""
INCREMENT 3 — Univers US complet -> table `societe`.

Source : company_tickers_exchange.json, publie par la SEC. Un seul appel rend
tous les emetteurs deposants avec CIK, nom et place de cotation. C'est la seule
source officielle, gratuite et stable du pont ticker <-> CIK, et le CIK est ce
qui ouvre l'API `frames` de l'increment suivant.

NE JAMAIS ECRASER CE QUE VANECK A ECRIT. VanEck publie un secteur et un pays
propres ; la SEC ne les publie pas ici. Toute colonne deja renseignee est
conservee par COALESCE — une source qui complete ne doit pas degrader.

Idempotent : relancable sans dommage.
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

import requests

CF_ACCOUNT = os.environ["CF_ACCOUNT"]
CF_DB = os.environ["CF_DB"]
CF_TOKEN = os.environ["CF_TOKEN"]

# La SEC exige un User-Agent identifiant. Sans lui : 403.
SEC_UA = os.environ.get("SEC_UA", "screener-perso contact@example.com")

D1_URL = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/d1/database/{CF_DB}/query"
SRC = "https://www.sec.gov/files/company_tickers_exchange.json"

# Places retenues. OTC et place vide sont ecartees : titres peu liquides,
# souvent sans comptes exploitables, et hors de tout univers investissable.
PLACES = {"NYSE", "NASDAQ", "NYSE AMERICAN", "NYSEAMERICAN", "CBOE", "BATS"}

CANARI_MIN = 4000
CANARI_TICKER = "AAPL"

RUN_TS = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def d1(sql, params=None):
    r = requests.post(
        D1_URL,
        headers={"Authorization": f"Bearer {CF_TOKEN}",
                 "Content-Type": "application/json"},
        json={"sql": sql, "params": params or []},
        timeout=60,
    )
    try:
        j = r.json()
    except Exception:
        print(f"ECHEC D1 : reponse illisible ({r.status_code}) {r.text[:300]}")
        sys.exit(1)
    if not j.get("success"):
        print(f"ECHEC D1 : {json.dumps(j.get('errors'))[:400]}")
        sys.exit(1)
    return j["result"]


def journal(statut, n, canari, message, detail=None):
    d1("INSERT INTO runs (debut, fin, etape, statut, lignes_ecrites, canari, message, detail)"
       " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
       [RUN_TS, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "incr3_univers_us", statut, n, canari, message,
        json.dumps(detail) if detail else None])


def lire_sec():
    req = urllib.request.Request(SRC, headers={"User-Agent": SEC_UA,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as rep:
        j = json.loads(rep.read().decode("utf-8", "replace"))

    # Deux formes possibles selon les millesimes du fichier : colonnes/lignes,
    # ou dictionnaire indexe. Les deux sont acceptees plutot que de supposer.
    if isinstance(j, dict) and "fields" in j and "data" in j:
        champs = [c.lower() for c in j["fields"]]
        return [dict(zip(champs, ligne)) for ligne in j["data"]]
    if isinstance(j, dict):
        out = []
        for v in j.values():
            if isinstance(v, dict):
                out.append({"cik": v.get("cik_str") or v.get("cik"),
                            "name": v.get("title") or v.get("name"),
                            "ticker": v.get("ticker"),
                            "exchange": v.get("exchange")})
        return out
    raise ValueError(f"forme inattendue : {type(j).__name__}")


def main():
    print(f"incr3_univers_us v1 — Run {RUN_TS}")

    try:
        brut = lire_sec()
    except (urllib.error.URLError, TimeoutError, ValueError,
            json.JSONDecodeError) as e:
        msg = f"lecture SEC impossible : {type(e).__name__} {str(e)[:150]}"
        print("PANNE : " + msg)
        journal("PANNE", 0, "ROUGE", msg)
        sys.exit(1)

    print(f"{len(brut)} entrees dans le fichier SEC")

    retenues = {}
    hors_place = 0
    places_vues = {}
    for e in brut:
        t = (e.get("ticker") or "").strip().upper()
        place = (e.get("exchange") or "").strip().upper()
        if not t:
            continue
        places_vues[place or "(vide)"] = places_vues.get(place or "(vide)", 0) + 1
        if place not in PLACES:
            hors_place += 1
            continue
        cik = e.get("cik")
        cik = str(cik).zfill(10) if cik is not None else None
        # Yahoo separe les classes d'actions par un tiret la ou la SEC ne met
        # rien ou un point. Meme convention que le mapping VanEck.
        ty = t.replace(".", "-").replace("/", "-")
        retenues[ty] = {"cik": cik, "nom": (e.get("name") or "").strip(),
                        "place": place}

    print(f"{len(retenues)} societes retenues, {hors_place} ecartees (place)")
    print("places rencontrees : " + ", ".join(
        f"{p} {n}" for p, n in sorted(places_vues.items(), key=lambda x: -x[1])[:8]))

    # CANARI. Un fichier tronque ou une forme changee produirait un univers
    # partiel qui se lirait comme un univers reel.
    if len(retenues) < CANARI_MIN or CANARI_TICKER not in retenues:
        msg = (f"canari rouge : {len(retenues)} societes (plancher {CANARI_MIN}), "
               f"{CANARI_TICKER} {'present' if CANARI_TICKER in retenues else 'ABSENT'}")
        print("PANNE : " + msg)
        journal("PANNE", 0, "ROUGE", msg, {"retenues": len(retenues)})
        sys.exit(1)

    COLS = ("ticker, cik, nom, place, pays_siege, eligible_pea, "
            "source_eligibilite, origine, maj")
    # COALESCE : la valeur DEJA en base gagne. VanEck a un secteur et un nom
    # commercial ; la SEC a la raison sociale et le CIK. Chacune complete.
    MAJ = ("cik=excluded.cik, "
           "nom=COALESCE(societe.nom, excluded.nom), "
           "place=COALESCE(societe.place, excluded.place), "
           "pays_siege=COALESCE(societe.pays_siege, excluded.pays_siege), "
           "eligible_pea=COALESCE(societe.eligible_pea, excluded.eligible_pea), "
           "source_eligibilite=COALESCE(societe.source_eligibilite, excluded.source_eligibilite), "
           "origine=CASE WHEN origine IS NULL THEN 'sec' "
           "WHEN instr(origine,'sec')>0 THEN origine ELSE origine||',sec' END, "
           "maj=excluded.maj")

    rangs = [[t, e["cik"], e["nom"], e["place"], "US", 0,
              "emetteur deposant SEC — hors UE/EEE", "sec", RUN_TS]
             for t, e in retenues.items()]

    # 9 colonnes x 10 lignes = 90 variables liees. D1 plafonne a 100.
    ecrites = 0
    valeurs_10 = ", ".join(["(?, ?, ?, ?, ?, ?, ?, ?, ?)"] * 10)
    for i in range(0, len(rangs), 10):
        lot = rangs[i:i + 10]
        valeurs = valeurs_10 if len(lot) == 10 else ", ".join(
            ["(?, ?, ?, ?, ?, ?, ?, ?, ?)"] * len(lot))
        plats = [v for ligne in lot for v in ligne]
        d1(f"INSERT INTO societe ({COLS}) VALUES {valeurs} "
           f"ON CONFLICT(ticker) DO UPDATE SET {MAJ}", plats)
        ecrites += len(lot)
        if ecrites % 1000 == 0 or ecrites == len(rangs):
            print(f"  ecrit {ecrites}/{len(rangs)}")

    tot = d1("SELECT COUNT(*) AS n FROM societe")[0]["results"][0]["n"]
    cik_ok = d1("SELECT COUNT(*) AS n FROM societe WHERE cik IS NOT NULL")[0]["results"][0]["n"]
    ve_cik = d1("SELECT COUNT(*) AS n FROM societe "
                "WHERE vaneck IS NOT NULL AND place = 'US' AND cik IS NULL"
                )[0]["results"][0]["n"]

    print("\n--- RAPPORT ---")
    print(f"societes ecrites      : {ecrites}")
    print(f"univers total         : {tot}")
    print(f"dont CIK renseigne    : {cik_ok}")
    print(f"VanEck US sans CIK    : {ve_cik}")

    journal("OK", ecrites, "VERT",
            f"{ecrites} societes SEC, univers {tot}, {cik_ok} CIK, {ve_cik} VanEck US orphelins",
            {"retenues": len(retenues), "hors_place": hors_place, "univers": tot})
    print("OK")


if __name__ == "__main__":
    main()
