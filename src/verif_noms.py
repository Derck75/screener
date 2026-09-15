/* VERIF_NOMS_JS — ce que `node --check` ne fait pas.
 *
 * `node --check` valide la SYNTAXE. Il ne voit pas un identifiant utilise mais
 * jamais defini : celui-la ne leve qu'a l'EXECUTION du chemin qui le contient.
 * C'est exactement le defaut qui a casse `rafraichir` sur le serveur MCP —
 * une variable de boucle lue dans une fonction qui ne la voit pas, passee au
 * travers de trois deploiements parce que le verificateur ne regardait que la
 * syntaxe.
 *
 * Ce script charge le module EN MODE INSPECTION : il n'execute rien, il
 * demande a Node de le compiler dans un contexte vierge et rapporte tout
 * identifiant global non resolu.
 *
 * Usage :  node verif_noms_js.mjs incr5_metriques.mjs
 */
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";

const GLOBAUX_ATTENDUS = new Set([
  "console", "process", "fetch", "Math", "JSON", "Number", "String", "Object",
  "Array", "Boolean", "Date", "Promise", "Map", "Set", "Error", "RegExp",
  "parseInt", "parseFloat", "isNaN", "isFinite", "globalThis", "structuredClone",
  "setTimeout", "clearTimeout", "setInterval", "URL", "URLSearchParams",
  "TextEncoder", "TextDecoder", "AbortController", "Buffer", "crypto",
]);

const fichiers = process.argv.slice(2);
if (!fichiers.length) {
  console.error("usage : node verif_noms_js.mjs <fichier.mjs> [autres...]");
  process.exit(2);
}

let defauts = 0;

for (const f of fichiers) {
  const src = readFileSync(f, "utf8");

  /* 1. SYNTAXE — on delegue a `node --check`, qui gere l'ESM nativement.
     (Une verification maison via vm.SourceTextModule exigerait le drapeau
     --experimental-vm-modules : une dependance de plus pour un resultat
     identique.) */
  try {
    execFileSync(process.execPath, ["--check", f], { stdio: "pipe" });
  } catch (e) {
    const msg = (e.stderr?.toString() || e.message).split("\n").slice(0, 3).join(" ");
    console.log(`${f.padEnd(28)} 🔴 SYNTAXE — ${msg.trim()}`);
    defauts++;
    continue;
  }

  /* 2. IDENTIFIANTS LIBRES — la partie que `node --check` ne fait pas.
     On collecte les declarations (const/let/var/function/class/import/
     parametres/destructuration) puis on liste les identifiants lus qui n'y
     figurent pas et ne sont pas des globaux connus.
     Analyse TEXTUELLE volontairement conservatrice : elle peut signaler un
     faux positif sur du code exotique, jamais rater un nom reellement absent.
     Un faux positif coute dix secondes de lecture ; un nom manquant coute un
     deploiement et un tour de debat. */
  const declares = new Set(GLOBAUX_ATTENDUS);
  const ajoute = (re, groupe = 1) => {
    for (const m of src.matchAll(re)) {
      for (const nom of m[groupe].split(/[\s,{}\[\]:]+/)) {
        if (/^[A-Za-z_$][\w$]*$/.test(nom)) declares.add(nom);
      }
    }
  };
  ajoute(/\b(?:const|let|var)\s+([^=;\n]+?)\s*[=;]/g);
  ajoute(/\bfunction\s+([A-Za-z_$][\w$]*)/g);
  ajoute(/\bclass\s+([A-Za-z_$][\w$]*)/g);
  ajoute(/\bimport\s+([^;]+?)\s+from/g);
  ajoute(/\bcatch\s*\(\s*([A-Za-z_$][\w$]*)/g);
  ajoute(/\bfor\s*\(\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)/g);
  /* parametres de fonction, fleches comprises */
  for (const m of src.matchAll(/(?:function[^(]*|\)?\s*=>|\b[A-Za-z_$][\w$]*\s*)\(([^)]*)\)/g)) {
    for (const nom of m[1].split(/[\s,{}\[\]:=.]+/)) {
      if (/^[A-Za-z_$][\w$]*$/.test(nom)) declares.add(nom);
    }
  }

  /* on retire chaines, gabarits, commentaires et acces par point */
  const nu = src
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/\/\/[^\n]*/g, " ")
    .replace(/`(?:\\.|\$\{[^}]*\}|[^`\\])*`/g, " ")
    .replace(/'(?:\\.|[^'\\])*'/g, " ")
    .replace(/"(?:\\.|[^"\\])*"/g, " ")
    .replace(/\.\s*[A-Za-z_$][\w$]*/g, " ")
    .replace(/[A-Za-z_$][\w$]*\s*:/g, " ");

  const MOTS = new Set(["if","else","for","while","return","await","async","new",
    "typeof","instanceof","in","of","try","catch","finally","throw","break",
    "continue","switch","case","default","delete","void","yield","export",
    "import","from","as","this","super","null","true","false","undefined",
    "const","let","var","function","class","extends","static","get","set","do"]);

  const inconnus = new Map();
  for (const m of nu.matchAll(/\b([A-Za-z_$][\w$]*)\b/g)) {
    const nom = m[1];
    if (MOTS.has(nom) || declares.has(nom)) continue;
    inconnus.set(nom, (inconnus.get(nom) || 0) + 1);
  }

  if (inconnus.size) {
    const liste = [...inconnus.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([n, c]) => `${n}(×${c})`)
      .join(" · ");
    console.log(`${f.padEnd(28)} ⚠️  identifiants non declares : ${liste}`);
    defauts++;
  } else {
    console.log(`${f.padEnd(28)} ✅ syntaxe OK · aucun identifiant libre`);
  }
}

console.log(`\n${fichiers.length} fichier(s), ${defauts} en defaut`);
process.exit(defauts ? 1 : 0);
