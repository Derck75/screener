#!/usr/bin/env python3
"""
GARDE — evite qu'une etape tourne deux fois quand deux declencheurs coexistent
(audit M8).

Le cron de GitHub part avec 2 h 30 a 6 h 40 de retard ; celui du worker
Cloudflare tombe a la minute et declenche les workflows par `workflow_dispatch`.
Les deux restent en place : le `schedule` de GitHub devient un FILET, qui ne
fait rien si l'etape a deja reussi dans la fenetre donnee. Un declenchement
manuel ou par le worker passe toujours.

Usage : python src/garde.py --etape incr7_valorisation --heures 20
Ecrit « faire=true|false » dans $GITHUB_OUTPUT. Aucune ecriture en base.
"""

import argparse
import calendar
import json
import os
import sys
import time
import urllib.request


def derniere_reussite(etape):
    a, b, t = (os.environ.get(k) for k in ("CF_ACCOUNT", "CF_DB", "CF_TOKEN"))
    if not all((a, b, t)):
        return None
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{a}/d1/database/{b}/query",
        data=json.dumps({"sql": "SELECT MAX(debut) AS d FROM runs WHERE statut = 'OK' "
                                "AND etape LIKE ?", "params": [etape]}).encode(),
        headers={"Authorization": f"Bearer {t}", "Content-Type": "application/json"})
    try:
        j = json.loads(urllib.request.urlopen(req, timeout=60).read())
        return j["result"][0]["results"][0]["d"]
    except Exception:
        return None


# BUDGET D1 DU JOUR (27/10/2026). Le plan gratuit accorde 100 000 lignes
# ecrites par jour UTC ; au-dela, D1 refuse toute ecriture jusqu'a minuit.
# Le 26/10, cinq relances manuelles de la chaine metriques -> prix ->
# valorisation l'ont porte a 128 648 (129 %). Une chaine complete coute
# ~30 000 lignes reelles (index et capitalisations compris) pour ~11 000
# journalisees : facteur 2,7, mesure ce jour-la. La garde refuse de lancer une
# chaine qui ferait passer l'estimation au-dessus de 90 000.
FACTEUR_REEL = 2.7
PLAFOND_D1 = 90000


def ecrites_aujourdhui():
    a, b, t = (os.environ.get(k) for k in ("CF_ACCOUNT", "CF_DB", "CF_TOKEN"))
    if not all((a, b, t)):
        return None
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{a}/d1/database/{b}/query",
        data=json.dumps({"sql": "SELECT COALESCE(SUM(lignes_ecrites), 0) AS n FROM runs "
                                "WHERE debut >= date('now') AND statut = 'OK'", "params": []}).encode(),
        headers={"Authorization": f"Bearer {t}", "Content-Type": "application/json"})
    try:
        j = json.loads(urllib.request.urlopen(req, timeout=60).read())
        return int(j["result"][0]["results"][0]["n"] or 0)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--etape", required=True, help="motif LIKE sur runs.etape")
    ap.add_argument("--heures", type=float, required=True)
    ap.add_argument("--forcer", action="store_true", help="ignorer la fenetre (relance deliberee)")
    ap.add_argument("--budget-chaine", type=int, default=0,
                    help="lignes D1 reelles que la chaine va ecrire ; 0 = pas de controle")
    a = ap.parse_args()
    evenement = os.environ.get("GITHUB_EVENT_NAME", "")
    faire, motif = True, "declenchement direct" + (" (force)" if a.forcer else "")
    if a.budget_chaine:
        deja = ecrites_aujourdhui()
        if deja is not None:
            estime = int(deja * FACTEUR_REEL)
            if estime + a.budget_chaine > PLAFOND_D1:
                print(f"garde {a.etape} : SAUTER — budget D1 du jour : ~{estime} lignes deja "
                      f"ecrites + {a.budget_chaine} prevues > {PLAFOND_D1} (plafond gratuit 100 000)")
                sortie = os.environ.get("GITHUB_OUTPUT")
                if sortie:
                    with open(sortie, "a") as f:
                        f.write("faire=false\n")
                return 0
            motif += f" · budget D1 ~{estime} + {a.budget_chaine} <= {PLAFOND_D1}"
    if evenement == "schedule" and not a.forcer:
        d = derniere_reussite(a.etape)
        if d:
            age = (time.time() - calendar.timegm(time.strptime(d[:19], "%Y-%m-%dT%H:%M:%S"))) / 3600
            if age < a.heures:
                faire, motif = False, f"deja reussie il y a {age:.1f} h (fenetre {a.heures:g} h)"
            else:
                motif = f"derniere reussite il y a {age:.1f} h"
        else:
            motif = "aucune reussite connue"
    print(f"garde {a.etape} : {'EXECUTER' if faire else 'SAUTER'} — {motif}")
    sortie = os.environ.get("GITHUB_OUTPUT")
    if sortie:
        with open(sortie, "a") as f:
            f.write(f"faire={'true' if faire else 'false'}\n")


if __name__ == "__main__":
    sys.exit(main())
