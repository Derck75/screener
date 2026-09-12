#!/usr/bin/env python3
"""
DIAGNOSTIC v4 — LECTURE SEULE. N'ECRIT RIEN, PAS MEME DANS `runs`.

Journaliser ce run couterait une ecriture, et ce script existe precisement
pour tourner quand le quota d'ecriture est epuise.

v4 suit le NOUVEAU schema : `comptes2` et ses 27 postes bruts, la typologie de
societe, la base de ROIC nommee, la contre-preuve. Il gere la TRANSITION :
tant que l'ancienne table `comptes` existe, il affiche les deux et signale
laquelle alimente reellement les metriques. Un diagnostic qui regarde la
mauvaise table est pire que pas de diagnostic.
"""

import json
import os
import sys

import requests

CF_ACCOUNT = os.environ["CF_ACCOUNT"]
CF_DB = os.environ["CF_DB"]
CF_TOKEN = os.environ["CF_TOKEN"]
D1_URL = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/d1/database/{CF_DB}/query"

POSTES = ["revenue", "ebit", "netIncome", "grossProfit", "cfo", "capex", "da",
          "sbc", "tax", "pretax", "amortAcq", "assets", "ppe", "intangTot",
          "intangExGW", "goodwill", "receivables", "inventory", "payables",
          "currentLiab", "debt", "cash", "equity", "shares", "deferredRev"]


def d1(sql, params=None, muet=False):
    r = requests.post(
        D1_URL,
        headers={"Authorization": f"Bearer {CF_TOKEN}", "Content-Type": "application/json"},
        json={"sql": sql, "params": params or []}, timeout=120)
    j = r.json()
    if not j.get("success"):
        if muet:
            return None
        print(f"  (requete refusee : {json.dumps(j.get('errors'))[:160]})")
        return None
    return j["result"][0]["results"]


def titre(t):
    print(f"\n{'=' * 62}\n{t}\n{'=' * 62}")


def existe(table):
    return d1(f"SELECT 1 AS n FROM {table} LIMIT 1", muet=True) is not None


def compte(table):
    r = d1(f"SELECT COUNT(*) AS n FROM {table}", muet=True)
    return r[0]["n"] if r else None


