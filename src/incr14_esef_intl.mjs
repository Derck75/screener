/* INCREMENT 14 — REPLI ESEF DES COMPTES INTERNATIONAUX (en sommeil).

   POURQUOI. Hors des États-Unis, les comptes du screener viennent de la seule
   StockAnalysis (incrément 8). Si le site tombe ou change de structure, le
   canari LVMH le dit — mais aucun exercice nouveau n'entre plus. Pour les
   sociétés européennes, le rapport annuel officiel existe au format ESEF sur
   filings.xbrl.org, indexé par LEI (incrément 13).

   EN SOMMEIL. Ce script ne fait RIEN tant que StockAnalysis sert : il ne
   s'éveille que si le canari de l'incrément 8 est rouge depuis 7 jours sans
   un seul passage productif entre-temps (ou sur `--forcer`). Coût en sommeil :
   deux lectures D1 par jour.

   CE QU'IL RÉUTILISE. L'extraction ESEF du serveur (`construireEsef`), déjà
   éprouvée sur les lignes détenues (mapping IFRS, choix de la devise, garde-fou
   de calibration). Le cœur DÉPLOYÉ est téléchargé et importé tel quel, comme
   pour les travaux lourds. La calibration se fait contre les derniers comptes
   StockAnalysis en base : un mapping qui ne recoupe pas n'écrit rien.

   CE QU'IL ÉCRIT. Uniquement les EXERCICES ABSENTS (INSERT OR IGNORE, source
   'esef') : jamais une ligne StockAnalysis n'est remplacée. Devise des comptes
   différente de celle déjà connue : société écartée (risque d'unité). */
import { telechargerWorker, chargerModule } from './lourd/lourd.mjs';

const E = process.env;
const ARGS = new Set(process.argv.slice(2));
const LOT = Number((process.argv.find(a => a.startsWith('--lot=')) || '--lot=40').split('=')[1]);
const ESSAI = ARGS.has('--essai'), FORCER = ARGS.has('--forcer');
const COLONNES = ['revenue', 'netIncome', 'ebit', 'grossProfit', 'cfo', 'capex', 'da', 'ebitda', 'sbc', 'tax', 'pretax', 'amortAcq', 'assets', 'ppe',
  'intangTot', 'intangExGW', 'goodwill', 'receivables', 'inventory', 'payables', 'currentLiab', 'debt', 'cash', 'equity', 'shares', 'deferredRev', 'deferredRevNC', 'flottant'];
const EUROPE = ['.PA', '.DE', '.L', '.AS', '.MI', '.MC', '.ST', '.OL', '.HE', '.CO', '.BR', '.VI', '.LS', '.IR', '.WA', '.AT', '.IC', '.BD', '.PR'];
const RUN_TS = new Date().toISOString().slice(0, 19) + 'Z';

