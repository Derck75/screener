#!/usr/bin/env python3
"""
INCREMENT 12 — Siege, ISIN et eligibilite PEA des societes internationales,
lus sur le profil StockAnalysis ; cotations secondaires reperees par ISIN.

POURQUOI (audit C1). L'eligibilite PEA etait PRESUMEE depuis la place de
cotation. C'est juste sur une place d'origine, et faux partout ou une place
cote des etrangeres :
  · une suisse lue sur Xetra ressortait allemande, donc « PEA » ;
  · Shell ou Unilever, lues sur Amsterdam, ressortaient neerlandaises — leur
    siege social et leur ISIN sont britanniques, elles ne sont PAS eligibles.
Le PEA s'apprecie au SIEGE SOCIAL, en UE/EEE. Le signal le plus proche que
l'on puisse lire est le prefixe de l'ISIN — pays de l'emetteur — controle par
son chiffre de cle ; le pays du siege administratif sert de repli.
Rien de tout cela ne remplace la liste du courtier : c'est une presomption
mieux fondee, et la source l'ecrit.

COTATIONS SECONDAIRES. Un meme ISIN sur deux tickers est une seule societe.
La cotation retenue est celle dont la place est dans le pays de l'emetteur ;
l'autre est marquee « secondaire », sort de la collecte de comptes (increment
8), et ses comptes et metriques deja calcules sont retires — sinon la meme
societe apparaissait deux fois dans le crible.

UN APPEL PAR SOCIETE, une fois par an (60 jours si la page manquait). File :
places mixtes d'abord, puis par capitalisation.

CANARI : ASML doit ressortir Pays-Bas, ISIN NL0010273215. Si la page a change
de forme, RIEN n'est ecrit.
"""

import argparse
import html as _html
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from commun import d1, journal, sonde, budget, migrer, RUN_TS  # noqa: E402
from places import (SA_CODE, PAYS_PLACE, MIXTES, EEE, PAYS_ISO,  # noqa: E402
                    ISIN_NON_PAYS, suffixe, symbole_sa, isin_valide)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
PAUSE = float(os.environ.get("PAUSE_SA", "1.0"))
LOT = int(os.environ.get("LOT_PROFIL", "250"))
CANARI = ("ASML.AS", "NL", "NL0010273215")

# Sources d'eligibilite que ce script a le droit de REMPLACER : les
# presomptions de place et ses propres lectures. Un pays DECLARE par VanEck ou
# lu sur un depot SEC n'est pas ecrase — l'ISIN y est seulement ajoute.
REMPLACABLES = ("presomption de place", "place mixte", "profil StockAnalysis")


def lire(url, s=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for essai in range(3):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", "replace"), 200
        except urllib.error.HTTPError as e:
            if e.code == 429:
                if s:
                    s.compte("429_rattrape")
                time.sleep(8 * (essai + 1))
                continue
            return None, e.code
        except Exception as e:
            if s:
                s.erreur("reseau_sa", type(e).__name__)
            return None, "reseau"
    return None, 429


def jetons(html):
    """Texte de la page en jetons, un par element. La structure exacte du
    tableau (td, div, dl) n'est pas supposee : on lit « Country » puis la
    valeur qui le suit, quelle que soit la balise qui les porte."""
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html or "", flags=re.S | re.I)
    t = _html.unescape(re.sub(r"<[^>]+>", "\n", t))
    return [x.strip() for x in t.split("\n") if x.strip()]


def extraire_profil(html):
    """Rend (pays_libelle, isin) — l'un ou l'autre peut manquer."""
    tk = jetons(html)
    # Ordre de confiance : une valeur REconnue apres « Country » ; puis la
    # donnee embarquee de la page ; puis, en dernier, une valeur non reconnue
    # (publiee telle quelle, elle ne decide de rien sans ISIN).
    pays, brut = None, None
    for i, x in enumerate(tk[:-1]):
        if x.lower().rstrip(":") == "country":
            v = tk[i + 1].strip()
            if v.lower() in PAYS_ISO:
                pays = v
                break
            if brut is None and 2 <= len(v) <= 40 and not re.search(r"\d", v):
                brut = v
    if pays is None:
        m = re.search(r"""["']?country["']?\s*:\s*["']([A-Za-z .'-]{2,40})["']""", html or "")
        pays = m.group(1) if m else brut
    isin = None
    for i, x in enumerate(tk[:-1]):
        if x.lower().startswith("isin"):
            v = tk[i + 1].strip().upper()
            if isin_valide(v):
                isin = v
                break
    if isin is None:
        for m in re.finditer(r"""["']?isin["']?\s*:\s*["']([A-Z0-9]{12})["']""", html or "", re.I):
            if isin_valide(m.group(1).upper()):
                isin = m.group(1).upper()
                break
    return pays, isin


