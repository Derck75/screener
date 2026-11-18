/* incr14 — repli ESEF en sommeil : éveil, calibration réutilisée, exercices
   absents seulement, devise divergente écartée. */
import { DatabaseSync } from 'node:sqlite';
import { eveil, lignesNeuves, main, installerPolitesse, postesSurs } from '../src/incr14_esef_intl.mjs';
let echecs = 0;
const ok = (c, m) => { console.log((c ? '✅ ' : '❌ ') + m); if (!c) echecs++; };
const db = new DatabaseSync(':memory:');
db.exec(`CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, debut TEXT, fin TEXT, etape TEXT, statut TEXT, lignes_ecrites INTEGER, canari TEXT, message TEXT, detail TEXT);
CREATE TABLE societe (ticker TEXT PRIMARY KEY, nom TEXT, lei TEXT, devise_comptes TEXT, comptes_maj TEXT, capitalisation REAL);
CREATE TABLE comptes2 (ticker TEXT, exercice INTEGER, revenue REAL, netIncome REAL, ebit REAL, grossProfit REAL, cfo REAL, capex REAL, da REAL, ebitda REAL, sbc REAL, tax REAL, pretax REAL, amortAcq REAL, assets REAL, ppe REAL, intangTot REAL, intangExGW REAL, goodwill REAL, receivables REAL, inventory REAL, payables REAL, currentLiab REAL, debt REAL, cash REAL, equity REAL, shares REAL, deferredRev REAL, deferredRevNC REAL, flottant REAL, source TEXT, PRIMARY KEY (ticker, exercice));`);
const q = async (sql, p = []) => { const st = db.prepare(sql); return /^\s*(SELECT|PRAGMA)/i.test(sql) ? st.all(...p).map(o => ({ ...o })) : (st.run(...p), []); };
const J = d => Date.parse(d + 'T08:00:00Z');
db.prepare("INSERT INTO runs (debut, etape, statut, lignes_ecrites, message) VALUES ('2026-11-01T08:00:00Z', 'incr8_comptes_intl_t0', 'OK', 69, '69 lignes')").run();
ok(!(await eveil(q, J('2026-11-18'))).actif, 'StockAnalysis sert : en sommeil');
db.prepare("INSERT INTO runs (debut, etape, statut, lignes_ecrites, message) VALUES ('2026-11-10T08:00:00Z', 'incr8_comptes_intl_t0', 'PANNE', 0, 'canari rouge : MC.PA statut 200')").run();
ok(!(await eveil(q, J('2026-11-14'))).actif, 'canari rouge depuis 4 j : pas encore');
ok((await eveil(q, J('2026-11-18'))).actif, 'canari rouge depuis 8 j : éveil');
const ln = lignesNeuves({ revenue: { 2024: 10, 2025: 12 }, capex: { 2025: -3 }, ebit: { 2025: 2 } }, new Set([2024]));
ok(ln.length === 1 && ln[0].exercice === 2025 && ln[0].l.capex === 3 && ln[0].l.revenue === 12, 'seul l\'exercice absent est retenu, capex en positif');
const ps = postesSurs({ revenue: { 2023: 100, 2024: 110, 2025: 120 }, ebit: { 2024: 20, 2025: 22 }, da: { 2024: 5, 2025: 6 }, cfo: { 2025: 9 } },
  { revenue: { 2023: 100.2, 2024: 110 }, ebit: { 2024: 21, 2025: 22 }, da: { 2024: 5, 2025: 6 }, cfo: { 2025: 9 } });
ok(Object.keys(ps).join() === 'revenue,da', 'postes sûrs : ≤ 0,5 % sur ≥ 2 exercices communs ; écart de 5 % ou recouvrement unique écartés');
ok(lignesNeuves({ revenue: { 2019: 1, 2026: 2 } }, new Set([2021, 2025])).map(n => n.exercice).join() === '2026', 'jamais d\'exercice antérieur au dernier connu');
// bout en bout avec un cœur simulé
db.exec("INSERT INTO societe VALUES ('MC.PA', 'LVMH', 'IOHHWNUXTU8Y8Y3SJM67', 'EUR', '2026-01-01', 300e9), ('SHEL.L', 'Shell', '21380068P1DRHMJ8KU70', 'USD', '2026-01-01', 200e9)");
db.exec("INSERT INTO comptes2 (ticker, exercice, revenue, ebit, source) VALUES ('MC.PA', 2023, 86.2e9, 22.8e9, 'stockanalysis'), ('MC.PA', 2024, 84.7e9, 19.6e9, 'stockanalysis')");
const passes = {};
const W = { reinitialiserCompteur() {}, async construireEsef(e, kv) {
  const sa = JSON.parse(await kv.get('sa:' + e.yahoo));
  passes[e.yahoo] = (passes[e.yahoo] || 0) + 1;
  // archive lue en plusieurs passages : la validation dit « mapping » tant que rien n'est lu
  if (e.yahoo === 'MC.PA' && passes[e.yahoo] < 3) return { statut: 'KO', cause: 'mapping', complet: false, curseur: 0, octet: passes[e.yahoo] * 4e6 };
  return e.yahoo === 'MC.PA' ? { statut: 'OK', complet: true, devise: 'EUR', series: { revenue: { 2023: sa.series.revenue[2023], 2024: sa.series.revenue[2024], 2025: 80.8e9 }, ebit: { 2023: 25e9, 2024: 19.6e9, 2025: 17.8e9 } } }
                             : { statut: 'OK', complet: true, devise: 'EUR', series: { revenue: { 2025: 1 } } }; } };
process.argv.push('--forcer');
const B = await main({ d1: q, W });
const l25 = db.prepare("SELECT * FROM comptes2 WHERE ticker = 'MC.PA' AND exercice = 2025").get();
ok(l25 && l25.revenue === 80.8e9 && l25.ebit === null && l25.source === 'esef' && db.prepare("SELECT source FROM comptes2 WHERE ticker='MC.PA' AND exercice=2024").get().source === 'stockanalysis', 'LVMH : 2025 ajouté (CA concordant), EBIT divergent laissé vide, 2024 intact');
ok(passes['MC.PA'] === 3, 'mapping KO en cours de lecture : on poursuit la lecture au lieu de conclure');
ok(B.devise === 1 && !db.prepare("SELECT 1 FROM comptes2 WHERE ticker = 'SHEL.L'").get(), 'Shell : devise ESEF ≠ devise connue → écartée, rien écrit');
ok(/1 exercice\(s\) écrits/.test(db.prepare("SELECT message FROM runs WHERE etape = 'incr14_esef_intl'").get().message), 'passage journalisé');
// politesse envers filings.xbrl.org : 429 rejoué après attente
let n = 0; const att = [];
globalThis.fetch = async () => ({ status: ++n < 3 ? 429 : 200, headers: { get: () => null } });
installerPolitesse(async ms => { att.push(ms); });
const r429 = await fetch('https://filings.xbrl.org/api/filings');
ok(r429.status === 200 && n === 3 && att.filter(x => x >= 10000).length === 2, '429 de filings.xbrl.org : rejoué après 10 puis 20 s');
process.exit(echecs ? 1 : 0);
