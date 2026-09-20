#!/usr/bin/env python3
"""
INCREMENT 10 — Notification Discord.

CE QUE CE SCRIPT PROTEGE. Le screener compte 291 candidates. Les envoyer
serait un deluge ; ne rien envoyer reviendrait a ne jamais consulter l'outil.
Quatre regles tiennent l'equilibre :

  1. AMORCAGE SILENCIEUX. Le premier passage n'envoie RIEN : il enregistre
     l'etat courant comme deja vu. Sans cela le premier message contiendrait
     291 lignes et le canal serait coupe le jour meme.
  2. LE DIFF, JAMAIS LA LISTE. Un screener quality-growth converge : les memes
     noms reviennent chaque semaine. Seules les ENTREES comptent.
  3. PLAFOND DUR de 5 noms par envoi. Au-dela, rien n'est actionnable : le
     cadre plafonne a 14 lignes et exige une decote.
  4. L'EXCEPTION EST RARE PAR CONSTRUCTION. Une alerte immediate qui se
     declenche souvent cesse d'etre une alerte.

CE QU'IL N'ENVOIE JAMAIS : de variation de cours, de societe deja signalee,
de drapeau d'hygiene seul. Et le statut reste WATCHLIST — le screener designe
ou regarder, il ne conclut pas.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from commun import d1, journal, sonde, RUN_TS  # noqa: E402

WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
PLAFOND_NOMS = int(os.environ.get("PLAFOND_NOMS", "5"))

# Candidate au sens de la notification hebdomadaire. Volontairement plus
# exigeant que le preset `strict` du screener : on ne signale pas ce qu'on
# consulte, on signale ce qui merite qu'on s'arrete.
CANDIDATE = """
    m.exclusion IS NULL
    AND m.profil_type IN ('industriel', 'capitalistique', 'asset_light')
    AND m.roic_median >= 15 AND m.spread_median > 0
    AND m.ca_cagr > 0 AND m.fcf_cagr > 0
    AND m.epv_sur_cours >= 0.60
    AND (s.suivi_serveur IS NULL OR s.suivi_serveur = 0)
"""

# L'exception : tout doit etre coche. Sur l'etat du 18/09, une seule societe
# y repond. Une ou deux alertes par an est le bon ordre de grandeur.
EXCEPTION = """
    AND m.epv_sur_cours >= 0.90
    AND s.vaneck IS NOT NULL AND s.vaneck_sorti_le IS NULL
    AND m.drapeaux IS NULL
    AND m.roic_median >= 20 AND m.n_ex_total >= 8
