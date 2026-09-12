/* ════════════════════════════════════════════════════════════════════════
   NOYAU — fonctions de calcul PURES, extraites du worker sans modification.

   NE PAS EDITER A LA MAIN. Genere depuis le worker. Toute correction se fait
   dans le worker, puis on regenere : c'est ce qui garantit qu'il n'existe
   QU'UNE definition du ROIC, du capital investi, de la dette et du profil de
   societe. Deux definitions produiraient des candidats sortis par le screener
   qui echouent a l'analyse.

   Aucune de ces fonctions n'appelle fetch, KV, env ni await.

   Source : worker-160.js
   ════════════════════════════════════════════════════════════════════════ */


function mediane(arr) {
  const v = arr.filter(x => Number.isFinite(x)).sort((a, b) => a - b);
  if (!v.length) return null;
  const m = Math.floor(v.length / 2);
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
}

function cagr(d, f, n) {
  if (!Number.isFinite(d) || !Number.isFinite(f) || d <= 0 || f <= 0 || n <= 0) return null;
  return Math.pow(f / d, 1 / n) - 1;
}

const pct = x => (!Number.isFinite(x)) ? "n.c." : (x * 100).toFixed(1).replace(".", ",") + " %";

const num = (x, d = 0) => (!Number.isFinite(x)) ? "n.c." : x.toLocaleString("fr-FR", { maximumFractionDigits: d });

function millions(x) {
  if (!Number.isFinite(x)) return "n.c.";
  const v = x / 1e6;
  return Math.abs(v) >= 1000 ? (v / 1000).toFixed(2).replace(".", ",") + " Md" : v.toFixed(0) + " M";
}

const CLE_BASE = c => (typeof c === "string" ? c.replace(/Ret$/, "") : c);

function roicRetenu(der) {
  const dern = der.annees[der.annees.length - 1];
  const l = dern ? der.lignes[dern] : null;
  if (!l) return { cle: null, CI0: null, mediane: null, serie: [], base: "aucun dénominateur exploitable" };

  const caDernRef = Number.isFinite(l.ca) && l.ca > 0 ? l.ca : null;

  /* ══ AIGUILLAGE DU NUMÉRATEUR — retraité ou publié, jamais les deux ══
     Le dénominateur ne change pas d'un iota : ce sont les mêmes quatre bases
     qu'avant. Seul le numérateur bascule sur le NOPAT hors amortissement des
     incorporels d'acquisition quand `derives` a jugé le retraitement légitime.
     POURQUOI ICI ET PAS AILLEURS : le cadre impose que le verdict de moat et
     le calcul de valeur retiennent LE MÊME ROIC. Un retraitement appliqué à
     la valeur mais pas au moat rejouerait exactement le défaut que ce
     dénominateur unique avait été écrit pour supprimer — juger 6 % d'un côté
     et en capitaliser 23 % de l'autre. La bascule se fait donc à la source
     commune, ou elle ne se fait pas. */
  const RT = !!(der.retraitement && der.retraitement.actif);
  const kROIC = RT ? "roicRet" : "roic";
  const kFIN  = RT ? "roicFinRet" : "roicFin";
  const kEXP  = RT ? "roicExpRet" : "roicExp";
  const medROIC = RT ? der.roicRetMedian : der.roicMedian;
  const medFIN  = RT ? der.roicFinRetMedian : der.roicFinMedian;
  const medEXP  = RT ? der.roicExpRetMedian : der.roicExpMedian;
  const npAn = a => {
    const x = der.lignes[a];
    if (!x) return null;
    if (RT && Number.isFinite(x.nopatRet)) return x.nopatRet;
    return Number.isFinite(x.nopat) ? x.nopat : null;
  };
  const sufRT = RT
    ? ` · NUMÉRATEUR RETRAITÉ de l'amortissement des incorporels d'acquisition (${der.retraitement.motif})`
    : "";

  /* ---- CONSTRUCTION DES QUATRE CANDIDATS ---------------------------------
     Un dénominateur POSITIF n'est pas pour autant EXPLOITABLE. Chez EVD le
     côté actif reste positif sur quatre exercices, mais à 2 % du chiffre
     d'affaires : le ROIC médian sort à 470 %, ce qui ne mesure plus une
     rentabilité mais la finesse du dénominateur. Le seuil de 10 % du CA
     disqualifie le candidat, il ne le pénalise pas.
     Le capital d'exploitation n'y est PAS soumis : il ne peut pas être
     écrasé par le flottant, puisqu'il ne retranche rien. Une intensité
     capitalistique faible y est une information sur la société, pas un
     artefact de définition. */
  const serie = cle => der.annees.map(a => der.lignes[a][cle]).filter(Number.isFinite);

  /* La médiane NON retraitée reste attachée au résultat : quand le
     retraitement est actif, les deux chiffres se publient côte à côte, jamais
     l'un à la place de l'autre. C'est la même doctrine que le ROIC organique. */
  const comptableEquiv = cle => {
    if (!RT) return null;
    if (cle === "roicExpRet") return der.roicExpMedian;
    if (cle === "roicFinRet") return der.roicFinMedian;
    if (cle === "roicRet") return der.roicMedian;
    return null;
  };

  const usuels = [];
  for (const c of [
    { cle: kFIN, ciCle: "ciFin", med: medFIN, base: "côté financement (dette + capitaux propres − trésorerie)", rang: 0 },
    { cle: kROIC, ciCle: "ci", med: medROIC, base: "côté actif (actif − passif courant − trésorerie)", rang: 1 }
  ]) {
    const s = serie(c.cle), ciD = l[c.ciCle];
    const tropFin = caDernRef && Number.isFinite(ciD) && ciD / caDernRef < 0.10;
    if (Number.isFinite(ciD) && ciD > 0 && !tropFin && Number.isFinite(c.med) && s.length >= 3) {
      usuels.push({ ...c, serie: s, CI0: ciD, couverture: s.length });
    }
  }

  const serieExp = serie(kEXP);
  const expOK = Number.isFinite(l.ciExp) && l.ciExp > 0 && serieExp.length >= 3 && Number.isFinite(medEXP);
  const EXP = expOK ? {
    cle: kEXP, ciCle: "ciExp", CI0: l.ciExp, mediane: medEXP, med: medEXP,
    serie: serieExp, couverture: serieExp.length, exploitation: true,
    /* Série COMPLÈTE, non retraitée, sur le même dénominateur : elle sert au
       test de PERSISTANCE, qui n'a pas besoin du retraitement — un ROIC
       comptable sous le WACC l'est a fortiori avant réintégration. */
    serieComplete: der.annees.map(a => der.lignes[a].roicExp).filter(Number.isFinite),
    sansStocks: !!l.ciExpSansStocks, flottant: Number.isFinite(l.flottant) ? l.flottant : null,
    base: "capital d'exploitation (immobilisations nettes + incorporels + BFR d'exploitation, hors trésorerie et hors flottant client)"
  } : null;

  const capBrut = a => {
    const x = der.lignes[a];
    return (x && Number.isFinite(x.dette) && Number.isFinite(x.equity)) ? x.dette + x.equity : null;
  };
  const serieB = [], ciB = {};
  for (const a of der.annees) {
    const ce = capBrut(a), np = npAn(a);
    ciB[a] = ce;
    if (Number.isFinite(ce) && ce > 0 && Number.isFinite(np)) serieB.push(np / ce);
  }
  const brutOK = serieB.length >= 3 && Number.isFinite(ciB[dern]) && ciB[dern] > 0;
  const BRUT = brutOK ? {
    cle: "roicBrut", CI0: ciB[dern], mediane: mediane(serieB), med: mediane(serieB),
    serie: serieB, couverture: serieB.length, brut: true, serieParAn: ciB,
    base: "capital employé brut (dette + capitaux propres, trésorerie NON déduite)"
  } : null;

  /* ---- CONTRE-PREUVE : ce qui ne décide pas se publie quand même ---------
     Quelle que soit la base retenue, l'écart avec le capital d'exploitation
     est attaché au résultat. C'est la seule façon honnête de traiter le cas
     où le meilleur dénominateur est aussi le plus court : il ne décide pas,
     mais il contredit — et une contradiction non publiée est une décision
     prise en silence. */
  const attacher = (choix) => {
    if (!choix) return choix;
    if (EXP && choix.cle !== kEXP && Number.isFinite(EXP.mediane) && Number.isFinite(choix.mediane) && choix.mediane !== 0) {
      const ec = Math.abs(EXP.mediane / choix.mediane - 1);
      choix.contreExp = {
        mediane: EXP.mediane, couverture: EXP.couverture, ecart: ec,
        materiel: ec > 0.30, sansStocks: EXP.sansStocks
      };
    }
    if (EXP && choix.cle === kEXP) {
      const alt = usuels.length ? usuels.slice().sort((a, b) => b.couverture - a.couverture)[0] : BRUT;
      if (alt && Number.isFinite(alt.med) && Number.isFinite(choix.mediane) && choix.mediane !== 0) {
        choix.contreAutre = { nom: alt.base, mediane: alt.med, couverture: alt.couverture, ecart: Math.abs(alt.med / choix.mediane - 1) };
      }
    }
    if (Number.isFinite(l.flottant)) { choix.flottant = l.flottant; choix.flottantSource = l.flottantSource || null; }
    return choix;
  };

  /* ---- P18/1 — LA PROFONDEUR DE SÉRIE N'EST PAS NÉGOCIABLE ---------------
     Défaut introduit à la version précédente et constaté immédiatement : le
     capital d'exploitation, plus juste, était retenu sur QUATRE exercices
     chez CTS Eventim pendant que le capital employé brut en couvrait ONZE.
     Le prix payé était invisible et lourd — le verdict de trajectoire
     s'inversait (ROIC « en renforcement » sur onze ans, « en repli sur trois
     exercices consécutifs » sur quatre), l'exercice de stress 2020 sortait
     de la fenêtre du ROIC au moment même où le serveur venait d'apprendre à
     le reconnaître, et la fenêtre retombait dans la bande courte du filtre.
     Un dénominateur plus juste sur une fenêtre trop courte n'est pas un
     progrès : c'est la même erreur qu'on venait de corriger, déplacée du
     numérateur vers la longueur de la série.
     RÈGLE : la couverture arbitre. Le capital d'exploitation ne l'emporte
     que lorsqu'il est à la fois PLUS JUSTE et SUFFISAMMENT PROFOND —
     au moins 60 % de la couverture du meilleur candidat usuel. En dessous,
     il ne décide pas, mais il est publié comme contre-preuve et réclame un
     `seed` des postes de bilan si l'écart est matériel.
     PENSÉE INVERSE, la question qui décide : cette règle rend-elle le
     nouveau dénominateur inerte ? Non — il garde la main sur exactement les
     trois cas pour lesquels il a été écrit : le flottant mesuré, la
     pathologie de division par presque rien, et l'absence de tout candidat
     usuel à couverture comparable. Elle lui retire seulement le droit de
     raccourcir une série de onze ans à quatre pour gagner en finesse. */
  const COUV_MIN = 0.60;

  if (!usuels.length) {
    /* Aucun dénominateur par soustraction n'est exploitable : le choix se
       fait entre exploitation et capital employé brut, par la couverture,
       l'exploitation l'emportant à égalité (elle ne retranche rien). */
    const cands2 = [EXP, BRUT].filter(Boolean);
    if (!cands2.length) return { cle: null, CI0: null, mediane: null, serie: [], base: "aucun dénominateur exploitable" };
    cands2.sort((a, b) => (b.couverture - a.couverture) || (a.cle === kEXP ? -1 : 1));
    const w = cands2[0];
    w.base += w.cle === kEXP
      ? " — retenu : aucun dénominateur par soustraction n'est exploitable, et il couvre " + w.couverture + " exercice(s)"
      : " — retenu : les deux dénominateurs par soustraction sont écrasés par un financement client ou par une trésorerie supérieure aux fonds propres, et il couvre " + w.couverture + " exercice(s) contre "
        + (EXP ? EXP.couverture : 0) + " au capital d'exploitation. La trésorerie n'étant pas déduite, le ROIC obtenu est volontairement MINORÉ plutôt qu'inexploitable";
    if (w.sansStocks) w.base += " ⚠️ stocks non servis, traités comme nuls : capital minoré, donc ROIC majoré";
    w.base += sufRT; w.retraite = RT; w.medianeComptable = comptableEquiv(w.cle);
    return attacher(w);
  }

  const meilleurUsuel = usuels.slice().sort((a, b) => (b.couverture - a.couverture) || (a.rang - b.rang))[0];
  const profondeurOK = EXP && EXP.couverture >= COUV_MIN * meilleurUsuel.couverture;

  const flottantMord = EXP && Number.isFinite(l.flottant) && l.flottant / EXP.CI0 >= 0.25;
  const dv = (Number.isFinite(der.roicMedian) && Number.isFinite(der.roicFinMedian) && der.roicMedian !== 0)
    ? Math.abs(der.roicFinMedian / der.roicMedian - 1) : null;
  const divergence = Number.isFinite(dv) && dv > 0.30;
  const pathologie = usuels.some(u => Math.abs(u.med) > 1.0);

  if (EXP && profondeurOK && (flottantMord || divergence || pathologie)) {
    const motif = flottantMord
      ? `flottant client de ${millions(l.flottant)}, soit ${pct(l.flottant / EXP.CI0)} du capital d'exploitation`
      : (pathologie
        ? "au moins un dénominateur par soustraction produit un ROIC supérieur à 100 %, c'est-à-dire une division par presque rien"
        : `les deux dénominateurs par soustraction divergent de ${pct(dv)} — au moins l'un des deux est faussé, et lui seul n'est pas obtenu par soustraction`);
    EXP.base += " — retenu parce que " + motif + `, sur ${EXP.couverture} exercice(s) contre ${meilleurUsuel.couverture} au meilleur candidat usuel`
      + (EXP.sansStocks ? " ⚠️ stocks non servis, traités comme nuls : capital minoré, donc ROIC majoré" : "") + sufRT;
    EXP.retraite = RT; EXP.medianeComptable = comptableEquiv(EXP.cle);
    return attacher(EXP);
  }

  /* ---- P18/2 — LA DIVERGENCE NE COURONNE PLUS LE CÔTÉ FINANCEMENT --------
     L'ancienne règle donnait la main au côté financement dès 30 % d'écart,
     au motif qu'il serait « insensible au flottant client ». L'algèbre du
     bilan dit le contraire : ci − ciFin = passifs non courants hors dette
     moins dette courante — le flottant client courant DISPARAÎT de cette
     différence, puisqu'il écrase les deux dénominateurs à la fois par la
     trésorerie qu'il apporte. La règle reposait donc sur une cause fausse,
     et sur Siemens Healthineers elle couronnait 11,2 % là où les deux
     autres bases convergent vers 5,7-6,0 %.
     REMPLACEMENT : à divergence forte, sans capital d'exploitation assez
     profond pour trancher, on retient LE PLUS BAS des deux et on le nomme.
     Même doctrine que « le CAGR le plus bas des deux fenêtres » : quand deux
     mesures d'une même chose se contredisent, la prudence n'est pas de
     choisir la plus flatteuse au nom d'une théorie. */
  if (divergence && usuels.length === 2) {
    const bas = usuels.slice().sort((a, b) => a.med - b.med)[0];
    return attacher({
      cle: bas.cle, CI0: bas.CI0, mediane: bas.med, serie: bas.serie, couverture: bas.couverture,
      retraite: RT, medianeComptable: comptableEquiv(bas.cle), sufRT,
      base: bas.base + ` — retenu comme LE PLUS BAS des deux : ils divergent de ${pct(dv)}, ce qui prouve qu'au moins l'un est faussé sans dire lequel`
        + (EXP ? `. Le capital d'exploitation trancherait (${pct(EXP.mediane)}) mais ne couvre que ${EXP.couverture} exercice(s) contre ${meilleurUsuel.couverture} : trop court pour décider, il est publié en contre-preuve` : "") + sufRT
    });
  }

  const w = usuels.slice().sort((a, b) => (b.couverture - a.couverture) || (a.rang - b.rang))[0];
  return attacher({ cle: w.cle, CI0: w.CI0, mediane: w.med, serie: w.serie, base: w.base + sufRT, couverture: w.couverture,
                    retraite: RT, medianeComptable: comptableEquiv(w.cle) });
}

