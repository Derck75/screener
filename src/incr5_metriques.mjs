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

import { derives, roicRetenu, profilSociete, ancrageEPV, ancrageEVA, mediane }
  from './noyau.js';
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

// CYCLIQUES PAR ACTIVITE. Le detecteur de dispersion ne voit pas une
// cyclique sur cinq exercices : Norsk Hydro, dont le resultat suit l'aluminium,
// passait par la voie croissance. Liste LIMITEE aux producteurs de matieres
// premieres et aux biens durables les plus sensibles au cycle. Laisses de cote
// volontairement : semi-conducteurs, materiaux de construction, chimie de
// specialite — ils auraient ecarte ASM International ou Sika, compounders au
// cycle reel mais a la tendance seculaire. Teste sur 26 libelles reels.
const CYCLIQUE_ACTIVITE = /alumin|steel|sidérurgie|\bmines?\b|mining|copper|cuivre|\bgold\b|mines d.or|silver|mines d.argent|charbon|\bcoal\b|uranium|crude petroleum|oil & gas (e&p|integrated|drilling|refining)|pétrole intégré|exploration pétrolière|raffinage|forage pétrolier|residential construction|construction résidentielle|operative builders|auto manufacturers|^automobile$|motor vehicles & passenger car|marine shipping|transport maritime|deep sea foreign|paper & paper|^papier$|lumber|^bois$|agricultural inputs|intrants agricoles|fertilizer|recreational vehicles|véhicules de loisirs|motor homes/i;

// Index « exercice:chiffre d'affaires » -> tickers, pour la detection des
// doubles consolidations.
const INDEX_CA = {};

// FINANCIERES PAR ACTIVITE DECLAREE. Le test de bilan du noyau cherche un
// profil bancaire ou foncier — dette rapportee au chiffre d'affaires. Un
// assureur n'en a pas : il porte des PROVISIONS TECHNIQUES, pas de la dette,
// et Munich Re, Talanx ou Mapfre passaient le filtre. Or son ROIC mesure le
// rendement de ses placements, et le cadre classe les financieres parmi les
// societes dont le FCF n'est pas exploitable.
// Les COURTIERS et agents sont epargnes : Marsh, Aon, Brown & Brown portent
// « insurance » dans leur libelle SEC mais n'ont aucun bilan de risque, et
// leur ROIC est reel.
// MAISONS DE TITRES ET MARCHES DE CAPITAUX ajoutes : Viel & Cie, holding
// d'un courtier interbancaire, passait parmi les candidates PEA. Leur bilan
// est un portefeuille de negociation, pas un capital d'exploitation.
// Deux pieges de nomenclature, testes :
//  - les BOURSES sont classees par la SEC sous « Security & Commodity
//    Brokers, Dealers, Exchanges » : seul le libelle exact des maisons de
//    titres (« ... & Flotation ») est vise, ICE, CME et Deutsche Borse restent ;
//  - l'exception ne protege plus que les courtiers D'ASSURANCE. Formulee sur
//    « broker », elle aurait aussi protege les maisons de titres.
// Rubrique SEC « Security Brokers, Dealers & Flotation » RETIREE apres mesure
// sur la base : elle aurait exclu SEI Investments et WisdomTree, gestionnaires
// d'actifs sans portefeuille de negociation, au ROIC reel. Les maisons de
// titres pures sont de toute facon ecartees par le test de bilan du noyau.
const ACTIVITE_FINANCIERE = /insurance|assurance|\bbank|banque|savings institution|mortgage bankers|capital markets|marchés de capitaux|investment banking/i;
const ACTIVITE_EPARGNEE = /insurance agents|insurance broker|courtage d'assurance/i;

// Duree de fade de la brique EVA, deduite du score de moat PROXY. Le cadre
// reserve vingt ans a un verdict Wide d'analyste ; un proxy ne l'autorise
// pas — quinze ans est le maximum ici, et c'est deja genereux.
function dureeFade(pts, max) {
  if (!max) return 5;
  const r = pts / max;
  return r >= 0.8 ? 15 : r >= 0.5 ? 10 : 5;
}

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

