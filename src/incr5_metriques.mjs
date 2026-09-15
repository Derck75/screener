/* INCREMENT 5 v3 — Metriques via le NOYAU du worker -> table `metriques`.
 *
 * Ce script NE CALCULE RIEN. Il lit les postes bruts, appelle les fonctions
 * du worker et stocke le resultat. C'est la seule facon de garantir qu'il
 * n'existe qu'UNE definition du ROIC, du capital investi et de la dette : un
 * candidat sorti d'ici doit rendre les memes chiffres sous `dossier`.
 *
 * Ce qu'on gagne par rapport a une reimplementation : quatre denominateurs
 * avec seuil d'intensite, capital employe brut en repli, contre-preuve
 * publiee, retraitement de l'amortissement d'acquisition, recomposition des
 * incorporels, typologie en six profils, detection de rupture de serie.
 *
 * Sonde integree, budget verifie, ecriture differentielle.
 */

import { derives, roicRetenu, profilSociete, ancrageEPV, mediane } from './noyau.js';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const { CF_ACCOUNT, CF_DB, CF_TOKEN } = process.env;
const URL_D1 = `https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT}/d1/database/${CF_DB}/query`;
const SEUIL = Number(process.env.SEUIL_USD || 9.0);   // PAS un WACC
/* PLAFOND D'ECRITURES. 0 = controle desactive : c'est le reglage correct en
   plan payant, ou un plafond local devient une contrainte artificielle qui
   bloque un systeme par ailleurs sain.
   ON TRACE L'ORIGINE DE LA VALEUR, PAS SEULEMENT LA VALEUR. Un log qui
   annonce « plafond 70000 » ne dit pas si ce 70000 vient d'un fichier perime
   ou d'une variable d'environnement posee dans le workflow. Ces deux causes
   ont des remedes opposes — recoller le fichier, ou retirer une ligne du
   YAML — et les confondre a deja coute plusieurs tours. */
const PLAFOND_BRUT = process.env.PLAFOND_ECRITURES;
const PLAFOND = Number(PLAFOND_BRUT || 0);
const PLAFOND_ORIGINE = (PLAFOND_BRUT === undefined || PLAFOND_BRUT === "")
  ? "defaut du fichier"
  : `variable d'environnement PLAFOND_ECRITURES="${PLAFOND_BRUT}"`;
const RUN_TS = new Date().toISOString().slice(0, 19) + "Z";
const CANARI = ["AAPL", "MSFT", "V", "MA", "NVDA"];

const S = { t0: Date.now(), phases: {}, compteurs: {}, erreurs: {}, phase: null, pt: 0 };
const phase = n => { if (S.phase) S.phases[S.phase] = Math.round((Date.now() - S.pt) / 100) / 10;
                     S.phase = n; S.pt = Date.now(); console.log(`[${n}]`); };
const compte = (k, n = 1) => { S.compteurs[k] = (S.compteurs[k] || 0) + n; };
const erreur = (c, ex) => { const e = S.erreurs[c] ||= { n: 0, exemple: null };
                            e.n++; if (!e.exemple && ex) e.exemple = String(ex).slice(0, 120); };

async function d1(sql, params = []) {
  const r = await fetch(URL_D1, {
    method: "POST",
    headers: { Authorization: `Bearer ${CF_TOKEN}`, "Content-Type": "application/json" },
    body: JSON.stringify({ sql, params }),
  });
  const j = await r.json().catch(() => null);
  if (!j?.success) {
    const err = JSON.stringify(j?.errors || r.status).slice(0, 300);
    // 7500 est le code generique de toute erreur SQL : le tester masquait le
    // vrai message. Seul le libelle du quota fait foi.
    if (err.includes("daily row write limit"))
      await fatal("QUOTA D1 EPUISE — reprise a minuit UTC, base laissee en etat partiel");
    await fatal("erreur D1 (message brut) : " + err);
  }
  return j.result;
}

