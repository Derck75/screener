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
import re
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

# Yahoo refuse les adresses des serveurs GitHub : 429 immediat, en 0,0 s, sur
# toutes les requetes. Ce n'est pas une question de cadence et attendre n'y
# change rien. Les listes StockAnalysis repondent — et portent DEJA le cours
# et la capitalisation. Quelques requetes remplacent 710 appels individuels.
LISTES_US = ["nyse-stocks", "nasdaq-stocks", "nyseamerican-stocks"]
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


class Sonde:
    """Sonde locale. `incr6_prix` est le seul script anterieur a `commun.py`
    et n'en herite pas : sans cette classe, tous les appels a s.phase et
    s.erreur echouaient par NameError."""

    def __init__(self, etape):
        self.etape, self.t0 = etape, time.time()
        self.phases, self.compteurs, self.erreurs = {}, {}, {}
        self.nom, self.pt = None, 0

    def phase(self, nom):
        if self.nom:
            self.phases[self.nom] = round(time.time() - self.pt, 1)
        self.nom, self.pt = nom, time.time()
        print(f"[{nom}]")

    def compte(self, cle, n=1):
        self.compteurs[cle] = self.compteurs.get(cle, 0) + n

    def erreur(self, cat, ex=None):
        e = self.erreurs.setdefault(cat, {"n": 0, "exemple": None})
        e["n"] += 1
        if e["exemple"] is None and ex:
            e["exemple"] = str(ex)[:120]

    def resume(self):
        if self.nom:
            self.phases[self.nom] = round(time.time() - self.pt, 1)
            self.nom = None
        return {"duree_s": round(time.time() - self.t0, 1), "phases": self.phases,
                "compteurs": self.compteurs, "erreurs": self.erreurs}

    def afficher(self):
        r = self.resume()
        print(f"\n--- SONDE {self.etape} — {r['duree_s']} s ---")
        if r["phases"]:
            print("  " + ", ".join(f"{k} {v}s" for k, v in r["phases"].items()))
        if r["compteurs"]:
            print("  " + ", ".join(f"{k}={v}" for k, v in sorted(r["compteurs"].items())))
        for c, e in sorted(r["erreurs"].items(), key=lambda x: -x[1]["n"]):
            print(f"  ERREUR {c} x{e['n']}" + (f" — {e['exemple']}" if e["exemple"] else ""))
        if not r["erreurs"]:
            print("  aucune erreur categorisee")
        return r


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


