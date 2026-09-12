#!/usr/bin/env python3
"""
UNIVERS INTERNATIONAL — EXPLORATION, AUCUNE ECRITURE.

Ne touche pas a D1. Ne demande aucun secret Cloudflare. Concu pour tourner
quand le quota d'ecriture est epuise, et pour repondre AVANT de coder
l'ingestion a trois questions qui decident de tout :

  1. Les listes StockAnalysis sont-elles lisibles par un script, et sous quel
     format ? Le site sert son HTML cote serveur (verifie en production par le
     worker), mais la forme du tableau n'est pas garantie.
  2. Combien de societes par place, et a partir de quelle capitalisation
     l'univers devient-il raisonnable ?
  3. Combien de candidates ELIGIBLES PEA en sortent reellement ? C'est le
     chiffre qui decide si l'objectif « 5 a 10 lignes PEA » est atteignable.

REGLE DE SECURITE REPRISE DU WORKER : deriver une URL, c'est la deviner. Un
suffixe mal mappe mene a la page d'une AUTRE societe, dont les comptes
seraient parfaitement plausibles et parfaitement faux. Le mode --verifier
controle que la page servie porte bien le symbole attendu.

Usage :
    python src/univers_intl.py                 # exploration, toutes les places
    python src/univers_intl.py --places epa    # une seule place
    python src/univers_intl.py --verifier 5    # controle 5 URL par place
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# place StockAnalysis -> (chemin de liste, suffixe Yahoo, pays, eligible PEA)
# L'eligibilite tient au SIEGE SOCIAL en UE/EEE, pas a la place de cotation :
# la place n'est ici qu'une presomption a confirmer societe par societe.
PLACES = {
    "epa": ("euronext-paris", ".PA", "FR", True),
    "ams": ("euronext-amsterdam", ".AS", "NL", True),
    "ebr": ("euronext-brussels", ".BR", "BE", True),
    "els": ("euronext-lisbon", ".LS", "PT", True),
    "etr": ("deutsche-boerse-xetra", ".DE", "DE", True),
    "mil": ("borsa-italiana", ".MI", "IT", True),
    "bme": ("bolsa-de-madrid", ".MC", "ES", True),
    "sto": ("stockholm-stock-exchange", ".ST", "SE", True),
    "cph": ("copenhagen-stock-exchange", ".CO", "DK", True),
    "hel": ("helsinki-stock-exchange", ".HE", "FI", True),
    "osl": ("oslo-stock-exchange", ".OL", "NO", True),
    # Hors PEA — Brexit et Suisse hors EEE. Utiles en CTO uniquement.
    "lon": ("london-stock-exchange", ".L", "GB", False),
    "swx": ("swiss-exchange", ".SW", "CH", False),
    # Japon : hors PEA, meme mecanique d'ingestion que l'Europe.
    "tyo": ("tokyo-stock-exchange", ".T", "JP", False),
}

CAP_MIN = 2e9   # en devise locale, ordre de grandeur seulement a ce stade


def lire(url, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "text/html,application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as rep:
        return rep.read().decode("utf-8", "replace")


def extraire(html):
    """Trois strategies, de la plus fiable a la plus grossiere.
    Rend (lignes, nom_de_la_strategie). Une strategie qui rend peu de lignes
    n'est pas validee pour autant : le nombre est publie, c'est au lecteur de
    juger s'il correspond a la place."""

    # A. JSON embarque — le plus sur quand il existe : pas d'ambiguite de
    #    colonnes, les champs sont nommes.
    for motif in (r'"data"\s*:\s*(\[\{.*?\}\])\s*[,\}]',
                  r'__NEXT_DATA__[^>]*>(\{.*?\})</script>'):
        m = re.search(motif, html, re.S)
        if not m:
            continue
        try:
            brut = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        lignes = brut if isinstance(brut, list) else None
        if lignes and isinstance(lignes[0], dict):
            cles = set(lignes[0])
            if {"s"} & cles or {"symbol"} & cles:
                out = []
                for l in lignes:
                    sym = l.get("s") or l.get("symbol")
                    if not sym:
                        continue
                    out.append({"sym": str(sym).upper(),
                                "nom": l.get("n") or l.get("name") or "",
                                "cap": l.get("marketCap") or l.get("mc")})
                if out:
                    return out, "json embarque"

    # B. Tableau HTML classique.
    lignes, bloc = [], re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S)
    for tr in bloc:
        cells = [re.sub(r'<[^>]+>', ' ', c).strip()
                 for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.S)]
        if len(cells) < 3:
            continue
        sym = cells[1] if re.fullmatch(r'[A-Z0-9.\-]{1,10}', cells[1] or '') else (
              cells[0] if re.fullmatch(r'[A-Z0-9.\-]{1,10}', cells[0] or '') else None)
        if sym:
            lignes.append({"sym": sym.upper(), "nom": " ".join(cells[2:3]), "cap": None})
    if lignes:
        return lignes, "tableau HTML"

    # C. Liens de cotation — grossier mais revele si la page a bien ete servie.
    syms = re.findall(r'/quote/[a-z]+/([A-Z0-9.\-]{1,10})/', html)
    if syms:
        vus, out = set(), []
        for s in syms:
            if s not in vus:
                vus.add(s)
                out.append({"sym": s, "nom": "", "cap": None})
        return out, "liens de cotation"

    return [], "AUCUNE"


