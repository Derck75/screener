#!/usr/bin/env python3
"""
INCREMENT 4 v2 — Postes BRUTS US via SEC frames -> table `comptes2`.

DEUX REGLES QUI CHANGENT TOUT PAR RAPPORT A v1
----------------------------------------------
1. AUCUN AGREGAT. v1 calculait le BFR, la dette et le FCF puis jetait les
   composants. Or le noyau a besoin des creances, stocks et fournisseurs
   SEPARES pour choisir entre ses quatre denominateurs, et des produits
   constates d'avance pour reperer un financement par le cycle d'exploitation.
   En agregeant trop tot, on lui retire ce qui lui permet de bien travailler.
   Les noms de colonnes sont ceux qu'attend `derives()`, au caractere pres.

2. ECRITURE DIFFERENTIELLE. Les comptes annuels bougent quatre fois par an.
   Reecrire 23 000 lignes chaque run epuise le quota D1 sans rien apporter.

MEMOIRE : les frames sont traitees poste par poste et filtrees immediatement
sur l'univers connu. Rien ne reste en memoire au-dela des CIK cibles.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from commun import (d1, journal, sonde, budget, verifier_schema,  # noqa: E402
                    ecrire_differentiel, RUN_TS)

SEC_UA = os.environ.get("SEC_UA", "screener-perso contact@example.com")
FRAMES = "https://data.sec.gov/api/xbrl/frames/us-gaap/{c}/{u}/{p}.json"
EXERCICES = [int(a) for a in os.environ.get(
    "EXERCICES", "2020,2021,2022,2023,2024,2025").split(",")]

# Postes de FLUX (periode annuelle). Plusieurs tags par poste : la taxonomie
# en offre plusieurs pour la meme notion, l'ordre est un ordre de preference.
DUREE = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"],
    "ebit": ["OperatingIncomeLoss"],
    "netIncome": ["NetIncomeLoss"],
    "grossProfit": ["GrossProfit"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment",
              "PaymentsToAcquireProductiveAssets"],
    "da": ["DepreciationDepletionAndAmortization",
           "DepreciationAmortizationAndAccretionNet"],
    "sbc": ["ShareBasedCompensation"],
    "amortAcq": ["AmortizationOfIntangibleAssets"],
    "tax": ["IncomeTaxExpenseBenefit"],
    "pretax": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
               "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "shares": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
}

# Postes de BILAN (instantane de cloture).
INSTANT = {
    "assets": ["Assets"],
    "ppe": ["PropertyPlantAndEquipmentNet"],
    # intangTot et intangExGW sont DEUX POSTES DISTINCTS, pas deux tags du
    # meme : le noyau les compare pour detecter une etiquette dementie.
    "intangTot": ["IntangibleAssetsNetIncludingGoodwill"],
    "intangExGW": ["IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet"],
    "goodwill": ["Goodwill"],
    "receivables": ["AccountsReceivableNetCurrent"],
    "inventory": ["InventoryNet"],
    "payables": ["AccountsPayableCurrent"],
    "currentLiab": ["LiabilitiesCurrent"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "equity": ["StockholdersEquity"],
    "deferredRev": ["ContractWithCustomerLiabilityCurrent", "DeferredRevenueCurrent"],
    "deferredRevNC": ["ContractWithCustomerLiabilityNoncurrent", "DeferredRevenueNoncurrent"],
    # Composants de dette : additionnes ici parce que le noyau attend un poste
    # `debt` unique, et que sa propre composition (esefComposerDette) vit cote
    # ESEF. Les loyers IFRS 16 / ASC 842 sont INCLUS, comme cote analyse.
    "_debtLT": ["LongTermDebtNoncurrent"],
    "_debtCT": ["LongTermDebtCurrent"],
    "_leaseLT": ["OperatingLeaseLiabilityNoncurrent"],
    "_leaseCT": ["OperatingLeaseLiabilityCurrent"],
}

COLONNES = ["revenue", "netIncome", "ebit", "grossProfit", "cfo", "capex", "da",
            "ebitda", "sbc", "tax", "pretax", "amortAcq", "assets", "ppe",
            "intangTot", "intangExGW", "goodwill", "receivables", "inventory",
            "payables", "currentLiab", "debt", "cash", "equity", "shares",
            "deferredRev", "deferredRevNC", "flottant"]

TICKER_OK = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")
CANARI = "AAPL"


def frame(concept, unite, periode, s):
    url = FRAMES.format(c=concept, u=unite, p=periode)
    req = urllib.request.Request(url, headers={"User-Agent": SEC_UA,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as rep:
            j = json.loads(rep.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        # 404 = ce tag n'existe pas pour cette periode. Fait sur la taxonomie,
        # pas panne : on passe au tag suivant sans le compter comme erreur.
        if e.code != 404:
            s.erreur(f"http_{e.code}", f"{concept}/{periode}")
        return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        s.erreur("reseau_sec", f"{concept}/{periode} {type(e).__name__}")
        return None
    out = {}
    for d in j.get("data", []):
        if d.get("cik") is not None and d.get("val") is not None:
            out[int(d["cik"])] = (float(d["val"]), d.get("end"))
    return out


def num(v):
    if v is None:
        return "NULL"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "NULL"
    return "NULL" if (f != f or f in (float("inf"), float("-inf"))) else repr(round(f, 4))


def main():
    s = sonde("incr4_comptes")
    print(f"incr4_comptes v2 — Run {RUN_TS}")

    s.phase("controles")
    verifier_schema("comptes2", ["revenue", "intangTot", "intangExGW",
                                 "currentLiab", "deferredRev", "sbc"])

    s.phase("univers")
    par_cik = {}
    for l in d1("SELECT ticker, cik, vaneck FROM societe "
                "WHERE cik IS NOT NULL")[0]["results"]:
        c, t = int(l["cik"]), l["ticker"]
        p = par_cik.get(c)
        # Un CIK peut porter plusieurs tickers (GOOG/GOOGL) : priorite a celui
        # que suit un indice moat, puis au plus court. Dupliquer les comptes
        # serait du volume sans information.
        if p is None or (l.get("vaneck") and not p[1]) or \
           (bool(l.get("vaneck")) == bool(p[1]) and len(t) < len(p[0])):
            par_cik[c] = (t, l.get("vaneck"))
    print(f"  {len(par_cik)} CIK dans l'univers")

    s.phase("frames")
    donnees, clotures = {}, {}
    for annee in EXERCICES:
        for groupe, periode in ((DUREE, f"CY{annee}"), (INSTANT, f"CY{annee}Q4I")):
            for poste, tags in groupe.items():
                unite = "shares" if poste == "shares" else "USD"
                for tag in tags:
                    s.compte("appels_sec")
                    f = frame(tag, unite, periode, s)
                    time.sleep(0.12)
                    if not f:
                        continue
                    for cik, (val, fin) in f.items():
                        if cik not in par_cik:
                            continue     # filtre immediat : la memoire ne
                        d = donnees.setdefault(cik, {}).setdefault(annee, {})
                        if poste not in d:        # garde l'univers entier
                            d[poste] = val
                            if fin and groupe is DUREE:
                                clotures.setdefault(cik, {}).setdefault(annee, fin)
        print(f"  {annee} traite")

    s.phase("composition")
    rangs = {}
    for cik, annees in donnees.items():
        ticker = par_cik[cik][0]
        if not TICKER_OK.match(ticker):
            s.erreur("ticker_invalide", ticker)
            continue
        for annee, d in annees.items():
            if d.get("revenue") is None and d.get("ebit") is None:
                continue   # entite sans exploitation : pas un manque de donnee
            morceaux = [d.get(k) for k in ("_debtLT", "_debtCT", "_leaseLT", "_leaseCT")]
            debt = sum(m for m in morceaux if m is not None) \
                if any(m is not None for m in morceaux) else None
            ebit, da = d.get("ebit"), d.get("da")
            ligne = {c: d.get(c) for c in COLONNES}
            ligne["debt"] = debt
            ligne["ebitda"] = (ebit + da) if (ebit is not None and da is not None) else None
            ligne["capex"] = abs(d["capex"]) if d.get("capex") is not None else None
            ligne["flottant"] = None      # saisissable a la main uniquement
            rangs[f"{ticker}|{annee}"] = ligne
            if ticker == CANARI:
                s.compte("canari_exercices")

    print(f"  {len(rangs)} lignes societe-exercice composees")

    s.phase("canari")
    if s.compteurs.get("canari_exercices", 0) < 3 or len(rangs) < 10000:
        msg = (f"canari rouge : {CANARI} {s.compteurs.get('canari_exercices', 0)} "
               f"exercices, {len(rangs)} lignes au total")
        print("PANNE : " + msg)
        journal("PANNE", "incr4_comptes", 0, "ROUGE", msg, s.resume())
        sys.exit(1)

    s.phase("differentiel")
    a_ecrire = ecrire_differentiel("comptes2", "ticker || '|' || exercice",
                                   rangs, COLONNES, s)
    if not a_ecrire:
        print("  rien a ecrire — comptes deja a jour")
        journal("OK", "incr4_comptes", 0, "VERT", "aucun changement", s.resume())
        s.afficher()
        return

    s.phase("ecriture")
    budget(len(a_ecrire), "comptes2")
    cols = "ticker, exercice, " + ", ".join(COLONNES) + ", source, clot"
    valeurs, ecrites = [], 0
    for cle, ligne in a_ecrire.items():
        ticker, annee = cle.split("|")
        cik = next((c for c, v in par_cik.items() if v[0] == ticker), None)
        clot = (clotures.get(cik, {}) or {}).get(int(annee))
        cl = "'" + clot[:10] + "'" if clot and len(clot) >= 10 else "NULL"
        cellules = ", ".join(num(ligne[c]) for c in COLONNES)
        valeurs.append(f"('{ticker}', {annee}, {cellules}, 'frames', {cl})")
    for i in range(0, len(valeurs), 200):
        lot = valeurs[i:i + 200]
        d1(f"INSERT OR REPLACE INTO comptes2 ({cols}) VALUES " + ", ".join(lot),
           lignes=len(lot), table="comptes2")
        ecrites += len(lot)
        if ecrites % 2000 == 0 or ecrites == len(valeurs):
            print(f"  ecrit {ecrites}/{len(valeurs)}")

    s.phase("rapport")
    tot = d1("SELECT COUNT(*) AS n FROM comptes2")[0]["results"][0]["n"]
    print(f"\n--- RAPPORT ---\nlignes en base : {tot}")
    couv = d1("SELECT COUNT(*) AS tot, " + ", ".join(
        f"SUM(CASE WHEN {c} IS NOT NULL THEN 1 ELSE 0 END) AS {c}"
        for c in COLONNES if c != "flottant") + " FROM comptes2")[0]["results"][0]
    t = max(1, couv["tot"])
    for c in COLONNES:
        if c != "flottant":
            print(f"  {c:14} {round(100 * (couv[c] or 0) / t, 1):5} %")

    r = s.afficher()
    journal("OK", "incr4_comptes", ecrites, "VERT",
            f"{ecrites} lignes ecrites, {tot} en base", r)
    print("OK")


if __name__ == "__main__":
    main()
