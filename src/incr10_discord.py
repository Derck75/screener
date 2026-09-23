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
from commun import d1, journal, sonde, migrer, RUN_TS  # noqa: E402
import datetime as _dt

WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
PLAFOND_NOMS = int(os.environ.get("PLAFOND_NOMS", "5"))

# Candidate au sens de la notification hebdomadaire. Le critere de
# croissance est un PROXY DU SEUIL N (bande verte, ratio <= 0,80), calcule au
# taux-obstacle — jamais le point 2 de la note PRIX, que le cadre calcule au
# WACC et interdit de fusionner avec N. Volontairement plus
# exigeant que le preset `strict` du screener : on ne signale pas ce qu'on
# consulte, on signale ce qui merite qu'on s'arrete.
CANDIDATE = """
    m.exclusion IS NULL
    AND m.profil_type IN ('industriel', 'capitalistique', 'asset_light')
    AND m.roic_median >= 15 AND m.spread_median > 0
    AND m.ca_cagr > 0 AND m.fcf_cagr > 0
    AND m.epv_sur_cours >= 0.60
    AND (s.suivi_serveur IS NULL OR s.suivi_serveur = 0)
    AND m.croissance_implicite IS NOT NULL
    AND m.croissance_demontree >= 4
    AND m.croissance_implicite <= 0.8 * m.croissance_demontree
    AND m.n_ex_roic_sup_seuil >= m.n_ex_total - 1
"""
# PERSISTANCE DU ROIC — l'approximation la plus fidele du filtre dur du cadre
# (« ROIC > WACC sans decrochage »), bien plus discriminante que le score de
# moat proxy, que trois societes de qualite sur quatre saturent. Un seul
# exercice sous le seuil est tolere : le cadre admet d'ecarter une annee de
# recession generalisee, a condition de la nommer.
# CROISSANCE REALISEE D'AU MOINS 4 %. Sans plancher, le critere degenerait
# pres de zero : Thermador passait avec 0,8 % realise, Lifevantage avec 0,3 %.
# Des societes stagnantes, bon marche par rapport a leur propre stagnation —
# pas un profil quality growth. Ajouter une condition ne peut que RETIRER des
# candidates : aucune rafale a craindre.

# L'EXCEPTION a DEUX VOIES. La voie historique exige huit exercices et VanEck :
# toutes les europeennes n'ont que cinq exercices, elle etait donc
# structurellement AMERICAINE — alors que le PEA est l'enveloppe fiscale
# prioritaire. La voie PEA remplace VanEck par le score de moat maximal et
# huit exercices par une croissance realisee d'au moins 6 %, reste dans le
# domaine de validite, et refuse une tresorerie qui gonflerait les mesures.
# Sur l'etat du 22/09, aucune societe PEA ne la franchit : elle reste rare.
EXCEPTION = """
    AND m.drapeaux IS NULL AND m.roic_median >= 20
    AND (
      (m.epv_sur_cours >= 0.90 AND m.n_ex_total >= 8
       AND s.vaneck IS NOT NULL AND s.vaneck_sorti_le IS NULL)
      OR
      (s.eligible_pea = 1
       AND COALESCE(m.concordance, m.epv_sur_cours) BETWEEN 0.90 AND 1.0
       AND m.score_moat >= m.score_moat_max
       AND m.croissance_demontree >= 6
       AND COALESCE(m.part_tresorerie, 0) < 0.4)
    )
"""

# Relance : une societe deja signalee revient si son cours a baisse d'au
# moins 25 % depuis le dernier signalement.
BAISSE_RELANCE = 0.25

