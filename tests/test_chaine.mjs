/* Chaine metriques -> valorisation sur une base D1 emulee (node:sqlite).
   Aucun reseau : la BCE est bouchonnee. Verifie les verdicts de l'audit :
   SPAC hors perimetre, « incomplet » distinct de « float », cyclique par
   chutes d'EBIT, moat propre du noyau, croissance demontree du cadre, seuil N
   deux phases, taux-obstacle par enveloppe, conversion de devise. */
import { creerBase, installer } from './faux_d1.mjs';

process.env.CF_ACCOUNT = 'a'; process.env.CF_DB = 'b'; process.env.CF_TOKEN = 'c';
const db = creerBase();
const ANS = Array.from({ length: 12 }, (_, i) => 2014 + i);
let ok = 0, ko = 0;
const verifier = (c, m) => { if (c) { ok++; console.log('  ✅ ' + m); } else { ko++; console.log('  ❌ ' + m); } };

function societe(t, o = {}) {
  db.prepare("INSERT INTO societe (ticker, cik, nom, secteur, sic, eligible_pea, capitalisation, devise_comptes, vaneck, origine) VALUES (?,?,?,?,?,?,?,?,?,?)")
    .run(t, o.cik ?? (t.includes('.') ? null : '1'), o.nom || t, o.secteur || null, o.sic || null, o.pea ?? 0, o.capi ?? null, o.devC || null, o.vaneck || null, o.origine || (t.includes('.') ? 'intl' : 'sec'));
}
function comptes(t, f, src = 'frames') {
  for (let i = 0; i < ANS.length; i++) {
    const r = f(i);
    if (!r) continue;
    const cols = Object.keys(r);
    db.prepare(`INSERT INTO comptes2 (ticker, exercice, ${cols.join(', ')}, source) VALUES (?, ?, ${cols.map(() => '?').join(', ')}, ?)`)
      .run(t, ANS[i], ...cols.map(c => r[c]), src);
  }
}
// Societe saine : croissance g, marge m, echelle k
const saine = (k, g, m = 0.3) => i => {
  const ca = k * Math.pow(1 + g, i), ebit = ca * m;
  return { revenue: ca, ebit, netIncome: ebit * 0.78, grossProfit: ca * 0.65, cfo: ebit * 0.95, capex: ca * 0.05,
    tax: ebit * 0.2, pretax: ebit * 0.98, assets: ca * 1.4, ppe: ca * 0.3, goodwill: ca * 0.1, receivables: ca * 0.15,
    inventory: ca * 0.05, payables: ca * 0.08, currentLiab: ca * 0.35, debt: ca * 0.2, cash: ca * 0.15,
    equity: ca * 0.6, shares: 1e9, intangExGW: ca * 0.02 };
};
for (const t of ['AAPL', 'V', 'MA', 'NVDA']) { societe(t, { capi: 1e12 }); comptes(t, saine(50e9, 0.10)); }
societe('MSFT', { capi: 3.5e12, secteur: 'Services-Prepackaged Software', vaneck: 'MOAT' });
comptes('MSFT', saine(90e9, 0.13, 0.42));   // FCF ~ 0.95*0.42*CA - 0.05*CA ; fin ~ 280 Md CA
// Cyclique : deux chutes d'EBIT > 25 %
societe('CYCL', { capi: 5e9 });
comptes('CYCL', i => { const s = saine(5e9, 0.03)(i); const f = [1, 1.1, 0.6, 0.7, 1, 1.2, 0.8, 0.55, 1, 1.1, 1.2, 1.25][i]; s.ebit *= f; s.pretax *= f; return s; });
// Croissance forte reguliere : ne doit PAS etre cyclique (ancienne regle le marquait)
societe('CROI', { capi: 60e9 });
comptes('CROI', saine(2e9, 0.18, 0.25));
// SPAC
societe('SPAC', { sic: '6770', secteur: 'Blank Checks', capi: 3e8 });
comptes('SPAC', saine(1e7, 0.0, 0.1));
// Incomplet : ni dette, ni passif courant, ni BFR
societe('INCO', { capi: 2e10 });
comptes('INCO', i => { const s = saine(3e9, 0.05)(i); for (const k of ['debt', 'currentLiab', 'receivables', 'inventory', 'payables', 'ppe']) delete s[k]; return s; });
// Equinor : comptes en USD, cotation en NOK
societe('EQNR.OL', { pea: 1, capi: 800e9, devC: 'USD' });   // 800 Md NOK ~ 72 Md USD
comptes('EQNR.OL', saine(100e9, 0.02, 0.2), 'stockanalysis');

const F = installer(db, { 'ecb.europa.eu': { body: "<Cube time='2026-09-30'>" + [['USD', '1.08'], ['JPY', '160'], ['GBP', '0.85'], ['CHF', '0.94'], ['SEK', '11.2'], ['NOK', '11.7'], ['DKK', '7.46'], ['PLN', '4.3'], ['CZK', '25'], ['HUF', '390'], ['ISK', '148'], ['KRW', '1500'], ['CAD', '1.5']].map(([c, r]) => `<Cube currency='${c}' rate='${r}'/>`).join('') + "</Cube>" } });

