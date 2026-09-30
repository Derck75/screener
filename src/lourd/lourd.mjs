#!/usr/bin/env node
/* ════════════════════════════════════════════════════════════════════════
   TRAVAUX LOURDS DU SERVEUR MCP — exécutés hors Cloudflare (audit C6)

   CE QUE FAIT CE SCRIPT. Il télécharge le code du worker `donnees-portefeuille`
   DÉPLOYÉ, l'importe tel quel dans Node et appelle SES fonctions — index
   EDGAR `companyfacts`, ingestion ESEF, pages StockAnalysis, outils délégués —
   en écrivant dans le MÊME espace KV par l'API Cloudflare. Une seule
   définition de chaque calcul, et aucune copie du worker dans ce dépôt public.

   POURQUOI. Le plan gratuit de Cloudflare accorde 10 ms de processeur par
   appel. Ces travaux en demandent de 20 à 250 : aucun découpage ne les y fait
   tenir (une décompression ZIP ou un JSON de 8 Mo ne reprennent pas d'un
   appel à l'autre). Ici, rien ne les borne.

   TROIS MODES.
   · `cron`      — passage quotidien, déclenché à l'heure par le cron du
                   worker : lots EDGAR incomplets ou vieux de plus de 7 jours
                   (index `companyfacts` relu à neuf), ESEF jusqu'à complétude,
                   StockAnalysis périmé (30 jours).
   · `planifie`  — même chose, déclenché par le `schedule` de GitHub : filet de
                   sécurité, sauté si un passage a réussi dans les 12 h.
   · demande     — `CLE` fournie : une demande du worker (rafraîchir une
                   société, ou un appel d'outil lourd) est LUE en KV derrière
                   cette clé aléatoire, et le résultat y est écrit.

   DÉLÉGATION INACTIVE CÔTÉ WORKER (variable `LOURD` absente) : le passage
   quotidien tourne en ESSAI — chargement, patches, lecture de l'univers,
   battement de cœur — sans écrire un seul compte, pour ne pas doubler le
   travail que le worker fait encore lui-même. C'est le mode de mise en route.

   JOURNAUX PUBLICS : des COMPTES, jamais un ticker ni un message d'erreur
   brut — ceux-ci vont en KV (`lourd:journal`), privé.
   ════════════════════════════════════════════════════════════════════════ */

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import url from 'node:url';

const ENV = process.env;
export const VERSION_SCRIPT = 'lourd v1';

/* ── Patches : [motif, remplacement, raison] ───────────────────────────────
   Ils relèvent des plafonds qui n'existent QUE pour le budget processeur et
   le plafond de sous-requêtes du worker. Chacun doit s'appliquer exactement
   une fois : un motif absent est publié (compte seulement), jamais ignoré. */
export const PATCHES = [
  [/const CF_PLAFOND_OCTETS = [^;]+;/, 'const CF_PLAFOND_OCTETS = 250000000;', 'companyfacts sans plafond de taille'],
  [/const ESEF_TAILLE_MAX = [^;]+;/, 'const ESEF_TAILLE_MAX = 64 * 1024 * 1024;', 'dépôts ESEF jusqu\'à 64 Mo'],
  [/const ESEF_ZIP_MAX = [^;]+;/, 'const ESEF_ZIP_MAX = 64 * 1024 * 1024;', 'archives ESEF jusqu\'à 64 Mo'],
  [/const ESEF_IX_MAX = [^;]+;/, 'const ESEF_IX_MAX = 64 * 1024 * 1024;', 'rapports iXBRL décompressés jusqu\'à 64 Mo'],
  [/const ESEF_TRANCHE_DEFAUT = [^;]+;/, 'const ESEF_TRANCHE_DEFAUT = 64 * 1024 * 1024;', 'un dépôt lu d\'un seul tenant'],
  [/const ESEF_ZIP_PAR_PASSAGE = [^;]+;/, 'const ESEF_ZIP_PAR_PASSAGE = 8;', 'huit archives par appel'],
  [/const PLAFOND_REQUETES = [^;]+;/, 'const PLAFOND_REQUETES = 400;', 'sous-requêtes non plafonnées à 50']
];

