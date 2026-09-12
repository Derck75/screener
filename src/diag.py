#!/usr/bin/env python3
"""
DIAGNOSTIC — LECTURE SEULE. N'ECRIT RIEN, PAS MEME DANS `runs`.

Journaliser ce run coûterait une ecriture, et ce script existe precisement
pour tourner quand le quota d'ecriture est epuise.

Repond a deux questions laissees ouvertes :
  1. La moitie de l'univers est exclue mecaniquement — a juste titre ?
     Les exemples par motif tranchent : si ce sont des banques, foncieres et
     coquilles, le filtre fait son travail ; si ce sont des industrielles
     ordinaires, c'est le seuil qui est faux, pas l'univers.
  2. ~300 series de ROIC sont en regime MIXTE. Une serie qui change de base
     en cours de route ne se compare pas a elle-meme.
"""

import json
import os
import sys

import requests

CF_ACCOUNT = os.environ["CF_ACCOUNT"]
CF_DB = os.environ["CF_DB"]
CF_TOKEN = os.environ["CF_TOKEN"]
D1_URL = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/d1/database/{CF_DB}/query"


def d1(sql, params=None):
    r = requests.post(
        D1_URL,
        headers={"Authorization": f"Bearer {CF_TOKEN}",
                 "Content-Type": "application/json"},
        json={"sql": sql, "params": params or []},
        timeout=120,
    )
    j = r.json()
    if not j.get("success"):
        print(f"ECHEC D1 : {json.dumps(j.get('errors'))[:400]}")
        sys.exit(1)
    return j["result"][0]["results"]


def titre(t):
    print(f"\n{'=' * 62}\n{t}\n{'=' * 62}")


