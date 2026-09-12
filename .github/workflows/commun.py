#!/usr/bin/env python3
"""
COMMUN — client D1, sonde d'execution, budget d'ecriture, garde de schema.

Importe par tous les scripts d'ingestion. Trois garde-fous, chacun ne pose
qu'UNE ecriture par run (la ligne de journal finale) : instrumenter ne doit
jamais couter plus cher que ce qu'on instrumente.

  1. BUDGET — avant toute ecriture, le script lit ce qui a deja ete ecrit
     aujourd'hui et refuse de demarrer s'il ne tient pas dans ce qui reste.
     Motif : un quota epuise en milieu de chaine laisse la base a moitie
     ecrite, etat plus couteux a diagnostiquer qu'un refus net.
     Cloudflare compte AUSSI les ecritures d'index : le cout reel d'une ligne
     est 1 + (nombre d'index de la table). Le budget en tient compte.

  2. SCHEMA — les colonnes attendues sont verifiees par PRAGMA avant le
     premier calcul. Un ALTER TABLE oublie echouerait sinon apres plusieurs
     minutes de travail, avec un message qui ne nomme pas la cause.

  3. SONDE — chronometre par phase, compte les appels reseau et classe les
     erreurs par CATEGORIE plutot que de les empiler. Le resume tient dans le
     champ `detail` d'une seule ligne de `runs` : la trace survit au run sans
     table supplementaire et sans croissance sans fin.
"""

import json
import os
import sys
import time

import requests

CF_ACCOUNT = os.environ["CF_ACCOUNT"]
CF_DB = os.environ["CF_DB"]
CF_TOKEN = os.environ["CF_TOKEN"]
D1_URL = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/d1/database/{CF_DB}/query"

# Plafond D1 Free : 100 000 lignes ecrites par jour. On s'arrete avant, pour
# garder de quoi relancer une etape en cas d'echec.
PLAFOND_JOUR = int(os.environ.get("PLAFOND_ECRITURES", "70000"))

# Index par table : chaque index ajoute une ecriture par ligne touchee.
INDEX = {"societe": 1, "metriques": 2, "comptes": 0, "runs": 0, "presets": 0}

RUN_TS = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Sonde:
    """Trace d'execution. Rien n'est ecrit avant `fin`."""

    def __init__(self, etape):
        self.etape = etape
        self.t0 = time.time()
        self.phase_nom = None
        self.phase_t0 = None
        self.phases = {}
        self.compteurs = {}
        self.erreurs = {}
        self.ecrites = 0

    def phase(self, nom):
        self.cloturer()
        self.phase_nom, self.phase_t0 = nom, time.time()
        print(f"[{nom}]")

    def cloturer(self):
        if self.phase_nom:
            self.phases[self.phase_nom] = round(time.time() - self.phase_t0, 1)
            self.phase_nom = None

    def compte(self, cle, n=1):
        self.compteurs[cle] = self.compteurs.get(cle, 0) + n

    def erreur(self, categorie, exemple=None):
        """Classe par CATEGORIE. Un exemple par categorie suffit a reproduire ;
        empiler des milliers de messages identiques ne dit rien de plus."""
        e = self.erreurs.setdefault(categorie, {"n": 0, "exemple": None})
        e["n"] += 1
        if e["exemple"] is None and exemple:
            e["exemple"] = str(exemple)[:120]

    def resume(self):
        self.cloturer()
        return {"duree_s": round(time.time() - self.t0, 1),
                "phases": self.phases,
                "compteurs": self.compteurs,
                "erreurs": self.erreurs,
                "ecrites": self.ecrites}

    def afficher(self):
        r = self.resume()
        print(f"\n--- SONDE {self.etape} — {r['duree_s']} s ---")
        if r["phases"]:
            print("  phases : " + ", ".join(f"{k} {v}s" for k, v in r["phases"].items()))
        if r["compteurs"]:
            print("  " + ", ".join(f"{k} {v}" for k, v in sorted(r["compteurs"].items())))
        for cat, e in sorted(r["erreurs"].items(), key=lambda x: -x[1]["n"]):
            print(f"  ERREUR {cat} x{e['n']}" + (f" — ex. {e['exemple']}" if e["exemple"] else ""))
        if not r["erreurs"]:
            print("  aucune erreur categorisee")
        return r


_sonde = None


def sonde(etape):
    global _sonde
    _sonde = Sonde(etape)
    return _sonde