const EXPORTS = ['appeler', 'lireUnivers', 'construireEdgar', 'construireEsef', 'construireSA', 'calculerEtEcrireAncrage',
  'reinitialiserCompteur', 'litJSON', 'lotIncomplet', 'LOT1', 'LOT2', 'LOT3', 'WORKER_VERSION'];

export function preparerSource(src) {
  const appliques = [], manques = [];
  for (const [motif, rempl, raison] of PATCHES) {
    const n = (src.match(new RegExp(motif.source, 'g')) || []).length;
    if (n === 1) { src = src.replace(motif, rempl); appliques.push(raison); }
    else manques.push(`${raison} (${n} occurrence(s))`);
  }
  return { src: src + `\nexport { ${EXPORTS.join(', ')} };\n`, appliques, manques };
}

export async function chargerModule(src) {
  const { src: mod, appliques, manques } = preparerSource(src);
  const f = path.join(os.tmpdir(), `worker-lourd-${process.pid}-${Date.now()}.mjs`);
  fs.writeFileSync(f, mod);
  try { return { W: await import(url.pathToFileURL(f).href), appliques, manques }; }
  finally { try { fs.unlinkSync(f); } catch { } }
}

/* ── Accès Cloudflare ───────────────────────────────────────────────────── */
const api = chemin => `https://api.cloudflare.com/client/v4/accounts/${ENV.CF_ACCOUNT_ID}${chemin}`;
const AUTH = () => ({ Authorization: `Bearer ${ENV.CF_API_TOKEN}` });
const NOM_WORKER = () => ENV.WORKER_NOM || 'donnees-portefeuille';
const attendre = ms => new Promise(r => setTimeout(r, ms));

async function avecReprise(f, essais = 4) {
  let err;
  for (let i = 0; i < essais; i++) {
    try {
      const r = await f();
      if (r && (r.status === 429 || r.status >= 500) && i < essais - 1) { await attendre(2000 * (i + 1)); continue; }
      return r;
    } catch (e) { err = e; await attendre(1500 * (i + 1)); }
  }
  throw err;
}

/* Le contenu d'un script se lit en multipart (un module par partie) ; on
   retient la partie JavaScript. Un corps non multipart est rendu tel quel. */
export function extraireModule(txt, contentType) {
  const m = /boundary="?([^";]+)"?/i.exec(contentType || '');
  if (!m && !txt.startsWith('--')) return txt;
  const sep = '--' + (m ? m[1] : txt.slice(2, txt.search(/\r?\n/)).trim());
  for (const part of txt.split(sep)) {
    const k = part.search(/\r?\n\r?\n/);
    if (k < 0) continue;
    const tete = part.slice(0, k);
    if (/filename="[^"]+\.m?js"/i.test(tete) || /application\/javascript/i.test(tete)) {
      return part.slice(k).replace(/^\r?\n\r?\n/, '').replace(/\r?\n$/, '');
    }
  }
  throw new Error('aucune partie JavaScript dans le contenu du script');
}

export async function telechargerWorker() {
  if (ENV.WORKER_FICHIER) return fs.readFileSync(ENV.WORKER_FICHIER, 'utf8');
  let dernier = 0;
  for (const chemin of [`/workers/scripts/${NOM_WORKER()}/content/v2`, `/workers/scripts/${NOM_WORKER()}`]) {
    const r = await avecReprise(() => fetch(api(chemin), { headers: AUTH() }));
    dernier = r.status;
    if (!r.ok) continue;
    return extraireModule(await r.text(), r.headers.get('content-type'));
  }
  throw new Error(`code du worker illisible (HTTP ${dernier}) — le jeton doit porter « Workers Scripts : lecture »`);
}

/* `true` / `false` d'après la variable `LOURD` du worker ; `null` si les
   réglages sont illisibles — on travaille alors comme si la délégation était
   active, et le journal le dit. */
export async function delegationActive() {
  if (ENV.DELEGATION) return ENV.DELEGATION === 'oui';
  try {
    const r = await avecReprise(() => fetch(api(`/workers/scripts/${NOM_WORKER()}/settings`), { headers: AUTH() }));
    if (!r.ok) return null;
    const j = await r.json();
    const b = ((j && j.result && j.result.bindings) || []).find(x => x && x.name === 'LOURD');
    return !!(b && String(b.text || '').trim().toLowerCase() === 'github');
  } catch { return null; }
}

