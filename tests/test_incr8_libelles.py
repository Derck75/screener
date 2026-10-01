"""Priorite des libelles StockAnalysis : le libelle de reference gagne, quel que
soit l'ordre des lignes (hors reseau)."""
import os, sys
os.environ.update(CF_ACCOUNT="a", CF_DB="b", CF_TOKEN="c", PAUSE_SA="0")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import incr8_comptes_intl as I8

ECHECS = []


def ok(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c:
        ECHECS.append(m)


def page(lignes):
    return ("<p>Financials in millions EUR</p><table><tr><th>Fiscal Year</th><th>FY 2024</th></tr>"
            + "".join(f"<tr><td>{l}</td><td>{v}</td></tr>" for l, v in lignes) + "</table>")


# Ordre des lignes tel que servi : la variante large APRES la reference.
res = I8.extraire(page([("Operating Income", "100"), ("EBIT", "130"),
                        ("Shares Outstanding (Diluted)", "50"), ("Shares Outstanding (Basic)", "48")]))
bil = I8.extraire(page([("Cash &amp; Equivalents", "20"), ("Cash &amp; Short-Term Investments", "35"),
                        ("Shareholders' Equity", "300"), ("Total Equity", "320")]))
ok(res["ebit"][2024] == 100e6, "EBIT : le resultat operationnel gagne sur la ligne EBIT")
ok(res["shares"][2024] == 50e6, "actions : diluees, pas de base")
ok(bil["cash"][2024] == 20e6, "tresorerie : hors placements a court terme")
ok(bil["equity"][2024] == 300e6, "capitaux propres : part du groupe, hors minoritaires")

# Ordre inverse : le resultat ne doit pas dependre de l'ordre des lignes.
inv = I8.extraire(page([("Total Equity", "320"), ("Shareholders' Equity", "300")]))
ok(inv["equity"][2024] == 300e6, "ordre des lignes inverse : meme resultat")

sys.exit(1 if ECHECS else 0)
