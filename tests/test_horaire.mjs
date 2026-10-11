/* horaire.mjs : exécute `tacheCron` du worker fourni, intercepte Discord vers
   une file KV, écrit le battement au passage horaire. Worker factice (le vrai
   est privé) ; WORKER_REEL=chemin pour un essai d'intégration local. */
import fs from 'node:fs';
import { executerTaches, d1Rest } from '../src/lourd/horaire.mjs';
let ok = 0, ko = 0; const v = (c, m) => { c ? ok++ : ko++; console.log((c ? '  ✅ ' : '  ❌ ') + m); };
class KV { constructor() { this.m = new Map(); } async get(k) { return this.m.has(k) ? this.m.get(k) : null; } async put(k, x) { this.m.set(k, String(x)); } async delete(k) { this.m.delete(k); } async list(o = {}) { return { keys: [...this.m.keys()].filter(k => !o.prefix || k.startsWith(o.prefix)).map(name => ({ name })), list_complete: true }; } }
const faux = `
const WORKER_VERSION = "w-test"; let LOURD = false;
function configurerLourd(env) { LOURD = env.LOURD === "github"; }
async function tacheCron(nom, kv, env, n) {
  if (nom === "digest") await fetch(env.DISCORD_WEBHOOK, { method: "POST", body: JSON.stringify({ embeds: [{ title: "point " + n }] }) });
  if (nom === "pxyf") await kv.put("px:A", "1");
  if (nom === "synchro") await env.SCREENER.prepare("SELECT 1").all();
}`;
const kv = new KV();
const appelsD1 = [];
const d1 = d1Rest('c', 'b', 't', async (u, i) => { appelsD1.push(JSON.parse(i.body).sql); return new Response(JSON.stringify({ success: true, result: [{ results: [{ x: 1 }] }] })); });
const r = await executerTaches({ source: faux, kv, taches: ['digest', 'pxyf', 'synchro'], tick: 42, d1 });
const q = [...kv.m.keys()].find(k => k.startsWith('discordq:'));
v(q && /point 42/.test(kv.m.get(q)), 'Discord intercepté et déposé en file KV, jamais posté');
v(kv.m.has('px:A') && JSON.parse(kv.m.get('cron:gh')).taches.includes('pxyf'), 'tâche exécutée, battement écrit au passage horaire');
v(appelsD1[0] === 'SELECT 1' && r.erreurs === 0, 'liaison D1 par API REST');
const kv2 = new KV();
await executerTaches({ source: faux, kv: kv2, taches: ['alertes'], tick: 5, d1 });
v(!kv2.m.has('cron:gh') && ![...kv2.m.keys()].some(k => k.startsWith('discordq:')), 'quart d\'heure sans message : aucune écriture KV');
if (process.env.WORKER_REEL) {
  globalThis.fetch = async (u) => new Response('{}', { status: 503 });
  const kv3 = new KV(); kv3.m.set('univers', JSON.stringify([{ yahoo: 'AAA.PA', nom: 'A' }]));
  const r3 = await executerTaches({ source: fs.readFileSync(process.env.WORKER_REEL, 'utf8'), kv: kv3, taches: ['digest', 'pxyf', 'alertes', 'veille'], tick: Math.floor(Date.now() / 180000), d1 });
  v(r3.worker && kv3.m.has('cron:gh') && [...kv3.m.keys()].some(k => k.startsWith('discordq:')), `worker réel ${r3.worker} : tâches exécutées, digest en file`);
}
console.log(`\n${ok} réussis, ${ko} échoués`); if (ko) process.exitCode = 1;
