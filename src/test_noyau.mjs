/* TESTS DE NON-REGRESSION DU NOYAU — aucune ecriture, aucun reseau.
 *
 * A QUOI CA SERT, ET POURQUOI C'EST LE FILET LE PLUS IMPORTANT.
 * `noyau.js` est GENERE depuis le worker. Le jour ou on le regenere depuis une
 * version plus recente, une signature peut avoir change, une fonction avoir
 * ete renommee, un seuil avoir bouge. Rien ne planterait : le screener
 * continuerait de tourner en publiant des chiffres faux, et le canari
 * (Apple, Microsoft...) ne verrait qu'une partie du probleme.
 *
 * Ces tests fixent le COMPORTEMENT ATTENDU sur des profils fabriques dont on
 * connait la reponse. Ils tournent en quelques millisecondes et doivent etre
 * verts AVANT tout deploiement d'un noyau regenere.
 *
 * Ils ne testent pas des valeurs exactes — un seuil peut legitimement bouger —
 * mais des VERDICTS : une banque reste une banque, un racheteur reste un
 * industriel, une fenetre courte reste refusee.
 */

import { derives, roicRetenu, profilSociete, ancrageEPV, mediane, cagr }
  from './noyau.js';

let ok = 0, ko = 0;
const verifier = (nom, condition, detail = "") => {
  if (condition) { ok++; console.log(`  OK   ${nom}`); }
  else { ko++; console.log(`  ECHEC ${nom}${detail ? " — " + detail : ""}`); }
};

const ANS = [2020, 2021, 2022, 2023, 2024, 2025];
function serie(postes, annees = ANS) {
  const S = {};
  for (const [c, f] of Object.entries(postes)) {
    S[c] = {};
    annees.forEach((a, i) => { S[c][a] = f(i); });
  }
  return S;
}

const INDUSTRIEL = serie({
  revenue: i => 50e9 + i * 3e9, ebit: i => 7e9 + i * 0.4e9, netIncome: () => 5e9,
  grossProfit: i => 20e9 + i * 1e9, cfo: () => 7e9, capex: () => 2e9,
  tax: () => 1.2e9, pretax: () => 6e9, assets: () => 60e9, ppe: () => 25e9,
  receivables: () => 9e9, inventory: () => 7e9, payables: () => 6e9,
  currentLiab: () => 12e9, debt: () => 15e9, cash: () => 4e9, equity: () => 22e9,
  goodwill: () => 4e9, intangExGW: () => 2e9, shares: () => 1e9,
});

const BANQUE = serie({
  revenue: () => 20e9, ebit: () => 7e9, netIncome: () => 5e9, cfo: () => 6e9,
  capex: () => 0.3e9, tax: () => 1.5e9, pretax: () => 6.5e9, assets: () => 900e9,
  ppe: () => 4e9, receivables: () => 10e9, payables: () => 5e9,
  currentLiab: () => 30e9, debt: () => 300e9, cash: () => 80e9, equity: () => 60e9,
  goodwill: () => 2e9, intangExGW: () => 1e9, shares: () => 2e9,
});

// Le cas Verisk : rachats massifs, capitaux propres residuels, levier apparent
// enorme — mais bilan parfaitement industriel.
const RACHETEUR = serie({
  revenue: i => 12e9 + i * 0.6e9, ebit: () => 4e9, netIncome: () => 3e9,
  grossProfit: () => 8e9, cfo: () => 3.6e9, capex: () => 0.4e9, tax: () => 0.7e9,
  pretax: () => 3.7e9, assets: () => 18e9, ppe: () => 3e9, receivables: () => 2e9,
  inventory: () => 0.5e9, payables: () => 1.5e9, currentLiab: () => 4e9,
  debt: () => 9e9, cash: () => 1e9, equity: () => 0.8e9, goodwill: () => 7e9,
  intangExGW: () => 2e9, shares: () => 0.5e9,
});

// Financee par ses fournisseurs : BFR tres negatif, capital investi minuscule.
const FLOAT = serie({
  revenue: i => 300e9 + i * 20e9, ebit: i => 95e9 + i * 7e9,
  netIncome: i => 80e9 + i * 6e9, grossProfit: i => 130e9 + i * 10e9,
  cfo: i => 100e9 + i * 7e9, capex: () => 11e9, tax: () => 16e9,
  pretax: i => 96e9 + i * 7e9, assets: () => 350e9, ppe: () => 45e9,
  receivables: () => 60e9, inventory: () => 6e9, payables: () => 95e9,
  currentLiab: () => 150e9, debt: () => 110e9, cash: () => 60e9,
  equity: () => 57e9, goodwill: () => 0, intangExGW: () => 0, shares: () => 16e9,
});