def d1(sql, params=None, lignes=0, table=None):
    """lignes/table servent au comptage du budget, pas a la requete."""
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
        _fatal(f"reponse D1 illisible ({r.status_code}) {r.text[:200]}")
    if not j.get("success"):
        err = json.dumps(j.get("errors"))[:300]
        # Le quota se nomme lui-meme : on le dit en clair plutot que de laisser
        # un code 7500 brut, indistinguable d'une erreur de requete.
        if "daily row write limit" in err or "7500" in err:
            _fatal("QUOTA D1 EPUISE — les ecritures reprennent a minuit UTC. "
                   "Base laissee en etat partiel : relancer l'etape demain.")
        _fatal(f"erreur D1 : {err}")
    if lignes and _sonde:
        _sonde.ecrites += lignes * (1 + INDEX.get(table or "", 0))
    return j["result"]


def _fatal(msg):
    print(f"ECHEC : {msg}")
    if _sonde:
        _sonde.erreur("d1", msg)
        journal("PANNE", _sonde.etape, 0, "ROUGE", msg, _sonde.resume())
    sys.exit(1)


def journal(statut, etape, lignes, canari, message, detail=None):
    requests.post(
        D1_URL,
        headers={"Authorization": f"Bearer {CF_TOKEN}",
                 "Content-Type": "application/json"},
        json={"sql": "INSERT INTO runs (debut, fin, etape, statut, lignes_ecrites,"
                     " canari, message, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
              "params": [RUN_TS, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                         etape, statut, lignes, canari, message[:400],
                         json.dumps(detail) if detail else None]},
        timeout=60,
    )


def budget(besoin, table):
    """Refuse de demarrer si l'ecriture prevue ne tient pas dans la journee."""
    deja = d1("SELECT COALESCE(SUM(lignes_ecrites), 0) AS n FROM runs "
              "WHERE debut >= date('now')")[0]["results"][0]["n"] or 0
    cout = besoin * (1 + INDEX.get(table, 0))
    print(f"budget : {deja} deja ecrites aujourd'hui, "
          f"{cout} prevues sur {table} (index compris), plafond {PLAFOND_JOUR}")
    if deja + cout > PLAFOND_JOUR:
        _fatal(f"BUDGET INSUFFISANT — {deja} + {cout} depasse {PLAFOND_JOUR}. "
               f"Etape non lancee, base laissee intacte. Reprendre apres minuit UTC.")
    return True


def ecrire_differentiel(table, cle, rangs, colonnes, s=None):
    """N'ecrit que ce qui a CHANGE.

    Les comptes annuels ne bougent que quatre fois par an : reecrire 23 000
    lignes chaque jour consomme le quota sans rien apporter. En regime de
    croisiere, cette fonction ecrit quelques dizaines de lignes.

    `rangs` : {cle: {colonne: valeur}}. Comparaison a 6 decimales — au-dela,
    un bruit d'arrondi ferait passer une ligne pour modifiee.
    """
    existant, page = {}, 0
    cols = ", ".join(colonnes)
    while True:
        r = d1(f"SELECT {cle}, {cols} FROM {table} ORDER BY {cle} "
               f"LIMIT 5000 OFFSET ?", [page * 5000])[0]["results"]
        if not r:
            break
        for l in r:
            existant[l[cle]] = l
        page += 1

    def pareil(a, b):
        for c in colonnes:
            x, y = a.get(c), b.get(c)
            if x is None and y is None:
                continue
            if x is None or y is None:
                return False
            try:
                if abs(float(x) - float(y)) > 1e-6:
                    return False
            except (TypeError, ValueError):
                if str(x) != str(y):
                    return False
        return True

    a_ecrire = {k: v for k, v in rangs.items()
                if k not in existant or not pareil(existant[k], v)}
    inchangees = len(rangs) - len(a_ecrire)
    print(f"  differentiel : {len(a_ecrire)} a ecrire, {inchangees} inchangees, "
          f"{len(existant)} en base")
    if s:
        s.compte("inchangees", inchangees)
        s.compte("modifiees", len(a_ecrire))
    return a_ecrire


def verifier_schema(table, colonnes):
    """Echoue tout de suite, pas apres dix minutes de calcul."""
    presentes = {l["name"] for l in
                 d1(f"PRAGMA table_info({table})")[0]["results"]}
    manquantes = [c for c in colonnes if c not in presentes]
    if manquantes:
        _fatal(f"colonnes absentes de `{table}` : {', '.join(manquantes)} — "
               f"executer les ALTER TABLE correspondants avant de relancer.")
    return True
