#!/usr/bin/env python3
"""
INCREMENT 8 — Comptes des societes NON americaines -> `comptes2`.

Pas de `frames` hors des Etats-Unis : il faut trois pages par societe (compte
de resultat, bilan, flux). Structure confirmee par `esef(action:"sonder")` :
tableau HTML, aucun JSON embarque, rendu SvelteKit servi cote serveur.

ROTATION OBLIGATOIRE. 1 900 societes x 3 pages = 5 700 requetes : un run
unique serait coupe et saturerait la source. Le parametre `tranche` traite un
sous-ensemble, et les tranches se completent sans s'ecraser.

MEME FORMAT QUE LES DEPOSANTS SEC. Les postes portent les noms qu'attend
`derives()` : le noyau ne doit pas savoir d'ou vient la donnee. C'est ce qui
fait qu'ajouter un pays est un travail de collecte, jamais de calcul.

D'ABORD --sonder SUR UNE SOCIETE, ensuite l'ingestion. Coder une extraction
sur une structure supposee est ce qui a coute six versions au chantier ESEF.
"""

import argparse
import html as _html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from commun import (d1, journal, sonde, budget, ecrire_differentiel,  # noqa: E402
                    RUN_TS)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# suffixe Yahoo -> code de place StockAnalysis
PLACE = {".PA": "epa", ".AS": "ams", ".BR": "ebr", ".LS": "els", ".IR": "dub",
         ".DE": "etr", ".MI": "mil", ".MC": "bme", ".VI": "vie", ".AT": "ath",
         ".ST": "sto", ".CO": "cph", ".HE": "hel", ".OL": "osl", ".IC": "ice",
         ".WA": "war", ".PR": "pra", ".BD": "bud", ".L": "lon", ".SW": "swx",
         ".T": "tyo"}

# Le compte de resultat rendait zero poste sur MC.PA comme sur ASML.AS, alors
# que bilan et flux fonctionnaient. Plusieurs chemins sont donc essayes et le
# premier qui rend des lignes est retenu — on mesure au lieu de supposer.
# Le compte de resultat se lit sur /income-statement/, PAS sur la racine :
# l'essai des trois chemins a montre 8 postes contre 4. Les quatre de plus
# — tax, pretax, ebitda, shares — portent le taux d'impot de l'EPV et le
# controle d'unite du nombre d'actions.
PAGES = {"income-statement/": "resultat", "balance-sheet/": "bilan",
         "cash-flow-statement/": "flux"}

PORTEE = {
    "resultat": {"revenue", "grossProfit", "ebit", "netIncome", "tax",
                 "pretax", "ebitda", "shares"},
    "bilan": {"assets", "ppe", "intangTot", "intangExGW", "goodwill",
              "receivables", "inventory", "payables", "currentLiab", "debt",
              "cash", "equity", "deferredRev", "deferredRevNC"},
    "flux": {"cfo", "capex", "da", "sbc", "amortAcq"},
}
CHEMINS_RESULTAT = ["", "income-statement/", "income/"]

# Libelle servi -> poste du noyau. Le libelle est normalise (minuscules, sans
# ponctuation) avant comparaison : StockAnalysis varie sur les majuscules.
POSTES = {
    "revenue": "revenue", "total revenue": "revenue",
    "gross profit": "grossProfit",
    "operating income": "ebit", "operating income loss": "ebit",
    "net income": "netIncome",
    "income tax": "tax", "income tax expense": "tax",
    "pretax income": "pretax", "ebitda": "ebitda",
    "depreciation amortization": "da", "depreciation and amortization": "da",
    "share based compensation": "sbc", "stock based compensation": "sbc",
    "shares outstanding diluted": "shares", "diluted shares outstanding": "shares",
    "operating cash flow": "cfo", "cash from operating activities": "cfo",
    "capital expenditures": "capex",
    "total assets": "assets",
    "property plant equipment": "ppe", "property plant and equipment": "ppe",
    "goodwill": "goodwill",
    "goodwill and intangibles": "intangTot",
    "other intangible assets": "intangExGW",
    "receivables": "receivables", "accounts receivable": "receivables",
    "inventory": "inventory",
    "accounts payable": "payables",
    "total current liabilities": "currentLiab",
    "total debt": "debt",
    "cash equivalents": "cash", "cash and equivalents": "cash",
    "shareholders equity": "equity", "total equity": "equity",
    "deferred revenue": "deferredRev", "unearned revenue": "deferredRev",
    # « Cash & Equivalents » et « Property, Plant & Equipment » : l'esperluette
    # decodee laisse un « & » que la normalisation transforme en espace, d'ou
    # ces formes avec et sans.
    "cash equivalents": "cash", "cash short term investments": "cash",
    "cash and equivalents": "cash", "cash amp equivalents": "cash",
    "property plant equipment": "ppe", "property plant amp equipment": "ppe",
    "property plant and equipment": "ppe",
    "total current liabilities": "currentLiab",
    "shareholders equity": "equity", "total equity": "equity",
    "ebit": "ebit", "operating profit": "ebit",
    "income before tax": "pretax", "pretax income": "pretax",
    "income tax expense": "tax",
    "shares outstanding basic": "shares",
}

