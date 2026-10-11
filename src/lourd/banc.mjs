/* BANC CPU — temps processeur de chaque outil et de chaque tâche planifiée du
   worker DÉPLOYÉ, sur une copie en mémoire du KV de production.
   1. ENREGISTREMENT : tout est exécuté une fois avec le vrai réseau ; chaque
      réponse est gardée (URL normalisée : jetons et horodatages retirés).
   2. MESURE À FROID : chaque appel est rejoué dans un PROCESSUS NEUF (JIT
      froid, comme un isolat Workers qui se réveille), réseau servi par
      l'enregistrement — le coût TLS de Node n'entre pas dans la mesure, comme
      il n'entre pas dans le CPU compté par Workers. Puis un second appel dans
      le même processus (isolat chaud).
   Rien n'est écrit en production. Résultats en D1 (`banc_cpu`, privé) ;
   JOURNAUX PUBLICS : rien. */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import url from 'node:url';
import { spawnSync } from 'node:child_process';
const ENV = process.env;
const ICI = path.dirname(url.fileURLToPath(import.meta.url));
const TMP = path.join(os.tmpdir(), 'banc');
const vraiFetch = globalThis.fetch;
const D1 = () => `https://api.cloudflare.com/client/v4/accounts/${ENV.CF_ACCOUNT_ID}/d1/database/${ENV.CF_DB}/query`;
const d1 = async (sql, params = []) => { const r = await vraiFetch(D1(), { method: 'POST', headers: { Authorization: `Bearer ${ENV.CF_API_TOKEN}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ sql, params }) }); const j = await r.json(); if (!j.success) throw new Error('D1 ' + JSON.stringify(j.errors).slice(0, 200)); return j.result; };
async function panne(e) { try { await d1("CREATE TABLE IF NOT EXISTS banc_cpu_err (quand TEXT, msg TEXT)"); await d1("INSERT INTO banc_cpu_err VALUES (?, ?)", [new Date().toISOString(), String(e && e.stack || e).slice(0, 2000)]); } catch { } process.exit(1); }

class KV {
  constructor(m) { this.m = m || new Map(); this.ecr = 0; }
  async get(k, t) { const v = this.m.get(k); if (v === undefined) return null; return ((typeof t === 'string' ? t : (t && t.type)) === 'json') ? JSON.parse(v) : v; }
  async put(k, v) { this.ecr++; this.m.set(k, String(v)); }
  async delete(k) { this.m.delete(k); }
  async list(o = {}) { return { keys: [...this.m.keys()].filter(k => !o.prefix || k.startsWith(o.prefix)).sort().map(name => ({ name })), list_complete: true }; }
}
const EXPORTS = ['appeler', 'fileTravail', 'executer', 'veille', 'alertesTick', 'construireCours', 'construireYahooFonda', 'ecrireYF', 'digestHebdo', 'reinitialiserCompteur', 'veilleKrachSeance', 'alertesConstruireSeuils', 'WORKER_VERSION'];
async function chargerW(src) {
  const f = path.join(os.tmpdir(), `worker-banc-${process.pid}-${Date.now()}.mjs`);
  fs.writeFileSync(f, src + `\nexport { ${EXPORTS.join(', ')} };\n`);
  try { return await import(url.pathToFileURL(f).href); } finally { try { fs.unlinkSync(f); } catch { } }
}
const normaliser = (u, i) => {
  let s = String(u && u.url ? u.url : u).replace(/([?&])(crumb|period1|period2|_|t|ts|timestamp|cb)=[^&]*/gi, '$1').replace(/\b\d{10,13}\b/g, 'N');
  return `${(i && i.method) || 'GET'} ${s} ${i && typeof i.body === 'string' ? i.body.slice(0, 300) : ''}`;
};

/* Le scénario : la liste des appels, identique dans les deux phases. */
function scenario(univ, pos, dump) {
  const det = new Set(); for (const e of Object.values(pos.enveloppes || {})) for (const t of Object.keys(e)) det.add(t.toUpperCase());
  const tickers = univ.filter(e => det.has(String(e.yahoo).toUpperCase())).map(e => e.yahoo);
  const lignes = tickers.map(t => ({ ticker: t, poids: 1 }));
  const L = [];
  for (const t of tickers) for (const [n, a] of [['cours', {}], ['fv', {}], ['fv_propre', {}], ['dossier', {}], ['dcf', {}], ['dcf', { croissance: 8 }], ['dcf', { taux: 10 }], ['moat', {}], ['notes', {}], ['journal', {}], ['resultats', {}], ['niveaux', {}], ['brokers', {}], ['consensus', {}], ['actu', {}], ['historique', {}]])
    L.push({ type: 'outil', nom: n, args: Object.assign({ ticker: t }, a), t });
  for (const [n, a] of [['univers', {}], ['agenda', {}], ['positions', {}], ['notes', {}], ['krach', {}], ['krach', { filtre: true }], ['veille', {}], ['macro', {}], ['prime', {}], ['stress_test', { lignes, capital: 50000 }], ['monte_carlo', { lignes, capital: 50000 }], ['correlation', { tickers: tickers.slice(0, 8) }], ['comparer', { tickers: tickers.slice(0, 3) }], ['tsr', { ticker: tickers[0] }]])
    L.push({ type: 'outil', nom: n, args: a, t: '—' });
  L.push({ type: 'cron', nom: 'veille' }, { type: 'cron', nom: 'alertesTick' }, { type: 'cron', nom: 'krachSeance' }, { type: 'cron', nom: 'seuils' }, { type: 'cron', nom: 'digest' });
  for (let k = 0; k < tickers.length; k += 8) L.push({ type: 'cron', nom: 'pxyf', lot: tickers.slice(k, k + 8) });
  return L;
}
async function executerAppel(W, kv, env, a, univ) {
  if (a.type === 'outil') return W.appeler(a.nom, a.args, env);
  if (a.nom === 'job') return W.executer(a.job, kv, env);
  if (a.nom === 'veille') return W.veille(kv, env, univ.map(x => x.yahoo), Math.floor(Date.now() / 10800000));
  if (a.nom === 'alertesTick') return W.alertesTick(kv, env, false);
  if (a.nom === 'krachSeance') return W.veilleKrachSeance(kv, env);
  if (a.nom === 'seuils') return W.alertesConstruireSeuils(kv);
  if (a.nom === 'digest') return W.digestHebdo(kv, env, { rejeu: true });
  if (a.nom === 'pxyf') { for (const t of a.lot) { const c = await W.construireCours(t); if (c) await kv.put('px:' + t, JSON.stringify(c)); await W.ecrireYF(kv, t, await W.construireYahooFonda(t, kv)); } }
}

/* ---- PHASE ENFANT : un appel, à froid puis à chaud ---- */
if (process.argv[2] === 'enfant') {
  try {
    const i = Number(process.argv[3]);
    const src = fs.readFileSync(path.join(TMP, 'worker.js'), 'utf8');
    const dump = JSON.parse(fs.readFileSync(path.join(TMP, 'kv.json'), 'utf8'));
    const enr = JSON.parse(fs.readFileSync(path.join(TMP, 'reseau.json'), 'utf8'));
    const appels = JSON.parse(fs.readFileSync(path.join(TMP, 'appels.json'), 'utf8'));
    let rates = 0, servis = 0;
    globalThis.fetch = async (u, init) => {
      const k = normaliser(u, init);
      if (/discord|api\.github\.com|exa\.ai/.test(k)) return new Response(null, { status: 204 });
      const e = enr[k]; if (!e) { rates++; return new Response('absent', { status: 599 }); }
      servis++; return new Response(e.s === 204 || e.s === 304 ? null : Buffer.from(e.b, 'base64'), { status: e.s, headers: e.h });
    };
    const W = await chargerW(src);
    const kv = new KV(new Map(Object.entries(dump))), env = { PF: kv };
    const a = appels[i], univ = JSON.parse(dump.univers || '[]');
    const mesure = async () => { W.reinitialiserCompteur(); const c0 = process.cpuUsage(); let ok = 1, err = null; try { await executerAppel(W, kv, env, a, univ); } catch (e) { ok = 0; err = String(e && e.message || e).slice(0, 150); } const c = process.cpuUsage(c0); return { ms: Math.round((c.user + c.system) / 100) / 10, ok, err }; };
    const froid = await mesure(); const r0 = rates, s0 = servis; const chaud = await mesure();
    process.stdout.write(JSON.stringify({ froid, chaud, rates: r0, servis: s0, ecr: kv.ecr }));
  } catch (e) { process.stdout.write(JSON.stringify({ froid: { ms: -1, ok: 0, err: String(e).slice(0, 150) } })); }
  process.exit(0);
}

/* ---- PHASE PRINCIPALE ---- */
process.on('uncaughtException', panne); process.on('unhandledRejection', panne);
const { telechargerWorker, KVRest } = await import('./lourd.mjs');
fs.mkdirSync(TMP, { recursive: true });
const src = await telechargerWorker();
fs.writeFileSync(path.join(TMP, 'worker.js'), src);
const rest = new KVRest(), cles = [];
for (let cur; ;) { const r = await rest.list({ cursor: cur }); cles.push(...r.keys.map(k => k.name)); if (r.list_complete) break; cur = r.cursor; }
const dump = {};
for (let i = 0; i < cles.length; i += 8) await Promise.all(cles.slice(i, i + 8).map(async k => { const v = await rest.get(k); if (v !== null) dump[k] = v; }));
fs.writeFileSync(path.join(TMP, 'kv.json'), JSON.stringify(dump));
const univ = JSON.parse(dump.univers || '[]'), pos = JSON.parse(dump.pos || '{}');
const W = await chargerW(src);
const appels = scenario(univ, pos, dump);
for (const j of W.fileTravail(univ)) appels.push({ type: 'cron', nom: 'job', job: j, t: j.e.yahoo, jt: j.t });
fs.writeFileSync(path.join(TMP, 'appels.json'), JSON.stringify(appels));
// enregistrement
const enr = {};
globalThis.fetch = async (u, init) => {
  const k = normaliser(u, init);
  if (/discord|api\.github\.com|exa\.ai/.test(k)) return new Response(null, { status: 204 });
  try {
    const r = await vraiFetch(u, init); const b = Buffer.from(await r.arrayBuffer());
    if (!enr[k]) enr[k] = { s: r.status, h: Object.fromEntries(r.headers), b: b.toString('base64') };
    return new Response(r.status === 204 || r.status === 304 ? null : b, { status: r.status, headers: r.headers });
  } catch (e) { throw e; }
};
{ const kv = new KV(new Map(Object.entries(dump))), env = { PF: kv }; for (const a of appels) { try { W.reinitialiserCompteur(); await executerAppel(W, kv, env, a, univ); } catch { } } }
fs.writeFileSync(path.join(TMP, 'reseau.json'), JSON.stringify(enr));
globalThis.fetch = vraiFetch;
// mesure à froid, un processus par appel
const R = [];
for (let i = 0; i < appels.length; i++) {
  const p = spawnSync(process.execPath, ['--no-warnings', url.fileURLToPath(import.meta.url), 'enfant', String(i)], { encoding: 'utf8', timeout: 120000, maxBuffer: 1 << 20 });
  let o; try { o = JSON.parse(p.stdout); } catch { o = { froid: { ms: -1, ok: 0, err: 'sortie illisible' } }; }
  const a = appels[i];
  const nom = a.type === 'outil' ? `outil:${a.nom}:${JSON.stringify(a.args).replace(/"lignes":\[[^\]]*\]/, '"lignes":[…]').slice(0, 60)}` : (a.nom === 'job' ? `cron:job:${a.jt}:${a.t}` : `cron:${a.nom}${a.lot ? ':' + a.lot.length : ''}`);
  R.push({ nom, froid: o.froid && o.froid.ms, chaud: o.chaud && o.chaud.ms, ok: o.froid && o.froid.ok, err: (o.froid && o.froid.err) || null, rates: o.rates || 0, servis: o.servis || 0, ecr: o.ecr || 0 });
}
await d1("DROP TABLE IF EXISTS banc_cpu");
await d1("CREATE TABLE banc_cpu (id INTEGER PRIMARY KEY AUTOINCREMENT, quand TEXT, version TEXT, nom TEXT, froid REAL, chaud REAL, ok INTEGER, err TEXT, rates INTEGER, servis INTEGER, ecr INTEGER)");
const quand = new Date().toISOString(), ver = W.WORKER_VERSION || '?';
for (let k = 0; k < R.length; k += 9) {
  const lot = R.slice(k, k + 9);
  await d1("INSERT INTO banc_cpu (quand, version, nom, froid, chaud, ok, err, rates, servis, ecr) VALUES " + lot.map(() => "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)").join(", "), lot.flatMap(r => [quand, ver, r.nom, r.froid, r.chaud, r.ok, r.err, r.rates, r.servis, r.ecr]));
}
