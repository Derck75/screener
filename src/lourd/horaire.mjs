#!/usr/bin/env node
/* ════════════════════════════════════════════════════════════════════════
   TÂCHES PLANIFIÉES DU SERVEUR MCP — exécutées hors Cloudflare (plan gratuit)

   Le cron du worker ne fait plus que DÉCLENCHER ce workflow (entrées
   `taches`, `tick`) : mesuré le 11/10/2026, le rafraîchissement horaire des
   cours et la veille de 3 h dépassaient les 10 ms de processeur du plan
   gratuit à chaque passage. Ce script télécharge le code DÉPLOYÉ du worker et
   appelle SA fonction `tacheCron` — une seule définition de chaque tâche.

   KV : par l'API (lecture de ses propres écritures, `kvLourd`).
   DISCORD : le secret reste dans le worker. Les messages sont interceptés et
   déposés dans une file KV (`discordq:…`) que le cron du worker envoie.
   D1 (pont screener) : par l'API REST, derrière une interface de liaison.
   JOURNAUX PUBLICS : rien (sortie muette dans le workflow).
   ════════════════════════════════════════════════════════════════════════ */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import url from 'node:url';
import { telechargerWorker, KVRest, kvLourd } from './lourd.mjs';

const ENV = process.env;
const FILE_DISCORD = 'https://file-discord.invalid/q';

/* Liaison D1 minimale (prepare/bind/run/all/first, batch) sur l'API REST. */
export function d1Rest(compte, base, jeton, f = fetch) {
  const url = `https://api.cloudflare.com/client/v4/accounts/${compte}/d1/database/${base}/query`;
  const q = async (sql, params) => {
    const r = await f(url, { method: 'POST', headers: { Authorization: `Bearer ${jeton}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ sql, params: params || [] }) });
    const j = await r.json();
    if (!j.success) throw new Error('D1 ' + JSON.stringify(j.errors || []).slice(0, 200));
    return j.result[0] || { results: [] };
  };
  const stmt = (sql, params = []) => ({
    bind: (...p) => stmt(sql, p),
    run: () => q(sql, params),
    all: () => q(sql, params),
    first: async col => { const r = await q(sql, params); const l = (r.results || [])[0]; return l ? (col ? l[col] : l) : null; },
    _sql: sql, _params: params
  });
  return { prepare: sql => stmt(sql), batch: async l => { const out = []; for (const s of l) out.push(await q(s._sql, s._params)); return out; } };
}

export async function executerTaches({ source, kv, taches, tick, d1 }) {
  const f = path.join(os.tmpdir(), `worker-horaire-${process.pid}-${Date.now()}.mjs`);
  fs.writeFileSync(f, source + '\nexport { tacheCron, configurerLourd, WORKER_VERSION };\n');
  let W;
  try { W = await import(url.pathToFileURL(f).href); } finally { try { fs.unlinkSync(f); } catch { } }
  const file = [];
  const vrai = globalThis.fetch;
  globalThis.fetch = async (u, init) => {
    const s = String(u && u.url ? u.url : u);
    if (s.startsWith(FILE_DISCORD)) { file.push(String((init && init.body) || '')); return new Response(null, { status: 204 }); }
    return vrai(u, init);
  };
  const env = { PF: kv, DISCORD_WEBHOOK: FILE_DISCORD, SCREENER: d1, LOURD: 'github' };
  W.configurerLourd(env);
  let erreurs = 0;
  try {
    for (const t of taches) { try { await W.tacheCron(t, kv, env, tick); } catch { erreurs++; } }
  } finally { globalThis.fetch = vrai; }
  if (file.length) await kv.put(`discordq:${Date.now()}-${Math.random().toString(16).slice(2, 8)}`, JSON.stringify(file), { expirationTtl: 172800 });
  if (taches.includes('pxyf')) await kv.put('cron:gh', JSON.stringify({ quand: new Date().toISOString(), taches, tick, erreurs, worker: W.WORKER_VERSION, discord: file.length }), { expirationTtl: 604800 });
  return { erreurs, discord: file.length, worker: W.WORKER_VERSION };
}

const direct = process.argv[1] && import.meta.url === url.pathToFileURL(path.resolve(process.argv[1])).href;
if (direct) {
  const taches = String(ENV.TACHES || '').split(',').map(s => s.trim()).filter(Boolean);
  const tick = Number(ENV.TICK) || Math.floor(Date.now() / 180000);
  try {
    const source = await telechargerWorker();
    if (!/function tacheCron\(/.test(source)) { process.exit(0); }   // worker antérieur à w198
    await executerTaches({ source, kv: kvLourd(new KVRest()), taches, tick, d1: d1Rest(ENV.CF_ACCOUNT_ID, ENV.CF_DB, ENV.CF_API_TOKEN) });
  } catch (e) { process.exitCode = 1; }
}
