"""Discord hors reseau : rattrapage des amorcages, canaris, journal distinct."""
import os, sys, sqlite3
os.environ.update(CF_ACCOUNT="a", CF_DB="b", CF_TOKEN="c")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import commun, incr10_discord as D
db = sqlite3.connect(":memory:"); db.row_factory = sqlite3.Row
db.executescript(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sql", "001_schema.sql")).read())
for c in ["origine TEXT", "statut_serveur TEXT"]:
    try: db.execute(f"ALTER TABLE societe ADD COLUMN {c}")
    except Exception: pass
for c in ["profil_type TEXT", "drapeaux TEXT", "croissance_implicite REAL", "croissance_demontree REAL", "taux_obstacle REAL",
          "capi_eur REAL", "roic_organique REAL", "part_tresorerie REAL", "concordance REAL", "eva_sur_cours REAL", "score_moat_max INTEGER",
          "ca_cagr5 REAL", "fcf_cagr5 REAL", "moat_propre TEXT"]:
    try: db.execute(f"ALTER TABLE metriques ADD COLUMN {c}")
    except Exception: pass
try: db.execute("ALTER TABLE runs ADD COLUMN detail TEXT")
except Exception: pass
def d1(sql, params=None, lignes=0, table=None):
    cur = db.execute(sql, params or [])
    return [{"results": [dict(r) for r in cur.fetchall()], "meta": {"changes": cur.rowcount}}]
commun.d1 = D.d1 = d1
journaux = []
D.journal = lambda *a, **k: journaux.append(a)
D.migrer = lambda *a, **k: []
# candidates
for i, (t, pea) in enumerate([("AAA", 1), ("BBB", 0), ("CCC", 1), ("DDD", 0)]):
    db.execute("INSERT INTO societe (ticker, nom, eligible_pea, pays_siege) VALUES (?,?,?,?)", (t, "Soc " + t, pea, "FR"))
    db.execute("""INSERT INTO metriques (ticker, exclusion, profil_type, roic_median, spread_median, ca_cagr, fcf_cagr, n_ex_roic_sup_seuil,
      n_ex_total, croissance_implicite, croissance_demontree, epv_sur_cours, capi_eur, score_moat, score_moat_max, cours, taux_obstacle)
      VALUES (?,NULL,'industriel',25,16,10,10,8,8,?,12,0.7,1e9,5,6,100,10)""", (t, 5 if t != "DDD" else 30))
for t in ("AAA", "BBB", "DDD", "ZZZ"):
    db.execute("INSERT INTO notifications (ticker, canal) VALUES (?, 'amorcage')", (t,))
# runs : incr11 en echec 3 fois apres un OK
for st in ["OK", "PANNE", "PANNE", "PANNE"]:
    db.execute("INSERT INTO runs (debut, etape, statut, canari) VALUES (datetime('now'), 'incr11_secteur_intl', ?, 'X')", (st,))
db.execute("INSERT INTO runs (debut, etape, statut, canari) VALUES (strftime('%Y-%m-%dT%H:%M:%SZ','now'), 'incr5_metriques', 'OK', 'VERT')")
D.WEBHOOK = ""
envois = []
D.envoyer = (lambda f: (lambda t, c, k: (envois.append((t, c)), True)[1]))(D.envoyer)
sys.argv = ["x", "--rattrapage"]
D.main()
ECHECS = []
def ok(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c:
        ECHECS.append(m)
t, c = envois[-1]
ok("rattrapage" in t and "AAA" in c and "BBB" in c and "DDD" not in c, "rattrapage : seules les encore-candidates partent (DDD, N trop haut, exclue)")
ok("4 candidates enregistrées" in c and "2 le sont encore" in c, "le message dit combien ne passent plus")
can = dict(db.execute("SELECT ticker, canal FROM notifications").fetchall())
ok(can.get("AAA") == "rattrapage" and "DDD" not in can and "ZZZ" not in can, f"envoyees marquees, perimees retirees {can}")
ok(journaux[-1][1] == "incr10_discord_rattrapage", "etape de journal distincte")
s = D.sante()
ok("incr11_secteur_intl 🔴 3 échec(s)" in s, "sante : incr11 signale en echec 3 fois de suite")
sys.argv = ["x", "--exception-seule"]; D.main()
ok(journaux[-1][1] == "incr10_discord_exception", "exception journalisee a part (garde hebdo intacte)")
sys.exit(1 if ECHECS else 0)
