"""incr13 — LEI depuis la correspondance ISIN-LEI (fichier GLEIF), ecriture differentielle."""
import io, os, sys, json, zipfile, sqlite3
os.environ.update(CF_ACCOUNT="a", CF_DB="b", CF_TOKEN="c")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import commun, incr13_lei as L
ECHECS = 0
def ok(c, m):
    global ECHECS
    print(("✅ " if c else "❌ ") + m); ECHECS += 0 if c else 1
db = sqlite3.connect(":memory:"); db.row_factory = sqlite3.Row
db.execute("CREATE TABLE societe (ticker TEXT PRIMARY KEY, isin TEXT, lei TEXT, lei_le TEXT)")
db.executemany("INSERT INTO societe (ticker, isin, lei) VALUES (?, ?, ?)", [("MC.PA", "FR0000121014", None), ("ASML.AS", "NL0010273215", "724500Y6DUVHQD6OXN27"), ("7203.T", "JP3633400001", None), ("X.PA", "FR0000000000", None)])
def faux_d1(sql, params=None, lignes=0, table=None):
    if sql.startswith("PRAGMA"):
        return [{"results": [dict(name=r[1]) for r in db.execute("PRAGMA table_info(societe)")]}]
    if sql.startswith("ALTER"):
        db.execute(sql); return [{"results": []}]
    cur = db.execute(sql, params or [])
    return [{"results": [dict(r) for r in cur.fetchall()] if cur.description else []}]
for m in (commun, L): m.d1 = faux_d1
L.journal = lambda *a, **k: journaux.append(a)
journaux = []
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("isin-lei.csv", "LEI,ISIN\nIOHHWNUXTU8Y8Y3SJM67,FR0000121014\n724500Y6DUVHQD6OXN27,NL0010273215\n5493006W3QUS5LMH6R84,JP3633400001\n")
class R:
    status_code = 200; content = buf.getvalue()
    def raise_for_status(self): pass
L.requests.get = lambda *a, **k: R()
sys.argv = ["incr13"]
L.main()
lei = {r["ticker"]: r["lei"] for r in db.execute("SELECT ticker, lei FROM societe")}
ok(lei["MC.PA"] == "IOHHWNUXTU8Y8Y3SJM67" and lei["7203.T"] == "5493006W3QUS5LMH6R84", "LEI retrouves par ISIN depuis le fichier (Europe et Japon)")
ok(lei["X.PA"] is None, "ISIN absent du fichier : rien d'invente")
ok(journaux and journaux[-1][2] == 2 and "Europe 2/3" in journaux[-1][4], "ecriture differentielle (ASML deja connu non reecrit), couverture Europe journalisee")
sys.exit(1 if ECHECS else 0)
