#!/usr/bin/env python3
"""
INCREMENT 4 — Comptes US via l'API `frames` de la SEC -> table `comptes`.

LEVIER : un appel `frames` rend UN concept pour TOUS les deposants. 22 postes
sur 6 exercices coutent ~150 appels au lieu de ~46 000 appels societe par
societe. C'est ce qui rend un screener mondial finançable sur un plan gratuit.

LIMITE ASSUMEE : `frames` retient le fait qui colle le mieux a la periode
calendaire demandee. Une societe a cloture decalee (juin, septembre) est mal
captee ou absente. La date de cloture reelle est stockee dans `clot` : un
exercice dont `clot` s'ecarte du 31 decembre se lit comme approximatif.

DEFINITIONS ALIGNEES SUR LE SERVEUR D'ANALYSE :
  dette = emprunts courants + non courants + passifs locatifs (ASC 842).
  Exclure les loyers produirait un ROIC flatte sur toute societe a parc loue,
  et un candidat qui echouerait au bareme suivant.

Idempotent : relancable sans dommage.
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error

import requests

CF_ACCOUNT = os.environ["CF_ACCOUNT"]
CF_DB = os.environ["CF_DB"]
CF_TOKEN = os.environ["CF_TOKEN"]
SEC_UA = os.environ.get("SEC_UA", "screener-perso contact@example.com")

D1_URL = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/d1/database/{CF_DB}/query"
FRAMES = "https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}/{unite}/{periode}.json"

EXERCICES = [2020, 2021, 2022, 2023, 2024, 2025]

# Chaque poste porte PLUSIEURS tags : la taxonomie us-gaap en offre plusieurs
# pour la meme notion et l'usage varie par societe et par epoque. L'ordre est
# un ordre de PREFERENCE — le premier tag servi gagne, les suivants comblent.
DUREE = {
    "ca": ["RevenueFromContractWithCustomerExcludingAssessedTax",
           "Revenues",
           "RevenueFromContractWithCustomerIncludingAssessedTax",
           "SalesRevenueNet"],
    "ebit": ["OperatingIncomeLoss"],
    "resultat_net": ["NetIncomeLoss"],
    "marge_brute": ["GrossProfit"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment",
              "PaymentsToAcquireProductiveAssets"],
    "actions_diluees": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "_impot": ["IncomeTaxExpenseBenefit"],
    "_avant_impot": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
}

INSTANT = {
    "actif_total": ["Assets"],
    "tresorerie": ["CashAndCashEquivalentsAtCarryingValue",
                   "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "capitaux_propres": ["StockholdersEquity"],
    "goodwill": ["Goodwill"],
    "incorporels": ["IntangibleAssetsNetExcludingGoodwill",
                    "FiniteLivedIntangibleAssetsNet"],
    "ppe": ["PropertyPlantAndEquipmentNet"],
    "_dette_lt": ["LongTermDebtNoncurrent"],
    "_dette_ct": ["LongTermDebtCurrent"],
    "_loyer_lt": ["OperatingLeaseLiabilityNoncurrent"],
    "_loyer_ct": ["OperatingLeaseLiabilityCurrent"],
    "_creances": ["AccountsReceivableNetCurrent"],
    "_stocks": ["InventoryNet"],
    "_fournisseurs": ["AccountsPayableCurrent"],
}

UNITE_ACTIONS = "shares"
COLONNES = ["ca", "ebit", "resultat_net", "marge_brute", "cfo", "capex", "fcf",
            "actif_total", "tresorerie", "dette", "capitaux_propres",
            "goodwill", "incorporels", "ppe", "bfr_exploitation",
            "actions_diluees", "impot_effectif"]

CANARI_MIN_SOCIETES = 2000
CANARI_TICKER = "AAPL"
CANARI_CA_MIN = 300e9

TICKER_OK = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")
RUN_TS = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def d1(sql, params=None):
    r = requests.post(
        D1_URL,
        headers={"Authorization": f"Bearer {CF_TOKEN}",
                 "Content-Type": "application/json"},
        json={"sql": sql, "params": params or []},
        timeout=120,
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
        "incr4_comptes_us", statut, n, canari, message,
        json.dumps(detail) if detail else None])


def frame(concept, unite, periode):
    """Rend {cik: (valeur, date_de_fin)} ou None si le concept n'existe pas."""
    url = FRAMES.format(concept=concept, unite=unite, periode=periode)
    req = urllib.request.Request(url, headers={"User-Agent": SEC_UA,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as rep:
            j = json.loads(rep.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        # 404 = ce concept n'est pas depose pour cette periode. C'est un fait
        # sur la taxonomie, pas une panne : on passe au tag suivant.
        if e.code == 404:
            return None
        print(f"    HTTP {e.code} sur {concept}/{periode}")
        return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    out = {}
    for d in j.get("data", []):
        cik = d.get("cik")
        val = d.get("val")
        if cik is None or val is None:
            continue
        out[int(cik)] = (float(val), d.get("end"))
    return out


def num(v):
    if v is None:
        return "NULL"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "NULL"
    if f != f or f in (float("inf"), float("-inf")):
        return "NULL"
    return repr(round(f, 4))


def main():
    print(f"incr4_comptes_us v1 — Run {RUN_TS}")

    # ---- univers : un seul ticker par CIK -----------------------------------
    res = d1("SELECT ticker, cik, vaneck FROM societe WHERE cik IS NOT NULL")
    par_cik = {}
    for l in res[0]["results"]:
        c = int(l["cik"])
        t = l["ticker"]
        prec = par_cik.get(c)
        # Priorite au ticker suivi par un indice moat, puis au plus court :
        # GOOG et GOOGL partagent un CIK, dupliquer leurs comptes serait du
        # volume sans information.
        if prec is None or (l.get("vaneck") and not prec[1]) or \
           (bool(l.get("vaneck")) == bool(prec[1]) and len(t) < len(prec[0])):
            par_cik[c] = (t, l.get("vaneck"))
    print(f"{len(par_cik)} CIK distincts dans l'univers")

    # ---- lecture des frames -------------------------------------------------
    donnees = {}   # {cik: {annee: {poste: valeur}}}
    clotures = {}  # {cik: {annee: date}}
    appels = manques = 0

    for annee in EXERCICES:
        print(f"exercice {annee}")
        for poste, tags in DUREE.items():
            unite = UNITE_ACTIONS if poste == "actions_diluees" else "USD"
            vus = 0
            for tag in tags:
                appels += 1
                f = frame(tag, unite, f"CY{annee}")
                time.sleep(0.12)
                if not f:
                    manques += 1
                    continue
                for cik, (val, fin) in f.items():
                    if cik not in par_cik:
                        continue
                    d = donnees.setdefault(cik, {}).setdefault(annee, {})
                    if poste not in d:
                        d[poste] = val
                        vus += 1
                        if fin:
                            clotures.setdefault(cik, {}).setdefault(annee, fin)
            print(f"  {poste:16} {vus}")

        for poste, tags in INSTANT.items():
            vus = 0
            for tag in tags:
                appels += 1
                f = frame(tag, "USD", f"CY{annee}Q4I")
                time.sleep(0.12)
                if not f:
                    manques += 1
                    continue
                for cik, (val, fin) in f.items():
                    if cik not in par_cik:
                        continue
                    d = donnees.setdefault(cik, {}).setdefault(annee, {})
                    if poste not in d:
                        d[poste] = val
                        vus += 1
            print(f"  {poste:16} {vus}")

    print(f"\n{appels} appels frames, {manques} sans reponse exploitable")

    # ---- composition des lignes --------------------------------------------
    rangs = []
    societes_retenues = set()
    for cik, annees in donnees.items():
        ticker = par_cik[cik][0]
        if not TICKER_OK.match(ticker):
            continue
        for annee, d in annees.items():
            # Filtre de substance : sans activite mesurable, la ligne n'est
            # pas un manque de donnee, c'est une entite sans exploitation.
            if d.get("ca") is None and d.get("ebit") is None:
                continue

            cfo, capex = d.get("cfo"), d.get("capex")
            fcf = cfo - abs(capex) if (cfo is not None and capex is not None) else None

            morceaux = [d.get(k) for k in ("_dette_lt", "_dette_ct",
                                           "_loyer_lt", "_loyer_ct")]
            dette = sum(m for m in morceaux if m is not None) \
                if any(m is not None for m in morceaux) else None

            cr, st, fo = d.get("_creances"), d.get("_stocks"), d.get("_fournisseurs")
            bfr = (cr or 0) + (st or 0) - (fo or 0) \
                if any(x is not None for x in (cr, st, fo)) else None

            imp, avant = d.get("_impot"), d.get("_avant_impot")
            taux = imp / avant if (imp is not None and avant and avant > 0) else None
            if taux is not None and not (0 <= taux <= 0.6):
                taux = None  # hors bornes plausibles : donnee absente, pas 0

            clot = (clotures.get(cik, {}) or {}).get(annee)
            valeurs = {
                "ca": d.get("ca"), "ebit": d.get("ebit"),
                "resultat_net": d.get("resultat_net"),
                "marge_brute": d.get("marge_brute"), "cfo": cfo,
                "capex": abs(capex) if capex is not None else None, "fcf": fcf,
                "actif_total": d.get("actif_total"),
                "tresorerie": d.get("tresorerie"), "dette": dette,
                "capitaux_propres": d.get("capitaux_propres"),
                "goodwill": d.get("goodwill"),
                "incorporels": d.get("incorporels"), "ppe": d.get("ppe"),
                "bfr_exploitation": bfr,
                "actions_diluees": d.get("actions_diluees"),
                "impot_effectif": taux,
            }
            cellules = ", ".join(num(valeurs[c]) for c in COLONNES)
            clot_sql = "'" + clot[:10] + "'" if clot and len(clot) >= 10 else "NULL"
            rangs.append(f"('{ticker}', {annee}, {cellules}, 'frames', {clot_sql})")
            societes_retenues.add(ticker)

    print(f"{len(rangs)} lignes societe-exercice, {len(societes_retenues)} societes")

    # ---- canari -------------------------------------------------------------
    ca_aapl = None
    for cik, annees in donnees.items():
        if par_cik[cik][0] == CANARI_TICKER:
            ca_aapl = (annees.get(2024) or {}).get("ca")
    if len(societes_retenues) < CANARI_MIN_SOCIETES or \
       ca_aapl is None or ca_aapl < CANARI_CA_MIN:
        msg = (f"canari rouge : {len(societes_retenues)} societes "
               f"(plancher {CANARI_MIN_SOCIETES}), {CANARI_TICKER} CA 2024 = {ca_aapl}")
        print("PANNE : " + msg)
        journal("PANNE", 0, "ROUGE", msg)
        sys.exit(1)

    # ---- ecriture -----------------------------------------------------------
    # Valeurs litterales : 21 colonnes contre 100 variables liees ne laisseraient
    # que 4 lignes par requete, soit plus de 11 000 allers-retours. Les tickers
    # sont valides par expression reguliere, tout le reste est numerique.
    cols = "ticker, exercice, " + ", ".join(COLONNES) + ", source, clot"
    ecrites = 0
    for i in range(0, len(rangs), 200):
        lot = rangs[i:i + 200]
        d1(f"INSERT OR REPLACE INTO comptes ({cols}) VALUES " + ", ".join(lot))
        ecrites += len(lot)
        if ecrites % 5000 == 0 or ecrites == len(rangs):
            print(f"  ecrit {ecrites}/{len(rangs)}")

    # ---- rapport ------------------------------------------------------------
    q = d1("SELECT COUNT(*) AS n, COUNT(DISTINCT ticker) AS s FROM comptes")[0]["results"][0]
    couv = d1("SELECT "
              "SUM(CASE WHEN ca IS NOT NULL THEN 1 ELSE 0 END) AS ca, "
              "SUM(CASE WHEN ebit IS NOT NULL THEN 1 ELSE 0 END) AS ebit, "
              "SUM(CASE WHEN fcf IS NOT NULL THEN 1 ELSE 0 END) AS fcf, "
              "SUM(CASE WHEN dette IS NOT NULL THEN 1 ELSE 0 END) AS dette, "
              "SUM(CASE WHEN marge_brute IS NOT NULL THEN 1 ELSE 0 END) AS mb, "
              "COUNT(*) AS tot FROM comptes")[0]["results"][0]
    ve = d1("SELECT COUNT(DISTINCT s.ticker) AS n FROM societe s "
            "JOIN comptes c ON c.ticker = s.ticker "
            "WHERE s.vaneck IS NOT NULL")[0]["results"][0]["n"]

    print("\n--- RAPPORT ---")
    print(f"lignes en base        : {q['n']}  ({q['s']} societes)")
    t = max(1, couv["tot"])
    for cle, lib in (("ca", "CA"), ("ebit", "EBIT"), ("fcf", "FCF"),
                     ("dette", "dette"), ("mb", "marge brute")):
        print(f"couverture {lib:12} : {round(100 * (couv[cle] or 0) / t, 1)} %")
    print(f"societes VanEck avec comptes : {ve}")

    journal("OK", ecrites, "VERT",
            f"{ecrites} lignes, {len(societes_retenues)} societes, {ve} VanEck couvertes",
            {"appels": appels, "lignes": ecrites,
             "societes": len(societes_retenues), "vaneck": ve})
    print("OK")


if __name__ == "__main__":
    main()