/* KV par l'API REST — même interface que la liaison d'un worker. */
export class KVRest {
  constructor() { this.base = api(`/storage/kv/namespaces/${ENV.CF_KV_ID}`); }
  async get(k, t) {
    const r = await avecReprise(() => fetch(`${this.base}/values/${encodeURIComponent(k)}`, { headers: AUTH() }));
    if (r.status === 404) return null;
    if (!r.ok) throw new Error(`KV lecture HTTP ${r.status}`);
    const v = await r.text();
    return (typeof t === 'string' ? t : (t && t.type)) === 'json' ? JSON.parse(v) : v;
  }
  async put(k, v, o) {
    const q = o && o.expirationTtl ? `?expiration_ttl=${Math.max(60, Math.round(o.expirationTtl))}` : '';
    const r = await avecReprise(() => fetch(`${this.base}/values/${encodeURIComponent(k)}${q}`, {
      method: 'PUT', headers: Object.assign({ 'Content-Type': 'text/plain; charset=utf-8' }, AUTH()), body: String(v)
    }));
    if (!r.ok) throw new Error(`KV écriture HTTP ${r.status}`);
  }
  async delete(k) {
    const r = await avecReprise(() => fetch(`${this.base}/values/${encodeURIComponent(k)}`, { method: 'DELETE', headers: AUTH() }));
    if (!r.ok && r.status !== 404) throw new Error(`KV effacement HTTP ${r.status}`);
  }
  async list(o = {}) {
    const p = new URLSearchParams();
    if (o.prefix) p.set('prefix', o.prefix);
    if (o.cursor) p.set('cursor', o.cursor);
    p.set('limit', String(Math.max(10, o.limit || 1000)));
    const r = await avecReprise(() => fetch(`${this.base}/keys?${p}`, { headers: AUTH() }));
    const j = await r.json();
    const c = j && j.result_info && j.result_info.cursor;
    return { keys: (j && j.result) || [], list_complete: !c, cursor: c || undefined };
  }
}

/* Surcouche indispensable, pas une optimisation :
   ① LECTURE DE SES PROPRES ÉCRITURES. Le KV est à cohérence différée (jusqu'à
      60 s) : relire `esef:X` juste après l'avoir écrit rendrait l'état
      PRÉCÉDENT, et la boucle d'ingestion referait le même dépôt en écrasant
      sa progression. Tout ce que ce passage écrit est donc servi de mémoire.
   ② INDEX `companyfacts` RELU À NEUF une fois par passage quand `neufCF` —
      le premier `get` rend `null`, ce qui déclenche le téléchargement — et
      gardé huit jours au lieu d'un — au-delà du cycle de reconstruction des lots (7 j) : un passage manqué ne fait pas
      retomber le worker sur son repli `companyconcept`.
   ③ COMPTE des écritures : le plan gratuit en accorde 1 000 par jour au
      compte ENTIER, worker compris. */
export function kvLourd(kv, { neufCF = false } = {}) {
  const memo = new Map(), relus = new Set();
  const o = {
    ecritures: 0,
    async get(k, t) {
      const type = typeof t === 'string' ? t : (t && t.type);
      if (neufCF && k.startsWith('cf:') && !relus.has(k)) { relus.add(k); return null; }
      if (memo.has(k)) { const v = memo.get(k); return v === null ? null : (type === 'json' ? JSON.parse(v) : v); }
      return kv.get(k, t);
    },
    async put(k, v, opt) {
      o.ecritures++;
      memo.set(k, String(v));
      return kv.put(k, v, k.startsWith('cf:') ? { expirationTtl: 691200 } : opt);
    },
    async delete(k) { o.ecritures++; memo.set(k, null); return kv.delete(k); },
    list: x => kv.list(x)
  };
  return o;
}

/* ── Bilan : compteurs publics, erreurs privées ────────────────────────── */
function nouveauBilan() { return { compteurs: {}, erreurs: [] }; }
function compte(B, k, n = 1) { B.compteurs[k] = (B.compteurs[k] || 0) + n; }
function erreur(B, etape, ticker, e) { B.erreurs.push({ quand: new Date().toISOString(), etape, ticker, message: String(e && e.message || e).slice(0, 300) }); }
const ageJours = d => { const t = d ? Date.parse(String(d).replace(' UTC', 'Z').replace(' ', 'T')) : NaN; return Number.isFinite(t) ? (Date.now() - t) / 86400000 : Infinity; };

