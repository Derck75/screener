"""Pagination des listes de place : une place mixte servie sur plusieurs pages
de 500 lignes ne doit plus perdre ses societes moyennes (hors reseau)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import univers_intl as U


def table(lignes):
    return ("<table><tr><th>No.</th><th>Symbol</th><th>Company Name</th><th>Market Cap</th></tr>"
            + "".join(f"<tr><td>{i}</td><td>{s}</td><td>{n}</td><td>{c}</td></tr>"
                      for i, (s, n, c) in enumerate(lignes, 1)) + "</table>")


# Page 1 : 500 grandes societes fictives (100 Md a 20 Md) ; page 2 : la moyenne
# qui comptait (5 Md), puis des petites sous le seuil ; page 3 ne doit pas etre lue.
p1 = [(f"G{i}", f"Grande Fictive {i} AG", f"{100 - i * 0.16:.1f}B") for i in range(500)]
p2 = [("MUS", "Musterfirma AG", "5B")] + [(f"P{i}", f"Petite Fictive {i} AG", "0.1B") for i in range(499)]
appels = []
PAGE3 = False


def lire(url, timeout=45):
    appels.append(url)
    if url.endswith("?page=2"):
        return table(p2)
    if url.endswith("?page=3") and PAGE3:
        return table([(f"T{i}", f"Tres Petite Fictive {i} AG", "0.05B") for i in range(10)])
    if "?page=" in url:
        raise AssertionError("page lue au-dela du seuil : " + url)
    return table(p1)


U.lire = lire
U.time.sleep = lambda x: None

ECHECS = []


def ok(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c:
        ECHECS.append(m)


lignes, forme, n, tronque = U.lire_liste("place-fictive", seuil_local=3e8)
syms = {l["sym"] for l in lignes}
ok(n == 2, f"deux pages lues, pas trois ({n})")
ok("MUS" in syms, "la societe moyenne de la page 2 est lue")
ok(not tronque, "liste non tronquee : arret sous le seuil")
ok(len(lignes) == 1000, f"aucun doublon entre pages ({len(lignes)})")

appels.clear()
PAGE3 = True
lignes, _, n, tronque = U.lire_liste("place-fictive", seuil_local=0)
ok(n == 3 and len(lignes) == 1010 and not tronque, "sans seuil : lecture jusqu'a la premiere page incomplete")

# Liste courte : une seule page, aucune requete de plus
U.lire = lambda url, timeout=45: (appels.append(url), table(p1[:40]))[1]
appels.clear()
lignes, _, n, tronque = U.lire_liste("place-courte", seuil_local=3e8)
ok(n == 1 and len(appels) == 1 and not tronque, "liste courte : une seule requete")

sys.exit(1 if ECHECS else 0)
