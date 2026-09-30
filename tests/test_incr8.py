"""Comptes internationaux hors reseau : codes de places, marques datees, lignes VanEck."""
import os, sys, re, json
os.environ.update(CF_ACCOUNT="a", CF_DB="b", CF_TOKEN="c", PAUSE_SA="0")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import commun, incr8_comptes_intl as I8

appels = []
def faux_d1(sql, params=None, lignes=0, table=None):
    appels.append((sql, params))
    if sql.startswith("PRAGMA"):
        return [{"results": [{"name": n} for n in ["ticker", "origine", "cik", "comptes_maj"]]}]
    if sql.startswith("SELECT s.ticker"):
        return [{"results": [{"ticker": t, "n": 0} for t in ["RACE.MI", "600519.SS", "BA.L", "EQNR.OL"]]}]
    if sql.startswith("SELECT COUNT(*) AS n FROM societe"):
        return [{"results": [{"n": 0}]}]
    if sql.startswith("SELECT COALESCE"):
        return [{"results": [{"n": 0}]}]
    if sql.startswith("SELECT ticker || '|'"):
        return [{"results": []}]
    if sql.startswith("UPDATE societe SET origine = REPLACE"):
        return [{"results": [], "meta": {"changes": 410}}]
    return [{"results": [], "meta": {"changes": 0}}]
for m in (commun, I8):
    m.d1 = faux_d1
I8.journal = lambda *a, **k: None
commun.journal = lambda *a, **k: None

PAGE = ('<table><tr><th>Fiscal Year</th><th>FY 2024</th><th>FY 2023</th></tr>'
        '<tr><td>Revenue</td><td>6,677</td><td>5,970</td></tr>'
        '<tr><td>Operating Income</td><td>1,888</td><td>1,617</td></tr></table>')
urls = []
def faux_lire(url, s=None):
    urls.append(url)
    if "/bit/RACE/" in url: return (PAGE, 200)
    if "/lon/BA/" in url: return (None, 404)
    if "/osl/EQNR/" in url: return (None, 503)
    raise AssertionError("url inattendue " + url)
I8.lire_code = faux_lire
sys.argv = ["incr8", "--taille", "10"]
I8.main()

ECHECS = []
def ok(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c:
        ECHECS.append(m)
print()
ok(any("/quote/bit/RACE/financials/" in u for u in urls), "Milan demandée sous « bit »")
ok(not any("600519" in u for u in urls), "place sans code (Shanghai) ignorée, aucun appel")
maj = [(q, p) for q, p in appels if q.startswith("UPDATE societe SET sans_sa_le")]
ok(any(p[1] == "404" and "BA.L" in p for q, p in maj), "BAE : trois 404 → motif 404 daté")
ok(any(p[1] == "incident" and "EQNR.OL" in p for q, p in maj), "Equinor : 503 → motif incident (retente à 2 jours)")
ok(any("comptes2" in q and "RACE.MI" in q for q, p in appels), "Ferrari : comptes écrits")
ok(any(q.startswith("UPDATE societe SET comptes_maj = ?, sans_sa_le = NULL") and "RACE.MI" in p for q, p in appels), "Ferrari : marque effacée à la lecture réussie")
ok(any(q.startswith("UPDATE societe SET origine = REPLACE") for q, p in appels), "anciennes marques sans_sa effacées à la migration")
sel = next(q for q, p in appels if q.startswith("SELECT s.ticker"))
ok("vaneck" in sel and "secondaire" in sel and "sans_sa_motif" in sel, "file : lignes VanEck incluses, secondaires exclues, retente datée")
sys.exit(1 if ECHECS else 0)
