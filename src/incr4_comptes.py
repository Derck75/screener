#!/usr/bin/env python3
"""
INCREMENT 6 — Etage prix -> colonnes de prix de `metriques`.

NE TRAITE QUE LES PRE-QUALIFIEES. Le filtre de qualite ne depend pas du prix :
payer un appel de cotation pour 4 400 societes dont 3 500 sont deja hors jeu
serait du temps et du quota depenses sans decision au bout.

UN SEUL APPEL PAR SOCIETE rend le cours du jour ET six ans de clotures
mensuelles. Le multiple median PROPRE AU TITRE en decoule sans appel
supplementaire — et c'est la seule reference de cherte utilisable : une
mediane sectorielle melange des societes aux moats et aux marges differents,
et n'est pas sourcable de facon stable.

PLANCHER EPV, JAMAIS UNE FAIR VALUE. EBIT median imposé, capitalise a un
taux-obstacle pose de 10 %, dette nette deduite. Aucun WACC, aucun beta. Il dit
ce que vaudrait la societe si elle ne grandissait plus jamais — un plancher, a
cote duquel se lit la part du cours qui repose sur une croissance non encore
realisee. Il ne se moyenne avec rien.

Idempotent : relancable sans dommage.
"""

import json
import os
import statistics as st
import sys
import time
import urllib.request
import urllib.error

import requests

CF_ACCOUNT = os.environ["CF_ACCOUNT"]
CF_DB = os.environ["CF_DB"]
CF_TOKEN = os.environ["CF_TOKEN"]

D1_URL = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/d1/database/{CF_DB}/query"
CHART = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
         "?range=6y&interval=1mo")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Seuil d'entree dans l'etage prix. Volontairement LARGE : le screener trie,
# il ne conclut pas, et une Narrow bien decotee doit pouvoir sortir.
ROIC_MIN = float(os.environ.get("ROIC_MIN_COURS", "10"))
TAUX_OBSTACLE_EPV = 0.10   # pose par le cadre, jamais derive d'un beta
TAUX_IMPOT_DEFAUT = 0.21
EPV_MIN_EXERCICES = 5

CANARI_TICKER = "AAPL"
CANARI_COURS_MIN = 50
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
        "incr6_prix", statut, n, canari, message,
        json.dumps(detail) if detail else None])


