#!/usr/bin/env python3
"""
INCREMENT 5 — Metriques fondamentales -> table `metriques`.

AUCUN COURS ICI, ET C'EST VOULU. Le filtre de qualite (rentabilite,
persistance, croissance) ne depend pas du prix. On ne paiera des appels de
cotation qu'aux societes qui l'ont franchi : quelques centaines au lieu de
4 400. C'est l'entonnoir, et c'est ce qui garde le dispositif gratuit.

TROIS ETATS, JAMAIS DEUX. Un critere rend passe, echoue, ou NON CALCULABLE.
Le score de moat est publie avec son DENOMINATEUR reel : un facteur non
mesurable sort du calcul au lieu de compter comme un mauvais resultat, sans
quoi le screener punirait la couverture partielle au lieu de la signaler.

Idempotent : relancable sans dommage.
"""

import json
import os
import statistics as st
import sys
import time

import requests

CF_ACCOUNT = os.environ["CF_ACCOUNT"]
CF_DB = os.environ["CF_DB"]
CF_TOKEN = os.environ["CF_TOKEN"]

D1_URL = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/d1/database/{CF_DB}/query"

# Seuil de rentabilite FORFAITAIRE, par devise : taux souverain 10 ans + 5 pts.
# CE N'EST PAS UN WACC et il ne doit jamais etre presente comme tel — aucun
# beta, aucune regression. Il sert a trier, pas a valoriser. Modifiable sans
# redeploiement par variable d'environnement.
SEUIL = {"USD": float(os.environ.get("SEUIL_USD", "9.0"))}
TAUX_IMPOT_DEFAUT = 0.21  # US federal, applique seulement faute de taux publie

MIN_EX_ROIC = 4     # sous 4 exercices, une persistance ne se juge pas
MIN_EX_MARGE = 4    # sous 4 points, les residus sont mecaniquement faibles
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
        "incr5_metriques", statut, n, canari, message,
        json.dumps(detail) if detail else None])


def num(v):
    if v is None:
        return "NULL"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "NULL"
    if f != f or f in (float("inf"), float("-inf")):
        return "NULL"
    return repr(round(f, 6))


def txt(v):
    if v is None:
        return "NULL"
    return "'" + str(v).replace("'", "''")[:120] + "'"


def cagr(serie):
    """serie = [(annee, valeur)] triee. Rend le taux annualise en %."""
    pts = [(a, v) for a, v in serie if v is not None]
    if len(pts) < 3:
        return None
    (a0, v0), (a1, v1) = pts[0], pts[-1]
    if v0 <= 0 or v1 <= 0 or a1 <= a0:
        return None
    return 100 * ((v1 / v0) ** (1 / (a1 - a0)) - 1)


def pente_et_residus(serie):
    """Regression lineaire simple. Rend (pente par an, ecart-type des residus)."""
    pts = [(a, v) for a, v in serie if v is not None]
    n = len(pts)
    if n < MIN_EX_MARGE:
        return None, None, n
    mx = sum(a for a, _ in pts) / n
    my = sum(v for _, v in pts) / n
    den = sum((a - mx) ** 2 for a, _ in pts)
    if den == 0:
        return None, None, n
    pente = sum((a - mx) * (v - my) for a, v in pts) / den
    res = [v - (my + pente * (a - mx)) for a, v in pts]
    return pente, (st.pstdev(res) if n > 1 else 0.0), n