# Fraicheur attendue de chaque etape, en jours. Au-dela, le message
# hebdomadaire le dit : un screener qui tourne sur des donnees perimees ne doit
# pas ressembler a un screener sain.
FRAICHEUR = [("metriques", "incr5_metriques%", 2), ("cours", "incr6_prix%", 2),
             ("comptes US", "incr4_comptes%", 9), ("comptes hors US", "incr8_comptes_intl%", 2)]



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
    if (c.get("part_tresorerie") or 0) >= 0.4:
        dr += f" 💰 trésorerie {round(100 * c['part_tresorerie'])} % du prix"
    act = activite(c.get("secteur"))
    # Le NOM d'abord, en gras : c'est ce qu'on lit. Le ticker suit, discret —
    # il sert a interroger l'outil, pas a reconnaitre la societe.
    t = (f"**{str(c.get('nom') or '')[:40]}**  `{c['ticker']}`\n"
         + (f"{act}\n" if act else "")
         # Formulation directe plutot que le sigle : « EPV/cours 60 % » ne dit
         # rien tant qu'on n'a pas la definition en tete.
         + f"Profits actuels : **{round(100 * c['epv_sur_cours'])} % du cours**"
         + (f" · avec croissance : {round(100 * c['eva_sur_cours'])} %"
            if c.get("eva_sur_cours") is not None else "") + "\n"
         + (f"Le prix suppose **{c['croissance_implicite']:+.1f} %/an**, "
            f"la société a fait {c['croissance_demontree']:+.1f} %\n"
            if c.get("croissance_implicite") is not None
            and c.get("croissance_demontree") is not None else "")
         + f"ROIC {round(c['roic_median'])} %, au-dessus de 9 % sur "
         + f"{c.get('n_ex_roic_sup_seuil', '?')}/{c['n_ex_total']} ex."
         + (" ⚠️ cycle incomplet" if (c.get("n_ex_total") or 0) < 8 else "") + " · "
         + f"moat {c['score_moat']}/{c['score_moat_max']} · {pea} · {pays}{ve}{dr}")
    return t


def compacte(c):
    """Une ligne par societe, pour la liste au-dela des cinq premieres."""
    return (f"**{str(c.get('nom') or '')[:30]}** `{c['ticker']}` "
            f"{round(100 * c['epv_sur_cours'])} %"
            + (" · PEA" if c.get("eligible_pea") == 1 else ""))


def sante():
    """Bilan de fraicheur des etapes, pour le message hebdomadaire.

    Le message part meme vide, pour qu'un canal muet ne ressemble pas a une
    panne. Mais un message qui arrive ne prouve pas que les donnees sont
    fraiches : l'etage prix peut echouer six jours de suite pendant que
    Discord continue d'annoncer « rien a signaler ». Ce bilan le dit."""
    maintenant = _dt.datetime.now(_dt.timezone.utc)
    alertes = []
    for nom, motif, jours in FRAICHEUR:
        r = d1("SELECT MAX(debut) AS d FROM runs WHERE statut = 'OK' AND etape LIKE ?",
               [motif])[0]["results"]
        d = r[0]["d"] if r else None
        if not d:
            alertes.append(f"{nom} : jamais")
            continue
        try:
            quand = _dt.datetime.strptime(d[:19], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=_dt.timezone.utc)
        except ValueError:
            continue
        age = (maintenant - quand).total_seconds() / 86400
        if age > jours:
            alertes.append(f"{nom} : {age:.0f} j")
    if not alertes:
        return "✅ *Toutes les étapes ont tourné dans les délais.*"
    return ("⚠️ **Données en retard** — " + " · ".join(alertes)
            + ". *Les candidates ci-dessus peuvent reposer sur des chiffres périmés.*")