def vers_yahoo(sym, suffixe):
    """Convention inverse de celle du worker : StockAnalysis ecrit le symbole
    avec un point la ou Yahoo met un tiret."""
    return sym.replace(".", "-") + suffixe


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--places", default="", help="codes separes par des virgules")
    ap.add_argument("--verifier", type=int, default=0,
                    help="nombre d'URL a controler par place")
    ap.add_argument("--cap-min", type=float, default=CAP_MIN)
    a = ap.parse_args()

    cibles = [p.strip() for p in a.places.split(",") if p.strip()] or list(PLACES)
    inconnues = [p for p in cibles if p not in PLACES]
    if inconnues:
        print(f"places inconnues : {inconnues}")
        sys.exit(1)

    print("EXPLORATION — aucune ecriture, aucun secret Cloudflare requis\n")
    total = pea = 0
    resume = []

    for code in cibles:
        chemin, suffixe, pays, elig = PLACES[code]
        url = f"https://stockanalysis.com/list/{chemin}/"
        try:
            html = lire(url)
        except urllib.error.HTTPError as e:
            print(f"{code:5} {pays}  HTTP {e.code} — chemin probablement faux : {chemin}")
            resume.append((code, pays, 0, "HTTP " + str(e.code)))
            continue
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"{code:5} {pays}  reseau : {type(e).__name__}")
            resume.append((code, pays, 0, "reseau"))
            continue

        lignes, strategie = extraire(html)
        avec_cap = [l for l in lignes if isinstance(l.get("cap"), (int, float))]
        retenues = [l for l in avec_cap if l["cap"] >= a.cap_min] if avec_cap else lignes

        print(f"{code:5} {pays}  {len(lignes):5} symboles  ({strategie})"
              f"{f' · {len(retenues)} au-dessus du seuil' if avec_cap else ' · capitalisation non servie'}"
              f"{'  [PEA]' if elig else ''}")
        for l in retenues[:3]:
            y = vers_yahoo(l["sym"], suffixe)
            cap = f"{l['cap'] / 1e9:.1f} Md" if isinstance(l.get("cap"), (int, float)) else "n.c."
            print(f"        {l['sym']:8} -> {y:12} {cap:>10}  {(l['nom'] or '')[:34]}")
        if not lignes:
            print(f"        extrait du HTML servi : {re.sub(r'<[^>]+>', ' ', html[:200])[:150]}")

        total += len(retenues)
        if elig:
            pea += len(retenues)
        resume.append((code, pays, len(retenues), strategie))

        # Controle anti-homonymie, repris du worker : une URL derivee n'est
        # acceptable que si la page servie porte le symbole attendu.
        if a.verifier and retenues:
            ok = faux = 0
            for l in retenues[:a.verifier]:
                u = f"https://stockanalysis.com/quote/{code}/{l['sym']}/"
                try:
                    p = lire(u, timeout=25)
                    titre = re.search(r'<title>(.*?)</title>', p, re.S)
                    t = (titre.group(1) if titre else "")
                    if l["sym"].split(".")[0] in t.upper():
                        ok += 1
                    else:
                        faux += 1
                        print(f"        URL NON VERIFIEE : {l['sym']} -> titre « {t[:60]} »")
                except Exception:
                    faux += 1
                time.sleep(0.4)
            print(f"        controle : {ok} verifiees, {faux} rejetees")
        time.sleep(0.6)

    print("\n--- SYNTHESE ---")
    for code, pays, n, strat in resume:
        print(f"  {code:5} {pays:3} {n:5}  {strat}")
    print(f"\n  univers international retenu : {total}")
    print(f"  dont places de la zone PEA    : {pea}")
    print("\n  Rappel : la place de cotation ne fait PAS l'eligibilite PEA —")
    print("  seul le siege social en UE/EEE la donne, et la liste du courtier")
    print("  fait foi. Le chiffre ci-dessus est un plafond, pas un resultat.")


if __name__ == "__main__":
    main()
