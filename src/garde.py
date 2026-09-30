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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--etape", required=True, help="motif LIKE sur runs.etape")
    ap.add_argument("--heures", type=float, required=True)
    a = ap.parse_args()
    evenement = os.environ.get("GITHUB_EVENT_NAME", "")
    faire, motif = True, "declenchement direct"
    if evenement == "schedule":
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