console.log('— incr5 —');
await import(new URL('../src/incr5_metriques.mjs', import.meta.url).href);
await new Promise(r => setTimeout(r, 300));
const M = t => db.prepare('SELECT * FROM metriques WHERE ticker = ?').get(t);
verifier(M('SPAC')?.exclusion?.startsWith('hors_perimetre — SPAC'), `SPAC hors périmètre (${M('SPAC')?.exclusion})`);
verifier(M('INCO')?.exclusion?.startsWith('incomplet'), `lacunaire → incomplet (${String(M('INCO')?.exclusion).slice(0, 60)})`);
verifier(String(M('CYCL')?.drapeaux || '').includes('cyclique') && M('CYCL').ebit_chutes >= 2, `deux chutes d'EBIT → cyclique (${M('CYCL')?.ebit_chutes})`);
verifier(!String(M('CROI')?.drapeaux || '').includes('cyclique'), `croissance de 18 %/an non cyclique (dispersion ${M('CROI')?.ebit_dispersion?.toFixed(2)})`);
verifier(M('MSFT')?.moat_propre && M('MSFT').score_moat_max >= 3, `moat propre du noyau (${M('MSFT')?.moat_propre} ${M('MSFT')?.score_moat}/${M('MSFT')?.score_moat_max})`);
verifier(M('MSFT')?.croissance_source === 'fcf' && M('MSFT').croissance_demontree > 10, `croissance démontrée FCF (${M('MSFT')?.croissance_demontree?.toFixed(1)} %)`);
verifier(M('MSFT')?.epv_capitaux > 0 && M('MSFT')?.plancher_epv > 0, 'EPV du noyau stockée');
verifier(Number.isFinite(M('MSFT')?.fcf_divergence) && Math.abs(M('MSFT').fcf_divergence) < 3 && M('MSFT').base_creux === 0, `FCF aligné sur CA et EBIT : aucune divergence (${M('MSFT')?.fcf_divergence?.toFixed(1)} pts), base saine`);
verifier(M('MSFT')?.fcf_depart > 0 && M('MSFT')?.noyau === 'w178', `FCF de départ et version du noyau (${M('MSFT')?.noyau})`);

// Etage prix simule
for (const [t, c] of [['MSFT', 470], ['AAPL', 230], ['V', 330], ['MA', 560], ['NVDA', 180], ['CROI', 90], ['CYCL', 40], ['EQNR.OL', 280]])
  db.prepare('UPDATE metriques SET cours = ? WHERE ticker = ?').run(c, t);

console.log('\n— incr7 —');
const V = await import(new URL('../src/incr7_valorisation.mjs', import.meta.url).href);
await V.main();
const msft = M('MSFT');
const y = msft.fcf_yield, gordon = 100 * (0.115 - y) / (1 + y);
verifier(msft.croissance_implicite > gordon + 3, `MSFT : N deux phases ${msft.croissance_implicite.toFixed(1)} % > Gordon ${gordon.toFixed(1)} %`);
verifier(msft.taux_obstacle === 11.5 && M('EQNR.OL').taux_obstacle === 10, 'taux-obstacle par enveloppe (CTO 11,5 · PEA 10)');
verifier(['vert', 'orange', 'jaune', 'rouge'].includes(msft.bande_n) && Math.abs(msft.n_ratio - msft.croissance_implicite / msft.croissance_demontree) < 1e-6, `bande du cadre (${msft.bande_n}, ratio ${msft.n_ratio.toFixed(2)})`);
const eq = M('EQNR.OL');
verifier(eq.devise_ecart === 'USD/NOK', `Equinor : capitalisation convertie en USD (${eq.devise_ecart})`);
const capiUSD = 800e9 * (1 / 11.7) / (1 / 1.08);
verifier(Math.abs(eq.fcf_yield / (M("EQNR.OL").fcf_depart / capiUSD) - 1) < 1e-4, `rendement FCF sur la capitalisation en USD (${(eq.fcf_yield * 100).toFixed(1)} %)`);
verifier(Math.abs(eq.capi_eur - 800e9 / 11.7) < 1, 'capitalisation en euros pour le plancher');
verifier(M('SPAC').croissance_implicite === null, 'exclue : pas valorisée');
// Reserve du cadre (§11) : calcul pur
{
  const base = { ticker: 'XX', capitalisation: 1e10, fcf_depart: 1e9, dette_nette: 0, eligible_pea: 0,
    croissance_demontree: 30, fcf_divergence: 2, base_creux: 0 };
  const sain = V.valoriser(base, { USD: 1, EUR: 1 });
  verifier(sain.bande_n === 'vert', `FCF aligné : bande conservée (${sain.bande_n})`);
  const div = V.valoriser({ ...base, fcf_divergence: 15 }, { USD: 1, EUR: 1 });
  verifier(div.bande_n === 'reserve' && /bande vert non retenue/.test(div.n_note) && div.n_ratio === sain.n_ratio, `FCF +15 pts : réserve, ratio inchangé (${div.n_note})`);
  const creux = V.valoriser({ ...base, base_creux: 1 }, { USD: 1, EUR: 1 });
  verifier(creux.bande_n === 'reserve' && /creux/.test(creux.n_note), 'départ en creux : réserve');
  const rouge = V.valoriser({ ...base, capitalisation: 5e10, croissance_demontree: 2, fcf_divergence: 15 }, { USD: 1, EUR: 1 });
  verifier(rouge.bande_n === 'rouge', 'un rouge reste rouge malgré la divergence');
}
// Radiation : le cours disparait, la bande ne doit pas survivre
db.prepare("UPDATE metriques SET cours = NULL WHERE ticker = 'CROI'").run();
await V.main();
verifier(M('CROI').bande_n === null && M('CROI').croissance_implicite === null, 'sans cours : ratios effacés');
console.log(`\n${ok} réussis, ${ko} échoués`);
F.restaurer();
if (ko) process.exitCode = 1;
