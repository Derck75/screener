"""
INCREMENT 13 — Identifiant legal (LEI) de chaque societe -> `societe.lei`.

POURQUOI. Le LEI est la cle qui ouvre les depots officiels hors StockAnalysis :
les rapports ESEF europeens (filings.xbrl.org) s'indexent par LEI. Il servira
au repli des comptes internationaux si StockAnalysis tombe (canari LVMH), et
plus largement de cle commune et stable — un ticker change, un LEI jamais.

SOURCE. Le fichier de correspondance ISIN -> LEI publie chaque jour par la
GLEIF avec l'ANNA (gratuit, licence ouverte) : UN telechargement pour toutes
les societes, aucune limite de debit. Repli : l'API GLEIF par ISIN, a petit
pas, pour les seules places europeennes encore sans LEI.

Ecriture differentielle : seules les societes dont le LEI change sont ecrites.
Mensuel ; un LEI ne bouge pas.
"""

import argparse
import csv
import io
import json
import os
import sys
import time
import zipfile

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from commun import d1, journal, sonde, migrer  # noqa: E402

FICHIER = "https://mapping.gleif.org/api/v2/isin-lei/latest/download"
API = "https://api.gleif.org/api/v1/lei-records"
EUROPE = (".PA", ".DE", ".L", ".AS", ".MI", ".MC", ".ST", ".OL", ".HE", ".CO", ".BR",
          ".VI", ".LS", ".IR", ".WA", ".AT", ".SW", ".IC", ".BD", ".PR")


def depuis_fichier(isins, s):
    """Correspondance complete en un telechargement ; rend {isin: lei}."""
    r = requests.get(FICHIER, timeout=300, allow_redirects=True)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    nom = next(n for n in z.namelist() if n.lower().endswith(".csv"))
    trouves = {}
    with z.open(nom) as f:
        for ligne in csv.reader(io.TextIOWrapper(f, encoding="utf-8")):
            if len(ligne) >= 2:
                lei, isin = ligne[0].strip(), ligne[1].strip()
                if isin in isins and len(lei) == 20:
                    trouves[isin] = lei
    s.compte("fichier_lignes_retenues", len(trouves))
    return trouves


def depuis_api(isins, s, lot):
    trouves = {}
    for isin in isins[:lot]:
        try:
            r = requests.get(API, params={"filter[isin]": isin, "page[size]": 1}, timeout=30)
            if r.status_code == 429:
                time.sleep(60)
                continue
            d = r.json().get("data") or []
            if d:
                trouves[isin] = d[0]["id"]
        except Exception as e:  # noqa: BLE001
            s.erreur("api_gleif", str(e)[:80])
        time.sleep(1.1)   # GLEIF : 60 requetes par minute
    return trouves


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lot", type=int, default=300, help="ISIN par run en repli API")
    a = ap.parse_args()
    s = sonde("incr13_lei")
    migrer("societe", {"lei": "TEXT", "lei_le": "TEXT"})
    lignes = d1("SELECT ticker, isin, lei FROM societe WHERE isin IS NOT NULL AND isin <> ''")[0]["results"]
    par_isin = {}
    for l in lignes:
        par_isin.setdefault(l["isin"].strip().upper(), []).append(l)
    print(f"{len(lignes)} societes avec ISIN, {sum(1 for l in lignes if l['lei'])} avec LEI")

    s.phase("correspondance")
    source = "fichier GLEIF-ANNA"
    try:
        trouves = depuis_fichier(set(par_isin), s)
    except Exception as e:  # noqa: BLE001
        s.erreur("fichier_gleif", str(e)[:120])
        source = "API GLEIF (repli)"
        manquants = [i for i, ls in par_isin.items() if not ls[0]["lei"] and any(l["ticker"].endswith(EUROPE) for l in ls)]
        trouves = depuis_api(manquants, s, a.lot)

    s.phase("ecriture")
    jour = time.strftime("%Y-%m-%d", time.gmtime())
    maj = [[l["ticker"], lei] for isin, lei in trouves.items() for l in par_isin.get(isin, []) if l["lei"] != lei]
    for i in range(0, len(maj), 400):
        lot = json.dumps(maj[i:i + 400])
        d1("UPDATE societe SET lei = j.lei, lei_le = ?2 FROM (SELECT json_extract(value, '$[0]') AS t, json_extract(value, '$[1]') AS lei "
           "FROM json_each(?1)) AS j WHERE societe.ticker = j.t", [lot, jour], lignes=len(maj[i:i + 400]), table="societe")
    tot = d1("SELECT COUNT(*) AS n, SUM(lei IS NOT NULL) AS l, "
             "SUM(CASE WHEN lei IS NOT NULL AND (" + " OR ".join(f"ticker LIKE '%{p}'" for p in EUROPE) + ") THEN 1 ELSE 0 END) AS le, "
             "SUM(CASE WHEN " + " OR ".join(f"ticker LIKE '%{p}'" for p in EUROPE) + " THEN 1 ELSE 0 END) AS e FROM societe WHERE isin IS NOT NULL")[0]["results"][0]
    msg = f"{len(maj)} LEI ecrits ({source}) · couverture {tot['l']}/{tot['n']} · Europe {tot['le']}/{tot['e']}"
    print(msg)
    s.afficher()
    journal("OK", "incr13_lei", len(maj), "VERT", msg, s.resume())


if __name__ == "__main__":
    main()