function trajectoireROIC(der, R) {
  const serie = (R && R.serie) ? R.serie : [];
  const o = { serie, statut: "indeterminee", baisses: 0, recent: null, ancien: null, delta: null, mention: "", note: null };
  if (serie.length < 3) return o;

  let b = 0;
  for (let i = serie.length - 1; i > 0; i--) { if (serie[i] < serie[i - 1]) b++; else break; }
  o.baisses = b;

  if (serie.length >= 5) {
    const rec = serie.slice(-2), anc = serie.slice(0, -2);
    o.recent = rec.reduce((a, c) => a + c, 0) / rec.length;
    o.ancien = anc.reduce((a, c) => a + c, 0) / anc.length;
    o.delta = o.recent - o.ancien;
  }

  /* DEUX SIGNAUX, UN SEUL STATUT. Les baisses consécutives captent un déclin
     récent et net ; l'écart de moyennes capte une érosion lente qu'une série
     en dents de scie masquerait. On retient le plus sévère des deux. */
  const deltaBaisse = Number.isFinite(o.delta) && o.delta < -0.03;
  const deltaHausse = Number.isFinite(o.delta) && o.delta > 0.03;
  if (b >= 3 || (deltaBaisse && b >= 2)) o.statut = "repli";
  else if (b >= 2 || deltaBaisse) o.statut = "flechissement";
  else if (deltaHausse) o.statut = "renforcement";
  else o.statut = "stable";

  o.recule = o.statut === "repli" || o.statut === "flechissement";
  o.fadeLineaire = o.statut === "repli";
  o.mention = o.statut === "repli" ? " (en repli)" : (o.statut === "flechissement" ? " (en fléchissement)" : (o.statut === "renforcement" ? " (en renforcement)" : ""));

  if (o.recule) {
    o.note = `⚠️ ROIC en ${o.statut === "repli" ? "repli" : "fléchissement"}` +
      (b >= 2 ? ` — ${b} exercices consécutifs de baisse (${pct(serie[serie.length - 1 - b])} → ${pct(serie[serie.length - 1])})` : "") +
      (Number.isFinite(o.delta) ? ` · moyenne des 2 derniers exercices ${pct(o.recent)} contre ${pct(o.ancien)} sur les précédents` : "") +
      `. Le score de moat porte sur la persistance PASSÉE et n'intègre pas cette trajectoire : un avantage qui se referme met deux à trois ans à s'y voir dans les chiffres.`;
  } else if (o.statut === "renforcement") {
    o.note = `✅ ROIC en renforcement — moyenne des 2 derniers exercices ${pct(o.recent)} contre ${pct(o.ancien)} sur les précédents.`;
  }
  return o;
}

