#!/usr/bin/env python3
"""
INCREMENT 2 — Univers VanEck -> table `societe`.

Reprend l'URL et le mapping Bloomberg -> Yahoo deja utilises par l'outil `moat`
du serveur d'analyse. Toute divergence ici produirait deux univers qui ne se
recoupent pas, donc des candidats introuvables a l'analyse.

Idempotent : relancable sans dommage.
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

import requests

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

CF_ACCOUNT = os.environ["CF_ACCOUNT"]
CF_DB = os.environ["CF_DB"]
CF_TOKEN = os.environ["CF_TOKEN"]

D1_URL = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/d1/database/{CF_DB}/query"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

ETF = {
    "MOAT": {"blockId": 144446, "pageId": 233093, "nom": "US (MOAT)"},
    "SMOT": {"blockId": 181566, "pageId": 233109, "nom": "US small-mid (SMOT)"},
    "MOTI": {"blockId": 144448, "pageId": 233097, "nom": "International (MOTI)"},
}

# Suffixe Bloomberg -> suffixe Yahoo. Copie conforme du worker.
BBG_YAHOO = {
    "GR": ".DE", "FP": ".PA", "NA": ".AS", "LN": ".L", "DC": ".CO", "SS": ".ST",
    "SW": ".SW", "JP": ".T", "AU": ".AX", "HK": ".HK", "C1": ".SS", "C2": ".SZ",
    "TT": ".TW", "BZ": ".SA", "IM": ".MI", "SM": ".MC", "NO": ".OL", "FH": ".HE",
}
BBG_EXCEPTIONS = {"NOVOB DC": "NOVO-B.CO"}

# UE + EEE. Le pays vient de VanEck : c'est une SOURCE DECLAREE, pas une
# deduction depuis le suffixe du ticker. Reste a confirmer contre la liste
# du courtier avant toute decision.
UE_EEE = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE", "IS", "LI", "NO",
}
NOMS_PAYS = {
    "austria": "AT", "belgium": "BE", "bulgaria": "BG", "croatia": "HR",
    "cyprus": "CY", "czech republic": "CZ", "czechia": "CZ", "denmark": "DK",
    "estonia": "EE", "finland": "FI", "france": "FR", "germany": "DE",
    "greece": "GR", "hungary": "HU", "ireland": "IE", "italy": "IT",
    "latvia": "LV", "lithuania": "LT", "luxembourg": "LU", "malta": "MT",
    "netherlands": "NL", "poland": "PL", "portugal": "PT", "romania": "RO",
    "slovakia": "SK", "slovenia": "SI", "spain": "ES", "sweden": "SE",
    "iceland": "IS", "liechtenstein": "LI", "norway": "NO",
    "united states": "US", "united kingdom": "GB", "switzerland": "CH",
    "japan": "JP", "china": "CN", "hong kong": "HK", "taiwan": "TW",
    "south korea": "KR", "korea": "KR", "india": "IN", "brazil": "BR",
    "canada": "CA", "australia": "AU", "singapore": "SG", "mexico": "MX",
    "south africa": "ZA", "israel": "IL", "indonesia": "ID", "thailand": "TH",
}

CANARI_MIN = {"MOAT": 30, "SMOT": 30, "MOTI": 30}

RUN_TS = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------
# D1
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# VanEck
# --------------------------------------------------------------------------

def holdings(cle):
    c = ETF[cle]
    url = ("https://www.vaneck.com/Main/HoldingsBlock/GetDataset/"
           f"?blockId={c['blockId']}&pageId={c['pageId']}&ticker={cle}")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=45) as rep:
            j = json.loads(rep.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        print(f"  {cle} : ECHEC de lecture ({type(e).__name__})")
        return None
    lignes = j.get("Holdings")
    if not isinstance(lignes, list):
        print(f"  {cle} : reponse sans champ Holdings")
        return None
    out = []
    for h in lignes:
        if h.get("AssetClass") != "Stock":
            continue
        out.append({
            "bbg": (h.get("Label") or "").strip(),
            "nom": (h.get("HoldingName") or "").strip(),
            "pays": (h.get("Country") or "").strip(),
            "secteur": (h.get("Sector") or "").strip(),
        })
    print(f"  {cle} : {len(out)} lignes actions")
    return out


def vers_yahoo(bbg):
    """'MSFT US' -> 'MSFT' ; 'AIR FP' -> 'AIR.PA' ; inconnu -> None."""
    if not bbg:
        return None
    if bbg in BBG_EXCEPTIONS:
        return BBG_EXCEPTIONS[bbg]
    morceaux = bbg.split()
    if len(morceaux) < 2:
        return None
    base, pays = morceaux[0], morceaux[-1].upper()
    if pays == "US":
        return base
    suffixe = BBG_YAHOO.get(pays)
    return base + suffixe if suffixe else None


def code_pays(valeur):
    """Accepte 'FR', 'France', 'FRANCE'. Rend None si non reconnu."""
    if not valeur:
        return None
    v = valeur.strip()
    if len(v) == 2 and v.isalpha():
        return v.upper()
    return NOMS_PAYS.get(v.lower())


# --------------------------------------------------------------------------
# Programme
# --------------------------------------------------------------------------

def main():
    print(f"Run {RUN_TS}")
    print("Lecture des holdings VanEck :")

    par_ticker = {}
    pays_inconnus = {}
    non_resolus = []
    panne = []

    for cle in ETF:
        lignes = holdings(cle)
        if lignes is None or len(lignes) < CANARI_MIN[cle]:
            panne.append(cle)
            continue
        for h in lignes:
            t = vers_yahoo(h["bbg"])
            if not t:
                non_resolus.append(h["bbg"])
                t = "?" + h["bbg"]
            e = par_ticker.setdefault(t, {
                "bbg": h["bbg"], "nom": h["nom"], "pays": h["pays"],
                "secteur": h["secteur"], "etf": [],
            })
            if cle not in e["etf"]:
                e["etf"].append(cle)

    # CANARI. Un ETF muet ou tronque signifie que la page a change de forme :
    # ecrire quand meme marquerait des centaines de societes comme SORTIES
    # d'un indice qu'elles n'ont jamais quitte.
    if panne:
        msg = f"canari rouge sur {', '.join(panne)} — aucune ecriture"
        print("PANNE : " + msg)
        d1("INSERT INTO runs (debut, fin, etape, statut, lignes_ecrites, canari, message)"
           " VALUES (?, ?, ?, ?, ?, ?, ?)",
           [RUN_TS, RUN_TS, "incr2_vaneck", "PANNE", 0, "ROUGE", msg])
        sys.exit(1)

    print(f"\n{len(par_ticker)} societes distinctes apres dedoublonnage")

    # Preparation des lignes
    rangs = []
    for t, e in par_ticker.items():
        cp = code_pays(e["pays"])
        if cp is None and e["pays"]:
            pays_inconnus[e["pays"]] = pays_inconnus.get(e["pays"], 0) + 1
        if cp is None:
            elig, src = None, f"pays non reconnu : {e['pays']}"
        elif cp in UE_EEE:
            elig, src = 1, f"siege {cp} (UE/EEE) — source VanEck, a confirmer courtier"
        else:
            elig, src = 0, f"siege {cp} hors UE/EEE — source VanEck"
        place = None
        if "." in t and not t.startswith("?"):
            place = t.split(".")[-1]
        elif not t.startswith("?"):
            place = "US"
        rangs.append([t, e["bbg"], e["nom"], cp, place, e["secteur"],
                      elig, src, ",".join(e["etf"]), RUN_TS])

    # Ecriture par lots. 10 colonnes x 60 lignes = 600 variables liees,
    # sous la limite SQLite de 999.
    COLS = ("ticker, ticker_bbg, nom, pays_siege, place, secteur, "
            "eligible_pea, source_eligibilite, vaneck, maj")
    MAJ = ("nom=excluded.nom, pays_siege=excluded.pays_siege, "
           "place=excluded.place, secteur=excluded.secteur, "
           "eligible_pea=excluded.eligible_pea, "
           "source_eligibilite=excluded.source_eligibilite, "
           "vaneck=excluded.vaneck, vaneck_sorti_le=NULL, maj=excluded.maj")
    ecrites = 0
    for i in range(0, len(rangs), 60):
        lot = rangs[i:i + 60]
        valeurs = ", ".join(["(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"] * len(lot))
        plats = [v for ligne in lot for v in ligne]
        d1(f"INSERT INTO societe ({COLS}) VALUES {valeurs} "
           f"ON CONFLICT(ticker) DO UPDATE SET {MAJ}", plats)
        ecrites += len(lot)
        print(f"  ecrit {ecrites}/{len(rangs)}")

    # Sorties d'indice : journalisees, jamais effacees.
    res = d1("UPDATE societe SET vaneck_sorti_le = ? "
             "WHERE vaneck IS NOT NULL AND vaneck_sorti_le IS NULL AND maj < ?",
             [RUN_TS, RUN_TS])
    sorties = res[0].get("meta", {}).get("changes", 0)

    # Rapport
    print("\n--- RAPPORT ---")
    print(f"societes ecrites      : {ecrites}")
    print(f"sorties d'indice      : {sorties}")
    print(f"tickers non resolus   : {len(non_resolus)}"
          f"  ({round(100 * len(non_resolus) / max(1, ecrites), 1)} %)")
    if non_resolus:
        print("  a corriger dans BBG_YAHOO ou BBG_EXCEPTIONS :")
        for b in sorted(set(non_resolus))[:25]:
            print(f"    {b}")
    if pays_inconnus:
        print("  libelles de pays non reconnus (a ajouter a NOMS_PAYS) :")
        for p, n in sorted(pays_inconnus.items(), key=lambda x: -x[1]):
            print(f"    {p} ({n})")

    pea = d1("SELECT COUNT(*) AS n FROM societe WHERE eligible_pea = 1 "
             "AND vaneck IS NOT NULL AND vaneck_sorti_le IS NULL")
    n_pea = pea[0]["results"][0]["n"]
    print(f"dont eligibles PEA    : {n_pea}")

    d1("INSERT INTO runs (debut, fin, etape, statut, lignes_ecrites, canari, message)"
       " VALUES (?, ?, ?, ?, ?, ?, ?)",
       [RUN_TS, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "incr2_vaneck", "OK", ecrites, "VERT",
        f"{ecrites} societes, {sorties} sorties, {len(non_resolus)} non resolus, {n_pea} PEA"])
    print("OK")


if __name__ == "__main__":
    main()
