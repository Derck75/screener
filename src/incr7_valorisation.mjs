/* INCREMENT 7 — Valorisation : ratios de prix calcules par le NOYAU du worker.
 *
 * POURQUOI UN ETAGE A PART (audit C2, M1). La « croissance exigee par le
 * prix » etait un Gordon perpetuel resolu en Python : g = (r - y) / (1 + y).
 * Microsoft y exigeait 9,5 %/an quand `dcf(MSFT, taux:11.5)` donne N = 25,3 %
 * sur dix ans puis 2,5 % — et la bande verte « prix <= 80 % du realise »
 * passait la ou le cadre colore rouge. L'ecart grandit avec le multiple, donc
 * precisement sur les compounders.
 * Ici, N est calcule par le MEME moteur que `dcf` : `croissanceImplicite`,
 * deux phases, dix ans puis 2,5 %, au taux-obstacle de l'enveloppe — 10 % PEA,
 * 11,5 % CTO —, sur la valeur d'entreprise (capitalisation + dette nette).
 *
 * CE QUI N'EST PAS LE SERVEUR, ET SE DIT : le FCF de depart est le plus bas du
 * dernier exercice et de la mediane des trois derniers (pas de TTM ici), et la
 * dette nette est celle du dernier exercice clos. Les chiffres restent donc
 * voisins de `dcf`, pas identiques ; seule l'analyse fait autorite.
 *
 * DEVISES : un flux se rapporte a une capitalisation DANS SA DEVISE. Equinor
 * publie en dollars et cote en couronnes : la capitalisation est convertie au
 * cours de reference BCE du jour avant tout ratio.
 */
import { croissanceImplicite, TERMINAL_G, NOYAU_SOURCE } from './noyau.js';

const { CF_ACCOUNT, CF_DB, CF_TOKEN } = process.env;
const URL_D1 = `https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT}/d1/database/${CF_DB}/query`;
const RUN_TS = new Date().toISOString().slice(0, 19) + "Z";
const ANNEES = 10;
const OBSTACLE = { pea: 0.10, cto: 0.115 };
const EPV_INVRAISEMBLABLE = 5;
export const BCE = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml";

// Euros par unite — REPLI seulement, quand la BCE ne cote pas la devise
// (TWD) ou ne repond pas. Ordre de grandeur : il classe et convertit a
// quelques pour cent pres, jamais il ne valorise au centime.
export const EUR_PAR_UNITE_REPLI = { EUR: 1, USD: 0.92, GBP: 1.17, CHF: 1.07, SEK: 0.090,
  NOK: 0.085, DKK: 0.134, JPY: 0.0058, PLN: 0.235, CZK: 0.040, HUF: 0.0026, ISK: 0.0068,
  KRW: 0.00063, TWD: 0.027, CAD: 0.63, AUD: 0.60, HKD: 0.118 };

// Devise de COTATION par place (suffixe Yahoo).
export const DEVISE_PLACE = { "": "USD", ".L": "GBP", ".SW": "CHF", ".ST": "SEK", ".CO": "DKK",
  ".OL": "NOK", ".IC": "ISK", ".WA": "PLN", ".PR": "CZK", ".BD": "HUF", ".T": "JPY",
  ".KS": "KRW", ".TW": "TWD", ".TO": "CAD", ".AX": "AUD", ".HK": "HKD" };
export const devisePlace = t => DEVISE_PLACE[t.includes(".") ? "." + t.split(".").pop() : ""] || "EUR";

const S = { compteurs: {}, erreurs: {} };
const compte = (k, n = 1) => { S.compteurs[k] = (S.compteurs[k] || 0) + n; };
const erreur = (c, ex) => { const e = S.erreurs[c] ||= { n: 0, exemple: null };
  e.n++; if (!e.exemple && ex) e.exemple = String(ex).slice(0, 120); };