def decider(pays_lib, isin):
    """(pays_siege, eligible_pea, fondement, divergence)."""
    hq = PAYS_ISO.get((pays_lib or "").strip().lower())
    pi = isin[:2] if isin and isin[:2] not in ISIN_NON_PAYS else None
    if pi:
        elig, base = pi in EEE, f"ISIN {pi}"
    elif hq:
        elig, base = hq in EEE, f"siege {hq}"
    else:
        return None, None, None, False
    divergence = bool(hq and pi and ((hq in EEE) != (pi in EEE)))
    return hq or pi, elig, base, divergence


def profil(ticker, s=None):
    code = SA_CODE.get(suffixe(ticker))
    url = f"https://stockanalysis.com/quote/{code}/{symbole_sa(ticker)}/company/"
    html, st = lire(url, s)
    if st != 200:
        return st, None, None, html
    p, i = extraire_profil(html)
    return (200 if (p or i) else "illisible"), p, i, html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sonder", default="", help="un ticker : affiche la lecture, n'ecrit rien")
    a = ap.parse_args()
    s = sonde("incr12_profil_intl")
    print(f"incr12_profil_intl v1 — Run {RUN_TS}")

    if a.sonder:
        st, p, i, html = profil(a.sonder, s)
        print(f"  statut {st} · pays « {p} » · ISIN {i}")
        print(f"  decision : {decider(p, i)}")
        tk = jetons(html or "")
        for k, x in enumerate(tk):
            if x.lower().rstrip(":") in ("country", "isin number", "isin"):
                print(f"  contexte : {' | '.join(tk[max(0, k - 2):k + 3])}")
        print("\nAucune ecriture.")
        return

    s.phase("canari")
    st, p, i, html = profil(CANARI[0], s)
    hq, _, _, _ = decider(p, i)
    if st != 200 or hq != CANARI[1] or i != CANARI[2]:
        tk = jetons(html or "")
        ctx = next((" | ".join(tk[max(0, k - 2):k + 3]) for k, x in enumerate(tk)
                    if x.lower().startswith(("country", "isin"))), "aucun libelle Country/ISIN")
        msg = f"canari rouge : {CANARI[0]} statut {st}, pays {p}, ISIN {i} — contexte : {ctx[:160]}"
        print("PANNE : " + msg)
        s.afficher()
        journal("PANNE", s.etape, 0, "ROUGE", msg, s.resume())
        sys.exit(1)
    time.sleep(PAUSE)

    s.phase("cibles")
    migrer("societe", {"profil_le": "TEXT", "profil_statut": "TEXT"})
    places = sorted({x.lstrip(".") for x in SA_CODE})
    mixtes = sorted({x.lstrip(".") for x in MIXTES})
    cibles = d1(
        "SELECT ticker, source_eligibilite FROM societe "
        "WHERE cik IS NULL AND instr(ticker, '.') > 0 "
        "AND instr(COALESCE(origine, ''), 'secondaire') = 0 "
        f"AND place IN ({', '.join('?' * len(places))}) "
        "AND (profil_le IS NULL OR profil_le < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', "
        "CASE WHEN profil_statut = 'ok' THEN '-365 days' ELSE '-60 days' END)) "
        f"ORDER BY CASE WHEN place IN ({', '.join('?' * len(mixtes))}) THEN 0 ELSE 1 END, "
        "capitalisation DESC LIMIT ?", places + mixtes + [LOT])[0]["results"]
    print(f"  {len(cibles)} societes a profiler (lot de {LOT})")

    s.phase("lecture")
    maj = []
    for k, l in enumerate(cibles, 1):
        t = l["ticker"]
        st, p, i, _ = profil(t, s)
        time.sleep(PAUSE)
        if st not in (200, 404, "illisible"):
            # 429, 403, 5xx, coupure : rien d'appris sur la SOCIETE. Seul un
            # 404 (page absente) ou une page servie sans pays ni ISIN datent
            # l'echec — sinon une rafale anti-robot parquait 250 societes
            # pour deux mois.
            s.compte(f"incident_{st}")
            continue
        if st != 200:
            s.compte(f"profil_{st}")
            maj.append((t, None, None, None, None, "404" if st == 404 else "illisible", l))
            continue
        hq, elig, base, div = decider(p, i)
        s.compte("lus")
        if div:
            s.compte("divergence_siege_isin")
        if hq and hq not in EEE and PAYS_PLACE.get(suffixe(t)) in EEE:
            s.compte("presomption_pea_corrigee")
        maj.append((t, hq, elig, i, (p, base, div), "ok", l))
        if k % 50 == 0:
            print(f"  {k}/{len(cibles)}")

    s.phase("ecriture")
    if maj:
        budget(len(maj), "societe")
    n = 0
    for t, hq, elig, isin, info, statut, l in maj:
        remplacable = (l.get("source_eligibilite") or "").startswith(REMPLACABLES) or not l.get("source_eligibilite")
        if statut == "ok" and remplacable and hq:
            p, base, div = info
            src = (f"profil StockAnalysis : siege {p or '?'} · ISIN {isin or 'absent'} — "
                   f"eligibilite presumee sur {base}"
                   + (" (siege et ISIN divergent)" if div else "")
                   + ", a confirmer contre la liste du courtier")
            d1("UPDATE societe SET pays_siege = ?, eligible_pea = ?, source_eligibilite = ?, "
               "isin = COALESCE(?, isin), profil_le = ?, profil_statut = ? WHERE ticker = ?",
               [hq, 1 if elig else 0, src[:200], isin, RUN_TS, statut, t], lignes=1, table="societe")
        else:
            d1("UPDATE societe SET isin = COALESCE(?, isin), profil_le = ?, profil_statut = ? "
               "WHERE ticker = ?", [isin, RUN_TS, statut, t], lignes=1, table="societe")
        n += 1

    s.phase("secondaires")
    marquees = secondaires(s)

    s.phase("rapport")
    reste = d1("SELECT COUNT(*) AS n FROM societe WHERE cik IS NULL AND instr(ticker, '.') > 0 "
               "AND instr(COALESCE(origine, ''), 'secondaire') = 0 AND profil_le IS NULL "
               f"AND place IN ({', '.join('?' * len(places))})", places)[0]["results"][0]["n"]
    print(f"\n  {n} profils ecrits · {marquees} cotations secondaires marquees · reste {reste}")
    r = s.afficher()
    journal("OK", s.etape, n + marquees, "VERT",
            f"{n} profils, {marquees} secondaires, {reste} restants", r)
    print("OK")