function moatPropre(der, wacc, vanEck) {
  const o = { points: 0, max: 0, detail: [], verdict: null, nonEvalues: [] };
  // MÊME dénominateur que la brique EVA : sans ça, le verdict qui fixe la
  // durée du fade et la valeur qui en découle ne parlent pas de la même
  // rentabilité. La base retenue est publiée.
  const R = roicRetenu(der);
  const roics = R.serie;
  const rMed = Number.isFinite(R.mediane) ? R.mediane : null;
  o.base = R.base;

  /* 1. Spread ROIC médian − WACC (2 points possibles)
     DEUX LECTURES, ON RETIENT LA MEILLEURE. L'écart absolu seul déclasse les
     moats d'infrastructure : Air Liquide gagne 9,5 % contre un WACC de 6,5 %,
     soit 3 points d'écart et 0/2, alors que le RATIO de 1,46 tenu 12
     exercices sur 12 est une preuve solide de rendement excédentaire.
     CONTRE-THÈSE, retenue dans la calibration : un ratio élevé peut venir
     d'un WACC bas (bêta faible) plutôt que d'un moat — une utility régulée à
     4 % de WACC et 6 % de ROIC afficherait 1,5 sans avantage concurrentiel.
     D'où l'asymétrie : le ratio ne donne JAMAIS 2/2. Seul un écart absolu
     large y mène. Le ratio ouvre le premier point, la durée (test 2) et les
     autres preuves font le reste. */
  if (rMed !== null && Number.isFinite(wacc)) {
    o.max += 2;
    const sp = rMed - wacc;
    const rt = wacc > 0 ? rMed / wacc : null;
    o.spread = sp; o.ratioSpread = rt;
    if (sp > 0.10) { o.points += 2; o.detail.push(`✅ Spread ROIC−WACC ${pct(sp)} (>10 pts) — 2/2`); }
    else if (sp >= 0.05) { o.points += 1; o.detail.push(`🟠 Spread ROIC−WACC ${pct(sp)} (5-10 pts) — 1/2`); }
    else if (Number.isFinite(rt) && rt >= 1.30) { o.points += 1; o.detail.push(`Écart absolu ${pct(sp)} faible, mais ROIC ${num(rt, 2)}× le WACC (≥1,30) — 1/2 par le ratio`); }
    else o.detail.push(`❌ Spread ROIC−WACC ${pct(sp)}${Number.isFinite(rt) ? ` et ratio ${num(rt, 2)}×` : ""} — 0/2`);
  } else o.nonEvalues.push("spread ROIC−WACC");

  /* 2. Aucune année sous le WACC
     🔴 DEUX QUESTIONS DIFFÉRENTES, DEUX FENÊTRES DIFFÉRENTES (w156).
     « Quel est le NIVEAU de rentabilité » et « y a-t-il eu DÉCROCHAGE » ne se
     mesurent pas sur le même échantillon. Le niveau a besoin du numérateur
     retraité, donc de la fenêtre où l'amortissement d'acquisition est servi.
     La PERSISTANCE, elle, a besoin de TOUTE l'histoire disponible — et elle
     n'a aucun besoin du retraitement, puisqu'un ROIC comptable sous le WACC
     l'est a fortiori avant réintégration.

     Le défaut observé : chez Broadcom, la médiane retraitée a restreint la
     série à 4 exercices sur 10, et le test de décrochage a été jugé sur ces
     4 seuls — verdict Narrow 3/6 là où 10 exercices étaient disponibles. Un
     moat jugé sur quatre exercices n'est pas un moat jugé. Et la fenêtre
     n'avait pas été CHOISIE : elle était le sous-produit de la couverture
     d'un poste comptable, c'est-à-dire du hasard de la publication.

     UNIVERSEL : la série longue est utilisée dès qu'elle est plus fournie,
     quelle que soit la société. Quand les deux coïncident — le cas de
     l'immense majorité du parc, sans retraitement — le comportement est
     rigoureusement inchangé, et aucun score ne bouge. */
  const serieLongue = (R.serieComplete && R.serieComplete.length > roics.length) ? R.serieComplete : roics;
  if (serieLongue.length >= 3 && Number.isFinite(wacc)) {
    o.max += 1;
    const sous = serieLongue.filter(r => r < wacc).length;
    /* 🔴 SÉPARATEUR ` · ` ET NON ` — `. Le rendu en table découpe la ligne sur
       le PREMIER ` — ` : label à gauche, score à droite. Une mention qui
       contient ce séparateur en amont du score fait passer la mention POUR le
       score, et la colonne « Résultat » perdait le 0/1. Le défaut était
       invisible au banc — il ne se voit qu'au rendu.
       RÈGLE : ` — ` est réservé au séparateur label/score dans `o.detail`.
       Toute mention interne s'insère avec ` · `. */
    const mention = serieLongue !== roics ? ` · série COMPLÈTE, non restreinte par le retraitement` : "";
    if (!sous) { o.points += 1; o.detail.push(`✅ Aucun exercice sous le WACC sur ${serieLongue.length}${mention} — 1/1`); }
    else o.detail.push(`❌ ${sous} exercice(s) sur ${serieLongue.length} sous le WACC${mention} — 0/1`);
  } else o.nonEvalues.push("régularité du spread");

  /* 3. Stabilité de la marge brute — RÉSIDUS AUTOUR DE LA TENDANCE
     L'écart-type brut compte une amélioration continue comme de
     l'instabilité : ASML était pénalisée pour être passée de 28,7 % à 52,8 %
     sous l'effet du mix EUV. Une progression régulière n'est pas de la
     volatilité — c'est même le contraire, c'est la signature d'un pricing
     power qui se renforce.
     CONTRE-THÈSE, et c'est elle qui dicte la méthode : une « tendance » peut
     être l'artefact d'un changement de périmètre — céder la division à
     faible marge fait monter la marge du groupe sans rien améliorer. Une
     cession produit une MARCHE D'ESCALIER, pas une pente : elle laisse un
     gros résidu autour de la droite et échoue donc au test, là où une
     progression organique année après année le passe. Mesurer les résidus
     plutôt que la dispersion brute distingue exactement ces deux cas. */
  const mb = der.annees.map(a => der.lignes[a].margeBrute).filter(Number.isFinite);
  if (mb.length >= 3) {
    o.max += 1;
    const n = mb.length;
    const mx = (n - 1) / 2;
    const my = mb.reduce((a, b) => a + b, 0) / n;
    let sxy = 0, sxx = 0;
    for (let i = 0; i < n; i++) { sxy += (i - mx) * (mb[i] - my); sxx += (i - mx) * (i - mx); }
    const pente = sxx > 0 ? sxy / sxx : 0;
    let sr = 0;
    for (let i = 0; i < n; i++) { const e = mb[i] - (my + pente * (i - mx)); sr += e * e; }
    const res = Math.sqrt(sr / n);
    const sd = Math.sqrt(mb.reduce((s, x) => s + (x - my) * (x - my), 0) / n);
    o.margePente = pente;
    /* Résidus et effectif EXPOSÉS, et non plus seulement consommés sur place :
       ils portent la seule contre-preuve de pouvoir de fixation des prix
       constructible à partir des comptes seuls (cf. `qualifierPricing`). */
    o.margeResidus = res; o.margeN = n; o.margeSD = sd;
    if (res < 0.02) {
      o.points += 1;
      const sens = Math.abs(pente) < 0.005 ? "stable" : (pente > 0 ? `en amélioration régulière (+${pct(pente)}/an)` : `en érosion régulière (${pct(pente)}/an)`);
      o.detail.push(`✅ Marge brute ${sens}, résidus ${pct(res)} autour de la tendance (<2 pts) — 1/1`);
    } else o.detail.push(`❌ Marge brute irrégulière, résidus ${pct(res)} autour de la tendance (≥2 pts, dispersion brute ${pct(sd)}) — 0/1`);
  } else o.nonEvalues.push("stabilité de la marge brute");

  /* 4. Présence dans un indice moat VanEck — CONFIRMATION, JAMAIS PÉNALITÉ.
     L'outil énonçait lui-même que l'absence n'est pas un verdict, puis la
     comptait 0/1 : une contradiction qui retirait un point à tout Wide
     authentique hors des ≤300 lignes couvertes par les trois ETF.
     Le test devient donc ASYMÉTRIQUE. Présent : +1 au numérateur ET au
     dénominateur — un tiers a jugé la valeur digne d'un indice moat, c'est
     une information. Absent : hors dénominateur, exactement comme une donnée
     manquante, parce que ce n'en est pas une.
     CONTRE-THÈSE : un test qui ne peut que rapporter gonfle mécaniquement
     les scores. Elle ne tient pas ici — le dénominateur s'ajuste avec le
     numérateur, donc le RATIO reste comparable d'une société à l'autre. Ce
     qui change, c'est qu'une absence de couverture cesse d'être lue comme
     une preuve à charge. */
  if (vanEck && vanEck.statut === "OK" && vanEck.present) {
    o.max += 1; o.points += 1;
    o.detail.push(`✅ Présent dans ${vanEck.ou} — 1/1 (confirmation par un tiers)`);
  } else if (vanEck && vanEck.statut === "OK") {
    o.nonEvalues.push("présence VanEck (absent — couverture partielle, hors dénominateur)");
  } else o.nonEvalues.push("présence VanEck");

  // 5. Régularité de la croissance du CA
  if (der.regularite && der.regularite.caN >= 3) {
    o.max += 1;
    const r = der.regularite.caH / der.regularite.caN;
    if (r >= 0.80) { o.points += 1; o.detail.push(`✅ CA en hausse ${der.regularite.caH}/${der.regularite.caN} (≥80 %) — 1/1`); }
    else o.detail.push(`❌ CA en hausse ${der.regularite.caH}/${der.regularite.caN} (<80 %) — 0/1`);
  } else o.nonEvalues.push("régularité du CA");

  /* AVANT la sortie anticipée : la qualification du pricing ne dépend que de
     la marge brute, jamais du nombre de briques de moat évaluables. La placer
     après aurait reproduit le défaut du §63 — une garde posée à un étage qui
     n'est pas celui qui décide. */
  o.pricing = qualifierPricing(o, der.rupture);
  if (o.max < 3) { o.verdict = null; o.motif = "moins de 3 points évaluables — aucun verdict propre publiable"; return o; }
  const ratio = o.points / o.max;
  o.ratio = ratio;
  o.verdict = ratio >= 0.80 ? "Wide" : (ratio >= 0.50 ? "Narrow" : "None");
  o.traj = trajectoireROIC(der, R);
  o.verdictAffiche = o.verdict + o.traj.mention;
  o.tendanceNote = o.traj.note;
  return o;
}

const PRICING_N_MIN = 6;

function qualifierPricing(o, rupture) {
  const res = o.margeResidus, n = o.margeN, pente = o.margePente;
  /* 🔴 SÉRIE NON HOMOGÈNE ⇒ INDÉCIDABLE, JAMAIS `false` (w159).
     DIVERGENCE INTERNE CONSTATÉE SUR ADYEN : `moat` refusait le point sur des
     résidus de 17,6 %, pendant que le panneau moat de `fv` qualifiait ce MÊME
     échec d'artefact d'un changement de définition du chiffre d'affaires. Les
     deux ne peuvent pas être vrais, et le serveur ne peut pas publier deux
     lectures contradictoires du même nombre.
     Le test des résidus mesure l'écart à une tendance. Si la série change de
     définition en cours de route, l'écart mesure le CHANGEMENT DE PÉRIMÈTRE et
     rien d'économique : il n'est ni une preuve ni une contre-preuve. Conclure
     `false` fabriquerait un plafond sur un artefact comptable — le faux
     positif exact que le principe 1 interdit, sur toute société ayant cédé une
     activité, changé de norme ou reclassé son chiffre d'affaires.
     Le détecteur existe déjà et distingue le saut DURABLE du creux avec
     retour au niveau antérieur : on le réutilise au lieu d'en écrire un
     second. Le point sort du dénominateur, aucun plafond ne recule, et le
     remède est nommé — `seed` des exercices manquants pour homogénéiser. */
  if (rupture) {
    return {
      etat: "absent", libelle: "➖ INDÉCIDABLE — série non homogène",
      saisie: "omettre le champ `pricing`",
      motif: `le test des résidus tourne sur une série de marge brute dont le serveur a détecté une rupture de définition — ${String(rupture).slice(0, 220)}. Un écart à la tendance mesuré sur une série non homogène chiffre le changement de périmètre, pas le pouvoir de fixation des prix : il n'est ni preuve ni contre-preuve. Le point est INDÉCIDABLE, sort du dénominateur, et aucun plafond ne recule. Remède : \`seed\` des exercices manquants pour rendre la série comparable à elle-même.`
    };
  }
  if (!Number.isFinite(res) || !Number.isFinite(n) || n < PRICING_N_MIN) {
    return {
      etat: "absent", libelle: "➖ NON ÉVALUABLE — fenêtre trop courte",
      saisie: "omettre le champ `pricing`",
      motif: `${Number.isFinite(n) ? n : 0} exercice(s) de marge brute exploitable(s), il en faut ${PRICING_N_MIN}. Une tendance ajustée sur moins de six points laisse des résidus mécaniquement faibles : le test conclurait à la régularité sans l'avoir mesurée, et surtout une érosion réelle ne pourrait pas y ressortir. C'est une limite de SOURCE — \`grossProfit\` manque chez beaucoup de déposants IFRS, et Yahoo plafonne à 4 exercices hors EDGAR — jamais un jugement sur la société. Aucun plafond ne recule.`
    };
  }
  const sens = Math.abs(pente) < 0.005 ? "stable" : (pente > 0 ? `en amélioration régulière (+${pct(pente)}/an)` : `en érosion régulière (${pct(pente)}/an)`);
  if (res >= 0.02) {
    return {
      etat: "false", libelle: "❌ CONTRE-PREUVE — irrégularité", res, n, pente,
      saisie: "`pricing: false`",
      motif: `marge brute irrégulière — résidus de ${pct(res)} autour de sa tendance sur ${n} exercices, au-delà du seuil de 2 points. Une société qui fixe ses prix ne subit pas des écarts de cette ampleur autour de sa propre trajectoire : le point est refusé et le plafond de 3/4 est ici FONDÉ, pas subi.`
    };
  }
  /* 🔴 SECONDE CONTRE-PREUVE, ATTRAPÉE AU BANC D'ESSAI. Les résidus mesurent
     le BRUIT autour de la tendance, jamais la tendance elle-même : une marge
     qui s'érode de 1 point par an, régulièrement, passait le test des résidus
     avec 1,2 % et ressortait « compatible ». Or une érosion régulière est la
     signature exacte de l'INVERSE de ce qu'on cherche — une société qui ne
     répercute pas ses coûts, année après année, sans même le bruit d'une
     négociation. Le seuil est celui de la bande « stable » déjà employée par
     la brique 3 : aucune constante nouvelle. */
  if (pente <= -0.005) {
    return {
      etat: "false", libelle: "❌ CONTRE-PREUVE — érosion", res, n, pente,
      saisie: "`pricing: false`",
      motif: `marge brute en érosion régulière de ${pct(pente)} par an sur ${n} exercices, résidus de ${pct(res)}. La régularité de la baisse aggrave le constat au lieu de l'atténuer : les coûts ne sont pas répercutés, et ils ne le sont pas par exception mais par tendance. RÉSERVE — un glissement de mix (ajout d'une activité structurellement moins margée) produit la même pente sans rien dire du pouvoir de fixation des prix. C'est le seul cas où un split prix/volume publié doit primer sur ce verdict.`
    };
  }
  return {
    etat: "absent", libelle: "➖ COMPATIBLE, NON PROUVÉ", res, n, pente, stable: true,
    saisie: "omettre le champ `pricing` — sauf split prix/volume publié, auquel cas `pricing: true`",
    motif: `marge brute ${sens}, résidus de ${pct(res)} sur ${n} exercices (<2 points), aucune érosion tendancielle. C'est une condition NÉCESSAIRE du pouvoir de fixation des prix, et elle est remplie — mais elle ne suffit pas : une marge tenue dans un marché sans tension sur les coûts ne démontre rien. Le point n'est donc ni acquis ni refusé, il est NON ÉVALUABLE, sort du dénominateur, et ne fait reculer aucun plafond.`
  };
}

