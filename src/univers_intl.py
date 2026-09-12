#!/usr/bin/env python3
"""
UNIVERS INTERNATIONAL v2 — EXPLORATION, AUCUNE ECRITURE.

CE QUE LA v1 A REVELE, ET QUI CHANGE LA CONCEPTION
--------------------------------------------------
Les listes triees par capitalisation sont dominees par des COTATIONS
SECONDAIRES de societes americaines : NVD = NVIDIA sur Xetra, 1AAPL = Apple
sur Milan, 0R2V = Apple a Londres. Les ingerer creerait Apple quatre fois et
noierait les vraies europeennes sous des doublons.

Le controle par titre ne voit rien : ces pages existent reellement. Il faut un
garde-fou par SOCIETE, pas par symbole — d'ou la detection par nom normalise,
contre l'univers SEC deja en base et entre places europeennes.

Trois autres corrections : les chemins de places sont DECOUVERTS sur la page
d'index au lieu d'etre devines (cinq 404 en v1), la capitalisation est captee
pour permettre un filtre de taille, et la pagination est tentee au-dela des
500 lignes servies par defaut.

LECTURES D1 SEULEMENT si --d1 est passe. Aucune ecriture, jamais : ce script
doit tourner meme quand le quota d'ecriture est epuise.
"""

import argparse
import json
import os
import re
import sys
import unicodedata
import time
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
INDEX = "https://stockanalysis.com/list/"

# code de place -> (suffixe Yahoo, pays, presomption PEA, motifs de recherche
# dans la page d'index). L'eligibilite PEA tient au SIEGE SOCIAL en UE/EEE :
# la place n'est qu'une presomption, a confirmer societe par societe.
PLACES = {
    "epa": (".PA", "FR", True,  ["euronext-paris"]),
    "ams": (".AS", "NL", True,  ["euronext-amsterdam"]),
    "ebr": (".BR", "BE", True,  ["euronext-brussels"]),
    "els": (".LS", "PT", True,  ["euronext-lisbon"]),
    "etr": (".DE", "DE", True,  ["deutsche-boerse-xetra", "frankfurt-stock-exchange"]),
    "mil": (".MI", "IT", True,  ["borsa-italiana", "italian"]),
    "bme": (".MC", "ES", True,  ["madrid"]),
    "sto": (".ST", "SE", True,  ["stockholm", "nasdaq-stockholm"]),
    "cph": (".CO", "DK", True,  ["copenhagen"]),
    "hel": (".HE", "FI", True,  ["helsinki"]),
    "osl": (".OL", "NO", True,  ["oslo"]),
    "lon": (".L",  "GB", False, ["london-stock-exchange"]),
    "swx": (".SW", "CH", False, ["six-swiss", "swiss"]),
    "tyo": (".T",  "JP", False, ["tokyo-stock-exchange"]),
}

# Formes juridiques retirees avant comparaison : « Apple Inc. » et « Apple
# Inc » doivent se reconnaitre, sinon le dedoublonnage ne sert a rien.
FORMES = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|llc|lp|"
    r"sa|s\.a|nv|n\.v|ag|se|spa|s\.p\.a|as|a\.s|asa|oyj|ab|aktiengesellschaft|"
    r"holding|holdings|group|groupe|the|sgps|kgaa|bv|b\.v|adr|class|cl)\b", re.I)