/* ── Travaux par société ───────────────────────────────────────────────── */
const EDGAR_VALIDITE_J = 7;
const SA_VALIDITE_J = 30;
const SA_ECHEC_J = 3;
const ESEF_PASSAGES_MAX = 12;

async function edgar(W, kv, e, B, force) {
  for (const [nom, lot] of [['fd1', W.LOT1], ['fd2', W.LOT2], ['fd3', W.LOT3]]) {
    try {
      const prec = await W.litJSON(kv, nom + ':' + e.yahoo);
      const vieux = !prec || ageJours(prec.horodatage) > EDGAR_VALIDITE_J;
      if (!force && !vieux && !W.lotIncomplet(prec, lot)) { compte(B, 'edgar_lots_a_jour'); continue; }
      W.reinitialiserCompteur();
      /* Même sémantique que `rafraichir` : `force` ou lot périmé repartent de
         zéro ; un lot seulement incomplet garde la mémoire de ses tentatives. */
      const res = await W.construireEdgar(e.sec, kv, lot, nom, null, (force || vieux) ? null : prec);
      await kv.put(nom + ':' + e.yahoo, JSON.stringify(res), { expirationTtl: 2592000 });
      compte(B, res && res.statut === 'OK' ? 'edgar_lots_ecrits' : 'edgar_lots_ko');
    } catch (err) { compte(B, 'edgar_erreurs'); erreur(B, 'edgar ' + nom, e.yahoo, err); }
  }
}

async function esef(W, kv, e, B, force) {
  let avant = null;
  for (let i = 0; i < ESEF_PASSAGES_MAX; i++) {
    try {
      const cur = await W.litJSON(kv, 'esef:' + e.yahoo);
      /* Sans LEI, seules les archives inscrites à la main (`paquets`) se lisent :
         le worker ne les met pas dans sa file, ce passage les termine. */
      if (!e.lei && !(cur && cur.origine === 'paquets' && Array.isArray(cur.depots) && cur.depots.length)) return;
      const frais = cur && cur.complet && cur.depotsLe && ageJours(cur.depotsLe) <= 90;
      if (frais && !(force && i === 0)) { compte(B, i ? 'esef_completes' : 'esef_a_jour'); return; }
      const trace = cur ? `${cur.curseur || 0}:${cur.octet || 0}:${(cur.depots || []).length}:${cur.depotsLe || ''}` : 'vide';
      if (trace === avant) { compte(B, 'esef_sans_progres'); return; }
      avant = trace;
      W.reinitialiserCompteur();
      const r = await W.construireEsef(e, kv, { lots: 4 });
      await kv.put('esef:' + e.yahoo, JSON.stringify(r), { expirationTtl: 7776000 });
      compte(B, 'esef_passages');
      if (r && r.complet) { compte(B, 'esef_completes'); return; }
      /* Découverte refusée par le réseau : réessayer dans la minute ne sert à
         rien, le passage de demain le fera. */
      if (r && r.cause === 'decouverte') { compte(B, 'esef_decouverte_ko'); return; }
    } catch (err) { compte(B, 'esef_erreurs'); erreur(B, 'esef', e.yahoo, err); return; }
  }
}

async function stockanalysis(W, kv, e, B, force) {
  try {
    const cur = await W.litJSON(kv, 'sa:' + e.yahoo);
    /* Une lecture en ÉCHEC ne se garde pas trente jours : une page momentanément
       refusée figerait l'absence pour un mois. Elle se retente après trois. */
    const validite = cur && cur.statut === 'OK' ? SA_VALIDITE_J : SA_ECHEC_J;
    if (!force && cur && cur.horodatage && ageJours(cur.horodatage) <= validite) { compte(B, 'sa_a_jour'); return; }
    W.reinitialiserCompteur();
    const r = await W.construireSA(e, kv);
    await kv.put('sa:' + e.yahoo, JSON.stringify(r), { expirationTtl: 5184000 });
    compte(B, r && r.statut === 'OK' ? 'sa_ecrits' : 'sa_ko');
    await attendre(Number(ENV.PAUSE_SA_MS ?? 1200));   // courtoisie envers la source
  } catch (err) { compte(B, 'sa_erreurs'); erreur(B, 'sa', e.yahoo, err); }
}