# Du plus long au plus court : « total current liabilities » doit gagner sur
# un prefixe plus court qui le contiendrait.
_POSTES_TRIES = sorted(POSTES, key=len, reverse=True)

COLONNES = ["revenue", "netIncome", "ebit", "grossProfit", "cfo", "capex", "da",
            "ebitda", "sbc", "tax", "pretax", "amortAcq", "assets", "ppe",
            "intangTot", "intangExGW", "goodwill", "receivables", "inventory",
            "payables", "currentLiab", "debt", "cash", "equity", "shares",
            "deferredRev", "deferredRevNC", "flottant"]


PAUSE = float(os.environ.get("PAUSE_SA", "1.0"))


def lire(url, s=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    # REPRISE SUR 429. Trois pages par societe a 0,35 s frappaient la source a
    # plus de deux requetes par seconde : 270 refus sur une tranche de 150, et
    # seules 35 societes lues. Une attente croissante rattrape ces refus au
    # lieu de perdre la societe.
    for essai in range(3):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                if s:
                    s.compte("429_rattrape")
                time.sleep(8 * (essai + 1))
                continue
            if s and e.code != 404:
                s.erreur(f"http_{e.code}", url[-50:])
            return None
        except Exception as e:
            if s:
                s.erreur("reseau_sa", f"{type(e).__name__} {url[-40:]}")
            return None
    if s:
        s.erreur("http_429_abandon", url[-50:])
    return None


def norm(t):
    """Normalise un libelle de ligne.

    ENTITES DECODEES D'ABORD : « Cash & Equivalents » arrivait en
    « cash amp equivalents » et ne correspondait a rien. C'est pourquoi `cash`
    et `ppe` manquaient alors que le reste du bilan etait lu."""
    t = _html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[^\w\s]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def reconnaitre(libelle):
    """Rend le poste du noyau, ou None.

    RECONNAISSANCE PAR PREFIXE, pas par egalite. StockAnalysis colle la
    variation au nom : « revenue revenue growth », « operating income
    operating income growth ». Une egalite stricte ne trouvait rien sur toute
    la page du compte de resultat — zero poste sur LVMH comme sur ASML.

    Le prefixe doit finir la chaine ou etre suivi d'un espace : « receivables »
    ne doit pas capturer « other receivables », qui est un autre poste.
    Les libelles sont essayes du plus long au plus court, pour que
    « total current liabilities » gagne sur « total current ».
    """
    if libelle in POSTES:
        return POSTES[libelle]
    for cle in _POSTES_TRIES:
        if libelle.startswith(cle) and (len(libelle) == len(cle)
                                        or libelle[len(cle)] == " "):
            return POSTES[cle]
    return None


def nombre(t):
    t = re.sub(r"<[^>]+>", "", t).strip().replace(",", "").replace("\u2212", "-")
    if not t or t in ("-", "—", "n/a", "upgrade"):
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    m = re.match(r"^-?[\d.]+$", t)
    if not m:
        return None
    v = float(t)
    return -v if neg else v


def extraire(html, page=None):
    """Rend {poste: {annee: valeur}}. Les montants sont en MILLIONS chez
    StockAnalysis, sauf le nombre d'actions : remis en unites ici, sans quoi
    le noyau comparerait des grandeurs de deux ordres differents."""
    if not html:
        return {}
    entetes = re.findall(r"<th[^>]*>(.*?)</th>", html, re.S)
    annees = []
    for h in entetes:
        m = re.search(r"(20\d\d)", re.sub(r"<[^>]+>", " ", h))
        annees.append(int(m.group(1)) if m else None)
    if not any(annees):
        return {}

    out = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)
        if len(cells) < 2:
            continue
        poste = reconnaitre(norm(cells[0]))
        if not poste:
            continue
        # Un poste lu hors de sa page est un faux positif de la reconnaissance
        # par prefixe : « Total Liabilities and Equity » captait
        # « total liabilities » et rendait le total de l'actif.
        if page and poste not in PORTEE.get(page, set()):
            continue
        for i, c in enumerate(cells):
            if i >= len(annees) or annees[i] is None:
                continue
            v = nombre(c)
            if v is None:
                continue
            # StockAnalysis sert TOUS ses montants en millions, nombre
            # d'actions compris : un facteur unique, pas de cas particulier.
            # Le controle d'unite de l'etage prix — cours x actions compare a
            # la capitalisation publiee — reste le filet si une place devait
            # servir une autre echelle.
            # Le capex est servi NEGATIF (sortie de tresorerie). Le noyau
            # attend une valeur positive, comme pour les deposants SEC.
            if poste == "capex":
                v = abs(v)
            out.setdefault(poste, {})[annees[i]] = v * 1e6
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sonder", default="", help="un ticker : affiche ce qui est extrait, n'ecrit rien")
    ap.add_argument("--tranche", type=int, default=0,
                    help="numero de tranche ; 0 = mode file, avance tout seul")
    ap.add_argument("--taille", type=int, default=150, help="societes par tranche")
    ap.add_argument("--places", default="", help="suffixes Yahoo, ex .PA,.AS")
    a = ap.parse_args()

    s = sonde(f"incr8_comptes_intl_t{a.tranche}")
    print(f"incr8_comptes_intl v6 — Run {RUN_TS}")

    if a.sonder:
        suf = "." + a.sonder.split(".")[-1] if "." in a.sonder else ""
        code, sym = PLACE.get(suf), a.sonder.split(".")[0].replace("-", ".")
        if not code:
            print(f"place inconnue pour {a.sonder}")
            sys.exit(1)
        total = {}
        print("  essai des chemins du compte de resultat :")
        for c in CHEMINS_RESULTAT:
            u = f"https://stockanalysis.com/quote/{code}/{sym}/financials/{c}"
            h = lire(u, s)
            d = extraire(h, "resultat") if h else {}
            print(f"    /{c or '(racine)'} : {len(h) if h else 0} car., {len(d)} postes")
            time.sleep(PAUSE)

        for chemin, nom in PAGES.items():
            url = f"https://stockanalysis.com/quote/{code}/{sym}/financials/{chemin}"
            html = lire(url, s)
            d = extraire(html, nom)
            print(f"\n  --- {nom} ---")
            print(f"  URL    : {url}")
            print(f"  servi  : {len(html) if html else 0} caracteres")
            print(f"  postes : {len(d)} — {', '.join(sorted(d)) or '(aucun)'}")
            if html:
                # Libelles de premiere colonne, reconnus ou non. C'est la seule
                # facon de savoir si la page est vide, si elle a change de forme,
                # ou si ce sont nos libelles qui ne correspondent plus.
                lus, inconnus = 0, []
                for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
                    c = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)
                    if len(c) < 2:
                        continue
                    lib = norm(c[0])
                    if not lib:
                        continue
                    lus += 1
                    if reconnaitre(lib) is None and len(inconnus) < 18:
                        inconnus.append(lib)
                print(f"  lignes de tableau lues : {lus}")
                if inconnus:
                    print(f"  libelles NON reconnus  : {' | '.join(inconnus)}")
                if not lus:
                    ent = re.findall(r"<th[^>]*>(.*?)</th>", html, re.S)[:6]
                    print(f"  en-tetes trouves       : {[norm(x) for x in ent]}")
                    print(f"  extrait                : "
                          f"{re.sub(r'<[^>]+>', ' ', html[:300])[:180]}")
            for k, v in d.items():
                total.setdefault(k, {}).update(v)
            time.sleep(PAUSE)
        print(f"\n{len(total)} postes distincts")
        for k in sorted(total):
            ans = sorted(total[k])
            print(f"  {k:14} {len(ans)} ex. {ans[0] if ans else ''}-{ans[-1] if ans else ''}"
                  f"  dernier = {total[k][ans[-1]]:,.0f}" if ans else "")
        print("\nAucune ecriture. Verifier ces chiffres contre le rapport annuel")
        print("AVANT d'ingerer 1 900 societes sur la meme extraction.")
        return

    s.phase("cibles")
    # `sans_sa` : societes dont la source ne publie aucune page — cotations
    # secondaires pour l'essentiel. Les reinterroger coute trois requetes par
    # tranche sans jamais rien rendre.
    W = ("s.origine LIKE '%intl%' AND s.cik IS NULL "
         "AND instr(COALESCE(s.origine, ''), 'sans_sa') = 0")
    if a.places:
        suf = [p.strip() for p in a.places.split(",") if p.strip()]
        W += " AND (" + " OR ".join("s.ticker LIKE ?" for _ in suf) + ")"
        params = ["%" + p for p in suf]
    else:
        params = []
    cibles = [l["ticker"] for l in d1(
        f"SELECT s.ticker, "
        f"(SELECT COUNT(*) FROM comptes2 c WHERE c.ticker = s.ticker) AS n "
        f"FROM societe s WHERE {W} "
        f"ORDER BY n ASC, s.capitalisation DESC",
        params)[0]["results"]]
    total_file = len(cibles)
    if a.tranche:
        # Tranche explicite : comportement d'origine, utile pour rejouer.
        d = (a.tranche - 1) * a.taille
        cibles = cibles[d:d + a.taille]
    else:
        # Mode file : on prend simplement les premieres, c'est-a-dire celles
        # qui n'ont aucun compte.
        cibles = cibles[:a.taille]
    print(f"  file totale : {total_file} societes restantes")
    print(f"  {len(cibles)} societes a traiter")
    if not cibles:
        journal("OK", s.etape, 0, "VERT", "aucune cible", s.resume())
        return

    s.phase("collecte")
    rangs, sans_page = {}, []
    for i, t in enumerate(cibles, 1):
        suf = "." + t.split(".")[-1]
        code, sym = PLACE.get(suf), t.split(".")[0].replace("-", ".")
        if not code:
            s.erreur("place_inconnue", t)
            continue
        total = {}
        for chemin in PAGES:
            d = extraire(lire(f"https://stockanalysis.com/quote/{code}/{sym}/financials/{chemin}", s),
                         PAGES[chemin])
            for k, v in d.items():
                total.setdefault(k, {}).update(v)
            time.sleep(PAUSE)
        if not total.get("revenue"):
            s.erreur("sans_revenue", t)
            sans_page.append(t)
            continue
        s.compte("societes_lues")
        for annee in sorted({x for v in total.values() for x in v}):
            ligne = {c: total.get(c, {}).get(annee) for c in COLONNES}
            if ligne["revenue"] is None and ligne["ebit"] is None:
                continue
            rangs[f"{t}|{annee}"] = ligne
        if i % 25 == 0:
            print(f"  {i}/{len(cibles)} — {len(rangs)} lignes")

    print(f"  {len(rangs)} lignes composees")
    if not rangs:
        journal("PANNE", s.etape, 0, "ROUGE", "aucune ligne extraite", s.resume())
        s.afficher()
        sys.exit(1)

    s.phase("differentiel")
    a_ecrire = ecrire_differentiel("comptes2", "ticker || '|' || exercice",
                                   rangs, COLONNES, s)
    if a_ecrire:
        s.phase("ecriture")
        budget(len(a_ecrire), "comptes2")
        cols = "ticker, exercice, " + ", ".join(COLONNES) + ", source"
        vals = []
        for cle, l in a_ecrire.items():
            tk, an = cle.rsplit("|", 1)
            cells = ", ".join("NULL" if l[c] is None else repr(round(float(l[c]), 4))
                              for c in COLONNES)
            vals.append(f"('{tk}', {an}, {cells}, 'stockanalysis')")
        n = 0
        for i in range(0, len(vals), 200):
            lot = vals[i:i + 200]
            d1(f"INSERT OR REPLACE INTO comptes2 ({cols}) VALUES " + ", ".join(lot),
               lignes=len(lot), table="comptes2")
            n += len(lot)
            print(f"  ecrit {n}/{len(vals)}")
    else:
        n = 0
        print("  rien a ecrire")

    # Le marquage passe par `origine`, deja porteur de la provenance : aucune
    # colonne nouvelle, et la requete de cibles les ecarte au tour suivant.
    if sans_page:
        s.phase("marquage")
        print(f"  {len(sans_page)} societes sans page — marquees pour ne plus "
              f"etre interrogees")
        for i in range(0, len(sans_page), 20):
            lot = sans_page[i:i + 20]
            d1("UPDATE societe SET origine = COALESCE(origine, '') || ',sans_sa' "
               "WHERE ticker IN (" + ", ".join("?" * len(lot)) + ") "
               "AND (origine IS NULL OR instr(origine, 'sans_sa') = 0)",
               lot, lignes=len(lot), table="societe")
        s.compte("marquees_sans_page", len(sans_page))

    reste = d1(f"SELECT COUNT(*) AS n FROM societe s WHERE {W} "
               f"AND NOT EXISTS (SELECT 1 FROM comptes2 c WHERE c.ticker = s.ticker)",
               params)[0]["results"][0]["n"]
    print(f"\n  reste sans comptes : {reste}"
          + ("  — relancer" if reste else "  — file vide"))
    s.compte("reste_file", reste)

    r = s.afficher()
    journal("OK", s.etape, n, "VERT",
            f"{n} lignes, {s.compteurs.get('societes_lues', 0)} societes, "
            f"{reste} restantes", r)
    print("OK")


if __name__ == "__main__":
    main()
