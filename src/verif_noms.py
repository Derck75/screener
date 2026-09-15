"""VERIF_NOMS — identifiants utilises sans jamais etre definis, cote Python.

CE QUE `python -m py_compile` NE FAIT PAS. La compilation valide la SYNTAXE.
Un nom utilise mais jamais defini ne leve qu'a l'EXECUTION du chemin qui le
contient — et si ce chemin est une branche d'erreur ou une etape d'ecriture
rare, il traverse plusieurs deploiements sans se montrer. C'est exactement
ainsi qu'un `import re` manquant a survecu a trois mises en ligne.

L'analyse se fait sur l'ARBRE SYNTAXIQUE, pas sur le texte : pas de faux
positif sur les mots d'une chaine, d'un commentaire ou d'une docstring.

Usage :  python verif_noms.py            (tous les .py du dossier courant)
         python verif_noms.py a.py b.py  (fichiers nommes)

Code de sortie 1 si un defaut est trouve : le workflow s'arrete avant le
deploiement.
"""
import ast
import builtins
import os
import sys


def definis(arbre):
    """Tous les noms que ce module rend disponibles, quelle qu'en soit l'origine."""
    noms = set(dir(builtins))
    for n in ast.walk(arbre):
        if isinstance(n, ast.Import):
            noms |= {(a.asname or a.name.split(".")[0]) for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            noms |= {(a.asname or a.name) for a in n.names}
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            noms.add(n.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            noms.add(n.id)
        elif isinstance(n, ast.arg):
            noms.add(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            noms.add(n.name)
        elif isinstance(n, ast.alias) and n.asname:
            noms.add(n.asname)
        elif isinstance(n, ast.Global):
            noms |= set(n.names)
        elif isinstance(n, ast.Nonlocal):
            noms |= set(n.names)
    # noms speciaux fournis par l'interpreteur
    noms |= {"__file__", "__name__", "__doc__", "__spec__", "__package__",
             "__loader__", "__builtins__", "self", "cls"}
    return noms


def lus(arbre):
    return {n.id for n in ast.walk(arbre)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def verifier(chemin):
    """Rend (ok, message)."""
    try:
        src = open(chemin, encoding="utf-8").read()
    except OSError as e:
        return False, f"illisible — {e}"
    try:
        arbre = ast.parse(src, filename=chemin)
    except SyntaxError as e:
        return False, f"SYNTAXE ligne {e.lineno} — {e.msg}"
    manquants = sorted(lus(arbre) - definis(arbre))
    if manquants:
        return False, "non definis : " + " · ".join(manquants)
    return True, "OK"


def main():
    cibles = sys.argv[1:]
    if not cibles:
        cibles = sorted(f for f in os.listdir(".") if f.endswith(".py"))
    defauts = 0
    for c in cibles:
        ok, msg = verifier(c)
        marque = "" if ok else "🔴 "
        print(f"{os.path.basename(c):<26} {marque}{msg}")
        if not ok:
            defauts += 1
    print(f"\n{len(cibles)} fichier(s), {defauts} en defaut")
    sys.exit(1 if defauts else 0)


if __name__ == "__main__":
    main()
