"""Prix : Nasdaq primaire aux Etats-Unis, recoupement, purge par anciennete (27/10/2026)."""
import os, sys
os.environ.update(CF_ACCOUNT="a", CF_DB="b", CF_TOKEN="c")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import incr6_prix as P

ok = 0
def verifier(c, m):
    global ok
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: sys.exit(1)
    ok += 1

rows = [{"symbol": "AAPL", "lastsale": "$347.725", "marketCap": "5074761240500.00"},
        {"symbol": "BRK/B", "lastsale": "$542.19", "marketCap": "1196239962242.00"},
        {"symbol": "JPM^D", "lastsale": "$25.10", "marketCap": ""},
        {"symbol": "XYZ", "lastsale": "NA", "marketCap": ""},
        {"symbol": "KO ", "lastsale": "$92.71", "marketCap": "NA"}]
nq = P.lire_nasdaq(rows)
verifier(nq["AAPL"] == (347.725, 5074761240500.0), "cours et capitalisation lus, symbole et dollar nettoyes")
verifier("BRK-B" in nq and "JPM^D" not in nq and "JPM-D" not in nq, "classe d'actions en tiret, preferentielles ignorees")
verifier("XYZ" not in nq and nq["KO"] == (92.71, None), "cours absent ignore ; capitalisation NA = None")

sa = {"AAPL": (347.0, None), "BRK-B": (540.0, None), "KO": (92.7, None)}
n, med, part = P.recouper(nq, sa)
verifier(n == 3 and med < 0.01 and part == 0, f"recoupement : 3 communs, ecart median {med:.3%}")
n, med, part = P.recouper({k: (v[0] * 100, v[1]) for k, v in nq.items()}, sa)
verifier(med > 50, "erreur d'unite (cents) : ecart median enorme, Nasdaq serait ecarte")

# purge : seuls les cours absents de TOUTES les sources depuis 10 jours sont effaces
verifier(P.PURGE_JOURS == 10, "purge apres 10 jours sans aucune source")

# lire_liste : 404 = fin de pagination, pas de nouvel essai
import urllib.error
essais = []
class S:
    def erreur(self, *a): pass
def faux_urlopen(req, timeout=None):
    essais.append(1)
    raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
P.urllib.request.urlopen = faux_urlopen
P.time.sleep = lambda s: None
verifier(P.lire_liste("https://x/list/nyse-stocks/?page=5", S()) is None and len(essais) == 1, "404 : un seul essai")
essais.clear()
def faux2(req, timeout=None):
    essais.append(1)
    if len(essais) < 3: raise TimeoutError("lent")
    class R:
        def __enter__(s): return s
        def __exit__(s, *a): return False
        def read(s): return b"<html>ok</html>"
    return R()
P.urllib.request.urlopen = faux2
verifier(P.lire_liste("https://x/list/nyse-stocks/", S()) == "<html>ok</html>" and len(essais) == 3, "deux refus passagers puis succes : liste lue")
cib = [f"F{i}.PA" for i in range(30)] + [f"U{i}" for i in range(100)] + ["X.AX"]
fa = P.places_faibles(cib, {f"U{i}" for i in range(95)} | {f"F{i}.PA" for i in range(10)})
verifier(fa == [(".PA", 10 / 30)], f"Paris cotee a 33 % : nommee, US 95 % et petite place ignorees ({fa})")
print(f"\n{ok} reussis")