"""


def envoyer(titre, corps, couleur):
    if not WEBHOOK:
        print("  DISCORD_WEBHOOK absent — message affiche, pas envoye")
        print(f"\n=== {titre} ===\n{corps}\n")
        return False
    charge = {"embeds": [{"title": titre, "description": corps[:3900],
                          "color": couleur}]}
    # USER-AGENT OBLIGATOIRE. L'API Discord est derriere Cloudflare, qui
    # refuse la signature par defaut de Python : HTTP 403, « error code 1010 ».
    # Le message ne vient pas de Discord mais de sa protection.
    req = urllib.request.Request(
        WEBHOOK, data=json.dumps(charge).encode(),
        headers={"Content-Type": "application/json",
                 "User-Agent": "screener-rayane (github-actions, python)"})
    try:
        urllib.request.urlopen(req, timeout=20)
        return True
    except urllib.error.HTTPError as e:
        print(f"  ECHEC Discord : HTTP {e.code} — {e.read()[:150]}")
    except Exception as e:
        print(f"  ECHEC Discord : {type(e).__name__}")
    return False


ACTIVITE = {
    "services-prepackaged software": "Logiciel",
    "services-computer programming, data processing, etc.": "Services numériques",
    "pharmaceutical preparations": "Pharmacie",
    "biological products, (no diagnostic substances)": "Biotechnologie",
    "state commercial banks": "Banque",
    "national commercial banks": "Banque",
    "fire, marine & casualty insurance": "Assurance dommages",
    "life insurance": "Assurance vie",
    "steel works, blast furnaces & rolling mills (coke ovens)": "Sidérurgie",
    "crude petroleum & natural gas": "Pétrole et gaz",
    "semiconductors & related devices": "Semiconducteurs",
    "retail-eating places": "Restauration",
    "retail-variety stores": "Distribution",
    "electric services": "Électricité",
    "real estate investment trusts": "Foncière",
    "motor vehicle parts & accessories": "Équipement automobile",
    "aircraft & parts": "Aéronautique",
    "surgical & medical instruments & apparatus": "Matériel médical",
    "air transportation, scheduled": "Transport aérien",
    "wholesale-drugs, proprietaries & druggists' sundries": "Distribution pharmaceutique",
    "services-management consulting services": "Conseil",
    "services-advertising agencies": "Publicité",
    "blank checks": "Coquille",
    "retail-apparel & accessory stores": "Habillement",
    "bituminous coal & lignite surface mining": "Charbon",
    "services-computer integrated systems design": "Services numériques",
    "industrial organic chemicals": "Chimie",
    "gold mining": "Mines d'or",
    "hotels & motels": "Hôtellerie",
    "cable & other pay television services": "Média",
    "electronic components & accessories": "Composants électroniques",
    "special industry machinery, nec": "Machines industrielles",
    "wholesale-electronic parts & equipment, nec": "Distribution électronique",
    "services-business services, nec": "Services aux entreprises",
}


def activite(brut):
    if not brut:
        return None
    b = brut.strip().lower()
    if b in ACTIVITE:
        return ACTIVITE[b]
    # Forme non traduite : on la raccourcit plutot que de l'ecarter. Mieux
    # vaut « Steel Works » que rien.
    court = brut.split(",")[0].split("(")[0].strip()
    if len(court) > 38:
        # Couper sur un espace : « Surface Mini » en plein mot est illisible.
        court = court[:38].rsplit(" ", 1)[0] + "…"
    return court or None


def ligne(c):
    pays = c.get("pays_siege") or "?"
    pea = "PEA" if c.get("eligible_pea") == 1 else "CTO"
    ve = " · VanEck" if c.get("vaneck") else ""
    dr = " 🚩" + str(c["drapeaux"]).split(",")[0] if c.get("drapeaux") else ""
    act = activite(c.get("secteur"))
    # Le NOM d'abord, en gras : c'est ce qu'on lit. Le ticker suit, discret —
    # il sert a interroger l'outil, pas a reconnaitre la societe.
    t = (f"**{str(c.get('nom') or '')[:40]}**  `{c['ticker']}`\n"
         + (f"{act}\n" if act else "")
         # Formulation directe plutot que le sigle : « EPV/cours 60 % » ne dit
         # rien tant qu'on n'a pas la definition en tete.
         + f"Profits actuels : **{round(100 * c['epv_sur_cours'])} % du cours**\n"
         + f"ROIC {round(c['roic_median'])} % sur {c['n_ex_total']} ex. · "
         + f"moat {c['score_moat']}/{c['score_moat_max']} · {pea} · {pays}{ve}{dr}")
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--amorcage", action="store_true",
                    help="enregistre l'etat courant SANS rien envoyer")
    ap.add_argument("--exception-seule", action="store_true",
                    help="ne verifie que l'alerte immediate")
    a = ap.parse_args()

    s = sonde("incr10_discord")
    print(f"incr10_discord v3 — Run {RUN_TS}")

    s.phase("lecture")
    base = ("FROM metriques m JOIN societe s ON s.ticker = m.ticker "
            "WHERE " + CANDIDATE)
    champs = ("m.ticker, s.nom, s.pays_siege, s.eligible_pea, s.vaneck, s.secteur, "
              "m.epv_sur_cours, m.roic_median, m.n_ex_total, m.score_moat, "
              "m.score_moat_max, m.drapeaux")
    candidates = d1(f"SELECT {champs} {base} ORDER BY m.epv_sur_cours DESC"
                    )[0]["results"]
    deja = {l["ticker"] for l in
            d1("SELECT ticker FROM notifications")[0]["results"]}
    print(f"  {len(candidates)} candidates, {len(deja)} deja signalees")
    s.compte("candidates", len(candidates))

    nouvelles = [c for c in candidates if c["ticker"] not in deja]
    print(f"  {len(nouvelles)} nouvelles")
    s.compte("nouvelles", len(nouvelles))

    # ---- amorcage ----------------------------------------------------------
    if a.amorcage:
        s.phase("amorcage")
        print(f"  AMORCAGE : {len(nouvelles)} candidates enregistrees, "
              f"aucun message envoye")
        for i in range(0, len(nouvelles), 20):
            lot = nouvelles[i:i + 20]
            vals, params = [], []
            for c in lot:
                vals.append("(?, ?, ?, ?, 'amorcage', 'etat initial')")
                params += [c["ticker"], RUN_TS, RUN_TS, c["epv_sur_cours"]]
            d1("INSERT INTO notifications (ticker, premiere, derniere, epv, "
               "canal, motif) VALUES " + ", ".join(vals) +
               " ON CONFLICT(ticker) DO NOTHING", params,
               lignes=len(lot), table="notifications")
        journal("OK", "incr10_discord", len(nouvelles), "VERT",
                f"amorcage : {len(nouvelles)} enregistrees", s.resume())
        s.afficher()
        return

    # ---- exception ---------------------------------------------------------
    s.phase("exception")
    exc = d1(f"SELECT {champs} {base} {EXCEPTION} ORDER BY m.epv_sur_cours DESC"
             )[0]["results"]
    exc_neuves = [c for c in exc if c["ticker"] not in deja]
    envoyees = []
    if exc_neuves:
        corps = "\n\n".join(ligne(c) for c in exc_neuves[:3])
        corps += ("\n\n*Tout est coché : pouvoir bénéficiaire au-dessus de 90 % "
                  "du cours, moat certifié, aucun drapeau, au moins huit "
                  "exercices. Cette alerte est calibrée pour se déclencher une "
                  "à deux fois par an — sa rareté est ce qui lui donne sa "
                  "valeur.*\n\n🔴 **Watchlist, pas une idée.** L'analyse "
                  "complète reste à faire.")
        if envoyer("🟢 Opportunité exceptionnelle", corps, 0x2ecc71):
            envoyees += exc_neuves[:3]
            s.compte("exception_envoyee", len(exc_neuves[:3]))
    else:
        print("  aucune exception — c'est le cas normal")

    if a.exception_seule:
        for c in envoyees:
            d1("INSERT INTO notifications (ticker, premiere, derniere, epv, "
               "canal, motif) VALUES (?, ?, ?, ?, 'exception', 'tout coche') "
               "ON CONFLICT(ticker) DO UPDATE SET derniere = excluded.derniere",
               [c["ticker"], RUN_TS, RUN_TS, c["epv_sur_cours"]],
               lignes=1, table="notifications")
        journal("OK", "incr10_discord", len(envoyees), "VERT",
                f"exception : {len(envoyees)}", s.resume())
        s.afficher()
        return

    # ---- hebdomadaire ------------------------------------------------------
    s.phase("hebdomadaire")
    reste = [c for c in nouvelles if c["ticker"] not in {x["ticker"] for x in envoyees}]
    lot = reste[:PLAFOND_NOMS]

    pea = sum(1 for c in candidates if c.get("eligible_pea") == 1)
    entete = (f"*{len(candidates)} candidates au total, dont {pea} éligibles PEA. "
              f"{len(reste)} nouvelles cette semaine.*")

    if lot:
        corps = entete + "\n\n" + "\n\n".join(ligne(c) for c in lot)
        if len(reste) > PLAFOND_NOMS:
            corps += (f"\n\n*{len(reste) - PLAFOND_NOMS} autres nouvelles ne sont "
                      f"pas affichées : au-delà de cinq, rien n'est actionnable. "
                      f"La liste complète reste accessible par "
                      f"`screener(preset:\"strict\")`.*")
        corps += ("\n\n*« Profits actuels » rapporte le pouvoir bénéficiaire "
                  "— ce que vaudrait la société si elle cessait de croître — à "
                  "ce que le marché la paie. À 60 %, six euros sur dix sont "
                  "couverts par les profits déjà dégagés, quatre reposent sur "
                  "une croissance à démontrer.*"
                  "\n\n🔴 **Watchlist, pas des idées.** Fenêtre courte hors "
                  "États-Unis, moat proxy, aucune fair value.")
        titre = "📋 Screener — nouvelles candidates"
        couleur = 0x3498db
    else:
        # L'embed part MEME VIDE : un canal muet ne doit pas ressembler a un
        # dispositif en panne. C'est la meme regle que le point hebdomadaire
        # du serveur d'analyse.
        corps = entete + "\n\n*Aucune nouvelle candidate. Dans un marché haut, " \
                         "c'est le cas de base — pas un défaut du dispositif.*"
        titre = "📋 Screener — rien à signaler"
        couleur = 0x95a5a6

    envoye = envoyer(titre, corps, couleur)
    if envoye:
        envoyees += lot

    s.phase("ecriture")
    for c in envoyees:
        d1("INSERT INTO notifications (ticker, premiere, derniere, epv, canal, "
           "motif) VALUES (?, ?, ?, ?, 'hebdo', 'nouvelle candidate') "
           "ON CONFLICT(ticker) DO UPDATE SET derniere = excluded.derniere",
           [c["ticker"], RUN_TS, RUN_TS, c["epv_sur_cours"]],
           lignes=1, table="notifications")

    print(f"\n--- RAPPORT ---\n  {len(candidates)} candidates, "
          f"{len(reste)} nouvelles, {len(envoyees)} signalees")
    r = s.afficher()
    journal("OK", "incr10_discord", len(envoyees), "VERT",
            f"{len(envoyees)} signalees sur {len(reste)} nouvelles", r)
    print("OK")


if __name__ == "__main__":
    main()