def lire(url, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "text/html,application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as rep:
        return rep.read().decode("utf-8", "replace")


TRANSLIT = str.maketrans({"ø": "o", "æ": "ae", "å": "a", "ß": "ss", "đ": "d",
                          "ł": "l", "þ": "th", "ð": "d", "œ": "oe"})


def normaliser(nom):
    """Rend une cle de comparaison. Accents retires, formes juridiques
    supprimees, tout colle sans espaces.

    LIMITE ASSUMEE : « A.P. Moller » et « AP Moller » ne se reduisent pas a la
    meme cle. L'heuristique attrape les cas qui comptent — les geantes
    americaines cotees en secondaire sur Xetra, Milan et Londres, dont le nom
    est ecrit a l'identique — et rate les abreviations. Les quasi-doublons
    sont SIGNALES plutot que resolus en silence : un faux appariement
    supprimerait une vraie societe de l'univers."""
    n = unicodedata.normalize("NFKD", (nom or "").lower().translate(TRANSLIT))
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = n.replace("'", "").replace("\u2019", "")   # l'oreal -> loreal
    n = re.sub(r"[^\w\s]", " ", n)
    n = FORMES.sub(" ", n)
    mots = [m for m in n.split() if len(m) > 1]
    return "".join(mots)


def cap_en_nombre(txt):
    """« 3.45T », « 123.45B », « 987.6M » -> nombre."""
    if not txt:
        return None
    m = re.match(r"^[^\d\-]*(-?[\d.,]+)\s*([TBMK])?", txt.strip(), re.I)
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return v * {"T": 1e12, "B": 1e9, "M": 1e6, "K": 1e3}.get(
        (m.group(2) or "").upper(), 1)


def decouvrir_chemins(s_debug=False):
    """Lit la page d'index au lieu de deviner les chemins : la v1 devinait et
    rendait cinq 404, dont l'Espagne et la Suede."""
    try:
        html = lire(INDEX)
    except Exception as e:
        print(f"  index illisible ({type(e).__name__}) — repli sur les chemins connus")
        return {}
    liens = set(re.findall(r'/list/([a-z0-9\-]+)/', html))
    print(f"  {len(liens)} chemins publies sur la page d'index")
    trouves = {}
    for code, (_, _, _, motifs) in PLACES.items():
        for m in motifs:
            exact = [l for l in liens if l == m]
            approx = [l for l in liens if m in l]
            if exact or approx:
                trouves[code] = (exact or sorted(approx, key=len))[0]
                break
    manquants = [c for c in PLACES if c not in trouves]
    if manquants:
        print(f"  places non trouvees sur l'index : {manquants}")
    return trouves


def extraire(html):
    """Lit l'EN-TETE du tableau pour situer les colonnes.

    La v2 devinait leur position et se trompait d'un cran : la premiere
    colonne est « No. », un simple numero de ligne, si bien que le symbole
    etait lu comme un nom et le nom jamais lu du tout. Un tableau se lit par
    ses en-tetes, jamais par l'ordre suppose de ses colonnes."""
    entetes = re.findall(r"<th[^>]*>(.*?)</th>", html, re.S)
    libelles = [re.sub(r"<[^>]+>", " ", h).strip().lower() for h in entetes]

    def trouver(*motifs):
        for i, l in enumerate(libelles):
            if any(m in l for m in motifs):
                return i
        return None

    i_sym = trouver("symbol", "ticker")
    i_nom = trouver("company name", "company", "name")
    i_cap = trouver("market cap", "marketcap", "mkt cap")
    if i_sym is None or i_nom is None:
        return [], f"en-tetes non reconnus : {libelles[:8]}"

    lignes = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [re.sub(r"<[^>]+>", " ", c).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) <= max(i_sym, i_nom):
            continue
        sym = cells[i_sym]
        if not re.fullmatch(r"[A-Za-z0-9.\-]{1,12}", sym or ""):
            continue
        lignes.append({
            "sym": sym.upper(),
            "nom": cells[i_nom],
            "cap": cap_en_nombre(cells[i_cap]) if (i_cap is not None and i_cap < len(cells)) else None,
        })
    return lignes, f"colonnes {i_sym}/{i_nom}/{i_cap}"


def charger_sec():
    """Noms de l'univers SEC. LECTURE SEULE — aucune ecriture, donc utilisable
    quand le quota d'ecriture est epuise."""
    a, b, t = (os.environ.get(k) for k in ("CF_ACCOUNT", "CF_DB", "CF_TOKEN"))
    if not all((a, b, t)):
        print("  variables Cloudflare absentes — detection SEC desactivee")
        return {}
    import urllib.request as u
    noms, page = {}, 0
    while True:
        req = u.Request(
            f"https://api.cloudflare.com/client/v4/accounts/{a}/d1/database/{b}/query",
            data=json.dumps({"sql": "SELECT ticker, nom FROM societe WHERE cik IS NOT NULL "
                                    "ORDER BY ticker LIMIT 2000 OFFSET ?",
                             "params": [page * 2000]}).encode(),
            headers={"Authorization": f"Bearer {t}", "Content-Type": "application/json"})
        try:
            j = json.loads(u.urlopen(req, timeout=90).read())
        except urllib.error.HTTPError as e:
            corps = e.read()[:200].decode("utf-8", "replace")
            print(f"  lecture D1 refusee : HTTP {e.code} — {corps}")
            return noms
        except Exception as e:
            print(f"  lecture D1 impossible : {type(e).__name__} — {str(e)[:150]}")
            return noms
        if not j.get("success"):
            print(f"  lecture D1 refusee : {json.dumps(j.get('errors'))[:200]}")
            return noms
        r = j.get("result", [{}])[0].get("results", [])
        if not r:
            break
        for l in r:
            n = normaliser(l.get("nom"))
            if n:
                noms[n] = l["ticker"]
        page += 1
    print(f"  {len(noms)} noms de deposants SEC charges pour la detection")
    return noms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--places", default="")
    ap.add_argument("--cap-min", type=float, default=2e9)
    ap.add_argument("--lister", action="store_true",
                    help="affiche les chemins publies sur la page d'index et s'arrete")
    ap.add_argument("--d1", action="store_true",
                    help="lit l'univers SEC pour detecter les cotations secondaires")
    a = ap.parse_args()

    cibles = [p.strip() for p in a.places.split(",") if p.strip()] or list(PLACES)
    print("EXPLORATION v2 — aucune ecriture\n")

    if a.lister:
        try:
            html = lire(INDEX)
        except Exception as e:
            print(f"index illisible : {type(e).__name__}")
            sys.exit(1)
        liens = sorted(set(re.findall(r'/list/([a-z0-9\-]+)/', html)))
        print(f"{len(liens)} chemins publies :\n")
        for l in liens:
            print(f"  {l}")
        print("\nLes chemins d'INDICE national (dax, cac-40, ftse-mib, omx...) sont")
        print("preferables aux chemins de PLACE : ils ne contiennent que des")
        print("societes locales, la ou une place cote aussi les etrangeres.")
        return

    print("[decouverte des chemins]")
    chemins = decouvrir_chemins()

    print("\n[univers SEC]")
    sec = charger_sec() if a.d1 else {}

    print("\n[places]")
    vus, resume = {}, []
    for code in cibles:
        if code not in PLACES:
            continue
        suffixe, pays, elig, _ = PLACES[code]
        chemin = chemins.get(code)
        if not chemin:
            print(f"{code:5} {pays}  chemin introuvable")
            resume.append((code, pays, 0, 0, 0, "chemin introuvable"))
            continue
        try:
            html = lire(f"https://stockanalysis.com/list/{chemin}/")
        except Exception as e:
            print(f"{code:5} {pays}  {type(e).__name__} sur /{chemin}/")
            resume.append((code, pays, 0, 0, 0, "erreur"))
            continue

        lignes, forme = extraire(html)
        avec_cap = sum(1 for l in lignes if l["cap"])
        secondaires = doublons = retenues = 0
        gardees = []
        for l in lignes:
            if a.cap_min and l["cap"] and l["cap"] < a.cap_min:
                continue
            n = normaliser(l["nom"])
            if not n:
                continue
            if n in sec:
                secondaires += 1          # cotation secondaire d'un deposant SEC
                continue
            if n in vus:
                doublons += 1             # deja vue sur une autre place europeenne
                continue
            vus[n] = (code, l["sym"])
            gardees.append(l)
            retenues += 1

        tronque = " TRONQUE" if len(lignes) >= 499 else ""
        if not lignes:
            print(f"{code:5} {pays}  aucune ligne — {forme}")
            resume.append((code, pays, 0, 0, 0, forme))
            continue
        print(f"{code:5} {pays}  {len(lignes):4} lues{tronque} · {avec_cap} avec capi · "
              f"{secondaires} secondaires · {doublons} doublons · {retenues} retenues"
              f"{'  [PEA]' if elig else ''}")
        for l in gardees[:3]:
            cap = f"{l['cap'] / 1e9:.1f} Md" if l["cap"] else "n.c."
            print(f"        {l['sym']:9} -> {l['sym'].replace('.', '-') + suffixe:14}"
                  f" {cap:>9}  {(l['nom'] or '')[:32]}")
        resume.append((code, pays, len(lignes), secondaires, retenues,
                       "tronque" if tronque else "complet"))
        time.sleep(0.6)

    print("\n[quasi-doublons — a trancher a la main]")
    cles = sorted(vus)
    signales = 0
    for i, k in enumerate(cles):
        for k2 in cles[i + 1:]:
            if not k2.startswith(k[:10]) or k == k2:
                continue
            if len(k) >= 10 and (k in k2 or k2 in k):
                print(f"  {vus[k][0]}:{vus[k][1]:9} « {k[:28]} »   ~   "
                      f"{vus[k2][0]}:{vus[k2][1]:9} « {k2[:28]} »")
                signales += 1
            break
    if not signales:
        print("  aucun")
    else:
        print(f"  {signales} paires — la normalisation ne les tranche pas, elle les montre")

    print("\n--- SYNTHESE ---")
    tot = pea = sec_tot = 0
    for code, pays, lues, secondaires, retenues, etat in resume:
        print(f"  {code:5} {pays:3} lues {lues:4}  secondaires {secondaires:4}  "
              f"retenues {retenues:4}  {etat}")
        tot += retenues
        sec_tot += secondaires
        if PLACES[code][2]:
            pea += retenues
    print(f"\n  univers retenu apres nettoyage : {tot}")
    print(f"  dont places de la zone PEA     : {pea}")
    print(f"  cotations secondaires ecartees : {sec_tot}")
    if not sec:
        print("\n  ATTENTION : detection des cotations secondaires DESACTIVEE.")
        print("  Relancer avec --d1 et les secrets Cloudflare : sans elle, Apple,")
        print("  NVIDIA et Alphabet entrent dans l'univers europeen par Xetra,")
        print("  Milan et Londres.")


if __name__ == "__main__":
    main()
