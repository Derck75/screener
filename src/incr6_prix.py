#!/usr/bin/env python3
"""
INCREMENT 6 v15 — Cours et capitalisations -> `metriques.cours`,
`societe.capitalisation`.

CE QUI A CHANGE (audit C2, M1, M5). Cet etage calculait lui-meme l'EPV, la
« croissance exigee » (un Gordon perpetuel, pas le seuil N du cadre) et la
croissance demontree — trois definitions paralleles a celles du serveur. Il ne
fait plus que COTER : toutes les societes non exclues, sans plancher de ROIC.
Les ratios de prix sont calcules ensuite par l'etage valorisation
(incr7_valorisation.mjs), avec le noyau du worker : un seul moteur DCF, une
seule EPV.

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

# Places internationales : (chemin de liste, suffixe Yahoo). Sans elles, les
# 1 894 societes ecrites par l'univers international n'ont aucun cours, donc
# aucune EPV, donc restent invisibles au tri par cherte — la moitie du
# dispositif serait inerte.
# Le suffixe convertit le symbole LOCAL servi par la liste (« MC ») vers le
# ticker de la base (« MC.PA »), exactement comme a l'ingestion de l'univers.
LISTES_INTL = [
    ("euronext-paris", ".PA"), ("euronext-amsterdam", ".AS"),
    ("euronext-brussels", ".BR"), ("euronext-lisbon", ".LS"),
    ("euronext-dublin", ".IR"), ("deutsche-boerse-xetra", ".DE"),
    ("borsa-italiana", ".MI"), ("madrid-stock-exchange", ".MC"),
    ("vienna-stock-exchange", ".VI"), ("athens-stock-exchange", ".AT"),
    ("nasdaq-stockholm", ".ST"), ("copenhagen-stock-exchange", ".CO"),
    ("nasdaq-helsinki", ".HE"), ("oslo-bors", ".OL"),
    ("nasdaq-iceland", ".IC"), ("warsaw-stock-exchange", ".WA"),
    ("prague-stock-exchange", ".PR"), ("budapest-stock-exchange", ".BD"),
    ("london-stock-exchange", ".L"), ("six-swiss-exchange", ".SW"),
    ("tokyo-stock-exchange", ".T"),
    ("korea-stock-exchange", ".KS"), ("taiwan-stock-exchange", ".TW"),
    ("toronto-stock-exchange", ".TO"),
]
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


def migrer(table, colonnes):
    """Cree les colonnes manquantes : deux incidents sont venus d'un ALTER
    TABLE oublie. PRAGMA table_info est deja eprouve par l'increment 4."""
    presentes = {l["name"] for l in
                 d1(f"PRAGMA table_info({table})")[0]["results"]}
    for nom, typ in colonnes.items():
        if nom not in presentes:
            d1(f"ALTER TABLE {table} ADD COLUMN {nom} {typ}")
            print(f"  migration {table} : {nom} ajoutee")


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


PAGINATIONS = ["?p={n}", "?page={n}", "?r=500&p={n}"]
PAGES_MAX = 12


# Les listes americaines lient en /stocks/AAPL/, les internationales en
# /quote/epa/MC/. Chercher un seul format faisait conclure a « page unique
# (4 symboles) » sur toutes les places europeennes — quatre liens residuels
# de menu — et plafonnait Paris, Xetra, Milan, Vienne, Londres et Tokyo a
# 500 valeurs chacune.
_LIENS = re.compile(r"/(?:stocks|quote/[a-z]+)/([A-Za-z0-9.\-]+)/")


def symboles(html):
    return set(m.upper() for m in _LIENS.findall(html or ""))