async function societe(W, kv, e, B, force) {
  if (e.sec) { await edgar(W, kv, e, B, force); return; }
  await esef(W, kv, e, B, force);
  await stockanalysis(W, kv, e, B, force);
}

async function journaliser(W, kv, B) {
  const ancien = (await W.litJSON(kv, 'lourd:journal')) || [];
  await kv.put('lourd:journal', JSON.stringify(B.erreurs.concat(Array.isArray(ancien) ? ancien : []).slice(0, 50)), { expirationTtl: 2592000 });
}

/* ── Point d'entrée ────────────────────────────────────────────────────── */
/* Sur GitHub Actions, chaque ligne de bilan remonte aussi en annotation : le
   journal complet n'est pas lisible hors de l'interface, les annotations le
   sont par l'API. Ces lignes ne portent que des comptes, jamais un ticker. */
const dire = m => console.log((ENV.GITHUB_ACTIONS ? '::notice::' : '') + m);

export async function executerLourd({ kv, source, cle, mode, delegation }) {
  const { W, appliques, manques } = await chargerModule(source);
  const B = nouveauBilan();
  const essai = !cle && delegation === false;
  dire(`${VERSION_SCRIPT} · worker ${W.WORKER_VERSION} · mode ${cle ? 'demande' : mode}${essai ? ' (ESSAI : délégation inactive côté worker, aucun compte écrit)' : ''}${delegation === null ? ' · réglages du worker illisibles, délégation supposée active' : ''}`);
  dire(`patches appliqués ${appliques.length}/${PATCHES.length}${manques.length ? ' — absents : ' + manques.join(' ; ') : ''}`);

  if (!cle && mode === 'planifie') {
    const b = await W.litJSON(kv, 'lourd:ok');
    if (b && b.quand && ageJours(b.quand) < 0.5 && !b.essai) {
      dire('passage réussi il y a moins de 12 h : rien à faire (filet de sécurité)');
      return { saute: true, bilan: {}, erreurs: 0 };
    }
  }

  /* ── Demande ponctuelle du worker ──
     Aucun battement ici : `lourd:ok` ne doit dire QUE le passage quotidien,
     sans quoi une demande isolée masquerait un passage quotidien en panne. */
  if (cle) {
    const rec = await W.litJSON(kv, 'lourd:r:' + cle);
    if (!rec) { dire('demande introuvable (expirée ou clé erronée) : rien à faire'); return { introuvable: true, bilan: {}, erreurs: 0 }; }
    let sortie = null;
    if (rec.type === 'outil') {
      try {
        W.reinitialiserCompteur();
        sortie = await W.appeler(rec.nom, rec.args || {}, { PF: kvLourd(kv) });
        compte(B, 'outils_executes');
      } catch (err) {
        compte(B, 'outils_erreurs'); erreur(B, 'outil ' + rec.nom, rec.ticker, err);
        sortie = `🔴 Échec hors Cloudflare : ${String(err && err.message || err).slice(0, 300)}`;
      }
    } else if (rec.type === 'ticker') {
      const kvT = kvLourd(kv, { neufCF: true });
      const univ = await W.lireUnivers(kvT);
      const e = univ.find(x => String(x.yahoo).toUpperCase() === String(rec.ticker || '').toUpperCase());
      if (!e) sortie = `🔴 ${rec.ticker} n'est pas dans l'univers suivi : rien à rafraîchir.`;
      else {
        await societe(W, kvT, e, B, true);
        let ancrage = '';
        try {
          const ra = await W.calculerEtEcrireAncrage(e.yahoo, kvT, { leger: true });
          ancrage = ra && ra.ko ? `\n\n⚠️ Ancrage propre non recalculé sur les comptes neufs : ${ra.ko}.` : '\n\nAncrage propre recalculé sur les comptes neufs.';
        } catch (err) { ancrage = '\n\n⚠️ Ancrage propre non recalculé (erreur pendant le calcul).'; erreur(B, 'ancrage', e.yahoo, err); }
        const c = B.compteurs;
        sortie = `## Rafraîchissement hors Cloudflare — ${e.yahoo}\n\n`
          + (e.sec
            ? `EDGAR : ${c.edgar_lots_ecrits || 0} lot(s) réécrit(s)${c.edgar_lots_ko ? `, ${c.edgar_lots_ko} en échec` : ''}, index \`companyfacts\` relu à neuf.`
            : `${c.esef_passages ? `ESEF : ${c.esef_passages} passage(s)${c.esef_completes ? ', backfill complet' : ''}. ` : ''}StockAnalysis : ${c.sa_ecrits ? 'réécrit' : (c.sa_ko ? 'en échec' : 'inchangé')}.`)
          + (B.erreurs.length ? `\n\n🔴 ${B.erreurs.length} erreur(s) : ${B.erreurs.map(x => `${x.etape} — ${x.message}`).join(' · ')}` : '')
          + ancrage
          + `\n\nRelancer \`dossier(${e.yahoo})\` pour lire les comptes neufs.`;
      }
    } else sortie = `🔴 Type de demande inconnu : ${rec.type}.`;

    if (rec.idx) {
      const idx = (await W.litJSON(kv, rec.idx)) || {};
      await kv.put(rec.idx, JSON.stringify(Object.assign(idx, { fin: new Date().toISOString(), sortie: String(sortie || '(sortie vide)').slice(0, 60000) })), { expirationTtl: 604800 });
    }
    if (B.erreurs.length) await journaliser(W, kv, B);
    dire(`demande traitée · ${Object.entries(B.compteurs).map(([k, v]) => `${k}=${v}`).join(', ') || 'aucun compte'} · erreurs ${B.erreurs.length}`);
    return { bilan: B.compteurs, erreurs: B.erreurs.length, sortie };
  }

  /* ── Passage quotidien ── */
  const kvC = kvLourd(kv, { neufCF: true });
  const univ = (await W.lireUnivers(kvC)).filter(e => e && e.statut !== 'ecartee');
  compte(B, 'societes', univ.length);
  compte(B, 'sec', univ.filter(e => e.sec).length);
  compte(B, 'hors_sec', univ.filter(e => !e.sec).length);
  if (!essai) for (const e of univ) await societe(W, kvC, e, B, false);

  const bilan = {};
  for (const [k, v] of Object.entries(B.compteurs)) if (!/a_jour$/.test(k)) bilan[k] = v;
  bilan.ecritures_kv = kvC.ecritures + 1 + (B.erreurs.length ? 1 : 0);
  await kv.put('lourd:ok', JSON.stringify({
    quand: new Date().toISOString(), mode, essai, bilan, erreurs: B.erreurs.length,
    worker: W.WORKER_VERSION, script: VERSION_SCRIPT, patchesAbsents: manques.length, delegation
  }), { expirationTtl: 2592000 });
  if (B.erreurs.length) await journaliser(W, kv, B);
  dire('bilan : ' + Object.entries(bilan).map(([k, v]) => `${k}=${v}`).join(', '));
  dire(`erreurs : ${B.erreurs.length}${B.erreurs.length ? ' (détail privé en KV lourd:journal)' : ''}`);
  return { bilan: B.compteurs, erreurs: B.erreurs.length };
}