def chart(sym):
    """Rend (cours, [(timestamp, close)], devise) ou None."""
    req = urllib.request.Request(CHART.format(sym=sym),
                                 headers={"User-Agent": UA, "Accept": "application/json"})
    for essai in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as rep:
                j = json.loads(rep.read().decode("utf-8", "replace"))
            break
        except urllib.error.HTTPError as e:
            if e.code == 429:            # quota Yahoo : attendre, pas abandonner
                time.sleep(5 * (essai + 1))
                continue
            return None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(1)
            continue
    else:
        return None

    try:
        r = j["chart"]["result"][0]
        meta = r.get("meta") or {}
        ts = r.get("timestamp") or []
        cl = ((r.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    except (KeyError, IndexError, TypeError):
        return None

    serie = [(t, c) for t, c in zip(ts, cl) if c is not None]
    cours = meta.get("regularMarketPrice")
    if cours is None and serie:
        cours = serie[-1][1]
    if not cours:
        return None
    return float(cours), serie, meta.get("currency")


def close_proche(serie, iso):
    """Cloture mensuelle la plus proche d'une date ISO."""
    if not serie or not iso:
        return None
    try:
        cible = time.mktime(time.strptime(iso[:10], "%Y-%m-%d"))
    except ValueError:
        return None
    best, ecart = None, None
    for t, c in serie:
        e = abs(t - cible)
        if ecart is None or e < ecart:
            best, ecart = c, e
    # Au-dela de 75 jours, le cours ne decrit plus la cloture visee.
    return best if (ecart is not None and ecart < 75 * 86400) else None


def main():
    print(f"incr6_prix v1 — Run {RUN_TS}")

    # ---- diagnostic demande : pourquoi la moitie de l'univers est ecartee ----
    print("\nventilation des exclusions mecaniques :")
    for l in d1("SELECT exclusion, COUNT(*) AS n FROM metriques "
                "WHERE exclusion IS NOT NULL GROUP BY exclusion ORDER BY n DESC"
                )[0]["results"]:
        print(f"  {l['n']:5}  {l['exclusion']}")
    mixte = d1("SELECT COUNT(*) AS n FROM metriques WHERE denominateur_roic = 'mixte'"
               )[0]["results"][0]["n"]
    print(f"  {mixte:5}  (hors exclusion) series a denominateur de ROIC mixte")

    # ---- cibles -------------------------------------------------------------
    cibles = [l["ticker"] for l in d1(
        "SELECT ticker FROM metriques WHERE exclusion IS NULL "
        "AND roic_median >= ? ORDER BY roic_median DESC", [ROIC_MIN]
    )[0]["results"]]
    print(f"\n{len(cibles)} societes pre-qualifiees (ROIC median >= {ROIC_MIN} %)")
    if not cibles:
        journal("PANNE", 0, "ROUGE", "aucune cible pre-qualifiee")
        sys.exit(1)

    # ---- comptes des cibles -------------------------------------------------
    vise = set(cibles)
    comptes = {}
    page = 0
    while True:
        r = d1("SELECT ticker, exercice, ebit, netIncome, cfo, capex, debt, "
               "cash, shares, tax, pretax, clot FROM comptes2 "
               "ORDER BY ticker, exercice LIMIT 5000 OFFSET ?", [page * 5000]
               )[0]["results"]
        if not r:
            break
        for l in r:
            if l["ticker"] in vise:
                comptes.setdefault(l["ticker"], {})[int(l["exercice"])] = l
        page += 1

    # ---- cotations ----------------------------------------------------------
    maj = []
    echecs = refus_epv = 0
    cours_canari = None

    for i, t in enumerate(cibles, 1):
        c = chart(t)
        time.sleep(0.25)
        if c is None:
            echecs += 1
            continue
        cours, serie, devise = c
        if t == CANARI_TICKER:
            cours_canari = cours

        annees = comptes.get(t) or {}
        ans = sorted(annees)
        if not ans:
            continue
        der = annees[ans[-1]]
        actions = der.get("shares")

        capi = cours * actions if actions else None
        fcf_der = (der["cfo"] - abs(der["capex"])) \
            if (der.get("cfo") is not None and der.get("capex") is not None) else None
        fcf_y = (fcf_der / capi) if (fcf_der is not None and capi) else None

        # -- plancher EPV ------------------------------------------------------
        epv = epv_sur_cours = None
        ebits = [annees[a]["ebit"] for a in ans if annees[a].get("ebit") is not None]
        taux = [annees[a]["tax"] / annees[a]["pretax"] for a in ans
                if annees[a].get("tax") is not None
                and (annees[a].get("pretax") or 0) > 0
                and 0 <= annees[a]["tax"] / annees[a]["pretax"] <= 0.6]
        if len(ebits) >= EPV_MIN_EXERCICES and actions:
            ebit_med = st.median(ebits)
            t_med = st.median(taux) if taux else TAUX_IMPOT_DEFAUT
            if ebit_med > 0:
                capitalise = ebit_med * (1 - t_med) / TAUX_OBSTACLE_EPV
                dn = (der.get("debt") or 0) - (der.get("cash") or 0)
                if dn < capitalise:
                    epv = (capitalise - dn) / actions
                    epv_sur_cours = epv / cours
                else:
                    refus_epv += 1   # dette nette > pouvoir capitalise
            else:
                refus_epv += 1
        else:
            refus_epv += 1

        # -- multiples : mediane DU TITRE, jamais du secteur --------------------
        per_cour = None
        bpa_der = (der["netIncome"] / actions) \
            if (der.get("netIncome") is not None and actions) else None
        if bpa_der and bpa_der > 0:
            per_cour = cours / bpa_der

        pers = []
        for a in ans:
            d = annees[a]
            act, rn = d.get("shares"), d.get("netIncome")
            if not act or rn is None or rn <= 0:
                continue
            px = close_proche(serie, d.get("clot") or f"{a}-12-31")
            if px:
                pers.append(px / (rn / act))
        per_med = st.median(pers) if len(pers) >= 3 else None
        ecart = (per_cour / per_med - 1) if (per_cour and per_med) else None

        maj.append((t, cours, capi, epv, epv_sur_cours, fcf_y, per_cour, per_med, ecart))
        if i % 100 == 0:
            print(f"  {i}/{len(cibles)} cotations, {echecs} echecs")

    print(f"  {len(maj)} cotations exploitables, {echecs} echecs")

    # ---- canari -------------------------------------------------------------
    if cours_canari is None or cours_canari < CANARI_COURS_MIN or \
       len(maj) < 0.6 * len(cibles):
        msg = (f"canari rouge : {CANARI_TICKER} = {cours_canari}, "
               f"{len(maj)}/{len(cibles)} cotations")
        print("PANNE : " + msg)
        journal("PANNE", 0, "ROUGE", msg)
        sys.exit(1)

    # ---- ecriture -----------------------------------------------------------
    ecrites = 0
    for t, cours, capi, epv, esc, fy, pc, pm, ec in maj:
        d1("UPDATE metriques SET cours = ?, plancher_epv = ?, epv_sur_cours = ?, "
           "fcf_yield = ?, per_courant = ?, per_median = ?, ecart_multiple = ?, "
           "maj = ? WHERE ticker = ?",
           [cours, epv, esc, fy, pc, pm, ec, RUN_TS, t])
        d1("UPDATE societe SET capitalisation = ? WHERE ticker = ?", [capi, t])
        ecrites += 1
        if ecrites % 200 == 0 or ecrites == len(maj):
            print(f"  ecrit {ecrites}/{len(maj)}")

    # ---- rapport ------------------------------------------------------------
    q = lambda s: d1(s)[0]["results"][0]["n"]
    print("\n--- RAPPORT ---")
    print(f"cotations ecrites      : {ecrites}")
    print(f"refus de domaine EPV   : {refus_epv}")
    print(f"EPV calculable         : {q('SELECT COUNT(*) AS n FROM metriques WHERE plancher_epv IS NOT NULL')}")
    print(f"multiple median propre : {q('SELECT COUNT(*) AS n FROM metriques WHERE per_median IS NOT NULL')}")
    print("\nentonnoir :")
    print(f"  qualite (ROIC>=15, spread>0, CA et FCF en hausse) : "
          f"{q('SELECT COUNT(*) AS n FROM metriques WHERE exclusion IS NULL AND roic_median >= 15 AND spread_median > 0 AND ca_cagr > 0 AND fcf_cagr > 0')}")
    print(f"  + sous son propre multiple median                 : "
          f"{q('SELECT COUNT(*) AS n FROM metriques WHERE exclusion IS NULL AND roic_median >= 15 AND spread_median > 0 AND ca_cagr > 0 AND fcf_cagr > 0 AND ecart_multiple < 0')}")
    print(f"  + plancher EPV >= 60 % du cours                   : "
          f"{q('SELECT COUNT(*) AS n FROM metriques WHERE exclusion IS NULL AND roic_median >= 15 AND spread_median > 0 AND ca_cagr > 0 AND fcf_cagr > 0 AND ecart_multiple < 0 AND epv_sur_cours >= 0.6')}")

    print("\ndix premiers par decote sur multiple propre :")
    for l in d1(
        "SELECT m.ticker, s.nom, m.roic_median, m.ecart_multiple, m.epv_sur_cours, "
        "m.score_moat, m.score_moat_max, s.vaneck FROM metriques m "
        "JOIN societe s ON s.ticker = m.ticker "
        "WHERE m.exclusion IS NULL AND m.roic_median >= 15 AND m.ca_cagr > 0 "
        "AND m.fcf_cagr > 0 AND m.ecart_multiple IS NOT NULL "
        "ORDER BY m.ecart_multiple ASC LIMIT 10")[0]["results"]:
        print(f"  {l['ticker']:7} ROIC {round(l['roic_median'] or 0):3} %  "
              f"multiple {round(100 * (l['ecart_multiple'] or 0)):+4} %  "
              f"EPV/cours {round(100 * (l['epv_sur_cours'] or 0)):3} %  "
              f"moat {l['score_moat']}/{l['score_moat_max']}"
              f"{'  VanEck' if l['vaneck'] else ''}  {(l['nom'] or '')[:28]}")

    journal("OK", ecrites, "VERT",
            f"{ecrites} cotations, {refus_epv} refus EPV, {echecs} echecs",
            {"cibles": len(cibles), "ecrites": ecrites, "echecs": echecs})
    print("OK")


if __name__ == "__main__":
    main()
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
