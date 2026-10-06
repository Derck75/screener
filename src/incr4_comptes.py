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

import datetime
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
# FENETRE LONGUE — 2014-2025. Six exercices ne suffisaient pas : 2020-2025
# contient le choc Covid, la relance de 2021 et le pic inflationniste de 2022,
# soit trois anomalies sur six. Une mediane calculee la-dessus n'est pas un
# pouvoir beneficiaire normalise, et l'EPV capitalisait des sommets de cycle —
# Builders FirstSource ressortait a 219 % du cours. Douze exercices traversent
# le creux petrolier de 2015-2016 et un cycle complet.
# Second effet : le filtre dur du cadre exige dix exercices pour un verdict
# ferme. A six, toute societe etait en fenetre courte.
# FENETRE GLISSANTE (audit M8) : les douze derniers exercices clos, calcules
# a l'execution. Ecrite en dur (2014-2025), elle n'aurait jamais lu 2026.
DERNIER_EXERCICE = time.gmtime().tm_year - 1
EXERCICES = [int(a) for a in (os.environ.get("EXERCICES") or ",".join(
    str(a) for a in range(DERNIER_EXERCICE - 11, DERNIER_EXERCICE + 1))).split(",")]

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
    # Troisieme tag, en dernier recours : Visa ne depose ses amortissements que
    # sous DepreciationAndAmortization (2007-2025). Libelle SEC « nonproduction »
    # : chez un industriel il peut exclure ce qui est loge dans le cout des ventes.
    "da": ["DepreciationDepletionAndAmortization",
           "DepreciationAmortizationAndAccretionNet",
           "DepreciationAndAmortization"],
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
    # DETTE PAR FAMILLES (audit C4). Deux tags seulement etaient lus :
    # PACCAR, D.R. Horton, Lennar, Deere ressortaient sans dette, donc avec un
    # capital investi errone, et sortaient du crible sous un faux motif. Chaque
    # famille est un poste distinct (un seul tag retenu par famille, dans
    # l'ordre), et la composition se fait plus bas, par candidats.
    "_ltdNC": ["LongTermDebtNoncurrent"],          # emprunts longs, part non courante
    "_ltdTot": ["LongTermDebt"],                   # emprunts longs, part courante COMPRISE
    "_debtCur": ["DebtCurrent"],                   # total de la dette courante
    "_ltdCur": ["LongTermDebtCurrent"],            # part courante des emprunts longs
    "_stb": ["ShortTermBorrowings", "CommercialPaper"],   # emprunts courts
    "_notes": ["NotesPayable"],                    # billets a payer (promoteurs, captives)
    # Loyers IFRS 16 / ASC 842 INCLUS, comme cote analyse.
    "_leaseLT": ["OperatingLeaseLiabilityNoncurrent"],
    "_leaseCT": ["OperatingLeaseLiabilityCurrent"],
    "_leaseTot": ["OperatingLeaseLiability"],
}


def dette_us(d):
    """Compose `debt` depuis les familles, par CANDIDATS : chaque candidat est
    une decomposition complete et sans recouvrement ; le PLUS GRAND l'emporte.
    Meme doctrine que la composition ESEF du serveur : une famille manquante
    fait sous-estimer, jamais surestimer, donc le maximum est le bon arbitre.
      A  part non courante + dette courante (totale, ou recomposee)
      B  emprunts longs TOTAUX + emprunts courts (sans la part courante,
         deja comprise dans le total)
      C  billets a payer, quand c'est la seule presentation du deposant
    Loyers d'exploitation ajoutes a chaque fois. Rend (dette, candidat)."""
    g = d.get
    ct_comp = None
    if g("_ltdCur") is not None or g("_stb") is not None:
        ct_comp = (g("_ltdCur") or 0) + (g("_stb") or 0)
    ct = g("_debtCur") if g("_debtCur") is not None else ct_comp
    cands = []
    if g("_ltdNC") is not None or ct is not None:
        cands.append(((g("_ltdNC") or 0) + (ct or 0), "A"))
    if g("_ltdTot") is not None:
        cands.append((g("_ltdTot") + (g("_stb") or 0), "B"))
    if g("_notes") is not None:
        cands.append((g("_notes"), "C"))
    fin, cand = max(cands) if cands else (None, None)
    loy = [x for x in (g("_leaseLT"), g("_leaseCT")) if x is not None]
    # Composantes ET total publies : le plus grand, une composante manquante
    # (part courante seule, par exemple) ne devant jamais sous-estimer.
    cands_loy = [x for x in (sum(loy) if loy else None, g("_leaseTot")) if x is not None]
    loyers = max(cands_loy) if cands_loy else None
    if fin is None and loyers is None:
        return None, None
    return (fin or 0) + (loyers or 0), cand

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


