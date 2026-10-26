"""Prix : purge des cours perimes gardee par la completude de la place (26/10/2026)."""
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

cibles = [f"US{i}" for i in range(100)] + [f"F{i}.PA" for i in range(20)]
# NYSE refusee : 40 % des americains seulement
table = {f"US{i}": (1.0, None) for i in range(40)}
table.update({f"F{i}.PA": (1.0, None) for i in range(19)})
places = P.places_purgeables(table, cibles, [("nyse-stocks", "")])
verifier("" not in places, "une liste americaine illisible : aucun cours americain efface")
verifier(".PA" in places, "Paris complete (19/20) : la radiee peut etre effacee")
places = P.places_purgeables(table, cibles, [])
verifier("" not in places, "listes lues mais 40 % de couverture : aucune purge")
table.update({f"US{i}": (1.0, None) for i in range(40, 97)})
verifier("" in P.places_purgeables(table, cibles, []), "97 % de couverture, toutes listes lues : purge permise")

# lire_liste : trois essais avant d'abandonner
appels = []
class S:
    def erreur(self, *a): appels.append(("erreur",) + a)
def faux_urlopen(req, timeout=None):
    appels.append("essai")
    if len([a for a in appels if a == "essai"]) < 3:
        raise TimeoutError("lent")
    class R:
        def __enter__(s): return s
        def __exit__(s, *a): return False
        def read(s): return b"<html>ok</html>"
    return R()
P.urllib.request.urlopen = faux_urlopen
P.time.sleep = lambda s: None
verifier(P.lire_liste("https://x/list/nyse-stocks/", S()) == "<html>ok</html>" and appels.count("essai") == 3, "deux refus passagers puis succes : liste lue")
print(f"\n{ok} reussis")