/* ── Ligne de commande ─────────────────────────────────────────────────── */
const direct = process.argv[1] && import.meta.url === url.pathToFileURL(path.resolve(process.argv[1])).href;
if (direct) {
  const manquants = ['CF_ACCOUNT_ID', 'CF_API_TOKEN', 'CF_KV_ID'].filter(k => !ENV[k]);
  if (manquants.length) { dire('secrets absents : ' + manquants.join(', ')); process.exit(1); }
  const cle = String(ENV.CLE || '').trim();
  if (cle && !/^[0-9a-f]{16}$/.test(cle)) { dire('clé de demande invalide'); process.exit(1); }
  try {
    const source = await telechargerWorker();
    const delegation = cle ? true : await delegationActive();
    const r = await executerLourd({ kv: new KVRest(), source, cle: cle || null, mode: ENV.MODE === 'planifie' ? 'planifie' : 'cron', delegation });
    const b = r.bilan || {};
    const produit = (b.edgar_lots_ecrits || 0) + (b.esef_passages || 0) + (b.sa_ecrits || 0) + (b.outils_executes || 0);
    if (r.erreurs && !produit) process.exitCode = 1;
  } catch (e) {
    dire('PANNE : ' + String(e && e.message || e).replace(/https?:\/\/\S+/g, '<url>').slice(0, 200));
    process.exit(1);
  }
}
