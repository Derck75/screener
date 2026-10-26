/* lourd.mjs lit le CŒUR publié à part (plan gratuit), jamais la porte. */
process.env.CF_ACCOUNT_ID = 'acc'; process.env.CF_API_TOKEN = 'tok';
const L = await import('../src/lourd/lourd.mjs');
let ko = 0;
const ok = (c, m) => { console.log((c ? '  ✅ ' : '  ❌ ') + m); if (!c) ko++; };
const COEUR = 'const WORKER_VERSION = "w200";\nasync function appeler(n) {}\nexport default {};\n';
const PORTE = 'const WORKER_VERSION = "porte-p1+w200";\nexport default {};\n';
const monde = scripts => { globalThis.fetch = async u => { const m = /\/workers\/scripts\/([^/]+)/.exec(String(u)); const s = m && scripts[m[1]]; return s ? new Response(s, { headers: { 'content-type': 'application/javascript' } }) : new Response('{}', { status: 404 }); }; };
monde({ 'donnees-portefeuille-coeur': COEUR, 'donnees-portefeuille': PORTE });
ok((await L.telechargerWorker()) === COEUR, 'cœur publié à part : lu en premier');
monde({ 'donnees-portefeuille': COEUR });
ok((await L.telechargerWorker()) === COEUR, 'plan payant sans cœur séparé : worker principal');
monde({ 'donnees-portefeuille': PORTE });
let err = null; try { await L.telechargerWorker(); } catch (e) { err = e.message; }
ok(/cœur illisible/.test(err || ''), 'seule la porte disponible : refus explicite, jamais la porte exécutée');
// 27/10/2026 — un échec de relecture ne remplace jamais une donnée valide
const ancien = { statut: 'OK', series: { revenue: { 2024: 1 } }, horodatage: '2026-09-01 00:00:00 UTC' };
let ch = L.choisirEcriture({ statut: 'KO', erreur: 'HTTP 429' }, ancien);
ok(ch.conserve && ch.valeur.series.revenue[2024] === 1 && /429/.test(ch.valeur.relectureKO.motif), 'relecture KO sur une donnée valide : données conservées, échec annoté');
ch = L.choisirEcriture({ statut: 'OK', series: { revenue: { 2025: 2 } } }, ancien);
ok(!ch.conserve && ch.valeur.series.revenue[2025] === 2, 'relecture OK : nouvelle donnée écrite');
ch = L.choisirEcriture({ statut: 'KO', erreur: 'x' }, { statut: 'KO' });
ok(!ch.conserve && ch.valeur.statut === 'KO', 'aucune donnée valide avant : l\'échec s\'écrit (et se retente à 3 jours)');
console.log(ko ? `${ko} échec(s)` : 'lecture du cœur conforme');
process.exit(ko ? 1 : 0);