function profilSociete(der, opt) {
  const dern = der.annees[der.annees.length - 1];
  const l = dern ? der.lignes[dern] : null;
  const o = { type: "industriel", evaOK: true, notes: [], ko: null };
  if (!l) { o.type = "industriel"; o.evaOK = false; o.ko = "aucun exercice exploitable"; return o; }

  const ca = Number.isFinite(l.ca) && l.ca > 0 ? l.ca : null;
  const R = roicRetenu(der);
  const CI = R.CI0;

  // 1. SÉRIE NON HOMOGÈNE — priorité absolue : aucun modèle ne tourne sur une
  //    série qui mélange deux définitions.
  if (der.rupture) {
    o.type = "incoherent"; o.evaOK = false;
    o.ko = `série comptable non homogène — ${der.rupture}. Aucun modèle de valeur ne tourne sur une série qui change de définition en cours de route : reprendre les comptes par \`seed\` depuis le rapport annuel`;
    return o;
  }

  // 2. BILAN FINANCIER OU FONCIER — capital investi sans définition industrielle.
  const detteBrute = Number.isFinite(l.dette) ? l.dette : null;
  const cp = Number.isFinite(l.equity) ? l.equity : null;
  if (Number.isFinite(detteBrute) && ca) {
    const detteSurCA = detteBrute / ca, levier = (cp && cp > 0) ? detteBrute / cp : null;
    /* 🔴 LE LEVIER SEUL NE CLASSE PLUS — IL MESURE AUSSI LES RACHATS.
       Verisk sortait en « profil financier ou foncier » avec une dette de
       1,2 fois son chiffre d'affaires, c'est-à-dire un bilan parfaitement
       industriel. La cause n'était pas au numérateur mais au DÉNOMINATEUR :
       des rachats d'actions massifs et continus compriment les capitaux
       propres jusqu'à les rendre résiduels, et le rapport dette/fonds propres
       explose sans qu'un seul euro de dette ait été ajouté.
       Un levier élevé mesure alors une POLITIQUE D'ALLOCATION, pas une nature
       de bilan — et le confondre avec un profil bancaire coupe l'EVA, le
       ROIC et le filtre dur sur exactement le genre de société que ce cadre
       cherche à identifier : celle qui rend son capital.
       La dette rapportée au CA, elle, ne se laisse pas déformer par les
       rachats. Elle devient donc obligatoire : une banque ou une foncière la
       porte à 5 ou 10 fois, un compounder qui se rachète reste sous 2. */
    /* 🔴 ET LE LEVIER NE CLASSE TOUJOURS PAS QUAND SA BASE A DISPARU.
       Rendre `detteSurCA` obligatoire n'a pas suffi : la branche du levier
       reste franchissable dès que les capitaux propres sont résiduels, et
       c'est précisément ce que produit une politique de rachats soutenue.
       Il manquait le test de NATURE, et il existe — il est même d'une netteté
       rare. Les capitaux propres rapportés au chiffre d'affaires SÉPARENT les
       deux populations sans recouvrement :
         · banque, assurance, foncière — le bilan EST le métier, les fonds
           propres valent PLUSIEURS FOIS le produit net : rapport de 5 à 10 ;
         · industriel qui rend son capital — les fonds propres sont un solde
           comptable comprimé par les rachats : rapport très inférieur à 1.
       Un rapport faible ne dit donc pas « bilan fragile », il dit « ces
       capitaux propres ne sont pas une base de mesure ». Exiger `cp/ca ≥ 1`
       ferme la branche du levier à tous les serial acquirers et à tous les
       racheteurs, sans jamais fermer la porte à une vraie financière — le
       seuil est posé loin sous leur plancher observé.
       PENSÉE INVERSE — une financière à fonds propres anormalement bas
       échapperait-elle au classement ? Elle reste attrapée par
       `detteSurCA > 2.5`, qui ne dépend pas des capitaux propres. Les deux
       branches se couvrent l'une l'autre. */
    const cpSurCA = (cp && cp > 0) ? cp / ca : null;
    if (detteSurCA > 2.5 || (Number.isFinite(levier) && levier > 4 && detteSurCA > 1.5 && Number.isFinite(cpSurCA) && cpSurCA >= 1)) {
      o.type = "financier"; o.evaOK = false;
      o.roeMedian = der.roeMedian;
      o.notes.push(`Dette ${detteSurCA.toFixed(1)}× le chiffre d'affaires${Number.isFinite(levier) ? `, levier ${levier.toFixed(1)}×` : ""} — les deux conditions sont réunies, le bilan n'est pas industriel.`);
      /* LE ROIC N'A PAS DE SUBSTITUT SUR UN BILAN BANCAIRE — LE ROE EN EST UN.
         Distinction qui n'était pas faite : chez CTS Eventim le ROIC est
         MESURABLE mais mal mesuré, et un meilleur dénominateur le répare.
         Chez une banque il n'est pas mal mesuré, il n'existe pas : la dette
         y est la matière première et non un mode de financement, il n'y a
         donc rien à mettre au dénominateur d'un rendement du capital investi.
         Aucun dénominateur alternatif ne réglera cela, jamais.
         Ce qui existe en revanche, c'est le ROE face au COÛT DES CAPITAUX
         PROPRES — pas face au WACC, qui n'a pas davantage de sens ici. Le
         serveur sert déjà les deux : le ROE par exercice ci-dessus, le Ke par
         `dcf()` (Rf + β × prime). Le filtre dur du cadre devient donc
         testable sur ces bilans, à condition de le lire dans sa version
         financière — ROE > Ke, durablement — et non dans sa version
         industrielle. C'est une décision de méthode, pas de serveur : le
         serveur fournit les deux nombres et le dit. */
      o.ko = `profil de bilan financier ou foncier (dette ${num(detteSurCA, 1)}× le chiffre d'affaires${Number.isFinite(levier) ? `, levier ${num(levier, 1)}× les capitaux propres` : ""}). Le capital investi et le ROIC n'ont pas de définition industrielle sur ces bilans — et contrairement au cas du flottant client, aucun dénominateur alternatif ne les répare : sur une banque la dette EST la matière première, il n'y a rien à mettre au dénominateur. La brique EVA ne s'applique donc pas et n'est pas approximée. SUBSTITUT TESTABLE : ROE médian ${pct(der.roeMedian)} sur ${der.annees.length} exercice(s), à confronter au COÛT DES CAPITAUX PROPRES (Ke = Rf + β × prime, publié par \`dcf\`) et non au WACC. Au-delà, banque ou assurance ⇒ dividendes actualisés ou résultat résiduel, P/B croisé au ROE ; foncière ⇒ FFO et actif net réévalué. Ces trois méthodes sortent du périmètre du serveur — la brique multiple prend le relais`;
      return o;
    }
  }

  // 3. FINANCÉE PAR SON CYCLE D'EXPLOITATION — le cas CTS Eventim.
  //    La billetterie encaisse les clients des mois avant l'événement : ce
  //    float est un financement gratuit qui écrase le capital investi des DEUX
  //    côtés. Côté actif il gonfle le passif courant, côté financement il
  //    gonfle la trésorerie. Le dénominateur tend vers zéro, le ROIC vers
  //    l'infini — 118 % à 557 % sur quatre exercices chez EVD — et la brique
  //    EVA capitaliserait vingt ans une rentabilité qui n'est qu'une division
  //    par presque rien.
  //    Isoler le float supposerait une ligne « produits constatés d'avance »
  //    que ni Yahoo ni la taxonomie servie ne fournissent. Sans elle, tout
  //    capital opérationnel reconstitué serait une supposition. On refuse.
  const ratioDen = (Number.isFinite(der.roicMedian) && Number.isFinite(der.roicFinMedian) && der.roicMedian !== 0)
    ? der.roicFinMedian / der.roicMedian : null;
  const intensite = (Number.isFinite(CI) && ca) ? CI / ca : null;
  if (R.brut) o.notes.push("Capital employé BRUT retenu (trésorerie non déduite) : les deux dénominateurs usuels étaient écrasés par un financement client ou par une trésorerie supérieure aux fonds propres. Le ROIC obtenu est volontairement minoré plutôt qu'inexploitable.");
  if (!Number.isFinite(CI) || CI <= 0) {
    o.type = "float"; o.evaOK = false;
    o.ko = "capital investi nul ou négatif sur les deux dénominateurs — société financée par son cycle d'exploitation (encaissements clients d'avance) ou par sa trésorerie. Le rendement du capital n'y a pas de sens : la brique EVA ne s'applique pas, la brique multiple prend l'ancrage";
    return o;
  }

  /* FENÊTRE INSUFFISANTE. La brique extrapole H années depuis les exercices
     observés. Sous quatre exercices, elle affirme vingt ans à partir de trois
     points : ce n'est plus un modèle, c'est une opinion habillée en calcul. */
  if (der.annees.length < 4) {
    o.type = "fenetre_courte"; o.evaOK = false;
    o.ko = `${der.annees.length} exercice(s) seulement dans la série. La brique EVA extrapole vingt ans : sous quatre exercices le rapport entre ce qu'on observe et ce qu'on affirme dépasse 1 pour 7, et la médiane de ROIC n'a pas de sens. Chercher un \`seed\` du rapport annuel avant tout ancrage intrinsèque`;
    return o;
  }
  if (Number.isFinite(intensite) && intensite < 0.10 && !R.brut) {
    o.type = "float"; o.evaOK = false;
    o.ko = `capital investi à ${pct(intensite)} du chiffre d'affaires seulement. En dessous de 10 %, le ROIC ne mesure plus une rentabilité mais la finesse de son dénominateur — capital amorti, immatériel passé en charges, ou exploitation financée par les clients. Capitaliser vingt ans un taux obtenu par division par presque rien produit un nombre sans rapport avec l'entreprise : la brique EVA est refusée, la brique multiple prend l'ancrage`;
    return o;
  }
  if (Number.isFinite(ratioDen) && (ratioDen > 3 || ratioDen < 0.33) && !R.brut) {
    o.notes.push(`Les deux dénominateurs divergent d'un facteur ${num(Math.max(ratioDen, 1 / ratioDen), 1)} (ROIC côté actif ${pct(der.roicMedian)} contre côté financement ${pct(der.roicFinMedian)}). C'est la signature d'un financement par le cycle d'exploitation : le ${CLE_BASE(R.cle) === "roicFin" ? "côté financement" : "côté actif"} est retenu, mais l'écart se lit comme une information sur le modèle économique, pas comme une erreur.`);
  }

  // 4. TRÉSORERIE NETTE DOMINANTE — n'interdit rien, change la lecture.
  if (Number.isFinite(l.dn) && Number.isFinite(CI) && l.dn < 0 && Math.abs(l.dn) > CI * 0.5) {
    o.tresorerieDominante = true;
    /* CETTE NOTE AFFIRMAIT UNE CHOSE QUI N'EST PLUS VRAIE PARTOUT.
       « Elle est ajoutée telle quelle à la valeur » : exact tant que le
       capital investi déduit la trésorerie, faux sur la base brute où elle y
       figure déjà, et faux dès qu'une partie de cette trésorerie appartient
       aux clients. La note dit désormais ce que le modèle fait vraiment, et
       pose la seule question qui compte devant un tas de liquidités : à qui
       est-il ? */
    const fl = Number.isFinite(l.flottant) ? l.flottant : null;
    const tr = Number.isFinite(l.dette) ? l.dette - l.dn : null;
    o.notes.push(`Trésorerie nette de ${millions(-l.dn)}, soit ${pct(Math.abs(l.dn) / CI)} du capital investi.`
      + (fl !== null && Number.isFinite(tr)
        ? ` Flottant client saisi : ${millions(fl)} sur ${millions(tr)} de trésorerie — ${fl >= tr ? "la totalité de cette trésorerie est de l'argent de tiers, RIEN n'est ajoutable à la valeur des actionnaires" : "seul l'excédent de " + millions(tr - fl) + " appartient aux actionnaires et entre dans la valeur"}.`
        : ` ⚠️ FLOTTANT CLIENT NON SAISI : avant de lire cette trésorerie comme une réserve d'actionnaires, vérifier qu'elle n'est pas de l'argent encaissé d'avance pour le compte de clients ou de mandants. Sur une billetterie, un voyagiste, un assureur ou un intermédiaire de paiement, elle ne l'est pas.`)
      + ` La part réellement ajoutée à la valeur dépend de la base de capital investi retenue, et elle est publiée avec la brique EVA.`);
  }

  // 5. INTENSITÉ CAPITALISTIQUE — diagnostic, jamais amputation.
  if (Number.isFinite(intensite)) {
    o.intensite = intensite;
    if (intensite < 0.25) {
      o.type = "asset_light";
      o.notes.push(`Capital investi à ${pct(intensite)} du chiffre d'affaires : société asset-light. La brique EVA s'y lit davantage comme une capitalisation du résultat opérationnel que comme un modèle de rentabilité du capital — le contrôle de sortie en multiple de NOPAT est ici la lecture qui compte.`);
    } else if (intensite > 1.5) {
      o.type = "capitalistique";
      o.notes.push(`Capital investi à ${pct(intensite)} du chiffre d'affaires : société très capitalistique. Le socle comptable pèse lourd dans la valeur et le sur-rendement peu — vérifier que les acquisitions passées ne gonflent pas artificiellement le capital investi.`);
    }
  }
  return o;
}

