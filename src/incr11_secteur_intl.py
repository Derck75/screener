#!/usr/bin/env python3
"""
INCREMENT 11 — Activite des societes internationales -> `societe.secteur`.

POURQUOI UN SCRIPT SEPARE. Le libelle d'activite des deposants americains vient
du fichier `submissions` de la SEC, deja interroge pour les sieges : aucun cout
supplementaire. Les societes hors EDGAR n'y figurent pas — les 59 candidates
PEA sortaient donc sans activite.

SOURCE. La page de cotation de StockAnalysis porte « Sector » et « Industry ».
Une requete par societe, UNE SEULE FOIS : une activite ne change pas.

D'ABORD --sonder, ENSUITE l'ingestion. Coder une extraction sur une structure
supposee est ce qui a coute six versions au chantier ESEF et trois a celui-ci.

File auto-progressive : chaque passage prend les societes sans activite, par
capitalisation decroissante. Priorite a celles qui ont des comptes — les autres
ne peuvent de toute facon pas devenir candidates.
"""

import argparse
import html as _html
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from commun import d1, journal, sonde, budget, RUN_TS  # noqa: E402

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
PAUSE = float(os.environ.get("PAUSE_SA", "1.0"))

PLACE = {".PA": "epa", ".AS": "ams", ".BR": "ebr", ".LS": "els", ".IR": "dub",
         ".DE": "etr", ".MI": "mil", ".MC": "bme", ".VI": "vie", ".AT": "ath",
         ".ST": "sto", ".CO": "cph", ".HE": "hel", ".OL": "osl", ".IC": "ice",
         ".WA": "war", ".PR": "pra", ".BD": "bud", ".L": "lon", ".SW": "swx",
         ".T": "tyo"}

# Traduction des industries StockAnalysis les plus frequentes. Les autres
# passent telles quelles : mieux vaut « Specialty Chemicals » que rien.
FR = {
    "software - application": "Logiciel", "software - infrastructure": "Logiciel",
    "information technology services": "Services numériques",
    "semiconductors": "Semiconducteurs",
    "semiconductor equipment & materials": "Équipement semiconducteurs",
    "drug manufacturers - general": "Pharmacie",
    "drug manufacturers - specialty & generic": "Pharmacie",
    "biotechnology": "Biotechnologie",
    "medical devices": "Matériel médical",
    "medical instruments & supplies": "Matériel médical",
    "banks - regional": "Banque", "banks - diversified": "Banque",
    "insurance - property & casualty": "Assurance dommages",
    "insurance - life": "Assurance vie",
    "insurance - diversified": "Assurance",
    "insurance - reinsurance": "Réassurance",
    "asset management": "Gestion d'actifs",
    "capital markets": "Marchés de capitaux",
    "steel": "Sidérurgie", "aluminum": "Aluminium", "copper": "Cuivre",
    "gold": "Mines d'or", "other industrial metals & mining": "Mines",
    "specialty chemicals": "Chimie de spécialité", "chemicals": "Chimie",
    "oil & gas integrated": "Pétrole intégré",
    "oil & gas e&p": "Exploration pétrolière",
    "oil & gas midstream": "Infrastructure pétrolière",
    "utilities - regulated electric": "Électricité",
    "utilities - regulated gas": "Gaz",
    "utilities - renewable": "Énergies renouvelables",
    "luxury goods": "Luxe", "apparel manufacturing": "Habillement",
    "apparel retail": "Distribution habillement",
    "footwear & accessories": "Chaussure et accessoires",
    "beverages - alcoholic": "Boissons alcoolisées",
    "beverages - wineries & distilleries": "Vins et spiritueux",
    "beverages - non-alcoholic": "Boissons",
    "packaged foods": "Agroalimentaire",
    "household & personal products": "Hygiène et beauté",
    "auto parts": "Équipement automobile", "auto manufacturers": "Automobile",
    "aerospace & defense": "Aéronautique et défense",
    "specialty industrial machinery": "Machines industrielles",
    "building products & equipment": "Matériaux de construction",
    "engineering & construction": "Ingénierie et construction",
    "integrated freight & logistics": "Logistique",
    "airlines": "Transport aérien", "marine shipping": "Transport maritime",
    "railroads": "Ferroviaire",
    "telecom services": "Télécoms",
    "advertising agencies": "Publicité",
    "entertainment": "Divertissement",
    "internet content & information": "Internet",
    "internet retail": "Commerce en ligne",
    "specialty retail": "Distribution spécialisée",
    "grocery stores": "Distribution alimentaire",
    "restaurants": "Restauration", "lodging": "Hôtellerie",
    "real estate services": "Immobilier", "reit - diversified": "Foncière",
    "conglomerates": "Conglomérat",
    "staffing & employment services": "Services RH",
    "consulting services": "Conseil",
    "security & protection services": "Sécurité",
    "waste management": "Gestion des déchets",
    "paper & paper products": "Papier",
    "packaging & containers": "Emballage",
    "electronic components": "Composants électroniques",
    "electrical equipment & parts": "Équipement électrique",
    "farm & heavy construction machinery": "Machines agricoles",
    "tobacco": "Tabac", "gambling": "Jeux d'argent",
}

# Trois formes d'affichage possibles selon les millesimes du site. Toutes sont
# essayees ; celle qui rend un resultat gagne.
MOTIFS = [
    r'>\s*Industry\s*</[^>]*>\s*<[^>]*>\s*(?:<a[^>]*>)?\s*([^<]{3,60})',
    r'"industry"\s*:\s*"([^"]{3,60})"',
    r'>\s*Sector\s*</[^>]*>\s*<[^>]*>\s*(?:<a[^>]*>)?\s*([^<]{3,60})',
    r'"sector"\s*:\s*"([^"]{3,60})"',
]