def periode_instant(clot, annee):
    """Frame instantanee qui porte le bilan de CLOTURE de l'exercice (audit M2).

    Le bilan etait lu au 31 decembre pour tout le monde (`CY{annee}Q4I`) :
    une societe qui clot en juin ou en septembre voyait ses flux d'un exercice
    rapportes au bilan d'un autre, six ou trois mois plus loin — le ROIC du
    screener divergeait de `dossier` pour Microsoft ou Visa. La cloture vient
    du flux annuel du meme exercice ; sans elle, le 31 decembre reste le repli."""
    if clot and len(clot) >= 7:
        try:
            y, m = int(clot[:4]), int(clot[5:7])
            if 1 <= m <= 12:
                return f"CY{y}Q{(m - 1) // 3 + 1}I"
        except ValueError:
            pass
    return f"CY{annee}Q4I"


def proche(fin, clot, jours=20):
    """Un instant retenu doit tomber sur la cloture (exercices de 52/53
    semaines compris). Sans cloture connue, aucune contrainte."""
    if not clot or not fin:
        return True
    try:
        ecart = datetime.date.fromisoformat(fin[:10]) - datetime.date.fromisoformat(clot[:10])
    except ValueError:
        return True
    return abs(ecart.days) <= jours


def collecter_annee(annee, par_cik, donnees, clotures, s, lire=None, pause=0.25):
    """Flux d'abord (ils donnent la cloture), bilan ensuite, cale sur elle."""
    lire = lire or frame

    def verser(poste, f, ciks=None, controle=False):
        for cik, (val, fin) in f.items():
            if cik not in par_cik or (ciks is not None and cik not in ciks):
                continue         # filtre immediat : la memoire ne garde que l'univers
            if controle and not proche(fin, clotures.get(cik, {}).get(annee)):
                s.compte("bilan_hors_cloture")
                continue
            d = donnees.setdefault(cik, {}).setdefault(annee, {})
            if poste not in d:
                d[poste] = val
                if fin and not controle:
                    clotures.setdefault(cik, {}).setdefault(annee, fin)

    for poste, tags in DUREE.items():
        unite = "shares" if poste == "shares" else "USD"
        for tag in tags:
            s.compte("appels_sec")
            f = lire(tag, unite, f"CY{annee}", s)
            time.sleep(pause)
            if f:
                verser(poste, f)

    cibles = {}
    for cik in par_cik:
        cibles.setdefault(periode_instant(clotures.get(cik, {}).get(annee), annee), set()).add(cik)
    decembre = f"CY{annee}Q4I"
    s.compte("bilans_hors_decembre", sum(len(v) for k, v in cibles.items() if k != decembre))
    for poste, tags in INSTANT.items():
        for tag in tags:
            for periode in sorted(cibles, key=lambda p: (p != decembre, p)):
                s.compte("appels_sec")
                f = lire(tag, "USD", periode, s)
                time.sleep(pause)
                if f:
                    verser(poste, f, cibles[periode], controle=True)


def num(v):
    if v is None:
        return "NULL"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "NULL"
    return "NULL" if (f != f or f in (float("inf"), float("-inf"))) else repr(round(f, 4))


def main():
    s = sonde("incr4_comptes_" + "_".join(str(e) for e in EXERCICES))
    print(f"incr4_comptes v3 — Run {RUN_TS} — exercices {EXERCICES[0]}-{EXERCICES[-1]}")

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
        collecter_annee(annee, par_cik, donnees, clotures, s)
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
            debt, cand = dette_us(d)
            if cand:
                s.compte("dette_candidat_" + cand)
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
    # CANARI A DENSITE VARIABLE. Un plancher fixe par exercice ferait echouer
    # les annees anciennes a tort : moins de societes deposaient en 2014, et
    # beaucoup de celles d'aujourd'hui n'etaient pas cotees. Le plancher suit
    # donc l'anciennete, et le temoin reste la vraie garde.
    def plancher(annee):
        recul = DERNIER_EXERCICE - annee
        return 2500 if recul <= 3 else (2000 if recul <= 7 else 1200)
    mini = sum(plancher(a) for a in EXERCICES)
    if s.compteurs.get("canari_exercices", 0) < 1 or len(rangs) < mini:
        msg = (f"canari rouge : {CANARI} {s.compteurs.get('canari_exercices', 0)} "
               f"exercices, {len(rangs)} lignes (plancher {mini})")
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