def journal(statut, etape, n, canari, message, detail=None):
    d1("INSERT INTO runs (debut, fin, etape, statut, lignes_ecrites, canari, message, detail)"
       " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
       [RUN_TS, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        etape, statut, n, canari, message,
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


def lire_liste(url, s):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        s.erreur("liste_sa", f"{type(e).__name__} {url[-30:]}")
        return None


def cours_depuis_listes(s):
    """Rend {ticker: (cours, capitalisation)} pour tout le panel americain.

    CE QUE CETTE VOIE NE DONNE PAS : l'historique mensuel, donc le multiple
    median propre au titre. `per_median` et `ecart_multiple` restent nuls.
    Ce n'est pas une perte seche : ce critere etait CIRCULAIRE — il compare le
    multiple courant a la mediane du titre, c'est-a-dire la quantite meme
    qu'emploie la brique de multiple de l'ancrage propre. Trier dessus
    revenait a selectionner les societes dont l'ancrage sortirait haut par
    construction, quelle que soit leur valeur reelle.
    """
    out = {}
    for chemin in LISTES_US:
        html = lire_liste(f"https://stockanalysis.com/list/{chemin}/", s)
        if not html:
            continue
        ent = [re.sub(r"<[^>]+>", " ", h).strip().lower()
               for h in re.findall(r"<th[^>]*>(.*?)</th>", html, re.S)]
        i_sym = next((i for i, l in enumerate(ent) if "symbol" in l), None)
        i_cap = next((i for i, l in enumerate(ent) if "market cap" in l), None)
        i_px = next((i for i, l in enumerate(ent)
                     if l.strip() in ("price", "stock price")), None)
        if i_sym is None or i_px is None:
            s.erreur("entetes_liste", f"{chemin} : {ent[:6]}")
            continue
        n = 0
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
            c = [re.sub(r"<[^>]+>", " ", x).strip()
                 for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if len(c) <= max(i_sym, i_px):
                continue
            try:
                px = float(c[i_px].replace(",", "").replace("$", "").strip())
            except ValueError:
                continue
            cap = None
            if i_cap is not None and i_cap < len(c):
                m = re.match(r"^[^\d]*([\d.,]+)\s*([TBMK])?", c[i_cap])
                if m:
                    cap = float(m.group(1).replace(",", "")) * {
                        "T": 1e12, "B": 1e9, "M": 1e6, "K": 1e3}.get(
                        (m.group(2) or "").upper(), 1)
            out[c[i_sym].upper().replace(".", "-")] = (px, cap)
            n += 1
        print(f"  {chemin} : {n} cours")
        s.compte("cours_lus", n)
        time.sleep(0.8)
    return out


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
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--tranche", type=int, default=0, help="0 = toutes")
    ap.add_argument("--nb-tranches", type=int, default=1)
    ap.add_argument("--test-reseau", action="store_true",
                    help="trois requetes Yahoo, codes HTTP bruts, aucune ecriture")
    a = ap.parse_args()
    s = Sonde("incr6_prix")
    if a.test_reseau:
        print("TEST RESEAU — aucune ecriture\n")
        for sym in ("AAPL", "MSFT", "MC.PA"):
            u = CHART.format(sym=sym)
            req = urllib.request.Request(u, headers={"User-Agent": UA,
                                                     "Accept": "application/json"})
            t0 = time.time()
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    corps = r.read(400).decode("utf-8", "replace")
                print(f"  {sym:7} HTTP {r.status} en {time.time()-t0:.1f}s")
                print(f"          debut du corps : {corps[:120]}")
            except urllib.error.HTTPError as e:
                detail = e.read()[:200].decode("utf-8", "replace")
                print(f"  {sym:7} HTTP {e.code} en {time.time()-t0:.1f}s — {detail}")
            except Exception as e:
                print(f"  {sym:7} {type(e).__name__} en {time.time()-t0:.1f}s — {str(e)[:120]}")
            time.sleep(1)
        print("\n429 = debit limite, une pause plus longue suffit.")
        print("401 ou 403 = acces refuse aux adresses GitHub : il faudra passer")
        print("par le worker Cloudflare ou par StockAnalysis.")
        return

    print(f"incr6_prix v4 — Run {RUN_TS}"
          + (f" — tranche {a.tranche}/{a.nb_tranches}" if a.tranche else ""))

    # ---- diagnostic demande : pourquoi la moitie de l'univers est ecartee ----
    print("\nventilation des exclusions mecaniques :")
    for l in d1("SELECT substr(exclusion, 1, instr(exclusion || ' —', ' —') - 1) AS motif, "
                "COUNT(*) AS n FROM metriques WHERE exclusion IS NOT NULL "
                "GROUP BY motif ORDER BY n DESC")[0]["results"]:
        print(f"  {l['n']:5}  {l['motif']}")
    mixte = d1("SELECT COUNT(*) AS n FROM metriques WHERE denominateur_roic = 'mixte'"
               )[0]["results"][0]["n"]
    print(f"  {mixte:5}  (hors exclusion) series a denominateur de ROIC mixte")

    # ---- cibles -------------------------------------------------------------
    cibles = [l["ticker"] for l in d1(
        "SELECT ticker FROM metriques WHERE exclusion IS NULL "
        "AND roic_median >= ? ORDER BY roic_median DESC", [ROIC_MIN]
    )[0]["results"]]
    print(f"\n{len(cibles)} societes pre-qualifiees (ROIC median >= {ROIC_MIN} %)")
    if a.tranche:
        # Repartition par modulo : chaque tranche recoit un echantillon
        # comparable, la ou un decoupage par bloc donnerait a la premiere
        # toutes les grosses capitalisations et fausserait les comparaisons.
        cibles = [t for i, t in enumerate(cibles) if i % a.nb_tranches == a.tranche - 1]
        print(f"  tranche {a.tranche} : {len(cibles)} societes")
    if not cibles:
        journal("PANNE", f"incr6_prix_t{a.tranche}", 0, "ROUGE", "aucune cible")
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

    # ---- cotations, depuis les listes ---------------------------------------
    s.phase("cotations")
    table = cours_depuis_listes(s)
    print(f"  {len(table)} cours disponibles")
    maj, refus_epv, echecs = [], 0, 0
    cours_canari = (table.get(CANARI_TICKER) or (None,))[0]

    for t in cibles:
        px = table.get(t.upper())
        if not px or not px[0]:
            echecs += 1
            s.erreur("sans_cours", t)
            continue
        cours, capi_liste = px
        annees = comptes.get(t) or {}
        ans = sorted(annees)
        if not ans:
            continue
        der = annees[ans[-1]]
        actions = der.get("shares")
        capi = capi_liste or (cours * actions if actions else None)
        fcf_der = (der["cfo"] - abs(der["capex"])) \
            if (der.get("cfo") is not None and der.get("capex") is not None) else None
        fcf_y = (fcf_der / capi) if (fcf_der is not None and capi) else None

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
                    refus_epv += 1
            else:
                refus_epv += 1
        else:
            refus_epv += 1

        per_cour = None
        bpa = (der["netIncome"] / actions) \
            if (der.get("netIncome") is not None and actions) else None
        if bpa and bpa > 0:
            per_cour = cours / bpa

        maj.append((t, cours, capi, epv, epv_sur_cours, fcf_y, per_cour, None, None))

    print(f"  {len(maj)} cotations exploitables, {echecs} echecs")

    # ---- canari -------------------------------------------------------------
    if a.tranche and CANARI_TICKER not in cibles:
        cours_canari = CANARI_COURS_MIN + 1   # le temoin n'est pas dans cette tranche
    if cours_canari is None or cours_canari < CANARI_COURS_MIN or \
       len(maj) < 0.5 * len(cibles):
        msg = (f"canari rouge : {CANARI_TICKER} = {cours_canari}, "
               f"{len(maj)}/{len(cibles)} cotations")
        print("PANNE : " + msg)
        s.afficher()
        journal("PANNE", f"incr6_prix_t{a.tranche}", 0, "ROUGE", msg)
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

    journal("OK", f"incr6_prix_t{a.tranche}", ecrites, "VERT",
            f"{ecrites} cotations, {refus_epv} refus EPV, {echecs} echecs",
            {"cibles": len(cibles), "ecrites": ecrites, "echecs": echecs})
    print("OK")


if __name__ == "__main__":
    main()