async function journal(statut, lignes, canari, message) {
  if (S.phase) S.phases[S.phase] = Math.round((Date.now() - S.pt) / 100) / 10;
  const detail = { duree_s: Math.round((Date.now() - S.t0) / 100) / 10,
                   phases: S.phases, compteurs: S.compteurs, erreurs: S.erreurs };
  await fetch(URL_D1, {
    method: "POST",
    headers: { Authorization: `Bearer ${CF_TOKEN}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      sql: "INSERT INTO runs (debut, fin, etape, statut, lignes_ecrites, canari, message, detail)"
         + " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
      params: [RUN_TS, new Date().toISOString().slice(0, 19) + "Z", "incr5_metriques",
               statut, lignes, canari, String(message).slice(0, 400), JSON.stringify(detail)],
    }),
  }).catch(() => {});
}

async function fatal(msg) {
  console.log("ECHEC : " + msg);
  erreur("fatal", msg);
  await journal("PANNE", 0, "ROUGE", msg);
  process.exit(1);
}

const nb = v => (v === null || v === undefined || !Number.isFinite(Number(v)))
  ? "NULL" : String(Math.round(Number(v) * 1e6) / 1e6);
// COUPER AVANT D'ECHAPPER. L'inverse tronquait parfois une apostrophe
// doublee en son milieu et laissait un guillemet orphelin : la requete
// entiere devenait invalide, sur un motif de refus contenant « d'affaires ».
const tx = v => (v === null || v === undefined)
  ? "NULL" : "'" + String(v).slice(0, 200).replaceAll("'", "''") + "'";

const COLONNES = ["roic_median", "roic_dernier", "spread_median", "n_ex_roic_sup_seuil",
  "n_ex_total", "seuil_rentabilite_forfaitaire", "denominateur_roic", "base_roic",
  "contre_preuve_ecart", "profil_type", "profil_ko", "ca_cagr", "fcf_cagr", "ebit_cagr",
  "ca_hausse_n", "ca_hausse_m", "fcf_positif_n", "fcf_positif_m", "mb_mediane",
  "conversion_fcf_rn", "actions_var_5a", "dette_sur_ca", "cp_sur_ca", "score_moat",
  "score_moat_max", "biais_acquereur", "n_non_calculable", "exclusion", "maj"];

/* EMPREINTE — met fin au debat « quel fichier tourne ? ».
   Une version ecrite a la main peut etre fausse : il suffit d'oublier de
   l'incrementer, et c'est exactement ce qui s'est produit — la correction du
   plafond n'avait pas bouge le « v2 », rendant le log indistinguable de la
   version d'avant. Une empreinte est calculee sur le contenu REEL du fichier
   qui s'execute : elle ne peut pas mentir. */
function empreinte() {
  try {
    const chemin = fileURLToPath(import.meta.url);
    const brut = readFileSync(chemin);
    return createHash('sha256').update(brut).digest('hex').slice(0, 8)
         + ` · ${brut.toString().split('\n').length} lignes`;
  } catch { return "indisponible"; }
}

async function main() {
  console.log(`incr5_metriques v3 (noyau partage) — Run ${RUN_TS}`);
  console.log(`empreinte ${empreinte()}`);
  console.log(`seuil de rentabilite forfaitaire : ${SEUIL} % — PAS un WACC`);
  console.log(`plafond d'ecritures : ${PLAFOND === 0 ? "AUCUN (controle desactive)" : PLAFOND} — origine : ${PLAFOND_ORIGINE}`);

  phase("univers");
  const soc = {};
  for (const l of (await d1("SELECT ticker, vaneck, vaneck_sorti_le FROM societe"))[0].results)
    soc[l.ticker] = l;

  phase("lecture comptes");
  const parTicker = {};
  for (let p = 0; ; p++) {
    const r = (await d1("SELECT * FROM comptes2 ORDER BY ticker, exercice LIMIT 5000 OFFSET ?",
                        [p * 5000]))[0].results;
    if (!r.length) break;
    for (const l of r) (parTicker[l.ticker] ||= []).push(l);
  }
  compte("societes", Object.keys(parTicker).length);
  console.log(`  ${Object.keys(parTicker).length} societes`);

  phase("noyau");
  const rangs = {}, etatCanari = {};
  for (const [ticker, lignes] of Object.entries(parTicker)) {
    // series[concept][annee] — le format exact qu'attend derives()
    const series = {};
    for (const l of lignes) {
      for (const [k, v] of Object.entries(l)) {
        if (["ticker", "exercice", "source", "clot"].includes(k)) continue;
        if (v === null || v === undefined) continue;
        (series[k] ||= {})[l.exercice] = Number(v);
      }
    }
    let der, R, P, epv;
    try {
      der = derives(series, "USD");
      R = roicRetenu(der);
      P = profilSociete(der, {});
      epv = ancrageEPV(der, null);
    } catch (e) { erreur("noyau", `${ticker} ${e.message}`); continue; }

    const ans = der.annees || [];
    const der1 = ans.length ? der.lignes[ans[ans.length - 1]] : null;
    const roicMed = Number.isFinite(R.mediane) ? R.mediane * 100 : null;
    const serie = (R.serie || []).map(x => x * 100);
    const nEx = serie.length;
    const nSup = serie.filter(x => x >= SEUIL).length;

    const srz = c => ans.map(a => der.lignes[a]?.[c]).filter(Number.isFinite);
    const cg = c => { const v = srz(c);
      return (v.length >= 3 && v[0] > 0 && v[v.length - 1] > 0)
        ? 100 * (Math.pow(v[v.length - 1] / v[0], 1 / (v.length - 1)) - 1) : null; };
    const caS = srz("ca"), fcfS = srz("fcf");

    // EXCLUSION : la typologie du noyau decide, pas un seuil local.
    // Un ROIC eleve n'exclut jamais — c'est la signature d'un modele
    // asset-light, et v1 ecartait ainsi Apple, Nvidia et Mastercard.
    const excl = ["financier", "float", "incoherent"].includes(P.type)
      ? `${P.type} — ${String(P.ko || "").slice(0, 150)}` : null;
    if (excl) compte("exclues_" + P.type);
    compte("profil_" + P.type);

    const ca = der1?.ca || null;
    const detteCA = (der1?.dette != null && ca) ? der1.dette / ca : null;
    const cpCA = (der1?.equity != null && ca) ? der1.equity / ca : null;
    const gw = der1?.goodwill, act = der1?.assets;
    const biais = (gw && act && gw / act > 0.30) ? 1 : 0;

    let pts = 0, mx = 0;
    const sv = soc[ticker] || {};
    if (sv.vaneck && !sv.vaneck_sorti_le) { pts += 3; mx += 3; }
    if (nEx >= 4) { mx += 3; const sous = nEx - nSup;
                    pts += sous === 0 ? 3 : sous === 1 ? 2 : sous === 2 ? 1 : 0; }
    if (roicMed != null) { mx += 2; pts += roicMed >= 15 ? 2 : roicMed >= 10 ? 1 : 0; }
    const mbS = ans.map(a => { const l = der.lignes[a];
      return (l?.ca && Number.isFinite(l?.margeBrute)) ? l.margeBrute * 100 : null; })
      .filter(Number.isFinite);
    const mbMed = mbS.length ? mediane(mbS) : null;

    if (CANARI.includes(ticker)) etatCanari[ticker] = { roicMed, type: P.type, excl };

    const actS = srz("shares");
    rangs[ticker] = {
      roic_median: roicMed, roic_dernier: serie.length ? serie[serie.length - 1] : null,
      spread_median: roicMed == null ? null : roicMed - SEUIL,
      n_ex_roic_sup_seuil: nSup, n_ex_total: nEx,
      seuil_rentabilite_forfaitaire: SEUIL,
      denominateur_roic: R.cle || null, base_roic: R.base || null,
      contre_preuve_ecart: R.contreExp?.ecart ?? R.contreAutre?.ecart ?? null,
      profil_type: P.type, profil_ko: P.ko ? String(P.ko).slice(0, 200) : null,
      ca_cagr: cg("ca"), fcf_cagr: cg("fcf"), ebit_cagr: cg("ebit"),
      ca_hausse_n: caS.filter((v, i) => i > 0 && v > caS[i - 1]).length,
      ca_hausse_m: Math.max(0, caS.length - 1),
      fcf_positif_n: fcfS.filter(v => v > 0).length, fcf_positif_m: fcfS.length,
      mb_mediane: mbMed,
      conversion_fcf_rn: null, actions_var_5a:
        (actS.length >= 2 && actS[0] > 0) ? 100 * (actS[actS.length - 1] / actS[0] - 1) : null,
      dette_sur_ca: detteCA, cp_sur_ca: cpCA,
      score_moat: pts, score_moat_max: mx, biais_acquereur: biais,
      n_non_calculable: [roicMed, cg("ca"), cg("fcf"), mbMed].filter(x => x == null).length,
      exclusion: excl, maj: RUN_TS,
    };
    if (Number.isFinite(epv?.valeur)) compte("epv_calculable");
  }

  phase("canari");
  const perdus = CANARI.filter(t => !etatCanari[t] || etatCanari[t].excl);
  for (const t of CANARI) {
    const e = etatCanari[t];
    console.log(e ? `  ${t.padEnd(6)} ROIC ${String(Math.round(e.roicMed ?? 0)).padStart(4)} % `
      + `profil ${e.type}${e.excl ? "  EXCLUE" : "  retenue"}` : `  ${t.padEnd(6)} ABSENT`);
  }

  phase("differentiel");
  const cols = COLONNES.filter(c => c !== "maj");
  const existant = {};
  for (let p = 0; ; p++) {
    const r = (await d1(`SELECT ticker, ${cols.join(", ")} FROM metriques `
                      + `ORDER BY ticker LIMIT 5000 OFFSET ?`, [p * 5000]))[0].results;
    if (!r.length) break;
    for (const l of r) existant[l.ticker] = l;
  }
  const pareil = (a, b) => cols.every(c => {
    const x = a[c], y = b[c];
    if (x == null && y == null) return true;
    if (x == null || y == null) return false;
    const nx = Number(x), ny = Number(y);
    return (Number.isFinite(nx) && Number.isFinite(ny))
      ? Math.abs(nx - ny) < 1e-6 : String(x) === String(y);
  });
  const aEcrire = Object.entries(rangs).filter(([t, v]) => !existant[t] || !pareil(existant[t], v));
  compte("inchangees", Object.keys(rangs).length - aEcrire.length);
  compte("modifiees", aEcrire.length);
  console.log(`  ${aEcrire.length} a ecrire, ${Object.keys(rangs).length - aEcrire.length} inchangees`);

  if (aEcrire.length) {
    phase("ecriture");
    const cout = aEcrire.length * 3;   // metriques porte 2 index
    if (PLAFOND > 0) {
      // Les intentions ne comptent que deux heures : un run qui n'a jamais
      // abouti n'a pas ecrit ce qu'il annoncait, et les cumuler bloquait le
      // systeme sur 69 720 ecritures fantomes.
      const deja = (await d1("SELECT COALESCE(SUM(lignes_ecrites),0) AS n FROM runs "
                           + "WHERE debut >= date('now') AND (statut = 'OK' OR "
                           + "(statut = 'EN COURS' AND debut >= datetime('now','-2 hours')))"
                            ))[0].results[0].n || 0;
      console.log(`  budget : ${deja} deja ecrites, ${cout} prevues, plafond ${PLAFOND}`);
      if (deja + cout > PLAFOND)
        await fatal(`BUDGET INSUFFISANT — ${deja} + ${cout} depasse ${PLAFOND}`);
    } else {
      console.log(`  budget : controle desactive, ${cout} lignes prevues`);
    }
    // Intention posee AVANT d'ecrire : un run coupe laisse sinon ses
    // ecritures invisibles au compteur du lendemain.
    await journal("EN COURS", cout, "—", `intention de ${cout} ecritures`);

    const liste = "ticker, " + COLONNES.join(", ");
    for (let i = 0; i < aEcrire.length; i += 100) {
      const lot = aEcrire.slice(i, i + 100).map(([t, v]) =>
        "(" + tx(t) + ", " + COLONNES.map(c =>
          ["denominateur_roic", "base_roic", "profil_type", "profil_ko",
           "exclusion", "maj"].includes(c) ? tx(v[c]) : nb(v[c])).join(", ") + ")");
      await d1(`INSERT OR REPLACE INTO metriques (${liste}) VALUES ` + lot.join(", "));
      if ((i + 100) % 1000 === 0) console.log(`  ecrit ${Math.min(i + 100, aEcrire.length)}/${aEcrire.length}`);
    }
  }

  phase("rapport");
  console.log("\n--- RAPPORT ---");
  for (const l of (await d1("SELECT profil_type, COUNT(*) AS n FROM metriques "
                          + "GROUP BY profil_type ORDER BY n DESC"))[0].results)
    console.log(`  profil ${String(l.profil_type).padEnd(16)} ${l.n}`);
  for (const [lib, w] of [["ROIC >= 15 %", "roic_median >= 15"],
       ["  + spread > 0", "roic_median >= 15 AND spread_median > 0"],
       ["  + CA et FCF en hausse", "roic_median >= 15 AND spread_median > 0 AND ca_cagr > 0 AND fcf_cagr > 0"]]) {
    const n = (await d1(`SELECT COUNT(*) AS n FROM metriques WHERE exclusion IS NULL AND ${w}`))[0].results[0].n;
    console.log(`  ${lib.padEnd(24)} ${n}`);
  }
  console.log("\n--- SONDE ---");
  console.log("  " + Object.entries(S.compteurs).map(([k, v]) => `${k}=${v}`).join(", "));
  for (const [c, e] of Object.entries(S.erreurs)) console.log(`  ERREUR ${c} x${e.n} — ${e.exemple || ""}`);

  await journal(perdus.length ? "PANNE" : "OK", aEcrire.length,
                perdus.length ? "ROUGE" : "VERT",
                perdus.length ? `canari perdu : ${perdus}` : `${aEcrire.length} lignes ecrites`);
  if (perdus.length) { console.log(`\nPANNE : canari perdu — ${perdus}`); process.exit(1); }
  console.log("OK");
}

main().catch(async e => { await fatal(e.stack?.slice(0, 300) || e.message); });