def main():
    print(f"incr5_metriques v1 — Run {RUN_TS}")
    seuil = SEUIL["USD"]
    print(f"seuil de rentabilite forfaitaire USD : {seuil} % (taux souverain + 5 pts)")

    # ---- univers ------------------------------------------------------------
    soc = {}
    for l in d1("SELECT ticker, vaneck, vaneck_sorti_le, devise FROM societe"
                )[0]["results"]:
        soc[l["ticker"]] = l

    # ---- comptes, pagines : une reponse de 24 000 lignes d'un bloc est fragile
    comptes = {}
    page = 0
    while True:
        r = d1("SELECT * FROM comptes ORDER BY ticker, exercice LIMIT 5000 OFFSET ?",
               [page * 5000])[0]["results"]
        if not r:
            break
        for l in r:
            comptes.setdefault(l["ticker"], {})[int(l["exercice"])] = l
        page += 1
    print(f"{sum(len(v) for v in comptes.values())} lignes de comptes, "
          f"{len(comptes)} societes")

    rangs = []
    stats = {"roic": 0, "exclues": 0, "mb": 0, "score5": 0}

    for ticker, annees in comptes.items():
        ans = sorted(annees)
        L = [annees[a] for a in ans]
        g = lambda k: [(a, annees[a].get(k)) for a in ans]
        val = lambda k: [annees[a].get(k) for a in ans if annees[a].get(k) is not None]
        manquants = 0

        # -- taux d'impot ------------------------------------------------------
        taux = val("impot_effectif")
        t_med = st.median(taux) if taux else TAUX_IMPOT_DEFAUT

        # -- ROIC, deux denominateurs, jamais arbitres en silence ---------------
        # Exploitation d'abord : il ne part pas du total du bilan, donc la
        # tresorerie ne l'atteint pas. Une levee de dette qui gonfle le cash
        # retrecirait un denominateur cote actif et ferait bondir le ROIC sans
        # qu'aucune performance n'ait change.
        roics, denom = [], None
        for a in ans:
            d = annees[a]
            ebit = d.get("ebit")
            if ebit is None:
                continue
            ppe, bfr = d.get("ppe"), d.get("bfr_exploitation")
            gw, inc = d.get("goodwill") or 0, d.get("incorporels") or 0
            base, src = None, None
            if ppe is not None and bfr is not None:
                base, src = ppe + bfr + gw + inc, "exploitation"
            else:
                cp, dt, tr = (d.get("capitaux_propres"), d.get("dette"),
                              d.get("tresorerie") or 0)
                if cp is not None and dt is not None:
                    base, src = cp + dt - tr, "financement"
            if base is None or base <= 0:
                continue
            roics.append((a, 100 * ebit * (1 - t_med) / base))
            denom = denom or src
            if src != denom:
                denom = "mixte"

        serie_roic = [v for _, v in roics]
        roic_med = st.median(serie_roic) if serie_roic else None
        roic_der = roics[-1][1] if roics else None
        n_ex = len(serie_roic)
        n_sup = sum(1 for v in serie_roic if v >= seuil)
        spread = roic_med - seuil if roic_med is not None else None
        if roic_med is None:
            manquants += 1
        else:
            stats["roic"] += 1

        # -- croissance --------------------------------------------------------
        ca_cagr, fcf_cagr, ebit_cagr = cagr(g("ca")), cagr(g("fcf")), cagr(g("ebit"))
        for x in (ca_cagr, fcf_cagr, ebit_cagr):
            if x is None:
                manquants += 1

        # -- regularite : DENOMINATEURS SEPARES, les deux series n'ont pas la
        #    meme profondeur et un denominateur commun serait faux.
        ca_s = val("ca")
        ca_h = sum(1 for i in range(1, len(ca_s)) if ca_s[i] > ca_s[i - 1])
        ca_m = max(0, len(ca_s) - 1)
        fcf_s = val("fcf")
        fcf_p, fcf_m = sum(1 for v in fcf_s if v > 0), len(fcf_s)

        # -- marge brute -------------------------------------------------------
        mb = [(a, 100 * annees[a]["marge_brute"] / annees[a]["ca"])
              for a in ans
              if annees[a].get("marge_brute") is not None
              and annees[a].get("ca") not in (None, 0)]
        mb_pente, mb_res, n_mb = pente_et_residus(mb)
        mb_med = st.median([v for _, v in mb]) if mb else None
        if mb_pente is None:
            manquants += 1
        else:
            stats["mb"] += 1

        # -- autres ------------------------------------------------------------
        conv = [annees[a]["fcf"] / annees[a]["resultat_net"] for a in ans
                if annees[a].get("fcf") is not None
                and (annees[a].get("resultat_net") or 0) > 0]
        conv_med = st.median(conv) if conv else None

        act = val("actions_diluees")
        act_var = 100 * (act[-1] / act[0] - 1) if len(act) >= 2 and act[0] > 0 else None

        der = L[-1]
        ca_der = der.get("ca")
        dette_ca = (der["dette"] / ca_der) if der.get("dette") is not None and ca_der else None
        cp_ca = (der["capitaux_propres"] / ca_der) if der.get("capitaux_propres") is not None and ca_der else None
        gw_actif = (der["goodwill"] / der["actif_total"]) \
            if der.get("goodwill") is not None and der.get("actif_total") else None
        biais_acq = 1 if (gw_actif is not None and gw_actif > 0.30) else 0

        # -- exclusions mecaniques, motif inscrit, jamais un DELETE -------------
        excl = None
        ebit_med = st.median(val("ebit")) if val("ebit") else None
        if dette_ca is not None and dette_ca > 2.5:
            excl = "profil financier ou foncier (dette/CA > 2,5)"
        elif dette_ca is not None and cp_ca is not None and dette_ca > 1.5 and cp_ca >= 1:
            excl = "profil financier ou foncier (levier et capitaux propres eleves)"
        elif roic_med is not None and roic_med > 60:
            excl = f"ROIC median {round(roic_med, 1)} % — artefact quasi certain"
        elif ebit_med is not None and ebit_med < 0:
            excl = "EBIT median negatif"
        if excl:
            stats["exclues"] += 1

        # -- score de moat : points obtenus SUR points evaluables ---------------
        pts = mx = 0
        s = soc.get(ticker) or {}
        if s.get("vaneck") and not s.get("vaneck_sorti_le"):
            pts += 3
            mx += 3          # absence VanEck = hors denominateur, jamais une
                             # penalite : la couverture Morningstar est partielle
                             # et frappe surtout l'Europe.
        if n_ex >= MIN_EX_ROIC:
            mx += 3
            sous = n_ex - n_sup
            pts += 3 if sous == 0 else (2 if sous == 1 else (1 if sous == 2 else 0))
        if roic_med is not None:
            mx += 2
            pts += 2 if roic_med >= 15 else (1 if roic_med >= 10 else 0)
        if mb_pente is not None and mb_res is not None:
            mx += 2
            if mb_res < 2 and mb_pente > -0.5:
                pts += 2
        if mx >= 5:
            stats["score5"] += 1

        rangs.append(
            f"({txt(ticker)}, {num(roic_med)}, {num(roic_der)}, {num(spread)}, "
            f"{n_sup}, {n_ex}, {num(seuil)}, {txt(denom)}, "
            f"{num(ca_cagr)}, {num(fcf_cagr)}, {num(ebit_cagr)}, "
            f"{ca_h}, {ca_m}, {fcf_p}, {fcf_m}, "
            f"{num(mb_med)}, {num(mb_pente)}, {num(mb_res)}, "
            f"{num(conv_med)}, {num(act_var)}, "
            f"{num(dette_ca)}, {num(cp_ca)}, "
            f"{pts}, {mx}, {biais_acq}, {manquants}, {txt(excl)}, {txt(RUN_TS)})")

    # ---- canari -------------------------------------------------------------
    if stats["roic"] < 1500:
        msg = f"canari rouge : ROIC calculable sur {stats['roic']} societes seulement"
        print("PANNE : " + msg)
        journal("PANNE", 0, "ROUGE", msg, stats)
        sys.exit(1)

    # ---- ecriture -----------------------------------------------------------
    cols = ("ticker, roic_median, roic_dernier, spread_median, "
            "n_ex_roic_sup_seuil, n_ex_total, seuil_rentabilite_forfaitaire, "
            "denominateur_roic, ca_cagr, fcf_cagr, ebit_cagr, "
            "ca_hausse_n, ca_hausse_m, fcf_positif_n, fcf_positif_m, "
            "mb_mediane, mb_pente, mb_residus, conversion_fcf_rn, "
            "actions_var_5a, dette_sur_ca, cp_sur_ca, "
            "score_moat, score_moat_max, biais_acquereur, "
            "n_non_calculable, exclusion, maj")
    ecrites = 0
    for i in range(0, len(rangs), 100):
        lot = rangs[i:i + 100]
        d1(f"INSERT OR REPLACE INTO metriques ({cols}) VALUES " + ", ".join(lot))
        ecrites += len(lot)
        if ecrites % 1000 == 0 or ecrites == len(rangs):
            print(f"  ecrit {ecrites}/{len(rangs)}")

    # ---- rapport ------------------------------------------------------------
    q = lambda s, p=None: d1(s, p)[0]["results"][0]["n"]
    qd = lambda v: q("SELECT COUNT(*) AS n FROM metriques WHERE denominateur_roic = ?", [v])
    print("\n--- RAPPORT ---")
    print(f"societes evaluees      : {ecrites}")
    print(f"ROIC calculable        : {stats['roic']}")
    print(f"  exploitation         : {qd('exploitation')}")
    print(f"  financement (repli)  : {qd('financement')}")
    print(f"marge brute evaluable  : {stats['mb']}")
    print(f"exclusions mecaniques  : {stats['exclues']}")
    print(f"score sur >= 5 points  : {stats['score5']}")
    print(f"ROIC >= 15 %           : {q('SELECT COUNT(*) AS n FROM metriques WHERE roic_median >= 15 AND exclusion IS NULL')}")
    print(f"  et spread > 0        : {q('SELECT COUNT(*) AS n FROM metriques WHERE roic_median >= 15 AND spread_median > 0 AND exclusion IS NULL')}")
    print(f"  et CA + FCF en hausse: {q('SELECT COUNT(*) AS n FROM metriques WHERE roic_median >= 15 AND spread_median > 0 AND ca_cagr > 0 AND fcf_cagr > 0 AND exclusion IS NULL')}")
    print(f"dont VanEck            : {q('SELECT COUNT(*) AS n FROM metriques m JOIN societe s ON s.ticker = m.ticker WHERE m.roic_median >= 15 AND m.spread_median > 0 AND m.exclusion IS NULL AND s.vaneck IS NOT NULL')}")

    journal("OK", ecrites, "VERT",
            f"{ecrites} societes, {stats['roic']} ROIC, {stats['exclues']} exclues", stats)
    print("OK")


if __name__ == "__main__":
    main()