def lire(url, s=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for essai in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                if s:
                    s.compte("429_rattrape")
                time.sleep(8 * (essai + 1))
                continue
            if s and e.code != 404:
                s.erreur(f"http_{e.code}", url[-45:])
            return None
        except Exception as e:
            if s:
                s.erreur("reseau_sa", f"{type(e).__name__}")
            return None
    return None


def extraire(html):
    """Rend (valeur, motif_utilise) ou (None, None)."""
    if not html:
        return None, None
    for i, m in enumerate(MOTIFS):
        r = re.search(m, html, re.I | re.S)
        if r:
            v = _html.unescape(r.group(1)).strip()
            if v and v.lower() not in ("n/a", "-", "none"):
                return v, f"motif {i + 1}"
    return None, None


def traduire(v):
    if not v:
        return None
    t = FR.get(v.strip().lower())
    if t:
        return t
    court = v.strip()
    if len(court) > 38:
        court = court[:38].rsplit(" ", 1)[0] + "…"
    return court


def url_de(ticker):
    suf = "." + ticker.split(".")[-1] if "." in ticker else ""
    code = PLACE.get(suf)
    if not code:
        return None
    sym = ticker.split(".")[0].replace("-", ".")
    return f"https://stockanalysis.com/quote/{code}/{sym}/"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sonder", default="", help="un ticker, n'ecrit rien")
    ap.add_argument("--taille", type=int, default=200)
    ap.add_argument("--avec-comptes", action="store_true", default=True,
                    help="limite aux societes ayant des comptes")
    a = ap.parse_args()

    s = sonde("incr11_secteur_intl")
    print(f"incr11_secteur_intl v1 — Run {RUN_TS}")

    # ---- sonde -------------------------------------------------------------
    if a.sonder:
        u = url_de(a.sonder)
        if not u:
            print(f"place inconnue pour {a.sonder}")
            sys.exit(1)
        print(f"  URL : {u}")
        h = lire(u, s)
        print(f"  servi : {len(h) if h else 0} caracteres")
        brut, motif = extraire(h)
        print(f"  extrait : {brut!r} ({motif or 'aucun motif'})")
        print(f"  traduit : {traduire(brut)!r}")
        if h and not brut:
            # Montrer ce que la page contient autour des mots cles, pour
            # corriger le motif sur des faits plutot que sur une supposition.
            for mot in ("Industry", "Sector"):
                i = h.find(mot)
                print(f"  contexte « {mot} » : "
                      + (re.sub(r"\s+", " ", h[i:i + 220]) if i >= 0 else "absent"))
        print("\nAucune ecriture.")
        return

    # ---- file --------------------------------------------------------------
    s.phase("cibles")
    W = ("s.origine LIKE '%intl%' AND (s.secteur IS NULL OR s.secteur = '')")
    if a.avec_comptes:
        W += " AND EXISTS (SELECT 1 FROM comptes2 c WHERE c.ticker = s.ticker)"
    cibles = [l["ticker"] for l in d1(
        f"SELECT s.ticker FROM societe s WHERE {W} "
        f"ORDER BY s.capitalisation DESC")[0]["results"]]
    total = len(cibles)
    cibles = cibles[:a.taille]
    print(f"  file totale : {total} · {len(cibles)} traitees ce passage")
    if not cibles:
        print("  file vide")
        journal("OK", "incr11_secteur_intl", 0, "VERT", "file vide", s.resume())
        return

    s.phase("collecte")
    maj, inconnus = [], {}
    for i, t in enumerate(cibles, 1):
        u = url_de(t)
        if not u:
            s.erreur("place_inconnue", t)
            continue
        brut, _ = extraire(lire(u, s))
        time.sleep(PAUSE)
        if not brut:
            s.erreur("sans_activite", t)
            continue
        fr = traduire(brut)
        if brut.strip().lower() not in FR:
            inconnus[brut] = inconnus.get(brut, 0) + 1
        maj.append((t, fr))
        if i % 50 == 0:
            print(f"  {i}/{len(cibles)} — {len(maj)} activites")

    print(f"  {len(maj)} activites relevees")
    if not maj:
        journal("PANNE", "incr11_secteur_intl", 0, "ROUGE",
                "aucune activite extraite", s.resume())
        s.afficher()
        sys.exit(1)

    s.phase("ecriture")
    budget(len(maj), "societe")
    n = 0
    for i in range(0, len(maj), 20):
        lot = maj[i:i + 20]
        vals, params = [], []
        for t, fr in lot:
            vals.append("(?, ?, ?)")
            params += [t, fr, RUN_TS]
        d1("INSERT INTO societe (ticker, secteur, maj) VALUES " + ", ".join(vals)
           + " ON CONFLICT(ticker) DO UPDATE SET secteur = excluded.secteur, "
             "maj = excluded.maj", params, lignes=len(lot), table="societe")
        n += len(lot)
    print(f"  ecrit {n}")

    if inconnus:
        print("\n  libelles non traduits, a ajouter a la table FR :")
        for k, v in sorted(inconnus.items(), key=lambda x: -x[1])[:12]:
            print(f"    {v:3}  {k}")

    reste = total - len(maj)
    print(f"\n  reste : {reste}" + ("  — relancer" if reste > 0 else "  — termine"))
    r = s.afficher()
    journal("OK", "incr11_secteur_intl", n, "VERT",
            f"{n} activites, {reste} restantes", r)
    print("OK")


if __name__ == "__main__":
    main()
