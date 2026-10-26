"""Garde : budget D1 du jour verifie avant toute chaine, forcee comprise (27/10/2026)."""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import garde as G

ok = 0
def verifier(c, m):
    global ok
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: sys.exit(1)
    ok += 1

def lancer(deja, *args):
    G.ecrites_aujourdhui = lambda: deja
    sortie = tempfile.NamedTemporaryFile("r", delete=False).name
    os.environ["GITHUB_OUTPUT"] = sortie
    os.environ["GITHUB_EVENT_NAME"] = "workflow_dispatch"
    sys.argv = ["garde.py", "--etape", "incr7_valorisation", "--heures", "12"] + list(args)
    G.main()
    return open(sortie).read()

verifier("faire=true" in lancer(13800, "--forcer", "--budget-chaine", "30000"), "journee normale (~37 000) + une chaine : execute")
verifier("faire=false" in lancer(47000, "--forcer", "--budget-chaine", "30000"), "le 26/10 apres quatre chaines (~127 000) : refuse, meme force")
verifier("faire=true" in lancer(None, "--forcer", "--budget-chaine", "30000"), "comptage illisible : on n'empeche pas la chaine")
verifier("faire=true" in lancer(999999, "--forcer"), "sans --budget-chaine : aucun controle")
print(f"\n{ok} reussis")