def pages_liste(chemin, s):
    """Rend la liste des pages HTML d'une cotation, pagination COMPRISE.

    🔴 DEFAUT CORRIGE ICI. La lecture ne prenait que la premiere page, et
    StockAnalysis en sert 500 lignes. Deux cotations ont rendu exactement 500
    — un nombre rond qui n'arrive jamais par hasard sur un marche de 2 800
    valeurs. Resultat : 1 242 cours pour 710 societes a servir, 409 absentes,
    et un canari rouge qui avait parfaitement raison.

    LE PARAMETRE DE PAGINATION N'EST PAS SUPPOSE, IL EST DECOUVERT. Trois
    formes d'URL sont essayees sur la page 2 ; on garde celle qui ramene des
    symboles NOUVEAUX, et on l'annonce dans le log. Coder en dur une forme non
    observee reviendrait a remplacer une panne bruyante par une panne muette :
    la page 2 repondrait 200 en reservant la page 1, et le total paraitrait
    plausible tout en restant tronque. D'ou le test sur la NOUVEAUTE des
    symboles, et non sur le code HTTP.
    """
    base = f"https://stockanalysis.com/list/{chemin}/"
    p1 = lire_liste(base, s)
    if not p1:
        return []
    pages, vus = [p1], symboles(p1)
    forme = None

    for cand in PAGINATIONS:
        essai = lire_liste(base + cand.format(n=2), s)
        if not essai:
            continue
        neufs = symboles(essai) - vus
        if len(neufs) >= 20:
            forme, pages, vus = cand, pages + [essai], vus | neufs
            print(f"  {chemin} : pagination « {cand} » — {len(neufs)} symboles neufs en page 2")
            break

    if forme is None:
        print(f"  {chemin} : page unique ({len(vus)} symboles) — aucune pagination detectee")
        s.compte("liste_page_unique")
        return pages

    n = 3
    while n <= PAGES_MAX:
        h = lire_liste(base + forme.format(n=n), s)
        if not h:
            break
        neufs = symboles(h) - vus
        if not neufs:
            break
        pages.append(h); vus |= neufs; n += 1
        time.sleep(0.6)
    print(f"  {chemin} : {len(pages)} page(s), {len(vus)} symboles distincts")
    return pages


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
    for chemin, suffixe in [(c, "") for c in LISTES_US] + LISTES_INTL:
        pages = pages_liste(chemin, s)
        if not pages:
            continue
        html = "".join(pages)
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
            # Le ticker de la base porte le suffixe de sa place ; le point
            # d'une classe d'actions devient un tiret, comme partout ailleurs.
            # Point final retire (Londres : « BA. ») — voir univers_intl.
            out[c[i_sym].upper().rstrip(".").replace(".", "-") + suffixe] = (px, cap)
            n += 1
        print(f"  {chemin}{' ' + suffixe if suffixe else ''} : {n} cours")
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

    try:
        import hashlib
        _b = open(__file__, "rb").read()
        print(f"empreinte {hashlib.sha256(_b).hexdigest()[:8]} · "
              f"{_b.count(chr(10).encode()) + 1} lignes")
    except OSError:
        print("empreinte indisponible")
    print(f"incr6_prix v15 (cours seuls) — Run {RUN_TS}"
          + (f" — tranche {a.tranche}/{a.nb_tranches}" if a.tranche else ""))

    migrer("metriques", {"maj_cours": "TEXT"})

    # ---- cibles -------------------------------------------------------------
    # TOUTES LES NON-EXCLUES (audit M5). Seules les societes a ROIC >= 10 %
    # recevaient un cours : le preset `decote`, annonce « sans filtre de
    # qualite », ne voyait donc que 1 253 societes sur 3 560. Le tri par
    # qualite appartient au crible, pas a la collecte des cours.
    cibles = [l["ticker"] for l in d1(
        "SELECT ticker FROM metriques WHERE exclusion IS NULL ORDER BY roic_median DESC"
    )[0]["results"]]
    print(f"\n{len(cibles)} societes non exclues")
    if a.tranche:
        # Repartition par modulo : chaque tranche recoit un echantillon
        # comparable, la ou un decoupage par bloc donnerait a la premiere
        # toutes les grosses capitalisations et fausserait les comparaisons.
        cibles = [t for i, t in enumerate(cibles) if i % a.nb_tranches == a.tranche - 1]
        print(f"  tranche {a.tranche} : {len(cibles)} societes")
    if not cibles:
        journal("PANNE", f"incr6_prix_t{a.tranche}", 0, "ROUGE", "aucune cible")
        sys.exit(1)

    # ---- nombre d'actions du dernier exercice : repli de capitalisation ----
    vise = set(cibles)
    actions_der = {}
    page = 0
    while True:
        r = d1("SELECT ticker, exercice, shares FROM comptes2 "
               "ORDER BY ticker, exercice LIMIT 5000 OFFSET ?", [page * 5000]
               )[0]["results"]
        if not r:
            break
        for l in r:
            if l["ticker"] in vise and l.get("shares"):
                actions_der[l["ticker"]] = l["shares"]     # ordre croissant : le dernier gagne
        page += 1

    # ---- cotations, depuis les listes ---------------------------------------
    s.phase("cotations")
    table = cours_depuis_listes(s)
    print(f"  {len(table)} cours disponibles")
    intl = sum(1 for k in table if "." in k)
    print(f"    dont {intl} hors des Etats-Unis")

    # ---- REPLI PAR TICKER, POUR CE QUE LES LISTES N'ONT PAS SERVI -----------
    # Il ne se declenche que sur le manquant. COUPE-CIRCUIT : Yahoo refuse les
    # adresses GitHub (429 immediat) ; sans lui, chaque manquant coutait trente
    # secondes de reprises, et l'etendue des cibles a toutes les non-exclues
    # aurait porte le passage a plusieurs heures.
    manquants = [t for t in cibles if t not in table]
    if manquants:
        print(f"  repli par ticker sur {len(manquants)} manquant(s)")
        rattrapes = echecs_suite = 0
        for i, t in enumerate(manquants):
            c = chart(t)
            if c:
                table[t] = (c[0], None)   # capitalisation non servie par cette voie
                rattrapes += 1
                echecs_suite = 0
            else:
                echecs_suite += 1
                if echecs_suite >= 10 and not rattrapes:
                    print("  repli interrompu : dix echecs d'affilee, source refusee")
                    s.compte("repli_interrompu")
                    break
            time.sleep(1.0 if i % 25 == 24 else 0.15)
        print(f"  {rattrapes} rattrape(s)")
        s.compte("rattrapes_yahoo", rattrapes)

    maj, echecs, perimes = [], 0, []
    cours_canari = (table.get(CANARI_TICKER) or (None,))[0]
    for t in cibles:
        px = table.get(t.upper())
        if not px or not px[0]:
            echecs += 1
            s.erreur("sans_cours", t)
            perimes.append(t)
            continue
        cours, capi_liste = px
        actions = actions_der.get(t)
        # CONTROLE D'UNITE SUR LE NOMBRE D'ACTIONS — il ne sert plus qu'au
        # repli de capitalisation : tous les ratios de prix se calculent
        # desormais sur la capitalisation, a l'etage valorisation.
        if actions and capi_liste and cours:
            rapport = (cours * actions) / capi_liste
            if rapport > 5 or rapport < 0.2:
                s.compte("actions_invalidees")
                actions = None
        capi = capi_liste or (cours * actions if actions else None)
        maj.append((t, cours, capi))

    print(f"  {len(maj)} cotations exploitables, {echecs} echecs")

    # ---- canari -------------------------------------------------------------
    if a.tranche and CANARI_TICKER not in cibles:
        cours_canari = CANARI_COURS_MIN + 1   # le temoin n'est pas dans cette tranche
    if cours_canari is None or cours_canari < CANARI_COURS_MIN or \
       len(maj) < 0.35 * len(cibles):
        msg = (f"canari rouge : {CANARI_TICKER} = {cours_canari}, "
               f"{len(maj)}/{len(cibles)} cotations")
        print("PANNE : " + msg)
        s.afficher()
        journal("PANNE", f"incr6_prix_t{a.tranche}", 0, "ROUGE", msg)
        sys.exit(1)

    # ---- ecriture, par lots (UPSERT : seules ces colonnes sont touchees) -----
    s.phase("ecriture")
    for i in range(0, len(maj), 30):
        lot = maj[i:i + 30]
        d1("INSERT INTO metriques (ticker, cours, maj_cours) VALUES "
           + ", ".join("(?, ?, ?)" for _ in lot)
           + " ON CONFLICT(ticker) DO UPDATE SET cours = excluded.cours, "
             "maj_cours = excluded.maj_cours",
           [x for t, c, _ in lot for x in (t, c, RUN_TS)])
        lot_c = [(t, k) for t, _, k in lot if k]
        if lot_c:
            d1("INSERT INTO societe (ticker, capitalisation) VALUES "
               + ", ".join("(?, ?)" for _ in lot_c)
               + " ON CONFLICT(ticker) DO UPDATE SET capitalisation = excluded.capitalisation",
               [x for t, k in lot_c for x in (t, k)])
    ecrites = len(maj)
    print(f"  ecrit {ecrites}")

    # ---- cours perimes -------------------------------------------------------
    # Une societe radiee gardait son dernier cours, et avec lui une decote qui
    # n'existe plus. Garde : seulement si la liste de SA place a ete lue.
    places_lues = {("." + k.rsplit(".", 1)[1]) if "." in k else "" for k in table}
    purges = [t for t in perimes
              if (("." + t.rsplit(".", 1)[1]) if "." in t else "") in places_lues]
    for i in range(0, len(purges), 40):
        lot = purges[i:i + 40]
        d1("UPDATE metriques SET cours = NULL, epv_sur_cours = NULL, "
           "eva_sur_cours = NULL, concordance = NULL, fcf_yield = NULL, "
           "per_courant = NULL, part_tresorerie = NULL, croissance_implicite = NULL, "
           "maj_cours = ? WHERE ticker IN (" + ", ".join("?" * len(lot)) + ")",
           [RUN_TS] + lot)
    if purges:
        print(f"  {len(purges)} cours perime(s) efface(s) : {' '.join(purges[:8])}")
        s.compte("cours_perimes", len(purges))

    s.afficher()
    journal("OK", f"incr6_prix_t{a.tranche}", ecrites, "VERT",
            f"{ecrites} cotations, {echecs} sans cours",
            {"cibles": len(cibles), "ecrites": ecrites, "echecs": echecs})
    print("OK")


if __name__ == "__main__":
    main()