async function d1(sql, params = []) {
  const r = await fetch(URL_D1, { method: "POST",
    headers: { Authorization: `Bearer ${CF_TOKEN}`, "Content-Type": "application/json" },
    body: JSON.stringify({ sql, params }) });
  const j = await r.json().catch(() => null);
  if (!j?.success) {
    const err = JSON.stringify(j?.errors || r.status).slice(0, 300);
    console.log("ECHEC D1 : " + err);
    await journal("PANNE", 0, "ROUGE", "erreur D1 : " + err);
    process.exit(1);
  }
  return j.result;
}

async function journal(statut, lignes, canari, message) {
  await fetch(URL_D1, { method: "POST",
    headers: { Authorization: `Bearer ${CF_TOKEN}`, "Content-Type": "application/json" },
    body: JSON.stringify({ sql: "INSERT INTO runs (debut, fin, etape, statut, lignes_ecrites, canari, message, detail)"
      + " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
      params: [RUN_TS, new Date().toISOString().slice(0, 19) + "Z", "incr7_valorisation", statut, lignes,
        canari, String(message).slice(0, 400), JSON.stringify(S)] }) }).catch(() => {});
}

async function migrer(table, colonnes) {
  const presentes = new Set((await d1(`PRAGMA table_info(${table})`))[0].results.map(l => l.name));
  for (const [nom, typ] of Object.entries(colonnes))
    if (!presentes.has(nom)) { await d1(`ALTER TABLE ${table} ADD COLUMN ${nom} ${typ}`); console.log(`  migration ${table} : ${nom}`); }
}

