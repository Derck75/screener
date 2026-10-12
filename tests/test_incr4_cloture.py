"""Bilan US cale sur la cloture reelle de l'exercice (audit M2), hors reseau."""
import os, sys
os.environ.update(CF_ACCOUNT="a", CF_DB="b", CF_TOKEN="c")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import commun, incr4_comptes as I4

ECHECS = []


def ok(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c:
        ECHECS.append(m)


class Sonde:
    def __init__(self):
        self.compteurs = {}

    def compte(self, cle, n=1):
        self.compteurs[cle] = self.compteurs.get(cle, 0) + n

    def erreur(self, *a):
        pass


# Trois deposants fictifs : 1 clot en juin, 2 en decembre, 3 sans flux annuel.
FRAMES = {
    ("Revenues", "CY2023"): {1: (100.0, "2023-06-30"), 2: (50.0, "2023-12-31")},
    ("Assets", "CY2023Q4I"): {1: (999.0, "2023-12-31"), 2: (500.0, "2023-12-31"), 3: (70.0, "2023-12-31")},
    ("Assets", "CY2023Q2I"): {1: (800.0, "2023-06-30"), 2: (480.0, "2023-06-30")},
}
lus = []


def lire(tag, unite, periode, s):
    lus.append((tag, periode))
    return FRAMES.get((tag, periode))


I4.DUREE = {"revenue": ["Revenues"]}
I4.INSTANT = {"assets": ["Assets"]}
par_cik = {1: ("AAA", None), 2: ("BBB", None), 3: ("CCC", None)}
donnees, clotures, s = {}, {}, Sonde()
I4.collecter_annee(2023, par_cik, donnees, clotures, s, lire=lire, pause=0)

ok(donnees[1][2023]["assets"] == 800.0, "cloture en juin : bilan du 30/06, pas du 31/12")
ok(donnees[2][2023]["assets"] == 500.0, "cloture en decembre : bilan du 31/12 inchange")
ok(donnees[3][2023]["assets"] == 70.0, "cloture inconnue : repli sur le 31/12")
ok(("Assets", "CY2023Q2I") in lus, "frame du deuxieme trimestre lue")
ok(s.compteurs.get("bilans_hors_decembre") == 1, "un bilan hors decembre compte")

ok(I4.periode_instant("2023-09-30", 2023) == "CY2023Q3I", "septembre -> T3")
ok(I4.periode_instant("2024-01-28", 2023) == "CY2023Q4I", "exercice clos fin janvier -> T4 de l'annee civile precedente (verifie SEC : NVIDIA)")
ok(I4.periode_instant("2026-01-25", 2025) == "CY2025Q4I", "NVIDIA FY2026 (25/01/2026) -> CY2025Q4I")
ok(I4.periode_instant("2025-04-27", 2025) == "CY2025Q1I", "fin avril -> T1 (31/03 le plus proche)")
ok(I4.periode_instant("2025-11-02", 2025) == "CY2025Q3I", "debut novembre -> T3 (30/09 a 33 j, 31/12 a 59 j)")
ok(I4.periode_instant("2025-06-27", 2025) == "CY2025Q2I" and I4.periode_instant("2025-09-27", 2025) == "CY2025Q3I", "fins de semaine pres de juin et septembre")
ok(I4.periode_instant(None, 2023) == "CY2023Q4I", "sans cloture -> 31/12")
ok(I4.proche("2023-07-01", "2023-06-30") and not I4.proche("2023-12-31", "2023-06-30"),
   "un instant a six mois de la cloture est refuse")

# Deposant qui clot fin janvier : flux CY2023 (fin 2024-01-28), bilan dans CY2023Q4I.
FRAMES[("Revenues", "CY2023")][4] = (60.0, "2024-01-28")
FRAMES[("Assets", "CY2023Q4I")][4] = (65.0, "2024-01-28")
FRAMES[("Assets", "CY2024Q1I")] = {4: (77.0, "2024-04-28")}
par_cik[4] = ("NVDA", None)
donnees, clotures, s = {}, {}, Sonde()
I4.collecter_annee(2023, par_cik, donnees, clotures, s, lire=lire, pause=0)
ok(donnees[4][2023].get("assets") == 65.0, "cloture fin janvier : bilan du 28/01 lu (et non vide)")

sys.exit(1 if ECHECS else 0)