// AUTO-MIGRATION : chaque script cree lui-meme les colonnes qu'il ecrit.
// Deux incidents sont venus d'un ALTER TABLE oublie dans la console.
async function migrer(table, colonnes) {
  const presentes = new Set((await d1(`PRAGMA table_info(${table})`))[0]
    .results.map(l => l.name));
  for (const [nom, typ] of Object.entries(colonnes))
    if (!presentes.has(nom)) {
      await d1(`ALTER TABLE ${table} ADD COLUMN ${nom} ${typ}`);
      console.log(`  migration ${table} : ${nom} ajoutee`);
    }
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
  "score_moat_max", "biais_acquereur", "ebit_dispersion", "drapeaux",
  "eva_capitaux", "eva_statut", "eva_h", "ca_cagr5", "fcf_cagr5", "roic_organique",
  "n_non_calculable", "exclusion", "maj"];

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
  console.log(`incr5_metriques v14 (noyau partage) — Run ${RUN_TS}`);
  console.log(`empreinte ${empreinte()}`);
  console.log(`seuil de rentabilite forfaitaire : ${SEUIL} % — PAS un WACC`);
  console.log(`plafond d'ecritures : ${PLAFOND === 0 ? "AUCUN (controle desactive)" : PLAFOND} — origine : ${PLAFOND_ORIGINE}`);

  phase("univers");
  await migrer("metriques", { ebit_dispersion: "REAL", drapeaux: "TEXT",
    eva_capitaux: "REAL", eva_statut: "TEXT", eva_h: "INTEGER",
    ca_cagr5: "REAL", fcf_cagr5: "REAL", roic_organique: "REAL" });
  await migrer("societe", { secteur: "TEXT" });
  const soc = {};
  for (const l of (await d1("SELECT ticker, vaneck, vaneck_sorti_le, devise, pays_siege, secteur "
                          + "FROM societe"))[0].results)
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
    for (const [an, v] of Object.entries(series.revenue || {}))
      if (v > 1e7) (INDEX_CA[an + ":" + v] ||= []).push(ticker);

    // EBIT RECONSTITUE. 444 societes etaient exclues comme « flottantes »
    // alors que leur capital est positif : elles ne publient pas de sous-total
    // de resultat d'exploitation — Eli Lilly, Zoetis, ADP — et sans EBIT le
    // noyau ne calcule aucun ROIC. Le resultat avant impot prend le relais.
    // Choix PRUDENT : pour une societe endettee il est un peu inferieur a
    // l'EBIT, qu'on sous-estime donc au lieu de l'inventer.
    for (const [an, pt] of Object.entries(series.pretax || {}))
      if (series.ebit?.[an] == null && Number.isFinite(pt)) {
        (series.ebit ||= {})[an] = pt;
        compte("ebit_reconstitue_exercices");
      }
    let der, R, P, epv;
    try {
      // DEVISE REELLE, jamais "USD" en dur. Sans effet sur les ratios — ROIC,
      // EPV rapportee au cours — mais faux des qu'une europeenne entre dans la
      // base, et le noyau s'en sert pour ses controles de coherence.
      der = derives(series, (soc[ticker] || {}).devise || "USD");
      R = roicRetenu(der);
      P = profilSociete(der, {});
      epv = ancrageEPV(der, null);
    } catch (e) { erreur("noyau", `${ticker} ${e.message}`); continue; }

    // FENETRE TRONQUEE. En passant a douze exercices, la fenetre a traverse
    // l'adoption de la norme ASC 606 en 2018 : les series « incoherentes »
    // sont passees de 424 a 670, et des compounders comme Philip Morris ou
    // Yum! ont ete exclus pour un changement de PERIMETRE comptable. Si les
    // six derniers exercices sont homogenes, ils sont retenus — la fenetre
    // courte est alors signalee par le nombre d'exercices, jamais masquee.
    if (P.type === "incoherent") {
      const annees = Object.keys(series.revenue || {}).map(Number).sort((a, b) => a - b);
      const garder = new Set(annees.slice(-6).map(String));
      if (garder.size >= 5) {
        const s6 = {};
        for (const [k, parAn] of Object.entries(series))
          s6[k] = Object.fromEntries(Object.entries(parAn).filter(([a]) => garder.has(String(a))));
        try {
          const d2 = derives(s6, (soc[ticker] || {}).devise || "USD");
          const P2 = profilSociete(d2, {});
          if (!["incoherent", "float", "financier"].includes(P2.type)) {
            der = d2; P = P2; R = roicRetenu(d2); epv = ancrageEPV(d2, null);
            compte("fenetre_tronquee");
          }
        } catch (e) { /* la societe reste exclue, motif d'origine */ }
      }
    }

    const ans = der.annees || [];
    const der1 = ans.length ? der.lignes[ans[ans.length - 1]] : null;
    const roicMed = Number.isFinite(R.mediane) ? R.mediane * 100 : null;
    const serie = (R.serie || []).map(x => x * 100);
    const nEx = serie.length;
    const nSup = serie.filter(x => x >= SEUIL).length;

    const srz = c => ans.map(a => der.lignes[a]?.[c]).filter(Number.isFinite);
    // CAGR LISSES : moyenne des deux premiers et des deux derniers exercices
    // des cinq points. Un CAGR d'un point a l'autre laissait une annee
    // exceptionnelle decider seule — le depot fiscal de Coca-Cola en 2024,
    // la base minuscule de Zoom en 2014.
    const cagrV = v => {
      if (v.length < 3) return null;
      const [d, f, n] = v.length >= 5
        ? [(v[0] + v[1]) / 2, (v[v.length - 2] + v[v.length - 1]) / 2, v.length - 2]
        : [v[0], v[v.length - 1], v.length - 1];
      return (d > 0 && f > 0) ? 100 * (Math.pow(f / d, 1 / n) - 1) : null;
    };
    const cg = c => cagrV(srz(c));
    // CAGR 5 ANS, a part : le cadre impose de citer le 5 ans et la fenetre
    // longue, et de RETENIR LE PLUS BAS. Hors des Etats-Unis, la fenetre
    // longue EST de cinq ans : pas de second calcul.
    const cg5 = c => { const v = srz(c); return v.length >= 7 ? cagrV(v.slice(-6)) : null; };
    // ROIC ORGANIQUE, hors goodwill acquis. Le cadre le lit avec le comptable :
    // l'un juge l'allocation, l'autre la qualite operationnelle. Broadcom,
    // Roper, TransDigm, Schneider sortaient sous 15 % en comptable seulement.
    const orgS = srz("roicOrg");
    const roicOrgMed = orgS.length >= 3 ? mediane(orgS) * 100 : null;
    const caS = srz("ca"), fcfS = srz("fcf");

    // ═══ COUCHE D'HYGIENE — drapeaux sur les CHAMPS, jamais d'exclusion ═══
    // Un screener qui ecarte une societe sur un soupcon cree un angle mort
    // permanent ; un screener qui signale laisse l'analyse trancher. Tous ces
    // controles sont arithmetiques sur des series deja en base : aucun appel
    // reseau, aucune ecriture supplementaire.
    const drapeaux = [];

    // 1. DISPERSION DE L'EBIT. L'EPV capitalise l'EBIT MEDIAN a perpetuite.
    //    Sur une cyclique dont la fenetre contient son pic — Builders
    //    FirstSource, Alpha Metallurgical, SandRidge et Atkore sortaient a
    //    plus de 120 % du cours — cette mediane n'est pas un pouvoir
    //    beneficiaire normalise, c'est un sommet de cycle capitalise.
    //    Greenwald normalise sur un cycle COMPLET ; six exercices dont deux
    //    exceptionnels ne le sont pas.
    const ebitS = srz("ebit").filter(v => v > 0);
    let ebitDisp = null;
    if (ebitS.length >= 4) {
      const med = mediane(ebitS);
      if (med > 0) {
        ebitDisp = Math.max(...ebitS) / med;
        if (ebitDisp >= 2) drapeaux.push("cyclique");
      }
    }

    // 2. DIVISION DU NOMINAL. Un ratio d'actions proche d'un entier de 2 a 10
    //    en un seul exercice n'est pas une emission : c'est un split. Il fausse
    //    SILENCIEUSEMENT tout agregat par action, donc les multiples, donc la
    //    cherte — l'objet meme du screener. Copart affichait un BPA en recul
    //    de 11,5 % par an qui n'etait que son split 4:1 de 2021.
    const actSerie = srz("shares");
    for (let i = 1; i < actSerie.length; i++) {
      if (!actSerie[i - 1]) continue;
      const r = actSerie[i] / actSerie[i - 1];
      for (const k of [2, 3, 4, 5, 7, 10]) {
        if (Math.abs(r - k) < 0.08 * k || Math.abs(r - 1 / k) < 0.08 / k) {
          drapeaux.push("split");
          break;
        }
      }
      if (drapeaux.includes("split")) break;
    }

    // 1 bis. CYCLIQUE PAR ACTIVITE — voir CYCLIQUE_ACTIVITE.
    if (!drapeaux.includes("cyclique")
        && CYCLIQUE_ACTIVITE.test(String((soc[ticker] || {}).secteur || "")))
      drapeaux.push("cyclique");

    // 2 bis. FLUX ATYPIQUES. Un FCF median superieur a deux fois et demie le
    //    resultat net ne decrit plus l'activite : fonds de clients en transit
    //    (Euronet, prestataires de paiement), ou amortissements d'acquisitions
    //    massifs (Broadcom). Dans les deux cas, les mesures fondees sur le FCF
    //    et celles fondees sur le resultat divergent, et le lecteur doit le
    //    savoir avant de lire l'une ou l'autre. Mediane sur les exercices a
    //    resultat positif : un exercice de BFR libere ne suffit pas.
    const conv = ans.map(a => der.lignes[a])
      .filter(l => l && Number.isFinite(l.convFCF) && l.rn > 0)
      .map(l => l.convFCF);
    if (conv.length >= 3 && mediane(conv) > 2.5) drapeaux.push("flux_atypique");

    // 3. MARGE BRUTE HORS BORNES. Une marge hors [0 %, 100 %] est
    //    arithmetiquement impossible : le champ est faux, pas la societe.
    //    Evolution AB sortait a 102,8 %.
    // 4. SERIE DE MARGE NON HOMOGENE : deja detectee par le noyau, qui publie
    //    INDECIDABLE plutot qu'un plafond fonde sur un changement de perimetre.

    // EXCLUSION : la typologie du noyau decide, pas un seuil local.
    // Un ROIC eleve n'exclut jamais — c'est la signature d'un modele
    // asset-light, et v1 ecartait ainsi Apple, Nvidia et Mastercard.
    const activite = String((soc[ticker] || {}).secteur || "");
    if (!["financier", "float", "incoherent"].includes(P.type)
        && ACTIVITE_FINANCIERE.test(activite) && !ACTIVITE_EPARGNEE.test(activite)) {
      P.type = "financier";
      P.evaOK = false;
      P.ko = `activité déclarée « ${activite.slice(0, 50)} » : un assureur ou une banque `
           + `porte des provisions ou des dépôts, pas de la dette — le test de bilan `
           + `ne le voit pas, et son ROIC mesure le rendement de ses placements`;
      compte("financier_par_activite");
    }
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
    let mbMed = mbS.length ? mediane(mbS) : null;
    if (mbMed != null && (mbMed < 0 || mbMed > 100)) {
      drapeaux.push("marge_aberrante");
      erreur("marge_hors_bornes", `${ticker} : ${mbMed.toFixed(1)} %`);
      mbMed = null;          // champ invalide, societe conservee
    }
    if (drapeaux.length) compte("drapeau_" + drapeaux[0]);

    // ── SECONDE MESURE : la brique EVA du noyau ──────────────────────────
    // L'EPV suppose une croissance NULLE ; l'EVA fait croitre le capital et
    // accumule le sur-profit pendant une duree de fade. Deux hypotheses
    // opposees : leur ACCORD est le signal, jamais leur moyenne.
    // Le taux-obstacle forfaitaire remplace le WACC — pas de beta, donc pas
    // de bruit qui n'a rien a voir avec la qualite de la societe.
    let evaCap = null, evaStatut = null, evaH = null;
    // `actions`, pas `shares` : le noyau renomme le poste dans ses lignes
    // derivees. Lire `shares` rendait undefined EN SILENCE — l'operateur ?.
    // ne signale rien — et la brique EVA ne s'executait jamais.
    const actionsDer = der1?.actions;
    if (actionsDer > 0) {
      evaH = dureeFade(pts, mx);
      try {
        const E = ancrageEVA(der, SEUIL / 100, evaH, actionsDer, { profil: P });
        if (E?.ko) {
          // Un refus de domaine est une INFORMATION, pas un echec : le noyau
          // dit que le modele ne s'applique pas a cette societe.
          evaStatut = String(E.ko).slice(0, 90);
          compte("eva_refus");
        } else if (Number.isFinite(E?.valeur)) {
          evaCap = E.valeur * actionsDer;
          evaStatut = "ok";
          compte("eva_calculable");
        }
      } catch (e) { erreur("eva", `${ticker} ${e.message}`); }
    } else {
      evaStatut = "actions indisponibles";
    }

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
      ca_cagr5: cg5("ca"), fcf_cagr5: cg5("fcf"), roic_organique: roicOrgMed,
      ca_hausse_n: caS.filter((v, i) => i > 0 && v > caS[i - 1]).length,
      ca_hausse_m: Math.max(0, caS.length - 1),
      fcf_positif_n: fcfS.filter(v => v > 0).length, fcf_positif_m: fcfS.length,
      mb_mediane: mbMed,
      conversion_fcf_rn: null, actions_var_5a:
        (actS.length >= 2 && actS[0] > 0) ? 100 * (actS[actS.length - 1] / actS[0] - 1) : null,
      dette_sur_ca: detteCA, cp_sur_ca: cpCA,
      ebit_dispersion: ebitDisp, drapeaux: drapeaux.length ? drapeaux.join(",") : null,
      eva_capitaux: evaCap, eva_statut: evaStatut, eva_h: evaH,
      // `assets` n'existe pas dans les lignes derivees : il reste dans les
      // series brutes. Meme piege que `shares` ci-dessus.
      score_moat: pts, score_moat_max: mx, biais_acquereur: biais,
      n_non_calculable: [roicMed, cg("ca"), cg("fcf"), mbMed].filter(x => x == null).length,
      exclusion: excl, maj: RUN_TS,
    };
    if (Number.isFinite(epv?.valeur)) compte("epv_calculable");
  }

  phase("doublons");
  // DOUBLE CONSOLIDATION. Christian Dior consolide LVMH : meme chiffre
  // d'affaires, meme actif, deux lignes dans le classement pour un seul actif
  // economique. Le test « peu de CA, beaucoup de capitaux propres » ne les
  // attrape pas, justement parce que la holding consolide. Ce qui les trahit
  // est l'IDENTITE du chiffre d'affaires.
  //
  // COMPARAISON PAR VOISINAGE, pas par classes. Un decoupage en tranches
  // souffre d'un effet de frontiere — deux valeurs a 0,3 % tombent de part et
  // d'autre — et regroupait 2,7 societes par tranche en moyenne, donc des
  // faux positifs en masse. Trier puis comparer au voisin immediat supprime
  // les deux defauts et reste lineaire.
  // SIGNATURE MESUREE SUR LA BASE, pas supposee. Une double consolidation
  // publie un chiffre d'affaires IDENTIQUE au million pres, sur PLUSIEURS
  // exercices : LVMH et Dior sur cinq, Plains, Empire State Realty et
  // NL Industries-CompX sur douze. Sur un seul exercice, la base ne contient
  // que des coincidences — Airbnb et M&T Bank, Aptiv et Forvia, des centaines,
  // souvent entre devises differentes. Les versions precedentes comparaient
  // le resultat d'exploitation a 2 % : celui de Dior est inferieur de 3,4 % a
  // celui de LVMH, et la paire passait a travers.
  const coOccurrences = {};
  for (const groupe of Object.values(INDEX_CA)) {
    if (groupe.length < 2 || groupe.length > 6) continue;   // valeur degeneree
    for (let i = 0; i < groupe.length; i++)
      for (let k = i + 1; k < groupe.length; k++) {
        const cle = [groupe[i], groupe[k]].sort().join("|");
        coOccurrences[cle] = (coOccurrences[cle] || 0) + 1;
      }
  }
  let nDoublons = 0;
  const marquer = (a, b) => {
    if (!rangs[a]) return;
    const d = rangs[a].drapeaux;
    if (String(d || "").includes("doublon")) return;
    rangs[a].drapeaux = (d ? d + "," : "") + "doublon:" + b;
    nDoublons++;
  };
  for (const [cle, n] of Object.entries(coOccurrences)) {
    if (n < 2) continue;
    const [a, b] = cle.split("|");
    marquer(a, b);
    marquer(b, a);
  }
  compte("doublons", nDoublons);
  console.log(`  ${nDoublons} lignes en double consolidation presumee`);

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
           "drapeaux", "eva_statut", "exclusion", "maj"].includes(c) ? tx(v[c]) : nb(v[c])).join(", ") + ")");
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
  for (const l of (await d1("SELECT drapeaux, COUNT(*) AS n FROM metriques "
                          + "WHERE drapeaux IS NOT NULL GROUP BY drapeaux ORDER BY n DESC"
                           ))[0].results)
    console.log(`  drapeau ${String(l.drapeaux).padEnd(18)} ${l.n}`);

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
