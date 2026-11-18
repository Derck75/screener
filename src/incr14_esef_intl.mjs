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
const PAUSE_MS = Number(E.PAUSE_ESEF_MS || 6000);

/* filings.xbrl.org limite le débit (HTTP 429 constatés à la première mesure,
   24 refus sur 25 : archives non lues, donc rien à calibrer). Toute requête
   vers l'index est espacée et rejouée sur 429, avant que le cœur ne la voie. */
export function installerPolitesse(attente = ms => new Promise(z => setTimeout(z, ms))) {
  const fetchBrut = globalThis.fetch;
  let dernier = 0;
  globalThis.fetch = async (u, init) => {
    const url = String(u && u.url ? u.url : u);
    if (!/filings\.xbrl\.org/.test(url)) return fetchBrut(u, init);
    for (let i = 0; ; i++) {
      const ecart = Date.now() - dernier;
      if (ecart < 1500) await attente(1500 - ecart);
      dernier = Date.now();
      const r = await fetchBrut(u, init);
      if (r.status !== 429 || i >= 4) return r;
      const ra = Number(r.headers && r.headers.get && r.headers.get('retry-after')) || 0;
      await attente(Math.max(ra * 1000, 10000 * (i + 1)));
    }
  };
}

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

/* POSTES SÛRS — la réponse au risque de mélange. La calibration du cœur
   accepte une société dès qu'une majorité de postes concorde, et écrit aussi
   des postes tolérés (jusqu'à 5 %) ou sans recouvrement. Ici on n'ajoute un
   exercice à une série StockAnalysis que pour les postes qui tombent AU
   DEMI-POUR-CENT près sur TOUS les exercices communs, au moins deux : même
   définition prouvée, donc aucune rupture de série à la jonction. Le reste
   de la ligne reste vide plutôt que d'être comblé par une autre définition. */
export const TOL_SUR = 0.005;
export function postesSurs(series, ref) {
  const out = {};
  for (const c of Object.keys(series || {})) {
    const sr = ref[c]; if (!sr) continue;
    const communs = Object.keys(series[c]).filter(a => Number.isFinite(series[c][a]) && Number.isFinite(sr[a]) && sr[a] !== 0);
    if (communs.length < 2) continue;
    const sans = c === 'capex' ? Math.abs : x => x;
    if (communs.every(a => Math.abs(sans(series[c][a]) - sans(sr[a])) / Math.abs(sans(sr[a])) <= TOL_SUR)) out[c] = series[c];
  }
  return out;
}

/* Lignes neuves à écrire : exercices POSTÉRIEURS au dernier connu (le repli
   prolonge la série, il ne réécrit pas son passé), CA ou EBIT présents. */
export function lignesNeuves(series, connus) {
  const ans = new Set();
  for (const c of Object.keys(series || {})) for (const a of Object.keys(series[c])) ans.add(Number(a));
  const out = [];
  for (const a of [...ans].sort()) {
    if (connus.has(a) || (connus.size && a <= Math.max(...connus))) continue;
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
  if (!dep.W) installerPolitesse();
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
      /* Un passage lit au plus 4 tranches de 4 Mo : une archive ESEF de 30 Mo
         en demande plusieurs. La validation se rejoue à CHAQUE passage, donc un
         « mapping » KO avant la fin du premier dépôt veut seulement dire « rien
         lu encore » : on ne s'arrête que sur fin de lecture, refus de découverte,
         absence de progrès, ou calibration acquise sur les deux dépôts récents
         (les exercices neufs sont là ; les anciens sont déjà en base). */
      let avant = '';
      for (let i = 0; i < 40; i++) {
        W.reinitialiserCompteur();
        r = await W.construireEsef(e, kv, { lots: 4, tranche: 4096 });
        await kv.put('esef:' + c.ticker, JSON.stringify(r));
        const pos = `${r.curseur || 0}:${r.octet || 0}`;
        if (r.complet || (r.statut === 'KO' && r.cause && r.cause !== 'mapping') || pos === avant) break;
        if (r.statut === 'OK' && (r.curseur || 0) >= 2) break;
        avant = pos;
      }
      if (!r || r.statut !== 'OK' || !r.series) { r && r.cause === 'absent_index' ? B.absentes++ : B.refusees++; if (ESSAI) console.log(`  ${c.ticker} : ${r ? (r.cause || r.statut) + ' — ' + String(r.erreur || '').slice(0, 90) + ` · ${Object.keys(r.brut || {}).length} postes lus (CA : ${Object.keys((r.brut || {}).revenue || {}).join(',') || '—'}), ${(r.depots || []).length} dépôts, curseur ${r.curseur || 0}, ${Object.values(r.depotsLus || {}).map(d => d.statut + '/' + (d.ecrits || 0)).join(' ')}` : 'rien'}`); continue; }
      if (c.devise_comptes && r.devise && c.devise_comptes !== r.devise) { B.devise++; continue; }
      B.validees++;
      const surs = postesSurs(r.series, ref);
      const neuves = lignesNeuves(surs, new Set(rows.map(x => Number(x.exercice))));
      if (ESSAI) console.log(`  ${c.ticker} : calibrée · ${Object.keys(surs).length}/${Object.keys(r.series).length} postes sûrs · à écrire : ${neuves.map(n => n.exercice).join(',') || 'rien'}`);
      if (!neuves.length || ESSAI) { B.lignes += ESSAI ? neuves.length : 0; continue; }
      for (const n of neuves) {
        await q(`INSERT OR IGNORE INTO comptes2 (ticker, exercice, ${COLONNES.join(', ')}, source) VALUES (?, ?, ${COLONNES.map(() => '?').join(', ')}, 'esef')`,
          [c.ticker, n.exercice, ...COLONNES.map(k => n.l[k])]);
        B.lignes++;
      }
      await q('UPDATE societe SET comptes_maj = ? WHERE ticker = ?', [RUN_TS, c.ticker]);
    } catch (err) { B.erreurs++; console.log(`  ${c.ticker} : ${String(err && err.message || err).slice(0, 120)}`); }
    if (!dep.W) await new Promise(z => setTimeout(z, PAUSE_MS));
  }
  const msg = `${ESSAI ? 'ESSAI · ' : ''}${B.societes} sociétés : ${B.validees} calibrées, ${B.refusees} refusées, ${B.absentes} absentes de l'index, ${B.devise} devise divergente · ${B.lignes} exercice(s) ${ESSAI ? 'à écrire' : 'écrits'}`;
  console.log(msg);
  if (!ESSAI) await q("INSERT INTO runs (debut, fin, etape, statut, lignes_ecrites, canari, message, detail) VALUES (?, ?, 'incr14_esef_intl', ?, ?, ?, ?, ?)",
    [RUN_TS, new Date().toISOString().slice(0, 19) + 'Z', B.erreurs && !B.validees ? 'PANNE' : 'OK', B.lignes, B.validees ? 'VERT' : 'ORANGE', msg, JSON.stringify(B)]);
  return B;
}

if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split('/').pop())) main().catch(e => { console.error(e); process.exit(1); });