export async function d1(sql, params = []) {
  const r = await fetch(`https://api.cloudflare.com/client/v4/accounts/${E.CF_ACCOUNT_ID}/d1/database/${E.CF_D1_SCREENER_ID}/query`,
    { method: 'POST', headers: { Authorization: `Bearer ${E.CF_API_TOKEN}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ sql, params }) });
  const j = await r.json();
  if (!j.success) throw new Error('D1 : ' + JSON.stringify(j.errors).slice(0, 200));
  return j.result[0].results;
}

/* Éveil : canari rouge depuis 7 jours, sans passage productif depuis. */
export async function eveil(q = d1, maintenant = Date.now()) {
  const ok = (await q("SELECT MAX(debut) AS d FROM runs WHERE etape = 'incr8_comptes_intl_t0' AND statut = 'OK' AND lignes_ecrites > 0"))[0].d || '';
  const p = (await q("SELECT MIN(debut) AS d, COUNT(*) AS n FROM runs WHERE etape = 'incr8_comptes_intl_t0' AND message LIKE 'canari rouge%' AND debut > ?", [ok]))[0];
  if (!p || !p.d) return { actif: false, motif: 'canari vert : StockAnalysis sert' };
  const j = (maintenant - Date.parse(p.d)) / 86400000;
  return j >= 7 ? { actif: true, motif: `canari rouge depuis ${Math.floor(j)} j (${p.n} passages)` } : { actif: false, motif: `canari rouge depuis ${Math.floor(j)} j : éveil à 7 j` };
}

export class KVMemoire {
  constructor(o = {}) { this.m = new Map(Object.entries(o).map(([k, v]) => [k, typeof v === 'string' ? v : JSON.stringify(v)])); }
  async get(k, t) { const v = this.m.get(k); return v === undefined ? null : t === 'json' ? JSON.parse(v) : v; }
  async put(k, v) { this.m.set(k, String(v)); }
  async delete(k) { this.m.delete(k); }
  async list() { return { keys: [...this.m.keys()].map(name => ({ name })), list_complete: true }; }
}

/* Lignes neuves à écrire : exercices absents de la base, CA ou EBIT présents. */
export function lignesNeuves(series, connus) {
  const ans = new Set();
  for (const c of Object.keys(series || {})) for (const a of Object.keys(series[c])) ans.add(Number(a));
  const out = [];
  for (const a of [...ans].sort()) {
    if (connus.has(a)) continue;
    const l = Object.fromEntries(COLONNES.map(c => [c, series[c] && Number.isFinite(series[c][a]) ? series[c][a] : null]));
    if (l.revenue === null && l.ebit === null) continue;
    if (l.capex !== null) l.capex = Math.abs(l.capex);
    out.push({ exercice: a, l });
  }
  return out;
}

export async function main(dep = {}) {
  const q = dep.d1 || d1;
  const ev = FORCER ? { actif: true, motif: 'forcé' } : await eveil(q);
  if (!ev.actif) { console.log(`En sommeil — ${ev.motif}.`); return { sommeil: true }; }
  console.log(`Éveillé — ${ev.motif}${ESSAI ? ' · ESSAI, rien n\'est écrit' : ''}`);
  const cibles = await q(`SELECT s.ticker, s.nom, s.lei, s.devise_comptes FROM societe s WHERE s.lei IS NOT NULL AND (${EUROPE.map(p => `s.ticker LIKE '%${p}'`).join(' OR ')}) ORDER BY ${ARGS.has('--grandes') ? 's.capitalisation DESC' : 's.comptes_maj ASC, s.capitalisation DESC'} LIMIT ?`, [LOT]);
  const W = dep.W || (await chargerModule(await telechargerWorker())).W;
  const B = { societes: 0, validees: 0, refusees: 0, absentes: 0, devise: 0, lignes: 0, erreurs: 0 };
  for (const c of cibles) {
    B.societes++;
    try {
      const rows = await q('SELECT * FROM comptes2 WHERE ticker = ?', [c.ticker]);
      const ref = {};
      for (const r of rows) for (const k of COLONNES) if (r[k] !== null && r[k] !== undefined) (ref[k] = ref[k] || {})[r.exercice] = r[k];
      const kv = new KVMemoire({ ['sa:' + c.ticker]: { statut: 'OK', series: ref } });
      const e = { yahoo: c.ticker, nom: c.nom, lei: c.lei };
      let r = null;
      for (let i = 0; i < 12; i++) {
        W.reinitialiserCompteur();
        r = await W.construireEsef(e, kv, { lots: 8 });
        await kv.put('esef:' + c.ticker, JSON.stringify(r));
        if (r.complet || r.statut === 'KO' && r.cause) break;
      }
      if (!r || r.statut !== 'OK' || !r.series) { r && r.cause === 'absent_index' ? B.absentes++ : B.refusees++; if (ESSAI) console.log(`  ${c.ticker} : ${r ? (r.cause || r.statut) + ' — ' + String(r.erreur || '').slice(0, 90) : 'rien'}`); continue; }
      if (c.devise_comptes && r.devise && c.devise_comptes !== r.devise) { B.devise++; continue; }
      B.validees++;
      const neuves = lignesNeuves(r.series, new Set(rows.map(x => Number(x.exercice))));
      if (!neuves.length || ESSAI) { B.lignes += ESSAI ? neuves.length : 0; continue; }
      for (const n of neuves) {
        await q(`INSERT OR IGNORE INTO comptes2 (ticker, exercice, ${COLONNES.join(', ')}, source) VALUES (?, ?, ${COLONNES.map(() => '?').join(', ')}, 'esef')`,
          [c.ticker, n.exercice, ...COLONNES.map(k => n.l[k])]);
        B.lignes++;
      }
      await q('UPDATE societe SET comptes_maj = ? WHERE ticker = ?', [RUN_TS, c.ticker]);
    } catch (err) { B.erreurs++; console.log(`  ${c.ticker} : ${String(err && err.message || err).slice(0, 120)}`); }
  }
  const msg = `${ESSAI ? 'ESSAI · ' : ''}${B.societes} sociétés : ${B.validees} calibrées, ${B.refusees} refusées, ${B.absentes} absentes de l'index, ${B.devise} devise divergente · ${B.lignes} exercice(s) ${ESSAI ? 'à écrire' : 'écrits'}`;
  console.log(msg);
  if (!ESSAI) await q("INSERT INTO runs (debut, fin, etape, statut, lignes_ecrites, canari, message, detail) VALUES (?, ?, 'incr14_esef_intl', ?, ?, ?, ?, ?)",
    [RUN_TS, new Date().toISOString().slice(0, 19) + 'Z', B.erreurs && !B.validees ? 'PANNE' : 'OK', B.lignes, B.validees ? 'VERT' : 'ORANGE', msg, JSON.stringify(B)]);
  return B;
}

if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split('/').pop())) main().catch(e => { console.error(e); process.exit(1); });