const EPV_TAUX_OBSTACLE = 0.10;

const EPV_MIN_EXERCICES = 5;

function ancrageEPV(der, px) {
  const o = { ko: null };
  const ans = (der && der.annees) || [];
  if (ans.length < EPV_MIN_EXERCICES) return { ko: `${ans.length} exercice(s) — un bénéfice NORMALISÉ n'a pas de sens sous ${EPV_MIN_EXERCICES}, la médiane ne neutraliserait aucun cycle` };
  const ebits = [], taux = [];
  for (const a of ans) {
    const l = der.lignes[a] || {};
    if (Number.isFinite(l.ebit)) ebits.push(l.ebit);
    if (Number.isFinite(l.tax) && Number.isFinite(l.pretax) && l.pretax > 0) {
      const t = l.tax / l.pretax;
      if (t > 0.05 && t < 0.50) taux.push(t);
    }
  }
  if (ebits.length < EPV_MIN_EXERCICES) return { ko: "résultat opérationnel absent sur trop d'exercices" };
  const med = x => { const y = x.slice().sort((a, b) => a - b); const m = y.length >> 1; return y.length % 2 ? y[m] : (y[m - 1] + y[m]) / 2; };
  const ebitN = med(ebits);
  /* Un pouvoir bénéficiaire NÉGATIF n'est pas une valeur basse, c'est une
     absence de pouvoir bénéficiaire. La brique se tait plutôt que de publier
     un plancher négatif qui se lirait comme une valorisation. */
  if (ebitN <= 0) return { ko: "résultat opérationnel médian négatif — aucun pouvoir bénéficiaire à capitaliser" };
  const tImp = taux.length >= 3 ? med(taux) : 0.25;
  const dern = ans[ans.length - 1], l = der.lignes[dern] || {};
  const actions = Number.isFinite(l.actions) && l.actions > 0 ? l.actions : null;
  if (!actions) return { ko: "nombre d'actions indisponible sur le dernier exercice" };
  const dn = Number.isFinite(l.dn) ? l.dn : 0;
  const vEnt = (ebitN * (1 - tImp)) / EPV_TAUX_OBSTACLE;
  const vAct = (vEnt - dn) / actions;
  if (!Number.isFinite(vAct) || vAct <= 0) return { ko: "dette nette supérieure au pouvoir bénéficiaire capitalisé — aucune valeur revenant aux actionnaires à croissance nulle" };
  o.valeur = vAct; o.ebitNormalise = ebitN; o.tauxImpot = tImp; o.detteNette = dn;
  o.tauxObstacle = EPV_TAUX_OBSTACLE; o.exercices = ebits.length;
  const cours = px && Number.isFinite(px.cours) ? px.cours : null;
  if (cours && cours > 0) {
    o.partCroissance = 1 - vAct / cours;
    o.lecture = o.partCroissance <= 0
      ? `Le cours est SOUS le pouvoir bénéficiaire à croissance nulle : le marché ne paie aucune croissance, et escompte même une érosion.`
      : `${(o.partCroissance * 100).toFixed(0)} % du cours repose sur une croissance NON ENCORE RÉALISÉE — le pouvoir bénéficiaire actuel, capitalisé à ${(EPV_TAUX_OBSTACLE * 100).toFixed(0)} %, en justifie ${(100 - o.partCroissance * 100).toFixed(0)} %.`;
  }
  return o;
}

function preuvesMoat(der) {
  const L = [];
  const n = der.annees.length;
  if (!n) return L;
  /* P10 — LE ROIC AFFICHÉ EST CELUI QUI CALCULE.
     Ce panneau publiait le ROIC côté ACTIF pendant que le score de moat et la
     brique EVA travaillaient sur le ROIC côté FINANCEMENT. Sur Siemens
     Healthineers, 6,0 % affiché contre 12,0 % utilisé — un facteur deux, et
     un spread publié que le chiffre juste au-dessus ne permettait pas de
     reconstituer. Les deux séries restent publiées, mais la RETENUE est
     nommée et vient en premier. */
  const R = roicRetenu(der);
  const roics = R.serie.length ? R.serie : der.annees.map(a => der.lignes[a].roic).filter(Number.isFinite);
  const mb = der.annees.map(a => der.lignes[a].margeBrute).filter(Number.isFinite);
  L.push(`ROIC médian retenu sur ${Number.isFinite(R.couverture) ? R.couverture : n} exercice(s)${Number.isFinite(R.couverture) && R.couverture < n ? ` (série de ${n})` : ""} : ${pct(R.mediane)} — base ${R.base}`);
  /* CONTRE-PREUVE PUBLIÉE — un dénominateur qui ne décide pas mais qui
     contredit doit être lu, sinon la contradiction est tranchée en silence. */
  if (R.contreExp && R.contreExp.materiel) {
    L.push(`🔴 CONTRE-PREUVE — le capital d'exploitation donne ${pct(R.contreExp.mediane)} sur ${R.contreExp.couverture} exercice(s), soit ${pct(R.contreExp.ecart)} d'écart avec la base retenue. Il est plus juste mais trop court pour décider. Écart matériel : un \`seed\` des postes de bilan (ppe, intangTot, receivables, inventory, payables) est nécessaire avant de fonder une décision sur ce ROIC.${R.contreExp.sansStocks ? " Stocks non servis dans ce calcul." : ""}`);
  } else if (R.contreExp) {
    L.push(`✅ Contre-preuve — le capital d'exploitation donne ${pct(R.contreExp.mediane)} sur ${R.contreExp.couverture} exercice(s), ${pct(R.contreExp.ecart)} d'écart : les deux bases se corroborent.`);
  }
  if (R.contreAutre) L.push(`Contre-preuve — ${R.contreAutre.nom} : ${pct(R.contreAutre.mediane)} sur ${R.contreAutre.couverture} exercice(s), ${pct(R.contreAutre.ecart)} d'écart.`);
  const autre = R.brut ? null : (CLE_BASE(R.cle) === "roicFin" ? der.roicMedian : der.roicFinMedian);
  const autreNom = CLE_BASE(R.cle) === "roicFin" ? "côté actif (actif − passif courant − trésorerie)" : "côté financement (dette + capitaux propres − trésorerie)";
  if (der.choc) L.push(`🟠 ${der.choc}`);
  if (R.exploitation) L.push(`🟢 Dénominateur d'exploitation retenu — il n'est pas obtenu en retranchant depuis le total du bilan, donc le flottant client ne l'atteint pas. C'est la base qui rend le filtre dur « ROIC > WACC sans décrochage » testable sur cette société.${R.sansStocks ? " ⚠️ Stocks non servis et traités comme nuls : le ROIC obtenu est majoré, à confirmer par un `seed`." : ""}`);
  if (R.brut) L.push("🟠 Les deux dénominateurs usuels sont inexploitables sur cette société — capital investi négatif ou écrasé par la trésorerie client. Le capital employé BRUT prend le relais : il ne déduit pas la trésorerie, donc il minore le ROIC plutôt que de le faire exploser. Score de moat et brique EVA travaillent sur ce même chiffre.");
  if (Number.isFinite(autre) && Number.isFinite(R.mediane)) {
    const rap = R.mediane !== 0 ? autre / R.mediane : null;
    L.push(`Autre dénominateur, ${autreNom} : ${pct(autre)}${Number.isFinite(rap) && (rap > 3 || rap < 0.33) ? " 🔴 écart supérieur à 3× — société financée par son cycle d'exploitation, voir le profil de bilan" : ""}`);
  }
  if (roics.length) {
    const mn = Math.min(...roics), mx = Math.max(...roics);
    L.push(`Fourchette ROIC (base retenue) : ${pct(mn)} — ${pct(mx)}`);
  }
  if (mb.length >= 2) {
    const mn = Math.min(...mb), mx = Math.max(...mb);
    L.push(`Marge brute : ${pct(mn)} — ${pct(mx)} (amplitude ${pct(mx - mn)}) — stabilité à travers le cycle`);
  }
  L.push(`Régularité : CA en hausse ${ratioReg(der.regularite.caH, der.regularite.caN)} · FCF positif ${ratioReg(der.regularite.fcfP, der.regularite.fcfN)}`);
  if (Number.isFinite(der.cagr.ca.retenu)) L.push(`CAGR CA retenu : ${pct(der.cagr.ca.retenu)}`);
  else if (der.rupture) L.push(`🔴 CAGR CA non publiable — ${der.rupture}. Reprendre la série par \`seed\` depuis le rapport annuel.`);
  if (der.cagr.ca.noteRegression) L.push(`⚠️ ${der.cagr.ca.noteRegression}`);
  if (der.cagr.ca.baseCreux) L.push(`⚠️ ${der.cagr.ca.baseCreux}`);
  return L;
}

function ratioReg(n, d) {
  if (!Number.isFinite(d) || d <= 0) return "non calculable (aucun exercice servi)";
  return `${n}/${d}`;
}

