#!/usr/bin/env python3
"""
INCREMENT 9 — Pays de siege REEL des deposants SEC -> `societe`.

POURQUOI. L'increment 3 inscrit `pays_siege = "US"` pour tout deposant SEC.
C'est exact sur le lieu de DEPOT et faux sur le siege : Noah Holdings depose a
Washington et opere depuis Shanghai, Criteo depuis Paris, InMode depuis Tel-Aviv.

Consequence mesuree : apres passage a la fenetre longue, sept des dix premieres
candidates par pouvoir beneficiaire etaient des certificats etrangers — Noah a
284 % du cours, Vipshop a 140 %, Yalla a 160 %. Le mecanisme est identifiable :
ces societes portent une tresorerie nette massive, l'EPV deduit la dette nette,
donc une tresorerie nette S'AJOUTE au pouvoir capitalise. Le marche leur applique
en face une decote de gouvernance ou de juridiction que le calcul ne voit pas.

Le pays n'est PAS un motif d'exclusion : Criteo est francaise et parfaitement
analysable. C'est une information manquante qu'on retablit, pas une qualite
qu'on juge. Le screener affiche et permet de filtrer ; il ne decide pas.

SOURCE. data.sec.gov/submissions/CIK##########.json, champ
addresses.business.stateOrCountryDescription. C'est l'ADRESSE DU SIEGE qu'il
faut lire, non le lieu d'incorporation : beaucoup de societes israeliennes ou
chinoises sont incorporees au Delaware ou aux Caimans.

Un appel par societe, UNE SEULE FOIS — un siege ne bouge pas. L'ecriture
differentielle evite de reinterroger ce qui est deja resolu.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from commun import d1, journal, sonde, budget, RUN_TS  # noqa: E402

SEC_UA = os.environ.get("SEC_UA", "screener-perso contact@example.com")
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
LOT = int(os.environ.get("LOT_SIEGE", "1200"))

# CODES a deux lettres des Etats et territoires americains. La SEC sert un
# CODE (« FL »), pas un libelle (« Florida ») : chercher le libelle rendait
# tout inconnu et classait 2 124 societes sur 2 255 hors des Etats-Unis.
# Les deux formes sont desormais acceptees.
CODES_US = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY", "PR", "GU", "VI", "AS", "MP",
}

CODES_CANADA = {"A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "B0"}

# Etats et territoires des Etats-Unis. Tout libelle absent de cette liste est
# un pays etranger. L'inverse — une liste de pays — serait toujours incomplete.
ETATS_US = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "district of columbia", "florida", "georgia",
    "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
    "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas",
    "utah", "vermont", "virginia", "washington", "west virginia", "wisconsin",
    "wyoming", "puerto rico", "guam", "virgin islands", "american samoa",
    "northern mariana islands",
}

# Libelle SEC -> code ISO 2 lettres. Les pays absents gardent leur libelle
# tronque a deux caracteres serait faux : on prefere marquer INCONNU et le dire.
PAYS = {
    "china": "CN", "hong kong": "HK", "israel": "IL", "france": "FR",
    "united kingdom": "GB", "germany": "DE", "netherlands": "NL",
    "switzerland": "CH", "canada": "CA", "japan": "JP", "india": "IN",
    "ireland": "IE", "bermuda": "BM", "cayman islands": "KY", "brazil": "BR",
    "singapore": "SG", "australia": "AU", "south korea": "KR", "taiwan": "TW",
    "mexico": "MX", "spain": "ES", "italy": "IT", "sweden": "SE",
    "denmark": "DK", "norway": "NO", "finland": "FI", "belgium": "BE",
    "luxembourg": "LU", "austria": "AT", "greece": "GR", "portugal": "PT",
    "poland": "PL", "argentina": "AR", "chile": "CL", "colombia": "CO",
    "south africa": "ZA", "turkey": "TR", "russia": "RU", "indonesia": "ID",
    "thailand": "TH", "malaysia": "MY", "philippines": "PH", "vietnam": "VN",
    "new zealand": "NZ", "united arab emirates": "AE", "saudi arabia": "SA",
    "cyprus": "CY", "malta": "MT", "monaco": "MC", "jersey": "JE",
    "guernsey": "GG", "isle of man": "IM", "british virgin islands": "VG",
    "marshall islands": "MH", "panama": "PA", "liberia": "LR", "greece ": "GR",
}


def siege(cik, s):
    """Rend (code_pays, libelle) ou (None, motif)."""
    url = SUBMISSIONS.format(cik=str(cik).zfill(10))
    req = urllib.request.Request(url, headers={"User-Agent": SEC_UA,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        s.erreur(f"http_{e.code}", f"CIK {cik}")
        return None, f"HTTP {e.code}"
    except Exception as e:
        s.erreur("reseau_sec", f"CIK {cik} {type(e).__name__}")
        return None, type(e).__name__

    adr = ((j.get("addresses") or {}).get("business") or {})
    # La description est preferee quand elle existe — « Cayman Islands » vaut
    # mieux que « E9 » a l'affichage — mais le code est ce qui est reellement
    # servi dans la plupart des depots.
    lib = (adr.get("stateOrCountryDescription") or "").strip()
    code_brut = (adr.get("stateOrCountry") or "").strip().upper()
    if not lib and not code_brut:
        return None, "adresse absente"

    if code_brut in CODES_US:
        return "US", lib or code_brut
    # Provinces canadiennes dans la nomenclature EDGAR : A0 Alberta,
    # A1 Colombie-Britannique, A6 Ontario, A8 Quebec... Une vingtaine de
    # societes ressortaient sous un code illisible.
    if code_brut in CODES_CANADA:
        return "CA", lib or "Canada"
    if lib and lib.lower() in ETATS_US:
        return "US", lib

    if lib:
        c = PAYS.get(lib.lower())
        if c:
            return c, lib
    # Code etranger non traduit : on le garde TEL QUEL plutot que d'inventer
    # une correspondance. L'essentiel — hors des Etats-Unis — est etabli, et
    # le code reste lisible pour completer la table plus tard.
    if code_brut and len(code_brut) <= 3:
        s.compte("code_non_traduit")
        return code_brut, lib or code_brut
    s.erreur("pays_inconnu", lib or code_brut)
    return "??", lib or code_brut


def main():
    s = sonde("incr9_siege")
    print(f"incr9_siege v3 — Run {RUN_TS}")

    s.phase("cibles")
    # Seules les societes analysables et jamais resolues. `source_eligibilite`
    # sert de marqueur : une fois renseignee par ce script, la societe n'est
    # plus reinterrogee.
    cibles = d1(
        "SELECT s.ticker, s.cik FROM societe s "
        "JOIN metriques m ON m.ticker = s.ticker "
        "WHERE s.cik IS NOT NULL AND m.exclusion IS NULL "
        "AND (s.source_eligibilite IS NULL OR s.source_eligibilite NOT LIKE 'siege SEC%') "
        "ORDER BY m.roic_median DESC LIMIT ?", [LOT])[0]["results"]
    print(f"  {len(cibles)} societes a resoudre (lot de {LOT})")
    if not cibles:
        print("  toutes resolues — rien a faire")
        journal("OK", "incr9_siege", 0, "VERT", "aucune cible", s.resume())
        return

    s.phase("interrogation")
    maj, etrangeres = [], 0
    for i, l in enumerate(cibles, 1):
        code, lib = siege(l["cik"], s)
        time.sleep(0.15)          # SEC : 10 requetes par seconde maximum
        if code is None:
            continue
        if code != "US":
            etrangeres += 1
            s.compte("etranger_" + code)
        maj.append((l["ticker"], code, lib))
        if i % 200 == 0:
            print(f"  {i}/{len(cibles)} — {etrangeres} sieges hors US")

    print(f"  {len(maj)} resolus, {etrangeres} hors des Etats-Unis")

    s.phase("canari")
    # Temoin : Apple doit ressortir en Californie. Si le champ lu n'est plus
    # celui qu'on croit, ce controle le dit avant d'ecrire 1 200 lignes.
    temoin = next((c for t, c, _ in maj if t == "AAPL"), None)
    if temoin is not None and temoin != "US":
        msg = f"canari rouge : AAPL siege = {temoin}"
        print("PANNE : " + msg)
        s.afficher()
        journal("PANNE", "incr9_siege", 0, "ROUGE", msg, s.resume())
        sys.exit(1)

    s.phase("ecriture")
    budget(len(maj), "societe")
    n = 0
    for i in range(0, len(maj), 10):
        lot = maj[i:i + 10]
        vals, params = [], []
        for t, code, lib in lot:
            vals.append("(?, ?, ?, ?)")
            params += [t, code, f"siege SEC : {lib[:60]}", RUN_TS]
        d1("INSERT INTO societe (ticker, pays_siege, source_eligibilite, maj) "
           "VALUES " + ", ".join(vals) + " ON CONFLICT(ticker) DO UPDATE SET "
           "pays_siege = excluded.pays_siege, "
           "source_eligibilite = excluded.source_eligibilite, "
           "maj = excluded.maj", params, lignes=len(lot), table="societe")
        n += len(lot)
        if n % 400 == 0 or n == len(maj):
            print(f"  ecrit {n}/{len(maj)}")

    s.phase("rapport")
    print("\n--- RAPPORT ---")
    for l in d1("SELECT pays_siege, COUNT(*) AS n FROM societe "
                "WHERE source_eligibilite LIKE 'siege SEC%' "
                "GROUP BY pays_siege ORDER BY n DESC LIMIT 15")[0]["results"]:
        print(f"  {str(l['pays_siege']):4} {l['n']}")
    reste = d1("SELECT COUNT(*) AS n FROM societe s JOIN metriques m "
               "ON m.ticker = s.ticker WHERE s.cik IS NOT NULL "
               "AND m.exclusion IS NULL AND (s.source_eligibilite IS NULL "
               "OR s.source_eligibilite NOT LIKE 'siege SEC%')")[0]["results"][0]["n"]
    print(f"\n  reste a resoudre : {reste}"
          + ("  — relancer pour le lot suivant" if reste else "  — termine"))

    r = s.afficher()
    journal("OK", "incr9_siege", n, "VERT",
            f"{n} sieges resolus, {etrangeres} hors US, {reste} restants", r)
    print("OK")


if __name__ == "__main__":
    main()