/* Taux BCE : euros par unite de devise. */
export async function tauxBCE() {
  const eur = { ...EUR_PAR_UNITE_REPLI };
  let source = "repli interne (BCE injoignable)";
  try {
    const r = await fetch(BCE);
    if (r.ok) {
      const x = await r.text();
      let n = 0;
      for (const m of x.matchAll(/currency=['"]([A-Z]{3})['"]\s+rate=['"]([\d.]+)['"]/g)) {
        const v = Number(m[2]);
        if (v > 0) { eur[m[1]] = 1 / v; n++; }
      }
      const date = (/time=['"](\d{4}-\d{2}-\d{2})['"]/.exec(x) || [])[1];
      if (n >= 10) source = `BCE du ${date || "?"} (${n} devises)`;
    }
  } catch { /* repli */ }
  return { eur, source };
}

const nb = v => (v === null || v === undefined || !Number.isFinite(Number(v))) ? "NULL" : String(Math.round(Number(v) * 1e6) / 1e6);
const tx = v => (v === null || v === undefined) ? "NULL" : "'" + String(v).slice(0, 120).replaceAll("'", "''") + "'";

/* Le calcul pur, testable sans base. `l` porte les colonnes lues. */
export function valoriser(l, eur) {
  const o = { croissance_implicite: null, taux_obstacle: null, n_ratio: null, bande_n: null, n_note: null,
    epv_sur_cours: null, eva_sur_cours: null, concordance: null, part_tresorerie: null,
    fcf_yield: null, per_courant: null, capi_eur: null, devise_ecart: null };
  const devC = devisePlace(l.ticker);
  const devF = l.devise_comptes || (l.ticker.includes(".") ? null : "USD");
  const capiC = Number(l.capitalisation);
  if (!(capiC > 0)) { o.n_note = "capitalisation indisponible"; return o; }
  const eC = eur[devC], eF = devF ? eur[devF] : eC;
  if (!(eC > 0) || !(eF > 0)) { o.n_note = `devise sans taux (${devC}/${devF})`; return o; }
  o.capi_eur = capiC * eC;
  // Capitalisation exprimee dans la devise des COMPTES.
  const capi = devF && devF !== devC ? capiC * eC / eF : capiC;
  o.devise_ecart = !devF ? "devise des comptes supposée égale à la cotation" : (devF !== devC ? `${devF}/${devC}` : null);

  const pea = Number(l.eligible_pea) === 1;
  const r = pea ? OBSTACLE.pea : OBSTACLE.cto;
  o.taux_obstacle = r * 100;
  const dn = Number.isFinite(Number(l.dette_nette)) && l.dette_nette !== null ? Number(l.dette_nette) : 0;
  const fcf0 = Number(l.fcf_depart);
  const ve = capi + dn;
  if (!(fcf0 > 0)) o.n_note = "FCF de départ négatif ou absent — N non calculable (le serveur basculerait en BPA)";
  else if (!(ve > 0)) o.n_note = "trésorerie nette supérieure à la capitalisation — N non calculable";
  else {
    const g = croissanceImplicite(ve, fcf0, r, TERMINAL_G, ANNEES);
    if (g && Number.isFinite(g.g)) {
      o.croissance_implicite = g.g * 100;
      if (g.horsBornes) o.n_note = `N ${g.horsBornes} — valeur plafonnée`;
    }
  }
  const gd = Number(l.croissance_demontree);
  if (o.croissance_implicite !== null && l.croissance_demontree !== null && gd > 0) {
    o.n_ratio = o.croissance_implicite / gd;
    // Bandes du cadre (§11-d), par PLAUSIBILITE : N rapporte a la croissance
    // demontree, le plus bas des fenetres.
    o.bande_n = o.n_ratio <= 0.80 ? "vert" : o.n_ratio <= 1.00 ? "orange" : o.n_ratio <= 1.20 ? "jaune" : "rouge";
  } else if (o.croissance_implicite !== null && l.croissance_demontree !== null) {
    o.bande_n = o.croissance_implicite <= gd ? "vert" : "rouge";   // croissance demontree nulle ou negative
  }

  if (Number.isFinite(Number(l.epv_capitaux)) && l.epv_capitaux !== null) {
    const e = Number(l.epv_capitaux) / capi;
    if (e > EPV_INVRAISEMBLABLE) o.n_note = (o.n_note ? o.n_note + " · " : "") + `EPV/cours ${e.toFixed(1)} invraisemblable, écartée`;
    else o.epv_sur_cours = e;
  }
  if (Number.isFinite(Number(l.eva_capitaux)) && l.eva_capitaux !== null) o.eva_sur_cours = Number(l.eva_capitaux) / capi;
  if (o.epv_sur_cours !== null && o.eva_sur_cours !== null) o.concordance = Math.min(o.epv_sur_cours, o.eva_sur_cours);
  o.part_tresorerie = dn < 0 ? -dn / capi : 0;
  if (fcf0 > 0) o.fcf_yield = fcf0 / capi;
  const rn = Number(l.rn_dernier);
  if (rn > 0) o.per_courant = capi / rn;
  return o;
}

const COLS = ["croissance_implicite", "taux_obstacle", "n_ratio", "bande_n", "n_note", "epv_sur_cours",
  "eva_sur_cours", "concordance", "part_tresorerie", "fcf_yield", "per_courant", "capi_eur", "devise_ecart"];
const TEXTE = new Set(["bande_n", "n_note", "devise_ecart"]);

export async function main() {
  console.log(`incr7_valorisation v1 — Run ${RUN_TS} — noyau ${NOYAU_SOURCE.worker} (${NOYAU_SOURCE.empreinte})`);
  await migrer("metriques", { croissance_implicite: "REAL", taux_obstacle: "REAL", n_ratio: "REAL",
    bande_n: "TEXT", n_note: "TEXT", epv_sur_cours: "REAL", eva_sur_cours: "REAL", concordance: "REAL",
    part_tresorerie: "REAL", fcf_yield: "REAL", per_courant: "REAL", capi_eur: "REAL",
    devise_ecart: "TEXT", maj_valorisation: "TEXT" });
  await migrer("societe", { devise_comptes: "TEXT" });

  // Une societe sans cours (radiee, liste muette) ou desormais exclue ne doit
  // pas garder une bande verte d'un autre jour : ses ratios sont effaces.
  await d1(`UPDATE metriques SET ${COLS.map(c => `${c} = NULL`).join(", ")}, maj_valorisation = ? `
    + "WHERE (cours IS NULL OR exclusion IS NOT NULL) AND (bande_n IS NOT NULL OR n_ratio IS NOT NULL "
    + "OR capi_eur IS NOT NULL OR croissance_implicite IS NOT NULL)", [RUN_TS]);

  const { eur, source } = await tauxBCE();
  console.log(`change : ${source}`);
  if (/repli/.test(source)) compte("change_repli");

  const lignes = [];
  for (let p = 0; ; p++) {
    const r = (await d1("SELECT m.ticker, m.cours, m.fcf_depart, m.dette_nette, m.epv_capitaux, m.eva_capitaux, "
      + "m.croissance_demontree, m.rn_dernier, s.capitalisation, s.eligible_pea, s.devise_comptes "
      + "FROM metriques m JOIN societe s ON s.ticker = m.ticker "
      + "WHERE m.exclusion IS NULL AND m.cours IS NOT NULL ORDER BY m.ticker LIMIT 5000 OFFSET ?", [p * 5000]))[0].results;
    if (!r.length) break;
    lignes.push(...r);
  }
  console.log(`  ${lignes.length} societes cotees et non exclues`);

  const sortie = [];
  for (const l of lignes) {
    try {
      const o = valoriser(l, eur);
      sortie.push([l.ticker, o]);
      if (o.croissance_implicite !== null) compte("n_calcule");
      if (o.bande_n) compte("bande_" + o.bande_n);
      if (o.devise_ecart && o.devise_ecart.includes("/")) compte("devise_convertie");
      if (o.devise_ecart && !o.devise_ecart.includes("/")) compte("devise_supposee");
    } catch (e) { erreur("valorisation", `${l.ticker} ${e.message}`); }
  }

  // Canari : Microsoft doit exiger nettement plus qu'un Gordon perpetuel.
  const msft = sortie.find(([t]) => t === "MSFT");
  const nMsft = msft && msft[1].croissance_implicite;
  console.log(`  canari MSFT : N = ${Number.isFinite(nMsft) ? nMsft.toFixed(1) + " %/an" : "n.c."}`);
  if (lignes.length > 500 && !(nMsft > 10)) {
    console.log("PANNE : canari MSFT hors plage (N attendu nettement au-dessus de 10 %)");
    await journal("PANNE", 0, "ROUGE", `canari MSFT : N = ${nMsft}`);
    process.exit(1);
  }

  for (let i = 0; i < sortie.length; i += 50) {
    const lot = sortie.slice(i, i + 50);
    await d1(`INSERT INTO metriques (ticker, ${COLS.join(", ")}, maj_valorisation) VALUES `
      + lot.map(([t, o]) => "(" + [tx(t), ...COLS.map(c => TEXTE.has(c) ? tx(o[c]) : nb(o[c])), tx(RUN_TS)].join(", ") + ")").join(", ")
      + ` ON CONFLICT(ticker) DO UPDATE SET ${COLS.map(c => `${c} = excluded.${c}`).join(", ")}, maj_valorisation = excluded.maj_valorisation`);
  }
  console.log(`  ${sortie.length} lignes valorisees`);
  console.log("  " + Object.entries(S.compteurs).map(([k, v]) => `${k}=${v}`).join(", "));
  for (const [c, e] of Object.entries(S.erreurs)) console.log(`  ERREUR ${c} x${e.n} — ${e.exemple || ""}`);
  await journal("OK", sortie.length, "VERT", `${sortie.length} valorisees, N sur ${S.compteurs.n_calcule || 0}`);
  console.log("OK");
}

if (import.meta.url === `file://${process.argv[1]}`) main().catch(async e => {
  console.log("ECHEC : " + (e.stack || e.message).slice(0, 300));
  await journal("PANNE", 0, "ROUGE", String(e.message).slice(0, 200));
  process.exit(1);
});
