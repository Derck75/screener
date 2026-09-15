"""EMPREINTE — mettre fin au débat « quel fichier tourne ? ».

Une version annoncée a la main peut etre fausse : il suffit d'oublier de
l'incrementer. Une EMPREINTE, elle, est calculee sur le contenu reel du
fichier qui s'execute. Elle ne peut pas mentir.

Usage, en tete de CHAQUE script, juste apres les imports :

    from empreinte import banniere
    banniere(__file__, "incr5_metriques", "v4")

Sortie dans le log :

    incr5_metriques v4 · empreinte 3f9a1c2e · 412 lignes · 2026-09-15T14:31:02Z

CE QUE CA REGLE. Vous produisez le fichier, vous notez son empreinte. Le log
affiche l'empreinte de ce qui tourne reellement. Elles concordent ou non — et
la question « le fichier deploye est-il le corrige ? » cesse d'etre une
opinion pour devenir une comparaison de huit caracteres.
"""
import hashlib
import datetime
import os


def empreinte(chemin):
    """SHA-256 du contenu reel, 8 premiers caracteres."""
    with open(chemin, "rb") as f:
        brut = f.read()
    return hashlib.sha256(brut).hexdigest()[:8], brut.count(b"\n") + 1


def banniere(chemin, nom, version):
    emp, lignes = empreinte(chemin)
    ts = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    print(f"{nom} {version} · empreinte {emp} · {lignes} lignes · {ts}", flush=True)
    return emp


if __name__ == "__main__":
    import sys
    cibles = sys.argv[1:] or sorted(
        f for f in os.listdir(".") if f.endswith((".py", ".mjs", ".js"))
    )
    print(f"{'FICHIER':<28} {'EMPREINTE':<10} LIGNES")
    for c in cibles:
        try:
            e, n = empreinte(c)
            print(f"{c:<28} {e:<10} {n}")
        except OSError as err:
            print(f"{c:<28} illisible — {err}")
