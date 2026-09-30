/* GENERER_NOYAU — produit src/noyau.js depuis le code du worker.
 *
 * POURQUOI UN GENERATEUR. Le noyau est la garantie qu'il n'existe qu'UNE
 * definition du ROIC, du capital investi, de l'EPV, du profil de societe et du
 * moteur DCF. Recopie a la main, il avait deja derive : l'en-tete visait
 * worker-160 quand le serveur tournait en w176, et le moteur deux phases de
 * `dcf` n'y figurait pas — d'ou un « seuil N » calcule autrement que par le
 * serveur (audit C2).
 *
 * CE QU'IL FAIT. Il analyse le worker (acorn), part des RACINES ci-dessous et
 * inclut, par fermeture transitive, toutes les declarations de premier niveau
 * qu'elles referencent. Il REFUSE d'ecrire si une declaration incluse touche
 * au reseau, au KV ou a l'environnement : le noyau doit rester pur.
 *
 * Usage : node outils/generer_noyau.mjs <worker.js> [src/noyau.js]
 * Dependance de developpement : acorn (npm i acorn), ou ACORN=<chemin>.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { createHash } from "node:crypto";

const require = createRequire(import.meta.url);
const acorn = (() => {
  for (const p of [process.env.ACORN, "acorn", "/opt/node-tools/node_modules/acorn"].filter(Boolean)) {
    try { return require(p); } catch { /* suivant */ }
  }
  throw new Error("acorn introuvable : npm i acorn, ou ACORN=<chemin du module>");
})();

export const RACINES = [
  "derives", "roicRetenu", "profilSociete", "moatPropre", "preuvesMoat",
  "ancrageEPV", "ancrageEVA", "trajectoireROIC",
  "valeurDCF", "croissanceImplicite", "valeurBPA", "croissanceImpliciteBPA",
  "mediane", "cagr", "pct", "num", "millions", "TERMINAL_G", "EPV_TAUX_OBSTACLE",
];

// Signatures d'impurete : un noyau qui les contient n'est plus un noyau.
const IMPUR = /\bfetch\s*\(|\bkv\.(get|put|delete|list)\b|\benv\.[A-Z_]+|\bawait\b|\bcaches\.|\bcrypto\.subtle\b/;

export function generer(src, nomSource) {
  const ast = acorn.parse(src, { ecmaVersion: "latest", sourceType: "module" });
  const decl = new Map();                     // nom -> { node, debut, fin }
  for (const n of ast.body) {
    if (n.type === "FunctionDeclaration" && n.id) decl.set(n.id.name, n);
    else if (n.type === "ClassDeclaration" && n.id) decl.set(n.id.name, n);
    else if (n.type === "VariableDeclaration")
      for (const d of n.declarations) if (d.id.type === "Identifier") decl.set(d.id.name, n);
  }
  const refs = node => {
    const out = new Set();
    const voir = (x, parent, cle) => {
      if (!x || typeof x.type !== "string") return;
      if (x.type === "Identifier") {
        const propriete = parent && ((parent.type === "MemberExpression" && cle === "property" && !parent.computed)
          || (parent.type === "Property" && cle === "key" && !parent.computed));
        if (!propriete) out.add(x.name);
      }
      for (const [k, v] of Object.entries(x)) {
        if (Array.isArray(v)) v.forEach(e => voir(e, x, k));
        else if (v && typeof v.type === "string") voir(v, x, k);
      }
    };
    voir(node, null, null);
    return out;
  };
  const inclus = new Set(), file = [...RACINES];
  const manquantes = RACINES.filter(r => !decl.has(r));
  if (manquantes.length) throw new Error("racines absentes du worker : " + manquantes.join(", "));
  while (file.length) {
    const nom = file.pop();
    const n = decl.get(nom);
    if (!n || inclus.has(n)) continue;
    inclus.add(n);
    for (const r of refs(n)) if (decl.has(r) && !inclus.has(decl.get(r))) file.push(r);
  }
  const noeuds = [...inclus].sort((a, b) => a.start - b.start);
  const impurs = noeuds.filter(n => IMPUR.test(src.slice(n.start, n.end)))
    .map(n => (n.id && n.id.name) || n.declarations.map(d => d.id.name).join(","));
  if (impurs.length) throw new Error("declarations impures refusees : " + impurs.join(", "));
  const corps = noeuds.map(n => src.slice(n.start, n.end)).join("\n\n");
  const version = (/const WORKER_VERSION = "([^"]+)"/.exec(src) || [])[1] || "inconnue";
  const emp = createHash("sha256").update(src).digest("hex").slice(0, 12);
  const noms = noeuds.flatMap(n => n.type === "VariableDeclaration" ? n.declarations.map(d => d.id.name) : [n.id.name]);
  const entete = `/* ════════════════════════════════════════════════════════════════════════
   NOYAU — fonctions de calcul PURES, extraites du worker sans modification.

   NE PAS EDITER A LA MAIN. Genere par outils/generer_noyau.mjs : toute
   correction se fait dans le worker, puis on regenere. C'est ce qui garantit
   qu'il n'existe QU'UNE definition du ROIC, du capital investi, de la dette,
   de l'EPV, du profil de societe et du moteur DCF deux phases.

   Aucune de ces fonctions n'appelle fetch, KV, env ni await (verifie a la
   generation).

   Source : ${nomSource} — worker ${version} — empreinte ${emp}
   ${noeuds.length} declarations, dont ${RACINES.length} exportees.
   ════════════════════════════════════════════════════════════════════════ */

export const NOYAU_SOURCE = ${JSON.stringify({ worker: version, empreinte: emp })};

`;
  return { texte: entete + corps + `\n\nexport { ${RACINES.join(", ")} };\n`, noms, version };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const [, , entree, sortie = new URL("../src/noyau.js", import.meta.url).pathname] = process.argv;
  if (!entree) { console.error("usage : node outils/generer_noyau.mjs <worker.js> [sortie]"); process.exit(2); }
  const src = readFileSync(entree, "utf8");
  const r = generer(src, entree.split("/").pop());
  writeFileSync(sortie, r.texte);
  console.log(`noyau genere depuis ${r.version} : ${r.noms.length} noms — ${r.noms.join(", ")}`);
}
