"""ecrire_differentiel(par=...) : relit seulement les lignes des tickers ecrits,
par l'index, et rend le meme differentiel que la relecture integrale."""
import os, sys, sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
for _k in ("CF_ACCOUNT", "CF_DB", "CF_TOKEN"): os.environ.setdefault(_k, "x")
import commun

db = sqlite3.connect(":memory:"); db.row_factory = sqlite3.Row
db.execute("CREATE TABLE comptes2 (ticker TEXT, exercice INTEGER, revenue REAL, ebit REAL, PRIMARY KEY (ticker, exercice))")
for t in ["AAA", "BBB", "CCC", "DDD"]:
    for a in range(2015, 2026):
        db.execute("INSERT INTO comptes2 VALUES (?, ?, ?, ?)", (t, a, a * 10.0, a * 1.0))
lues, sqls = [], []
def faux_d1(sql, params=None, lignes=0, table=None):
    sqls.append(sql)
    r = [dict(x) for x in db.execute(sql, params or [])]
    lues.append(len(r))
    return [{"results": r}]
commun.d1 = faux_d1
rangs = {f"BBB|{a}": {"revenue": a * 10.0, "ebit": a * 1.0} for a in range(2015, 2026)}
rangs["BBB|2025"]["ebit"] = 99.0          # une ligne modifiee
rangs["EEE|2025"] = {"revenue": 1.0, "ebit": 1.0}   # une ligne nouvelle
cible = commun.ecrire_differentiel("comptes2", "ticker || '|' || exercice", rangs, ["revenue", "ebit"],
                                    par=("ticker", lambda k: k.rsplit("|", 1)[0]))
ok = set(cible) == {"BBB|2025", "EEE|2025"}
print(("✅" if ok else "❌") + f" differentiel cible : {sorted(cible)}")
ok2 = sum(lues) == 11 and all("WHERE ticker IN" in q for q in sqls)
print(("✅" if ok2 else "❌") + f" lignes relues : {sum(lues)} (11 attendues, au lieu de 44)")
lues.clear(); sqls.clear()
plein = commun.ecrire_differentiel("comptes2", "ticker || '|' || exercice", rangs, ["revenue", "ebit"])
ok3 = set(plein) == set(cible)
print(("✅" if ok3 else "❌") + " meme resultat que la relecture integrale")
sys.exit(0 if ok and ok2 and ok3 else 1)