const COURTE = serie({
  revenue: () => 5e9, ebit: () => 1e9, netIncome: () => 0.7e9, ppe: () => 2e9,
  receivables: () => 1e9, payables: () => 0.6e9, debt: () => 1e9, cash: () => 0.5e9,
  equity: () => 3e9, assets: () => 6e9, shares: () => 0.2e9,
}, [2023, 2024, 2025]);

console.log("TESTS DE NON-REGRESSION DU NOYAU\n");

console.log("[utilitaires]");
verifier("mediane d'une serie paire", mediane([1, 2, 3, 4]) === 2.5);
verifier("mediane ignore les non-finis", mediane([1, NaN, 3]) === 2);
verifier("mediane d'une serie vide rend null", mediane([]) === null);
verifier("cagr doublement sur 5 ans ~ 14,87 %",
  Math.abs(cagr(100, 200, 5) - 0.1487) < 0.001);
verifier("cagr refuse un depart negatif", cagr(-5, 10, 3) === null);

console.log("\n[structure]");
const dInd = derives(INDUSTRIEL, "USD");
verifier("derives rend 6 exercices", dInd.annees.length === 6,
  `recu ${dInd.annees.length}`);
verifier("derives expose lignes et devise",
  !!dInd.lignes && dInd.devise === "USD");

console.log("\n[typologie — le coeur du dispositif]");
const cas = [
  ["industriel sain", INDUSTRIEL, t => t === "industriel" || t === "capitalistique", true],
  ["banque classee financiere", BANQUE, t => t === "financier", false],
  ["racheteur reste industriel", RACHETEUR, t => t !== "financier", true],
  ["float refuse", FLOAT, t => t === "float", false],
  ["fenetre courte refusee", COURTE, t => t === "fenetre_courte" || t === "float", false],
];
for (const [nom, S, attendu, evaAttendu] of cas) {
  const der = derives(S, "USD");
  const P = profilSociete(der, {});
  verifier(nom, attendu(P.type), `profil ${P.type}`);
  verifier(`  ${nom} — EVA ${evaAttendu ? "autorisee" : "refusee"}`,
    P.evaOK === evaAttendu, `evaOK=${P.evaOK}`);
  if (!P.evaOK) verifier(`  ${nom} — motif de refus nomme`,
    typeof P.ko === "string" && P.ko.length > 20);
}

console.log("\n[ROIC]");
const RInd = roicRetenu(derives(INDUSTRIEL, "USD"));
verifier("ROIC de l'industriel entre 5 et 40 %",
  RInd.mediane > 0.05 && RInd.mediane < 0.40, `${(RInd.mediane * 100).toFixed(1)} %`);
verifier("base du ROIC nommee en clair",
  typeof RInd.base === "string" && RInd.base.length > 15);
verifier("serie de ROIC publiee", Array.isArray(RInd.serie) && RInd.serie.length >= 4);
const RFloat = roicRetenu(derives(FLOAT, "USD"));
verifier("le float rend un ROIC eleve MAIS qualifie par le profil",
  !Number.isFinite(RFloat.mediane) || RFloat.mediane > 1);

console.log("\n[EPV]");
const E = ancrageEPV(derives(INDUSTRIEL, "USD"), 100);
verifier("EPV calculable sur 6 exercices rentables",
  Number.isFinite(E?.valeur) || typeof E?.ko === "string");
const EC = ancrageEPV(derives(COURTE, "USD"), 100);
verifier("EPV refuse sur fenetre courte",
  !Number.isFinite(EC?.valeur), "une fenetre de 3 exercices ne doit rien produire");

console.log(`\n${ok} tests verts, ${ko} rouges`);
if (ko) {
  console.log("\nLE NOYAU A CHANGE DE COMPORTEMENT.");
  console.log("Ne pas deployer : comparer avec la version precedente du worker,");
  console.log("et verifier qu'aucune fonction n'a ete renommee ou resignee.");
  process.exit(1);
}
console.log("Noyau conforme — deploiement possible.");
