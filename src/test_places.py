#!/usr/bin/env python3
"""Non-regression hors reseau : table des places, ISIN, lecture de profil,
decision d'eligibilite, conversion Bloomberg (audit C1)."""

import os
import sys

os.environ.setdefault("CF_ACCOUNT", "x")
os.environ.setdefault("CF_DB", "x")
os.environ.setdefault("CF_TOKEN", "x")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from places import SA_CODE, isin_valide, suffixe, symbole_sa  # noqa: E402
import incr12_profil_intl as P  # noqa: E402
import incr2_vaneck as V  # noqa: E402

ko = 0


def ok(cond, msg):
    global ko
    print(("  OK    " if cond else "  ECHEC ") + msg)
    ko += 0 if cond else 1


# Les sept codes qui renvoyaient 404 sur chaque page
for suf, code in {".MI": "bit", ".LS": "eli", ".IR": "ise", ".WA": "wse",
                  ".KS": "krx", ".TW": "tpe", ".TO": "tsx"}.items():
    ok(SA_CODE.get(suf) == code, f"{suf} -> {code}")

ok(suffixe("NOVO-B.CO") == ".CO" and symbole_sa("NOVO-B.CO") == "NOVO.B", "symbole NOVO-B.CO")
ok(symbole_sa("BA.L") == "BA", "symbole BA.L")

ok(isin_valide("NL0010273215") and isin_valide("DE0007164600"), "ISIN valides")
ok(not isin_valide("NL0010273216") and not isin_valide("NL001027321"), "ISIN invalides rejetes")

td = ('<table><tr><td>Country</td><td>Netherlands</td></tr></table>'
      '<table><tr><td>ISIN Number</td><td>NL0010273215</td></tr></table>')
ok(P.extraire_profil(td) == ("Netherlands", "NL0010273215"), "profil en tableau")
js = '<nav><a>Country</a><a>Market</a></nav><script>d={country:"Germany",isin:"DE0007164600"}</script>'
ok(P.extraire_profil(js) == ("Germany", "DE0007164600"), "profil en donnees embarquees, menu ignore")

ok(P.decider("United Kingdom", "GB00BP6MXD84")[:2] == ("GB", False), "Shell : GB, non PEA")
ok(P.decider("Switzerland", "CH0038863350")[:2] == ("CH", False), "Nestle : CH, non PEA")
ok(P.decider("Italy", "NL0011585146")[:2] == ("IT", True), "Ferrari : siege IT, ISIN NL, PEA")
ok(P.decider("Netherlands", None)[:3] == ("NL", True, "siege NL"), "sans ISIN : repli sur le siege")
ok(P.decider(None, None) == (None, None, None, False), "rien lu : aucune decision")

ok(V.vers_yahoo("BA/ LN") == "BA.L" and V.vers_yahoo("BF/B") == "BF-B", "Bloomberg : barre finale / classe")

print(f"\n{ko} echec(s)")
sys.exit(1 if ko else 0)