def enregistrer(c, canal, motif):
    d1("INSERT INTO notifications (ticker, premiere, derniere, epv, canal, motif, "
       "cours_signale) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(ticker) DO UPDATE SET "
       "derniere = excluded.derniere, epv = excluded.epv, canal = excluded.canal, "
       "motif = excluded.motif, cours_signale = excluded.cours_signale",
       [c["ticker"], RUN_TS, RUN_TS, c["epv_sur_cours"], canal, motif, c.get("cours")],
       lignes=1, table="notifications")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--amorcage", action="store_true",
                    help="enregistre l'etat courant SANS rien envoyer")
    ap.add_argument("--exception-seule", action="store_true",
                    help="ne verifie que l'alerte immediate")
    a = ap.parse_args()

    s = sonde("incr10_discord")
    print(f"incr10_discord v7 — Run {RUN_TS}")

    # Auto-migration : plus aucun ALTER TABLE a passer a la main.
    d1("CREATE TABLE IF NOT EXISTS notifications (ticker TEXT PRIMARY KEY, "
       "premiere TEXT, derniere TEXT, epv REAL, canal TEXT, motif TEXT)")
    migrer("notifications", {"cours_signale": "REAL"})
    migrer("societe", {"suivi_serveur": "INTEGER", "statut_serveur": "TEXT"})

    s.phase("lecture")
    base = ("FROM metriques m JOIN societe s ON s.ticker = m.ticker "
            "WHERE " + CANDIDATE)
    champs = ("m.ticker, s.nom, s.pays_siege, s.eligible_pea, s.vaneck, s.secteur, "
              "m.epv_sur_cours, m.roic_median, m.n_ex_total, m.score_moat, "
              "m.score_moat_max, m.drapeaux, m.eva_sur_cours, m.part_tresorerie, "
              "m.croissance_implicite, m.croissance_demontree, m.cours, m.concordance, "
              "m.n_ex_roic_sup_seuil")
    ordre = ("ORDER BY (COALESCE(m.concordance, m.epv_sur_cours) > 1.0) ASC, "
             "COALESCE(m.concordance, m.epv_sur_cours) DESC")
    candidates = d1(f"SELECT {champs} {base} {ordre}")[0]["results"]
    notif = {l["ticker"]: l for l in
             d1("SELECT ticker, epv, cours_signale FROM notifications")[0]["results"]}
    print(f"  {len(candidates)} candidates, {len(notif)} deja signalees")
    s.compte("candidates", len(candidates))

    nouvelles = [c for c in candidates if c["ticker"] not in notif]
    print(f"  {len(nouvelles)} nouvelles")
    s.compte("nouvelles", len(nouvelles))

    # ---- amorcage ----------------------------------------------------------
    if a.amorcage:
        s.phase("amorcage")
        for c in nouvelles:
            enregistrer(c, "amorcage", "etat initial")
        print(f"  AMORCAGE : {len(nouvelles)} candidates enregistrees, aucun message envoye")
        journal("OK", "incr10_discord", len(nouvelles), "VERT",
                f"amorcage : {len(nouvelles)} enregistrees", s.resume())
        s.afficher()
        return

    # ---- exception ---------------------------------------------------------
    s.phase("exception")
    exc = d1(f"SELECT {champs} {base} {EXCEPTION} {ordre}")[0]["results"]
    exc_neuves = [c for c in exc if c["ticker"] not in notif][:3]
    envoyees = []
    if exc_neuves:
        corps = "\n\n".join(ligne(c) for c in exc_neuves)
        corps += ("\n\n*Tout est coché : pouvoir bénéficiaire d'au moins 90 % du cours, "
                  "ROIC d'au moins 20 %, aucun drapeau — et soit un moat certifié "
                  "VanEck sur huit exercices, soit, pour une société éligible PEA, le "
                  "score de moat maximal avec une croissance réalisée d'au moins 6 %. "
                  "Calibrée pour se déclencher une à deux fois par an.*"
                  "\n\n🔴 **Watchlist, pas une idée.** L'analyse complète reste à faire.")
        if envoyer("🟢 Opportunité exceptionnelle", corps, 0x2ecc71):
            for c in exc_neuves:
                enregistrer(c, "exception", "tout coche")
            envoyees += exc_neuves
            s.compte("exception_envoyee", len(exc_neuves))
    else:
        print("  aucune exception — c'est le cas normal")

    if a.exception_seule:
        journal("OK", "incr10_discord", len(envoyees), "VERT",
                f"exception : {len(envoyees)}", s.resume())
        s.afficher()
        return

    # ---- relances ----------------------------------------------------------
    # Une societe signalee une fois ne l'etait plus jamais, meme apres une
    # baisse de 30 %. Pour un investisseur qui achete la decote, c'est pourtant
    # ce moment-la qui compte.
    s.phase("relances")
    relances = []
    for c in candidates:
        n = notif.get(c["ticker"])
        if not n or c["ticker"] in {x["ticker"] for x in envoyees}:
            continue
        if n.get("cours_signale") and c.get("cours"):
            baisse = 1 - c["cours"] / n["cours_signale"]
        elif n.get("epv") and c.get("epv_sur_cours"):
            # Signalements anterieurs a cette version : pas de cours memorise.
            # A pouvoir beneficiaire constant, un cours en baisse de 25 % fait
            # monter l'EPV rapportee au cours d'un tiers.
            baisse = 1 - n["epv"] / c["epv_sur_cours"]
        else:
            continue
        if baisse >= BAISSE_RELANCE:
            relances.append((c, baisse))
    relances.sort(key=lambda x: -x[1])
    relances = relances[:3]
    s.compte("relances", len(relances))

    # ---- hebdomadaire ------------------------------------------------------
    s.phase("hebdomadaire")
    reste = [c for c in nouvelles if c["ticker"] not in {x["ticker"] for x in envoyees}]
    lot, autres = reste[:PLAFOND_NOMS], reste[PLAFOND_NOMS:PLAFOND_NOMS + 20]

    pea = sum(1 for c in candidates if c.get("eligible_pea") == 1)
    entete = (f"*{len(candidates)} candidates au total, dont {pea} éligibles PEA. "
              f"{len(reste)} nouvelles cette semaine.*")
    blocs = [entete]
    if lot:
        blocs.append("\n\n".join(ligne(c) for c in lot))
    if autres:
        # La liste exhaustive que tu demandais, sans noyer le message : une
        # ligne par societe au-dela des cinq detaillees.
        blocs.append("**Autres nouvelles**\n" + "\n".join(compacte(c) for c in autres))
        if len(reste) > PLAFOND_NOMS + 20:
            blocs.append(f"*… et {len(reste) - PLAFOND_NOMS - 20} de plus, "
                         f"présentées la semaine prochaine.*")
    if relances:
        blocs.append("🔻 **Déjà signalées, désormais moins chères**\n" + "\n".join(
            f"**{str(c.get('nom') or '')[:30]}** `{c['ticker']}` — cours en baisse de "
            f"{round(100 * b)} % depuis le signalement, EPV {round(100 * c['epv_sur_cours'])} %"
            for c, b in relances))
    if lot or relances:
        blocs.append("*« Profits actuels » rapporte le pouvoir bénéficiaire — ce que "
                     "vaudrait la société si elle cessait de croître — à ce que le marché "
                     "la paie. La ligne suivante confronte la croissance que le prix exige "
                     "pour rapporter 10 % par an en PEA, 11,5 % en CTO, à celle réellement "
                     "réalisée — au moins 4 % par an pour figurer ici. ⚠️ cycle incomplet : "
                     "cinq exercices seulement, la médiane peut capitaliser un sommet.*"
                     "\n\n🔴 **Watchlist, pas des idées.** Moat proxy, aucune fair value.")
        titre, couleur = "📋 Screener — nouvelles candidates", 0x3498db
    else:
        # L'embed part MEME VIDE : un canal muet ne doit pas ressembler a un
        # dispositif en panne.
        blocs.append("*Aucune nouvelle candidate. Dans un marché haut, c'est le cas "
                     "de base — pas un défaut du dispositif.*")
        titre, couleur = "📋 Screener — rien à signaler", 0x95a5a6
    blocs.append(sante())

    if envoyer(titre, "\n\n".join(blocs), couleur):
        s.phase("ecriture")
        for c in lot + autres:
            enregistrer(c, "hebdo", "nouvelle candidate")
        for c, b in relances:
            enregistrer(c, "hebdo", f"relance apres baisse de {round(100 * b)} %")
        envoyees += lot + autres

    print(f"\n--- RAPPORT ---\n  {len(candidates)} candidates, {len(reste)} nouvelles, "
          f"{len(envoyees)} signalees, {len(relances)} relances")
    r = s.afficher()
    journal("OK", "incr10_discord", len(envoyees), "VERT",
            f"{len(envoyees)} signalees, {len(relances)} relances", r)
    print("OK")


if __name__ == "__main__":
    main()
