/* BANC CPU — temps processeur de chaque outil et de chaque tâche planifiée du
   worker DÉPLOYÉ, sur une copie en mémoire du KV de production. Deux passes :
   la première enregistre les réponses réseau, la seconde les rejoue et MESURE
   (le coût TLS de Node n'entre pas dans la mesure, comme il n'entre pas dans le
   CPU compté par Workers). Rien n'est écrit en production : KV en mémoire,
   aucun Discord, aucun GitHub. Résultats en D1 (table `banc_cpu`, privée) ;
   JOURNAUX PUBLICS : des comptes, jamais un ticker. */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import url from 'node:url';
import { telechargerWorker, KVRest } from './lourd.mjs';
const ENV = process.env;
class KV {
  constructor() { this.m = new Map(); }
  async get(k, t) { const v = this.m.get(k); if (v === undefined) return null; return ((typeof t === 'string' ? t : (t && t.type)) === 'json') ? JSON.parse(v) : v; }
  async put(k, v) { this.m.set(k, String(v)); }
  async delete(k) { this.m.delete(k); }
  async list(o = {}) { return { keys: [...this.m.keys()].filter(k => !o.prefix || k.startsWith(o.prefix)).sort().map(name => ({ name })), list_complete: true }; }
}
async function chargerWorker(exports) {
  const src = await telechargerWorker();
  const f = path.join(os.tmpdir(), `worker-banc-${process.pid}.mjs`);
  fs.writeFileSync(f, src + `\nexport { ${exports.join(', ')} };\n`);
  try { return await import(url.pathToFileURL(f).href); } finally { try { fs.unlinkSync(f); } catch { } }
}
const vraiFetch = globalThis.fetch;
/* Toute panne va en D1 (privé), jamais dans le journal public. */
async function panne(e) {
  try {
    const D1 = `https://api.cloudflare.com/client/v4/accounts/${ENV.CF_ACCOUNT_ID}/d1/database/${ENV.CF_DB}/query`;
    const q = (sql, params = []) => vraiFetch(D1, { method: 'POST', headers: { Authorization: `Bearer ${ENV.CF_API_TOKEN}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ sql, params }) });
    await q("CREATE TABLE IF NOT EXISTS banc_cpu_err (quand TEXT, msg TEXT)");
    await q("INSERT INTO banc_cpu_err VALUES (?, ?)", [new Date().toISOString(), String(e && e.stack || e).slice(0, 2000)]);
  } catch { }
  process.exit(1);
}
process.on('uncaughtException', panne); process.on('unhandledRejection', panne);
const rest = new KVRest();
const cles = [];
for (let cur; ;) { const r = await rest.list({ cursor: cur }); cles.push(...r.keys.map(k => k.name)); if (r.list_complete) break; cur = r.cursor; }
const dump = {};
for (let i = 0; i < cles.length; i += 8) await Promise.all(cles.slice(i, i + 8).map(async k => { const v = await rest.get(k); if (v !== null) dump[k] = v; }));
console.log(`copie KV : ${Object.keys(dump).length} clés`);
const kvNeuf = () => { const k = new KV(); for (const [a, b] of Object.entries(dump)) k.m.set(a, b); return k; };
const W = await chargerWorker(['appeler', 'fileTravail', 'executer', 'veille', 'alertesTick', 'construireCours', 'construireYahooFonda', 'ecrireYF', 'digestHebdo', 'reinitialiserCompteur', 'veilleKrachSeance', 'alertesConstruireSeuils', 'WORKER_VERSION']);

// ---- 2. enregistrement / rejeu réseau ----
const enreg = new Map(); let mode = 'enregistrer', rates = 0;
const cleReq = (u, i) => `${(i && i.method) || 'GET'} ${String(u && u.url ? u.url : u)} ${i && typeof i.body === 'string' ? i.body.slice(0, 200) : ''}`;
globalThis.fetch = async (u, init) => {
  const k = cleReq(u, init);
  if (/discord|api\.github\.com|exa\.ai/.test(k)) return new Response('{}', { status: 204 });
  if (mode === 'rejouer') {
    const e = enreg.get(k);
    if (e) return new Response(e.status === 204 ? null : e.body, { status: e.status, headers: e.headers });
    rates++;
    return new Response('rejeu absent', { status: 599 });
  }
  try {
    const r = await vraiFetch(u, init);
    const body = await r.arrayBuffer();
    enreg.set(k, { status: r.status, headers: Object.fromEntries(r.headers), body });
    return new Response(r.status === 204 ? null : body, { status: r.status, headers: r.headers });
  } catch (e) { enreg.set(k, { status: 599, headers: {}, body: new ArrayBuffer(0) }); throw e; }
};

// ---- 3. scénario ----
const univ = JSON.parse(dump.univers || '[]');
const pos = JSON.parse(dump.pos || '{}');
const det = new Set(); for (const env of Object.values(pos.enveloppes || {})) for (const t of Object.keys(env)) det.add(t.toUpperCase());
const tickers = univ.filter(e => det.has(String(e.yahoo).toUpperCase())).map(e => e.yahoo);
const lignes = tickers.map(t => ({ ticker: t, poids: 1 }));
const outils = [];
for (const t of tickers) {
  for (const [n, a] of [['cours', {}], ['fv', {}], ['fv_propre', {}], ['dossier', {}], ['dcf', {}], ['dcf', { croissance: 8 }], ['dcf', { taux: 10 }], ['dcf', { unite: 'bpa' }], ['moat', {}], ['notes', {}], ['journal', {}], ['resultats', {}], ['niveaux', {}], ['brokers', {}], ['consensus', {}], ['dividendes', {}], ['esef', {}]])
    outils.push([n, Object.assign({ ticker: t }, a), t]);
}
for (const [n, a] of [['univers', {}], ['agenda', {}], ['positions', {}], ['notes', {}], ['krach', {}], ['krach', { filtre: true }], ['veille', {}], ['protocole', { nom: 'krach' }], ['macro', {}], ['prime', {}], ['stress_test', { lignes, capital: 50000 }], ['monte_carlo', { lignes, capital: 50000 }], ['correlation', { tickers: tickers.slice(0, 8) }], ['comparer', { tickers: tickers.slice(0, 3) }]])
  outils.push([n, a, '—']);

async function passe(mesurer) {
  const kv = kvNeuf(), env = { PF: kv };
  const res = [];
  const cpu = async (nom, fn) => {
    W.reinitialiserCompteur();
    const c0 = process.cpuUsage(), t0 = Date.now(); let ok = true, err = null;
    try { await fn(); } catch (e) { ok = false; err = String(e && e.message || e).slice(0, 120); }
    const c = process.cpuUsage(c0);
    if (mesurer) res.push({ nom, ms: Math.round((c.user + c.system) / 100) / 10, mur: Date.now() - t0, ok, err });
  };
  // tâches planifiées
  const jobs = W.fileTravail(univ);
  for (const j of jobs) await cpu(`cron:${j.t}:${j.e.yahoo}`, () => W.executer(j, kv, env));
  for (let k = 0; k < tickers.length; k += 8) await cpu(`cron:pxyf:lot${k / 8}`, async () => {
    for (const t of tickers.slice(k, k + 8)) { const c = await W.construireCours(t); if (c) await kv.put('px:' + t, JSON.stringify(c)); await W.ecrireYF(kv, t, await W.construireYahooFonda(t, kv)); }
  });
  await cpu('cron:veille', () => W.veille(kv, env, univ.map(x => x.yahoo), Math.floor(Date.now() / 10800000)));
  await cpu('cron:alertesTick', () => W.alertesTick(kv, env, false));
  await cpu('cron:krachSeance', () => W.veilleKrachSeance(kv, env));
  await cpu('cron:seuils', () => W.alertesConstruireSeuils(kv));
  await cpu('cron:digest', () => W.digestHebdo(kv, env, { rejeu: true }));
  // outils : deux appels, le second sur cache chaud
  for (const [n, a, t] of outils) {
    await cpu(`outil:${n}:${JSON.stringify(a).replace(/"lignes":\[[^\]]*\]/, '"lignes":[…]').slice(0, 60)}`, () => W.appeler(n, a, env));
    await cpu(`outil2:${n}:${JSON.stringify(a).replace(/"lignes":\[[^\]]*\]/, '"lignes":[…]').slice(0, 60)}`, () => W.appeler(n, a, env));
  }
  return res;
}
await passe(false);
console.log(`enregistrement : ${enreg.size} réponses`);
mode = 'rejouer';
const R = await passe(true);
console.log(`rejeu : ${rates} requêtes absentes`);
const D1 = `https://api.cloudflare.com/client/v4/accounts/${ENV.CF_ACCOUNT_ID}/d1/database/${ENV.CF_DB}/query`;
const d1 = async (sql, params = []) => { const r = await vraiFetch(D1, { method: 'POST', headers: { Authorization: `Bearer ${ENV.CF_API_TOKEN}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ sql, params }) }); const j = await r.json(); if (!j.success) throw new Error('D1 ' + JSON.stringify(j.errors).slice(0, 200)); return j.result; };
await d1("CREATE TABLE IF NOT EXISTS banc_cpu (id INTEGER PRIMARY KEY AUTOINCREMENT, quand TEXT, version TEXT, nom TEXT, ms REAL, mur INTEGER, ok INTEGER, err TEXT)");
await d1("DELETE FROM banc_cpu");
const quand = new Date().toISOString(), ver = W.WORKER_VERSION || '?';
for (let k = 0; k < R.length; k += 14) {
  const lot = R.slice(k, k + 14);
  await d1("INSERT INTO banc_cpu (quand, version, nom, ms, mur, ok, err) VALUES " + lot.map(() => "(?, ?, ?, ?, ?, ?, ?)").join(", "), lot.flatMap(r => [quand, ver, r.nom, r.ms, r.mur, r.ok ? 1 : 0, r.err]));
}
console.log(`${R.length} mesures écrites, ${R.filter(r => r.ms > 10).length} au-dessus de 10 ms, ${rates} rejeux absents`);