def secondaires(s):
    """Un ISIN, une societe. Garde la cotation de la place du pays emetteur."""
    lignes = d1(
        "SELECT s.ticker, s.isin, s.pays_siege, s.capitalisation, s.vaneck, s.vaneck_vu_le, "
        "(SELECT COUNT(*) FROM comptes2 c WHERE c.ticker = s.ticker) AS n "
        "FROM societe s WHERE s.isin IS NOT NULL AND s.cik IS NULL "
        "AND instr(COALESCE(s.origine, ''), 'secondaire') = 0 AND s.isin IN ("
        "  SELECT isin FROM societe WHERE isin IS NOT NULL AND cik IS NULL "
        "  AND instr(COALESCE(origine, ''), 'secondaire') = 0 "
        "  GROUP BY isin HAVING COUNT(*) > 1)")[0]["results"]
    groupes = {}
    for l in lignes:
        groupes.setdefault(l["isin"], []).append(l)

    def score(l):
        suf = suffixe(l["ticker"])
        pp = PAYS_PLACE.get(suf)
        return ((4 if pp == l["isin"][:2] else 0) + (2 if pp and pp == l["pays_siege"] else 0)
                + (1 if suf not in MIXTES else 0), l["n"] or 0, l["capitalisation"] or 0)

    marquees = 0
    for isin, g in groupes.items():
        g.sort(key=score, reverse=True)
        garde, autres = g[0], g[1:]
        for l in autres:
            d1("UPDATE societe SET origine = COALESCE(origine, '') || ',secondaire' "
               "WHERE ticker = ?", [l["ticker"]], lignes=1, table="societe")
            if l.get("vaneck"):
                # Drapeau TRANSFERE en entier, date de derniere vue comprise :
                # sans elle, le passage VanEck suivant journalisait une sortie
                # d'indice sur la cotation retenue. L'increment 2 reporte
                # ensuite lui-meme ses lignes secondaires sur la primaire.
                d1("UPDATE societe SET vaneck = COALESCE(vaneck, ?), "
                   "vaneck_vu_le = COALESCE(?, vaneck_vu_le), vaneck_sorti_le = NULL "
                   "WHERE ticker = ?",
                   [l["vaneck"], l.get("vaneck_vu_le"), garde["ticker"]], lignes=1, table="societe")
            d1("DELETE FROM comptes2 WHERE ticker = ?", [l["ticker"]])
            d1("DELETE FROM metriques WHERE ticker = ?", [l["ticker"]])
            marquees += 1
            s.compte("secondaire_marquee")
        print(f"  ISIN {isin[:2]}… : {garde['ticker']} retenue, "
              f"{', '.join(x['ticker'] for x in autres)} secondaire(s)")
    return marquees


if __name__ == "__main__":
    main()