function derives(series, devise) {
  const S = c => series[c] || {};
  const ans = [...new Set(
    Object.keys(S("revenue")).concat(Object.keys(S("netIncome"))).concat(Object.keys(S("ebit")))
  )].map(Number).filter(Number.isFinite).sort();

  const d = { annees: ans, lignes: {}, devise };
  if (!ans.length) return d;
  const v = (c, a) => { const x = S(c)[a]; return Number.isFinite(x) ? x : null; };

  for (const a of ans) {
    const ca = v("revenue", a), ebit = v("ebit", a), rn = v("netIncome", a), gp = v("grossProfit", a);
    const cfo = v("cfo", a), capex = v("capex", a), da = v("da", a), ebitdaY = v("ebitda", a);
    const tax = v("tax", a), pretaxSrv = v("pretax", a);
    /* SUBSTITUT PAR IDENTITÉ COMPTABLE — résultat avant impôt = résultat net
       + impôt. Exact hors intérêts minoritaires et activités abandonnées,
       tous deux négligeables ici. Ce repli est ce qui rend le taux d'impôt
       INDÉPENDANT du nom du tag : deviner des noms de tags est un jeu sans
       fin, une identité comptable ne se périme pas. Il est signalé, jamais
       silencieux, parce qu'il vaut approximation et non relevé. */
    let pretax = pretaxSrv, pretaxDerive = false;
    if ((pretax === null || pretax === 0) && rn !== null && tax !== null) {
      const x = rn + Math.abs(tax);
      if (x > 0) { pretax = x; pretaxDerive = true; }
    }
    const act = v("assets", a), pc = v("currentLiab", a), cash = v("cash", a), dette = v("debt", a);

    const fcf = (cfo !== null && capex !== null) ? cfo - Math.abs(capex) : null;
    const sbc = v("sbc", a);
    // SBC déjà additionné dans le CFO (poste non-cash) : le retirer du FCF
    // donne le "FCF réel" hors effet de la rémunération en actions — utile
    // car le SBC masque une dilution qui ne coûte rien en cash aujourd'hui
    // mais en coûtera aux actionnaires existants demain.
    const fcfAjSBC = (fcf !== null && Number.isFinite(sbc)) ? fcf - sbc : null;
    const ti = (tax !== null && pretax && pretax !== 0) ? Math.abs(tax / pretax) : null;
    const nopat = (ebit !== null && ti !== null) ? ebit * (1 - ti) : null;
    /* ══ NOPAT RETRAITÉ — LE NUMÉRATEUR REMIS EN FACE DE SON DÉNOMINATEUR ══
       Calculé pour TOUS les exercices, ACTIVÉ pour peu de sociétés : la
       décision d'usage se prend plus bas, une fois la couverture et le poids
       du goodwill mesurés sur la série entière (`d.retraitement`). Ici on ne
       fait que produire la grandeur, jamais l'imposer.
       BORNES DE SÉCURITÉ — TROIS INVARIANTS, ET SURTOUT PAS UN RAPPORT À
       L'EBIT. La première version bornait le poste à 60 % de l'EBIT publié.
       Elle était fausse, et fausse exactement là où le retraitement sert :
       l'année qui suit une acquisition majeure, l'amortissement des
       incorporels acquis DÉPASSE couramment l'EBIT qu'il vient d'écraser —
       c'est le fait à corriger, pas une anomalie de tag. Testé sur une série
       de type Broadcom, cette borne écartait 5 exercices sur 9 et le
       retraitement ne s'activait jamais : un correctif désarmé par son propre
       garde-fou.
       Les trois invariants retenus ne regardent donc pas le résultat, ils
       regardent la COHÉRENCE COMPTABLE du poste lui-même :
       (i) l'amortissement annuel des incorporels ne peut pas dépasser la
           moitié du stock d'incorporels au bilan — au-delà, la durée de vie
           implicite tomberait sous deux ans, ce qu'aucun actif d'acquisition
           ne connaît ;
       (ii) il est une COMPOSANTE des amortissements totaux : il ne peut pas
            leur être supérieur (tolérance 2 % pour les arrondis et les
            périmètres de dépôt légèrement différents) ;
       (iii) il ne peut pas dépasser la moitié du chiffre d'affaires.
       Un tag mal scopé — amortissement total, cumul depuis l'origine,
       dotations d'un segment — échoue à (i) ou à (ii). Un amortissement
       d'acquisition authentique les passe tous les trois, même quand il
       dépasse l'EBIT. */
    const amortAcq = v("amortAcq", a);
    const intangRef = (v("intangTot", a) !== null) ? v("intangTot", a)
                    : ((v("intangExGW", a) !== null && v("goodwill", a) !== null) ? v("intangExGW", a) + v("goodwill", a) : null);
    const amortOK = Number.isFinite(amortAcq) && amortAcq > 0
      && (intangRef === null || amortAcq <= 0.50 * intangRef)
      && (da === null || amortAcq <= 1.02 * Math.abs(da))
      && (ca === null || amortAcq <= 0.50 * ca);
    const amortAcqRetenu = amortOK ? amortAcq : null;
    /* NOPAT retraité publié même si l'EBIT publié est négatif : c'est
       précisément l'exercice post-acquisition, celui que le retraitement
       existe pour rendre lisible. Le résultat retraité doit en revanche être
       positif, faute de quoi on ne retraite rien — un ROIC négatif corrigé en
       ROIC négatif n'apprend rien et brouille la série. */
    const ebitRet = (ebit !== null && amortAcqRetenu !== null) ? ebit + amortAcqRetenu : null;
    const nopatRet = (ebitRet !== null && ebitRet > 0 && ti !== null) ? ebitRet * (1 - ti) : null;
    /* DEUX DÉNOMINATEURS, jamais un seul.
       A) CÔTÉ ACTIF — actif − passif courant − trésorerie. Défaut historique,
          mais il soustrait TOUT le passif courant : chez une société financée
          par son cycle d'exploitation (billetterie encaissée d'avance,
          abonnements payés d'avance), ce float écrase le dénominateur et
          produit un ROIC aberrant. CTS Eventim et Verisk l'ont révélé.
       B) CÔTÉ FINANCEMENT — dette + capitaux propres − trésorerie. Mesure
          l'argent réellement immobilisé par les apporteurs de fonds, donc
          INSENSIBLE au float, qui est un financement gratuit fourni par les
          clients et non par un investisseur.
       Un écart large entre A et B n'est pas un bug : c'est le signal que la
       société se finance sur son exploitation. Les deux sont publiés, l'écart
       est signalé, et aucun des deux n'est retenu en silence à la place de
       l'autre — la divergence est une information, pas une nuisance. */
    const equity = v("equity", a);
    const ci = (act !== null && pc !== null && cash !== null) ? act - pc - cash : null;
    const ciFin = (dette !== null && equity !== null && cash !== null) ? dette + equity - cash : null;

    /* C) CAPITAL D'EXPLOITATION — construit par le HAUT du bilan.
       immobilisations nettes + incorporels (goodwill inclus) + besoin en fonds
       de roulement d'exploitation (créances + stocks − fournisseurs).
       NI la trésorerie NI le flottant client n'y entrent : ils ne sont pas
       soustraits, ils sont ABSENTS du périmètre. C'est la différence de fond
       avec A et B, qui partent du total du bilan et retranchent — donc qui
       comptent le flottant deux fois chez une société qui encaisse d'avance
       (une fois en trésorerie à l'actif, une fois en produits constatés
       d'avance au passif courant).
       CE QUE CE DÉNOMINATEUR NE FAIT PAS : il n'ajoute aucun jugement. Chaque
       poste est un poste publié. Il ne s'applique donc qu'aux exercices où
       ces postes existent, et se tait ailleurs — jamais de reconstitution.
       STOCKS ABSENTS : traités comme nuls, ce qui MINORE le capital et donc
       MAJORE le ROIC. Le sens de l'erreur est le mauvais : le drapeau
       `ciExpSansStocks` est levé et publié partout où le dénominateur sert. */
    const ppe = v("ppe", a);
    const intangY = v("intangTot", a), intangEx = v("intangExGW", a), gw = v("goodwill", a);
    /* ---- INVARIANT : INCORPORELS Y COMPRIS GOODWILL ≥ GOODWILL --------------
       Le goodwill EST une composante des incorporels consolidés : le poste ne
       peut pas lui être inférieur. Quand il l'est, c'est que le déposant a
       utilisé le tag « including goodwill » pour une valeur qui l'exclut —
       cas constaté sur Amazon le 30/08/2026 : 4,95 Md déposés sous ce tag
       pour 23,27 Md de goodwill.
       Faire confiance au NOM du tag revenait à publier un dénominateur amputé
       du goodwill, donc un ROIC surestimé, avec l'étiquette rassurante
       « consolidé ». L'invariant tranche sur les CHIFFRES et non sur le nom :
       une contradiction arithmétique ne se discute pas.
       PENSÉE INVERSE — risque de recomposer à tort ? Il faudrait qu'une
       société publie des incorporels consolidés inférieurs à son propre
       goodwill, ce qui est comptablement impossible. Le seul cas limite, un
       goodwill mal daté d'un exercice, est couvert par la publication du mode
       retenu année par année. */
    let intang = null, intangMode = null;
    if (intangY !== null && gw !== null && intangY < gw) {
      intang = intangY + gw;
      intangMode = "étiquette démentie (poste < goodwill) — recomposé";
    } else if (intangY !== null) { intang = intangY; intangMode = "consolidé"; }
    else if (intangEx !== null && gw !== null) { intang = intangEx + gw; intangMode = "recomposé (hors goodwill + goodwill)"; }
    else if (intangEx !== null) { intang = intangEx; intangMode = "hors goodwill — MINORÉ"; }
    else if (gw !== null) { intang = gw; intangMode = "goodwill seul — MINORÉ"; }
    const rec = v("receivables", a), inv = v("inventory", a), pay = v("payables", a);
    let ciExp = null, ciExpSansStocks = false;
    if (ppe !== null && intang !== null && rec !== null && pay !== null) {
      if (inv === null) ciExpSansStocks = true;
      const bfr = rec + (inv === null ? 0 : inv) - pay;
      const x = ppe + intang + bfr;
      if (x > 0) ciExp = x;
    }
    /* FLOTTANT CLIENT — mesuré, jamais supposé. Produits constatés d'avance
       courants et non courants. Sert à NOMMER le poste dont le cadre exige
       qu'il soit nommé, et à mesurer sa taille face au capital d'exploitation.
       Il n'entre dans aucun calcul : un financement gratuit fourni par les
       clients n'est ni du capital investi, ni de la dette. */
    /* FLOTTANT CLIENT — SAISI EN PRIORITÉ, DÉDUIT À DÉFAUT.
       `flottant` est un champ de `seed` à part entière : sur une billetterie,
       l'argent des clients et des organisateurs ne se range PAS en produits
       constatés d'avance. Chez CTS Eventim, les acomptes reçus ne sont que la
       partie visible — l'essentiel figure en autres passifs financiers
       courants, poste qu'aucune taxonomie ne rattache au flottant. Un montant
       automatique faible ne prouve donc rien, et c'est pourquoi la saisie
       manuelle prime sans discussion.
       À défaut de saisie, on retombe sur les produits constatés d'avance
       servis par la source, en sachant qu'ils MINORENT le flottant réel. */
    const flottantSaisi = v("flottant", a);
    const dr = v("deferredRev", a), drNC = v("deferredRevNC", a);
    const flottantAuto = (dr === null && drNC === null) ? null : (dr || 0) + (drNC || 0);
    const flottant = flottantSaisi !== null ? flottantSaisi : flottantAuto;
    const flottantSource = flottantSaisi !== null ? "saisi" : (flottantAuto !== null ? "produits constatés d'avance servis" : null);
    const ebitda = Number.isFinite(ebitdaY) ? ebitdaY : ((ebit !== null && da !== null) ? ebit + da : null);
    const dn = (dette !== null && cash !== null) ? dette - cash : null;

    d.lignes[a] = {
      ca, ebit, rn, fcf, ebitda,
      roic: (nopat !== null && ci && ci > 0) ? nopat / ci : null,
      roicFin: (nopat !== null && ciFin && ciFin > 0) ? nopat / ciFin : null,
      roicExp: (nopat !== null && ciExp && ciExp > 0) ? nopat / ciExp : null,
      /* Trois ROIC retraités, un par dénominateur, strictement parallèles aux
         trois précédents. Ils ne remplacent rien tant que `d.retraitement`
         n'est pas actif — et quand il l'est, les DEUX séries restent publiées. */
      amortAcq: amortAcqRetenu, nopatRet,
      roicRet: (nopatRet !== null && ci && ci > 0) ? nopatRet / ci : null,
      roicFinRet: (nopatRet !== null && ciFin && ciFin > 0) ? nopatRet / ciFin : null,
      roicExpRet: (nopatRet !== null && ciExp && ciExp > 0) ? nopatRet / ciExp : null,
      /* ROIC ORGANIQUE — capital investi MOINS le goodwill acquis.
         Sur un serial acquirer, le goodwill peut représenter la majorité du
         dénominateur : Broadcom porte 97,8 Md USD de goodwill après Brocade,
         CA, Symantec et VMware, ce qui écrase son ROIC médian à 6,0 % quand
         le dernier exercice ressort à 18,4 %. Toute une classe de compounders
         paraît alors médiocre alors qu'elle ne l'est pas.
         🔴 IL NE REMPLACE JAMAIS LE ROIC COMPTABLE, et la raison est
         économique, pas prudentielle : l'argent des acquisitions a bien été
         dépensé. Un acquéreur qui surpaye détruit de la valeur, et c'est
         précisément le ROIC comptable qui le capte. Retirer le goodwill
         reviendrait à effacer le prix payé.
         LES DEUX SE LISENT ENSEMBLE, ET C'EST L'ÉCART QUI INFORME :
         · comptable = rendement du capital TOTAL engagé, prix des
           acquisitions compris → juge l'ALLOCATION DU CAPITAL ;
         · organique = rendement des seuls actifs d'exploitation → juge la
           QUALITÉ OPÉRATIONNELLE ;
         · l'écart entre les deux MESURE ce que les acquisitions ont coûté en
           rendement. Écart faible = croissance interne ou acquisitions bien
           payées. Écart large = un opérateur solide qui achète cher.
         Un écart large n'est donc jamais un downgrade de moat : c'est un
         point d'allocation, au sens de l'étape 4b de la grille. */
      roicOrg: (nopat !== null && ci && gw !== null && (ci - gw) > 0) ? nopat / (ci - gw) : null,
      goodwillPartCI: (ci && ci > 0 && gw !== null) ? gw / ci : null,
      /* ROE — le seul rendement du capital qui ait un sens sur un bilan
         financier, où le passif EST la matière première et où le capital
         investi n'a pas de définition industrielle. Calculé pour toutes les
         sociétés, publié pour toutes, mais c'est sur le profil financier
         qu'il devient la métrique de tête, face au coût des capitaux propres
         et non face au WACC. */
      roe: (rn !== null && equity && equity > 0) ? rn / equity : null,
      margeEbit: (ca && ebit !== null) ? ebit / ca : null,
      margeBrute: (ca && gp !== null) ? gp / ca : null,
      convFCF: (fcf !== null && rn && rn !== 0) ? fcf / rn : null,
      detteEbitda: (dn !== null && ebitda && ebitda > 0) ? dn / ebitda : null,
      tauxImpot: ti, pretaxDerive, intangMode,
      eps: v("eps", a), actions: v("shares", a),
      goodwill: v("goodwill", a), goodwillImp: v("goodwillImp", a),
      buybacks: v("buybacks", a), sharesRach: v("sharesRach", a), dividendes: v("dividendes", a),
      sbc, fcfAjSBC, sbcPctFCF: (Number.isFinite(sbc) && fcf && fcf > 0) ? sbc / fcf : null,
      /* EXPOSÉS POUR L'ANCRAGE PROPRE — ces trois postes étaient calculés puis
         jetés. La brique EVA a besoin du capital investi en niveau (pas
         seulement du ROIC qui en dérive), et la brique multiples de la dette
         nette par exercice pour reconstituer une valeur d'entreprise. */
      ci, ciFin, ciExp, ciExpSansStocks, flottant, flottantSource, dn, nopat, dette, equity
    };
  }

  const dern = ans[ans.length - 1];

  /* P8 — RUPTURE DE DÉFINITION D'UNE SÉRIE.
     Le cas Adyen : marge brute de 14,9 % à 89,3 % sur quatre exercices, CAGR
     du CA à −33 %. Aucune société ne fait ça ; c'est le mélange de deux
     définitions du chiffre d'affaires (revenu brut incluant les frais de
     règlement une année, revenu net l'autre). Une série ainsi mélangée n'est
     pas « bruitée », elle est INCOMPARABLE d'un exercice à l'autre : tout
     taux de croissance qu'on en tire est un artefact de taxonomie.
     On ne répare pas — savoir quelle définition est la bonne suppose le
     rapport annuel, donc un `seed`. On MARQUE, et tout ce qui consomme un
     CAGR en aval le sait. */
  d.rupture = null;
  {
    /* UNE RUPTURE EST UN SAUT, PAS UNE DÉRIVE.
       Le test portait sur l'AMPLITUDE de la marge brute sur toute la fenêtre.
       Faux positif immédiat sur Amazon : 22,3 % en 2007, 50,3 % en 2025, soit
       28 points d'amplitude — et rien d'anormal, c'est la bascule du commerce
       de détail vers AWS et la publicité sur dix-huit ans. Le serveur bloquait
       entièrement une ligne détenue pour cause de transformation réussie.
       Le vrai signal d'un changement de définition est un SAUT entre deux
       exercices CONSÉCUTIFS — Adyen passe de 14,9 % à 85 % en un an, ce
       qu'aucune entreprise ne fait. On teste donc l'écart d'une année sur
       l'autre, et uniquement entre exercices réellement adjacents : un trou
       dans la série ne doit pas se lire comme une marche. */
    /* UN SAUT NE SUFFIT PAS : IL DOIT PERSISTER.
       Le test ne regardait que l'écart entre deux exercices consécutifs. Faux
       positif immédiat sur CTS Eventim : marge brute de 27,8 % en 2019 à
       3,8 % en 2020, parce que les salles étaient fermées et les concerts
       annulés. Le serveur y lisait un changement de définition du chiffre
       d'affaires, bloquait la série entière, et refusait ensuite TOUTE brique
       de valorisation — sur une société dont les comptes sont parfaitement
       homogènes et dont l'exercice 2020 est au contraire l'observation la
       plus informative de la fenêtre.
       CE QUI DISTINGUE LES DEUX, et c'est vérifiable sans rapport annuel :
       · un changement de DÉFINITION ne revient jamais en arrière — le nouveau
         niveau de marge est le nouveau régime, définitivement
       · un CHOC D'EXPLOITATION revient — la marge retrouve son niveau
         antérieur dès que la cause disparaît
       On teste donc la RÉVERSION sur les deux exercices suivants. Marge
       revenue à moins de 5 points du niveau d'avant ⇒ ce n'était pas une
       rupture, c'est un exercice de stress, et il est NOMMÉ comme tel au lieu
       de bloquer la série. Le cadre réclame précisément un exercice de stress
       identifié pour juger la durabilité d'un moat : le serveur le fournit au
       lieu de le jeter.
       PENSÉE INVERSE : une société dont la marge s'effondre puis remonte
       grâce à un changement de périmètre passerait pour un choc. C'est
       pourquoi la réversion doit être VERS LE NIVEAU D'AVANT, pas simplement
       une remontée — un retour à 5 points près sur une chute de plus de
       20 points ne s'obtient pas par accident. */
    d.choc = null;
    for (let i = 1; i < ans.length; i++) {
      if (ans[i] - ans[i - 1] !== 1) continue;
      const m0 = d.lignes[ans[i - 1]].margeBrute, m1 = d.lignes[ans[i]].margeBrute;
      if (!(Number.isFinite(m0) && Number.isFinite(m1))) continue;
      if (Math.abs(m1 - m0) <= 0.20) continue;

      let revenu = null;
      for (let j = i + 1; j <= i + 2 && j < ans.length; j++) {
        const mj = d.lignes[ans[j]].margeBrute;
        if (Number.isFinite(mj) && Math.abs(mj - m0) <= 0.05) { revenu = ans[j]; break; }
      }
      if (revenu !== null) {
        const ca0 = d.lignes[ans[i - 1]].ca, ca1 = d.lignes[ans[i]].ca;
        const chute = (Number.isFinite(ca0) && ca0 > 0 && Number.isFinite(ca1)) ? ca1 / ca0 - 1 : null;
        if (!d.choc) {
          d.choc = `exercice de stress ${ans[i]} — marge brute ${pct(m0)} → ${pct(m1)}`
            + (Number.isFinite(chute) ? `, chiffre d'affaires ${chute < 0 ? "en repli de " + pct(-chute) : "en hausse de " + pct(chute)}` : "")
            + `, retour à ${pct(d.lignes[revenu].margeBrute)} dès ${revenu}. La série n'est PAS hétérogène : le niveau d'avant est retrouvé, ce qu'un changement de définition ne fait jamais. Exercice utilisable comme test de résistance du moat, et à exclure explicitement de toute année de départ de CAGR.`;
        }
        continue;
      }
      d.rupture = `marge brute de ${pct(m0)} à ${pct(m1)} entre ${ans[i - 1]} et ${ans[i]} — un saut de ${pct(Math.abs(m1 - m0))} en un exercice, sans retour au niveau antérieur sur les deux exercices suivants, ne décrit pas une entreprise : il décrit un changement de définition du chiffre d'affaires`;
      break;
    }
    if (!d.rupture) {
      for (let i = 1; i < ans.length; i++) {
        const v0 = d.lignes[ans[i - 1]].ca, v1 = d.lignes[ans[i]].ca;
        const e0 = d.lignes[ans[i - 1]].ebit, e1 = d.lignes[ans[i]].ebit;
        if (!(Number.isFinite(v0) && Number.isFinite(v1) && v0 > 0 && v1 > 0)) continue;
        const chuteCA = v1 / v0 - 1;
        if (chuteCA < -0.30 && Number.isFinite(e0) && Number.isFinite(e1) && e0 > 0 && e1 > e0 * 0.85) {
          d.rupture = `chiffre d'affaires ${ans[i - 1]}→${ans[i]} en repli de ${pct(-chuteCA)} alors que l'EBIT tient — changement de périmètre ou de définition du CA, pas une contraction d'activité`;
          break;
        }
      }
    }
  }

  d.cagr = {};
  for (const k of ["ca", "ebit", "fcf", "eps"]) {
    const o = {};
    for (const n of [5, 10]) {
      const a0 = dern - n;
      o[n] = (d.lignes[a0] && d.lignes[dern]) ? cagr(d.lignes[a0][k], d.lignes[dern][k], n) : null;
    }
    o.retenu = (o[5] !== null && o[10] !== null) ? Math.min(o[5], o[10]) : (o[5] !== null ? o[5] : o[10]);
    // FENÊTRE COURTE : ni 5 ni 10 ans disponibles (société jeune, scission
    // récente, ou série serveur tronquée). Plutôt que de renvoyer n.c. et de
    // bloquer toute l'analyse en aval — DCF propre compris — on calcule sur la
    // plus longue fenêtre réellement disponible POUR CET AGRÉGAT et on la
    // NOMME. Une fenêtre courte se lit sur son barème, elle ne bloque pas.
    // Plancher à 2 intervalles (3 exercices) : en dessous, un CAGR n'est
    // qu'une variation déguisée en taux.
    if (o.retenu === null) {
      let a0 = null;
      for (const a of ans) { const v0 = d.lignes[a][k]; if (Number.isFinite(v0) && v0 > 0) { a0 = a; break; } }
      const nf = (a0 !== null) ? dern - a0 : 0;
      if (a0 !== null && nf >= 2) {
        const c0 = cagr(d.lignes[a0][k], d.lignes[dern][k], nf);
        if (c0 !== null) { o.court = { n: nf, debut: a0, fin: dern, valeur: c0 }; o.retenu = c0; }

        /* P5 — SUR FENÊTRE COURTE, DEUX POINTS NE FONT PAS UNE TENDANCE.
           Un CAGR de bout en bout sur trois intervalles ne mesure que le
           rapport entre l'année de départ et l'année d'arrivée. Si le départ
           est un creux — GTT 2022, creux de commandes GNL — le taux obtenu
           décrit la sortie du creux et non le rythme de la société : 37,7 %
           par an chez GTT, chiffre qui a ensuite plafonné la croissance du
           capital dans la brique EVA et fait exploser la valeur.
           CORRECTIF : régression log-linéaire, qui utilise TOUS les points au
           lieu des deux extrêmes, et on retient LE PLUS BAS des deux — même
           doctrine que le « plus bas entre 5 et 10 ans » appliquée ailleurs.
           Les deux valeurs sont publiées, aucune n'est effacée. */
        const pts = ans.map(a => ({ x: a, y: d.lignes[a][k] })).filter(p => Number.isFinite(p.y) && p.y > 0);
        if (pts.length >= 3) {
          const n = pts.length;
          const mx = pts.reduce((s, p) => s + p.x, 0) / n;
          const my = pts.reduce((s, p) => s + Math.log(p.y), 0) / n;
          let num2 = 0, den = 0;
          for (const p of pts) { num2 += (p.x - mx) * (Math.log(p.y) - my); den += (p.x - mx) * (p.x - mx); }
          if (den > 0) {
            const cr = Math.exp(num2 / den) - 1;
            if (Number.isFinite(cr)) {
              o.regression = { valeur: cr, n };
              if (o.retenu === null || cr < o.retenu) {
                o.retenuAvantRegression = o.retenu;
                o.retenu = cr;
                o.noteRegression = `CAGR de bout en bout ${pct(o.court ? o.court.valeur : NaN)} (${a0}→${dern}) contre ${pct(cr)} par régression log-linéaire sur ${n} points. Le plus bas est retenu : sur une fenêtre courte, deux extrémités ne font pas une tendance.`;
              }
            }
          }
          // Année de base non représentative : le départ est le minimum de la
          // série ET nettement sous sa médiane. Le signaler, jamais corriger.
          const ys = pts.map(p => p.y), med = mediane(ys);
          if (Number.isFinite(med) && med > 0 && pts[0].y === Math.min(...ys) && pts[0].y < med * 0.80) {
            o.baseCreux = `année de base ${a0} : point le plus bas de la série et ${pct(1 - pts[0].y / med)} sous sa médiane. Tout taux calculé depuis ce départ décrit une sortie de creux.`;
          }
        }
      }
    }
    if (d.rupture) {
      o.nonFiable = d.rupture;
      o.retenuBrut = o.retenu;
      o.retenu = null;   // ne peut plafonner ni alimenter aucun modèle
    }
    d.cagr[k] = o;
  }

  /* 🔴 LA FENÊTRE NE DOIT PLUS TRONQUER CE QUE LA SOURCE SERT MAINTENANT.
     `slice(-10)` a été calibré à une époque où dix exercices étaient un
     plafond rarement atteint. La bascule `companyfacts` en sert dix-sept : le
     compteur en ignorait sept, et le dossier annonçait « 17 exercices
     exploitables » deux lignes au-dessus d'une régularité mesurée sur dix.
     Le biais grandit à mesure que la couverture s'améliore — c'est le pire
     genre de défaut, celui qui s'aggrave quand le système progresse. */
  const f = ans.slice();
  /* 🔴 DÉNOMINATEUR = CE QUI EST CALCULABLE, JAMAIS LA LARGEUR DE LA FENÊTRE.
     Les deux compteurs comptaient leur numérateur sur les exercices RENSEIGNÉS
     et leur dénominateur sur la fenêtre ENTIÈRE. Un poste manquant se lisait
     donc comme un échec : « CA en hausse 8/9 » là où le réel était 14/16, et
     « FCF positif 4/10 » sur quatre exercices servis, tous positifs.
     Le défaut est UNIVERSEL, pas propre à un déposant : il frappe toute
     société à couverture partielle — non-déposants SEC, ESEF incomplet,
     jeunes cotations — et toujours dans le même sens, celui qui pénalise. Une
     donnée absente devient une régularité absente.
     RÈGLE : un ratio de régularité ne mesure que les exercices où la question
     se pose. Les autres sortent du dénominateur, comme un point QUALITÉ non
     évaluable sort du sien. */
  const paires = f.map((a, i) => (i > 0 ? [d.lignes[f[i - 1]].ca, d.lignes[a].ca] : null))
    .filter(p => p && Number.isFinite(p[0]) && Number.isFinite(p[1]));
  const fcfs = f.filter(a => Number.isFinite(d.lignes[a].fcf));
  d.regularite = {
    caH: paires.filter(p => p[1] > p[0]).length,
    caN: paires.length,
    fcfP: fcfs.filter(a => d.lignes[a].fcf > 0).length,
    fcfN: fcfs.length
  };
  d.roicMedian = mediane(ans.map(a => d.lignes[a].roic));
  d.roicFinMedian = mediane(ans.map(a => d.lignes[a].roicFin));
  d.roicExpMedian = mediane(ans.map(a => d.lignes[a].roicExp));
  d.roicOrgMedian = mediane(ans.map(a => d.lignes[a].roicOrg));
  d.goodwillPartMedian = mediane(ans.map(a => d.lignes[a].goodwillPartCI));
  d.roicRetMedian = mediane(ans.map(a => d.lignes[a].roicRet));
  d.roicFinRetMedian = mediane(ans.map(a => d.lignes[a].roicFinRet));
  d.roicExpRetMedian = mediane(ans.map(a => d.lignes[a].roicExpRet));

  /* ══ DÉCISION DE RETRAITEMENT — PRISE UNE FOIS, PUBLIÉE PARTOUT ══
     TROIS CONDITIONS CUMULATIVES, aucune n'est un réglage de confort.
     (1) POIDS. Goodwill ≥ 25 % du capital investi en médiane. En dessous, le
         double compte existe toujours mais ne déplace pas la lecture : on ne
         touche donc pas au ROIC de 90 % des sociétés suivies pour corriger
         un effet immatériel. C'est un choix de PÉRIMÈTRE, pas de principe —
         et il est écrit ici pour qu'il puisse être discuté.
     (2) COUVERTURE. Le poste doit être servi sur au moins 60 % des exercices
         de la série. Une correction intermittente fabriquerait exactement le
         défaut que le serveur combat ailleurs : une série qui change de
         définition en cours de route et ne se compare plus à elle-même.
     (3) SENS. Le retraitement doit RELEVER le ROIC. S'il l'abaisse, le tag
         capté n'est pas celui qu'on croit et on s'abstient.
     CE QUE LE RETRAITEMENT NE FAIT PAS : il n'efface pas le prix payé. Le
     goodwill et les incorporels acquis restent intégralement au dénominateur.
     Un acquéreur qui surpaye garde donc un ROIC bas, et le point ALLOCATION
     DU CAPITAL continue de le sanctionner. La seule chose retirée est la
     charge qui facturait une deuxième fois un actif déjà compté en entier. */
  const couvRet = ans.filter(a => Number.isFinite(d.lignes[a].amortAcq)).length;
  const couvRoic = ans.filter(a => Number.isFinite(d.lignes[a].roic) || Number.isFinite(d.lignes[a].roicExp)).length;
  const partGW = d.goodwillPartMedian;
  const rt = { actif: false, couverture: couvRet, couvertureRoic: couvRoic, partGW, motif: null };
  if (!Number.isFinite(partGW) || partGW < 0.25) {
    rt.motif = Number.isFinite(partGW)
      ? `goodwill à ${(partGW * 100).toFixed(1)} % du capital investi, sous le seuil de 25 % : le double compte existe mais ne déplace pas la lecture`
      : "part du goodwill dans le capital investi non mesurable";
  /* 🔴 SEUIL DE COUVERTURE RELATIF — DÉFAUT INDUIT, CORRIGÉ ICI (w155).
     La couverture d'`amortAcq` était rapportée à la LONGUEUR DE LA FENÊTRE
     ROIC. Ces deux grandeurs bougent indépendamment : la reconstitution des
     amortissements par les trimestres (§96) a allongé la fenêtre de 6 à 9
     exercices sans qu'`amortAcq` suive, le ratio est passé sous 60 %, le
     régime a basculé et le ROIC publié de Broadcom s'est effondré de 13,7 % à
     5,8 % alors que RIEN n'avait changé dans les comptes.

     C'est le motif du `slice(-10)` : un défaut qui s'aggrave à mesure que la
     COUVERTURE S'AMÉLIORE. Le pire profil, parce qu'il punit le progrès.

     🔴 ET LA CORRECTION NAÏVE EST PIRE. Restreindre la médiane aux seuls
     exercices retraitables produirait un BIAIS DE SÉLECTION : `amortAcq` est
     servi sur les exercices RÉCENTS, c'est-à-dire précisément après la
     digestion des acquisitions. On publierait 13,7 % en écartant les années de
     faible rendement — un faux positif fabriqué par la fenêtre, exactement ce
     que le principe 1 interdit.

     ARBITRAGE : on ne CHOISIT pas entre les deux séries, on publie LES DEUX
     avec leur fenêtre respective, et la divergence devient l'information.
     C'est le motif de la CONTRE-PREUVE déjà employé pour les dénominateurs de
     ROIC — une règle existante appliquée à un nouvel axe, jamais une règle
     nouvelle à défendre.

     Le seuil devient donc ABSOLU et non plus relatif : au moins 4 exercices
     retraitables, dont le PLUS RÉCENT — sans quoi le retraitement décrirait un
     passé révolu. Un seuil absolu ne peut pas être déplacé par un progrès de
     couverture ailleurs. */
  } else if (couvRet < 4) {
    rt.motif = `amortissement des incorporels d'acquisition servi sur ${couvRet} exercice(s) seulement, moins des 4 requis : trop peu pour une médiane retraitée. Remède : \`seed\` du poste depuis les comptes annuels`;
  } else if (ans.length && !Number.isFinite(d.lignes[ans[ans.length - 1]].amortAcq)) {
    rt.motif = `amortissement des incorporels d'acquisition absent du DERNIER exercice (${ans[ans.length - 1]}) : un retraitement qui s'arrête avant la fin décrirait un passé révolu, pas la rentabilité actuelle. Remède : \`seed\` du poste`;
  } else if (!Number.isFinite(d.roicExpRetMedian) && !Number.isFinite(d.roicRetMedian)) {
    rt.motif = "aucune médiane retraitée calculable";
  } else {
    const av = Number.isFinite(d.roicExpMedian) ? d.roicExpMedian : d.roicMedian;
    const ap = Number.isFinite(d.roicExpRetMedian) ? d.roicExpRetMedian : d.roicRetMedian;
    if (Number.isFinite(av) && Number.isFinite(ap) && ap <= av) {
      rt.motif = `le retraitement ABAISSE le ROIC médian (${(av * 100).toFixed(1)} % → ${(ap * 100).toFixed(1)} %) : le tag capté n'est pas l'amortissement d'acquisition, on s'abstient`;
    } else {
      rt.actif = true;
      rt.avant = av; rt.apres = ap;
      rt.motif = `goodwill à ${(partGW * 100).toFixed(1)} % du capital investi et amortissement d'acquisition servi sur ${couvRet}/${couvRoic} exercice(s)`;
    }
  }
  d.retraitement = rt;
  d.roeMedian = mediane(ans.map(a => d.lignes[a].roe));
  const a0 = f[0];
  let va = (d.lignes[a0] && d.lignes[a0].actions && d.lignes[dern].actions)
    ? (d.lignes[dern].actions / d.lignes[a0].actions - 1) : null;
  // Garde-fou : un split d'actions non ajusté dans les faits XBRL produit un
  // écart de plusieurs centaines de % qui n'est PAS une dilution/rachat réel.
  // Au-delà de ±300%, c'est presque certainement un split, pas du capital
  // allocation — on le signale plutôt que d'afficher un chiffre faux.
  d.varActionsSuspecte = Number.isFinite(va) && Math.abs(va) > 3;
  d.varActions = d.varActionsSuspecte ? null : va;
  d.impairments = ans.filter(a => Number.isFinite(d.lignes[a].goodwillImp) && d.lignes[a].goodwillImp > 0);
  return d;
}

export { derives, roicRetenu, profilSociete, moatPropre, preuvesMoat,
         ancrageEPV, trajectoireROIC, mediane, cagr, pct, num, millions };