def main():
    print("diag v1 — LECTURE SEULE, aucune ecriture")

    titre("VOLUMES")
    for table in ("societe", "comptes", "metriques", "runs"):
        n = d1(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]
        print(f"  {table:12} {n}")

    titre("EXCLUSIONS MECANIQUES — motif, volume, exemples")
    for l in d1("SELECT exclusion, COUNT(*) AS n FROM metriques "
                "WHERE exclusion IS NOT NULL GROUP BY exclusion ORDER BY n DESC"):
        print(f"\n  {l['n']:5}  {l['exclusion']}")
        ex = d1("SELECT m.ticker, s.nom, m.dette_sur_ca, m.cp_sur_ca, m.roic_median "
                "FROM metriques m LEFT JOIN societe s ON s.ticker = m.ticker "
                "WHERE m.exclusion = ? LIMIT 6", [l["exclusion"]])
        for e in ex:
            print(f"         {e['ticker']:7} dette/CA {round(e['dette_sur_ca'] or 0, 2):6} "
                  f" CP/CA {round(e['cp_sur_ca'] or 0, 2):6}  {(e['nom'] or '')[:34]}")

    titre("EXCLUS QUI NE SONT PEUT-ETRE PAS DES FINANCIERES")
    # Un vrai profil bancaire ou foncier a un CA petit face a son bilan ET des
    # capitaux propres eleves. Une industrielle endettee, non.
    r = d1("SELECT COUNT(*) AS n FROM metriques WHERE exclusion LIKE 'profil financier%' "
           "AND roic_median >= 10 AND ca_cagr > 0")
    print(f"  exclues pour profil de bilan mais ROIC >= 10 % et CA en hausse : {r[0]['n']}")
    for e in d1("SELECT m.ticker, s.nom, m.dette_sur_ca, m.cp_sur_ca, m.roic_median "
                "FROM metriques m LEFT JOIN societe s ON s.ticker = m.ticker "
                "WHERE m.exclusion LIKE 'profil financier%' AND m.roic_median >= 10 "
                "AND m.ca_cagr > 0 ORDER BY m.roic_median DESC LIMIT 12"):
        print(f"    {e['ticker']:7} ROIC {round(e['roic_median'] or 0):3} %  "
              f"dette/CA {round(e['dette_sur_ca'] or 0, 2):5}  "
              f"CP/CA {round(e['cp_sur_ca'] or 0, 2):5}  {(e['nom'] or '')[:32]}")

    titre("DENOMINATEUR DU ROIC")
    for l in d1("SELECT COALESCE(denominateur_roic,'(aucun)') AS d, COUNT(*) AS n "
                "FROM metriques GROUP BY d ORDER BY n DESC"):
        print(f"  {l['n']:5}  {l['d']}")

    titre("ORIGINE DU REGIME MIXTE")
    # Le mixte vient d'un poste de bilan absent sur CERTAINS exercices
    # seulement : le calcul bascule alors de base en cours de serie.
    r = d1("SELECT "
           "SUM(CASE WHEN ppe IS NULL THEN 1 ELSE 0 END) AS sans_ppe, "
           "SUM(CASE WHEN bfr_exploitation IS NULL THEN 1 ELSE 0 END) AS sans_bfr, "
           "SUM(CASE WHEN ppe IS NOT NULL AND bfr_exploitation IS NOT NULL "
           "THEN 1 ELSE 0 END) AS complet, COUNT(*) AS tot FROM comptes")[0]
    t = max(1, r["tot"])
    print(f"  lignes de comptes            : {r['tot']}")
    print(f"  sans PPE                     : {r['sans_ppe']} ({round(100*r['sans_ppe']/t,1)} %)")
    print(f"  sans BFR                     : {r['sans_bfr']} ({round(100*r['sans_bfr']/t,1)} %)")
    print(f"  exploitation calculable      : {r['complet']} ({round(100*r['complet']/t,1)} %)")

    print("\n  societes dont la base d'exploitation est PARTIELLE :")
    for l in d1("SELECT ticker, COUNT(*) AS tot, "
                "SUM(CASE WHEN ppe IS NOT NULL AND bfr_exploitation IS NOT NULL "
                "THEN 1 ELSE 0 END) AS ok FROM comptes GROUP BY ticker "
                "HAVING ok > 0 AND ok < tot LIMIT 1")[:1]:
        pass
    r2 = d1("SELECT COUNT(*) AS n FROM (SELECT ticker, COUNT(*) AS tot, "
            "SUM(CASE WHEN ppe IS NOT NULL AND bfr_exploitation IS NOT NULL "
            "THEN 1 ELSE 0 END) AS ok FROM comptes GROUP BY ticker "
            "HAVING ok > 0 AND ok < tot)")[0]["n"]
    print(f"    {r2} societes ont la base sur une PARTIE seulement de leurs exercices")

    titre("COUVERTURE DES POSTES")
    r = d1("SELECT COUNT(*) AS tot, "
           "SUM(CASE WHEN ca IS NOT NULL THEN 1 ELSE 0 END) AS ca, "
           "SUM(CASE WHEN ebit IS NOT NULL THEN 1 ELSE 0 END) AS ebit, "
           "SUM(CASE WHEN fcf IS NOT NULL THEN 1 ELSE 0 END) AS fcf, "
           "SUM(CASE WHEN dette IS NOT NULL THEN 1 ELSE 0 END) AS dette, "
           "SUM(CASE WHEN marge_brute IS NOT NULL THEN 1 ELSE 0 END) AS mb, "
           "SUM(CASE WHEN goodwill IS NOT NULL THEN 1 ELSE 0 END) AS gw, "
           "SUM(CASE WHEN impot_effectif IS NOT NULL THEN 1 ELSE 0 END) AS imp "
           "FROM comptes")[0]
    t = max(1, r["tot"])
    for k, lib in (("ca", "CA"), ("ebit", "EBIT"), ("fcf", "FCF"), ("dette", "dette"),
                   ("mb", "marge brute"), ("gw", "goodwill"), ("imp", "taux d'impot")):
        print(f"  {lib:14} {round(100 * (r[k] or 0) / t, 1):5} %")

    titre("ETAGE PRIX")
    r = d1("SELECT COUNT(*) AS tot, "
           "SUM(CASE WHEN cours IS NOT NULL THEN 1 ELSE 0 END) AS cours, "
           "SUM(CASE WHEN plancher_epv IS NOT NULL THEN 1 ELSE 0 END) AS epv, "
           "SUM(CASE WHEN per_median IS NOT NULL THEN 1 ELSE 0 END) AS pm "
           "FROM metriques")[0]
    print(f"  cours renseigne              : {r['cours']}")
    print(f"  plancher EPV                 : {r['epv']}")
    print(f"  multiple median propre       : {r['pm']}")
    if not r["cours"]:
        print("  (l'increment 6 n'a pas encore tourne)")

    titre("DERNIERS RUNS")
    for l in d1("SELECT id, etape, statut, lignes_ecrites, debut, message "
                "FROM runs ORDER BY id DESC LIMIT 12"):
        print(f"  {l['id']:4} {l['debut'][:16]} {l['etape']:18} {l['statut']:6} "
              f"{l['lignes_ecrites']:6}  {(l['message'] or '')[:52]}")

    tot = d1("SELECT SUM(lignes_ecrites) AS n FROM runs WHERE debut >= date('now')")[0]["n"]
    print(f"\n  lignes ecrites aujourd'hui (hors index) : {tot or 0}")
    print("  rappel : Cloudflare compte AUSSI les ecritures d'index —")
    print("  metriques porte 2 index, societe 1 : le cout reel est 2 a 3x ce chiffre.")


if __name__ == "__main__":
    main()
