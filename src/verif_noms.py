#!/usr/bin/env python3
"""
VERIFICATEUR DE NOMS — aucune execution, aucun reseau, aucune ecriture.

Cherche tout nom utilise mais jamais defini : import oublie, variable creee
dans une autre fonction, coquille. Ces erreurs ne se voient ni a la lecture
ni au controle de syntaxe — elles n'apparaissent qu'a l'execution, et
seulement si la ligne fautive est atteinte. C'est ainsi qu'un `import re`
absent a survecu a une livraison : le fichier compilait parfaitement.

A lancer sur TOUS les scripts avant chaque deploiement.
"""
import ast, builtins, sys

def noms_non_definis(chemin):
    src = open(chemin).read()
    arbre = ast.parse(src)
    definis = set(dir(builtins)) | {"__name__", "__file__"}
    # imports, def, class, assignations de niveau module
    for n in ast.walk(arbre):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                definis.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            definis.add(n.name)
            definis.update(a.arg for a in n.args.args) if hasattr(n, "args") else None
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store,)):
            definis.add(n.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            definis.add(n.name)
        elif isinstance(n, (ast.comprehension,)):
            for t in ast.walk(n.target):
                if isinstance(t, ast.Name):
                    definis.add(t.id)
        elif isinstance(n, ast.arg):
            definis.add(n.arg)
    manquants = {}
    for n in ast.walk(arbre):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in definis:
            manquants.setdefault(n.id, n.lineno)
    return manquants

cibles = sys.argv[1:] or sorted(
    f for f in __import__("os").listdir(".")
    if f.endswith(".py") and f != "verif_noms.py")
faux = 0
for f in cibles:
    try:
        m = noms_non_definis(f)
    except SyntaxError as e:
        print(f"{f:26} SYNTAXE l.{e.lineno} : {e.msg}")
        faux += 1
        continue
    if m:
        faux += 1
    print(f"{f:26} " + ("OK" if not m else
          "MANQUE " + ", ".join(f"{k} (l.{v})" for k, v in sorted(m.items()))))

print(f"\n{len(cibles)} fichiers, {faux} en defaut")
sys.exit(1 if faux else 0)