def main():
    print("diag v4 — LECTURE SEULE, aucune ecriture")

    titre("VOLUMES")
    tables = {}
    for t in ("societe", "comptes", "comptes2", "metriques", "presets", "runs"):
        n = compte(t)
        tables[t] = n
        print(f"  {t:12} {'absente' if n is None else n}")

    # QUELLE TABLE ALIMENTE REELLEMENT LES METRIQUES ?
    # Pendant la bascule, les deux coexistent : confondre les deux ferait
    # chercher un bug dans la mauvaise.
    if tables.get("comptes") and tables.get("comptes2"):
        print("\n  ⚠ LES DEUX TABLES DE COMPTES EXISTENT.")
        print("    `comptes2` (postes bruts) alimente le noyau ;")
        print("    `comptes` est l'archive d'avant bascule, plus lue par personne.")
        print("    Ne la supprimer qu'apres validation du controle croise.")
    elif not tables.get("comptes2"):
        print("\n  ⚠ `comptes2` VIDE OU ABSENTE : l'increment 4 v2 n'a pas encore tourne.")

    if tables.get("comptes2"):
        titre("COUVERTURE DES POSTES BRUTS")
        sel = ", ".join(f"SUM(CASE WHEN {c} IS NOT NULL THEN 1 ELSE 0 END) AS {c}"
                        for c in POSTES)
        r = d1(f"SELECT COUNT(*) AS tot, {sel} FROM comptes2")
        if r:
            row, t = r[0], max(1, r[0]["tot"])
            faibles = []
            for c in POSTES:
                p = round(100 * (row[c] or 0) / t, 1)
                marque = ""
                if p < 40:
                    marque = "  ← faible"
                    faibles.append(c)
                print(f"  {c:14} {p:5} %{marque}")
            if faibles:
                print(f"\n  Postes sous 40 % : {', '.join(faibles)}")
                print("  Un poste peu servi n'est pas une panne : il limite le nombre")
                print("  de denominateurs disponibles, et le noyau le dit lui-meme.")

        titre("EXERCICES SERVIS PAR SOCIETE")
        r = d1("SELECT n, COUNT(*) AS societes FROM (SELECT ticker, COUNT(*) AS n "
               "FROM comptes2 GROUP BY ticker) GROUP BY n ORDER BY n")
        for l in (r or []):
            print(f"  {l['n']} exercice(s) : {l['societes']} societes")
        print("  Sous 4 exercices, le noyau refuse la brique EVA — c'est une regle,")
        print("  pas un defaut de collecte.")

    if tables.get("metriques"):
        titre("TYPOLOGIE DES SOCIETES")
        r = d1("SELECT COALESCE(profil_type,'(non calcule)') AS t, COUNT(*) AS n "
               "FROM metriques GROUP BY t ORDER BY n DESC")
        if r and any(l["t"] != "(non calcule)" for l in r):
            for l in r:
                print(f"  {l['n']:5}  {l['t']}")
        else:
            print("  (colonne absente ou increment 5 v2 pas encore lance)")

        titre("BASE DE ROIC RETENUE")
        r = d1("SELECT COALESCE(base_roic, denominateur_roic, '(aucune)') AS b, "
               "COUNT(*) AS n FROM metriques GROUP BY b ORDER BY n DESC LIMIT 8")
        for l in (r or []):
            print(f"  {l['n']:5}  {str(l['b'])[:70]}")

        r = d1("SELECT COUNT(*) AS n FROM metriques WHERE contre_preuve_ecart > 0.30")
        if r:
            print(f"\n  {r[0]['n']} societes ou la contre-preuve diverge de plus de 30 %")
            print("  — le denominateur retenu ne decide pas seul, l'ecart se lit")
            print("  comme une information sur le modele economique.")

        titre("EXCLUSIONS")
        r = d1("SELECT exclusion, COUNT(*) AS n FROM metriques "
               "WHERE exclusion IS NOT NULL GROUP BY exclusion ORDER BY n DESC LIMIT 10")
        for l in (r or []):
            print(f"  {l['n']:5}  {str(l['exclusion'])[:68]}")
            ex = d1("SELECT m.ticker, s.nom FROM metriques m "
                    "LEFT JOIN societe s ON s.ticker = m.ticker "
                    "WHERE m.exclusion = ? LIMIT 4", [l["exclusion"]])
            for e in (ex or []):
                print(f"         {e['ticker']:7} {(e['nom'] or '')[:44]}")

        titre("CANARI — les compounders doivent survivre au filtre")
        r = d1("SELECT ticker, roic_median, profil_type, exclusion FROM metriques "
               "WHERE ticker IN ('AAPL','MSFT','V','MA','NVDA','NFLX','BKNG','HON')")
        for l in (r or []):
            etat = f"EXCLUE — {str(l['exclusion'])[:40]}" if l["exclusion"] else "retenue"
            print(f"  {l['ticker']:6} ROIC {round(l['roic_median'] or 0):5} %  "
                  f"{str(l['profil_type'] or '?'):14} {etat}")

        titre("ENTONNOIR")
        for lib, w in (("univers evalue", "1=1"),
                       ("hors exclusion", "exclusion IS NULL"),
                       ("ROIC >= 15 %", "exclusion IS NULL AND roic_median >= 15"),
                       ("  + spread > 0", "exclusion IS NULL AND roic_median >= 15 AND spread_median > 0"),
                       ("  + CA et FCF en hausse", "exclusion IS NULL AND roic_median >= 15 "
                        "AND spread_median > 0 AND ca_cagr > 0 AND fcf_cagr > 0"),
                       ("  + cours connu", "exclusion IS NULL AND roic_median >= 15 "
                        "AND spread_median > 0 AND ca_cagr > 0 AND fcf_cagr > 0 AND cours IS NOT NULL"),
                       ("  + sous son multiple median", "exclusion IS NULL AND roic_median >= 15 "
                        "AND spread_median > 0 AND ca_cagr > 0 AND fcf_cagr > 0 AND ecart_multiple < 0")):
            r = d1(f"SELECT COUNT(*) AS n FROM metriques WHERE {w}")
            if r:
                print(f"  {lib:28} {r[0]['n']}")

        titre("ETAGE PRIX")
        r = d1("SELECT SUM(CASE WHEN cours IS NOT NULL THEN 1 ELSE 0 END) AS cours, "
               "SUM(CASE WHEN plancher_epv IS NOT NULL THEN 1 ELSE 0 END) AS epv, "
               "SUM(CASE WHEN per_median IS NOT NULL THEN 1 ELSE 0 END) AS pm "
               "FROM metriques")
        if r:
            print(f"  cours renseigne        : {r[0]['cours'] or 0}")
            print(f"  plancher EPV           : {r[0]['epv'] or 0}")
            print(f"  multiple median propre : {r[0]['pm'] or 0}")

    titre("SONDES — dernier run de chaque etape")
    r = d1("SELECT etape, statut, detail, MAX(id) AS id FROM runs "
           "WHERE detail IS NOT NULL GROUP BY etape ORDER BY etape")
    for l in (r or []):
        try:
            d = json.loads(l["detail"])
        except Exception:
            continue
        print(f"\n  {l['etape']} ({l['statut']})")
        if isinstance(d, dict):
            if d.get("duree_s"):
                print(f"    duree {d['duree_s']} s")
            for k in ("phases", "compteurs"):
                if d.get(k):
                    print(f"    {k} : " + ", ".join(f"{a}={b}" for a, b in d[k].items()))
            for cat, e in (d.get("erreurs") or {}).items():
                print(f"    ERREUR {cat} x{e.get('n')} — {e.get('exemple') or ''}")
            if not any(k in d for k in ("phases", "compteurs", "erreurs")):
                print(f"    {json.dumps(d)[:170]}")

    titre("DERNIERS RUNS")
    r = d1("SELECT id, etape, statut, lignes_ecrites, debut, message "
           "FROM runs ORDER BY id DESC LIMIT 12")
    for l in (r or []):
        n = l["lignes_ecrites"] if l["lignes_ecrites"] is not None else 0
        print(f"  {l['id']:4} {(l['debut'] or '')[:16]} {(l['etape'] or ''):18} "
              f"{(l['statut'] or ''):6} {n:6}  {(l['message'] or '')[:50]}")

    titre("BUDGET D'ECRITURE DU JOUR")
    r = d1("SELECT COALESCE(SUM(lignes_ecrites),0) AS n FROM runs WHERE debut >= date('now')")
    tot = (r[0]["n"] if r else 0) or 0
    print(f"  lignes declarees aujourd'hui : {tot}   (plafond D1 Free : 100000)")
    print("  Cloudflare compte AUSSI les ecritures d'index : metriques en porte 2,")
    print("  societe 1 — le cout reel depasse ce chiffre.")
    r = d1("SELECT etape, COUNT(*) AS runs, SUM(lignes_ecrites) AS n FROM runs "
           "WHERE debut >= date('now') GROUP BY etape ORDER BY n DESC")
    for l in (r or []):
        print(f"    {l['etape']:20} {l['runs']} run(s)  {l['n'] or 0} lignes")
    if tot > 60000:
        print("\n  ⚠ Au-dela de 60 000, ne plus lancer d'etape lourde aujourd'hui.")


if __name__ == "__main__":
    main()
