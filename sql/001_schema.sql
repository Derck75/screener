-- SCHEMA REEL de la base D1 `screener`, releve le 30/09/2026 (sqlite_master).
-- Il remplace la version d'origine, devenue fausse : colonnes ajoutees au fil
-- des etapes, table `comptes` remplacee par `comptes2`, index idx_excl absent.
--
-- Chaque script cree lui-meme les colonnes qu'il ecrit (fonction `migrer`) :
-- ce fichier sert a comprendre la base et a monter une base de test, pas a
-- la migrer. Colonnes ajoutees par les correctifs de l'audit (lots 1 a 3) :
--   societe   : sans_sa_le, sans_sa_motif, devise_comptes (incr8) ; profil_le,
--               profil_statut (incr12) ; sic (incr9) ; secteur_essai (incr11)
--   metriques : moat_propre, croissance_source, epv_capitaux, epv_statut,
--               fcf_depart, dette_nette, rn_dernier, ebit_chutes, noyau (incr5) ;
--               maj_cours (incr6) ; n_ratio, bande_n, n_note, capi_eur,
--               devise_ecart, maj_valorisation (incr7)

CREATE TABLE IF NOT EXISTS societe (ticker TEXT PRIMARY KEY, cik TEXT, isin TEXT, nom TEXT, pays_siege TEXT, place TEXT, devise TEXT, secteur TEXT, capitalisation REAL, eligible_pea INTEGER, source_eligibilite TEXT, vaneck TEXT, vaneck_sorti_le TEXT, suivi_serveur INTEGER DEFAULT 0, maj TEXT, ticker_bbg TEXT, vaneck_vu_le TEXT, origine TEXT, statut_serveur TEXT, comptes_maj TEXT);
CREATE INDEX IF NOT EXISTS idx_soc_univers ON societe (vaneck, eligible_pea, capitalisation);

CREATE TABLE IF NOT EXISTS comptes2 (ticker TEXT, exercice INTEGER, revenue REAL, netIncome REAL, ebit REAL, grossProfit REAL, cfo REAL, capex REAL, da REAL, ebitda REAL, sbc REAL, tax REAL, pretax REAL, amortAcq REAL, assets REAL, ppe REAL, intangTot REAL, intangExGW REAL, goodwill REAL, receivables REAL, inventory REAL, payables REAL, currentLiab REAL, debt REAL, cash REAL, equity REAL, shares REAL, deferredRev REAL, deferredRevNC REAL, flottant REAL, source TEXT, clot TEXT, PRIMARY KEY (ticker, exercice));

CREATE TABLE IF NOT EXISTS metriques (ticker TEXT PRIMARY KEY, roic_median REAL, roic_dernier REAL, spread_median REAL, n_ex_roic_sup_seuil INTEGER, n_ex_total INTEGER, seuil_rentabilite_forfaitaire REAL, ca_cagr REAL, fcf_cagr REAL, ebit_cagr REAL, ca_hausse_n INTEGER, ca_hausse_m INTEGER, fcf_positif_n INTEGER, fcf_positif_m INTEGER, mb_mediane REAL, mb_pente REAL, mb_residus REAL, conversion_fcf_rn REAL, actions_var_5a REAL, dette_ebitda REAL, dette_sur_ca REAL, cp_sur_ca REAL, cours REAL, plancher_epv REAL, epv_sur_cours REAL, fcf_yield REAL, per_courant REAL, per_median REAL, ecart_multiple REAL, score_moat INTEGER, biais_acquereur INTEGER DEFAULT 0, n_non_calculable INTEGER, exclusion TEXT, maj TEXT, denominateur_roic TEXT, score_moat_max INTEGER, profil_type TEXT, profil_ko TEXT, base_roic TEXT, contre_preuve_ecart REAL, ebit_dispersion REAL, drapeaux TEXT, eva_capitaux REAL, eva_statut TEXT, eva_h INTEGER, eva_sur_cours REAL, concordance REAL, part_tresorerie REAL, croissance_implicite REAL, croissance_demontree REAL, taux_obstacle REAL, ca_cagr5 REAL, fcf_cagr5 REAL, roic_organique REAL);
CREATE INDEX IF NOT EXISTS idx_screen ON metriques (score_moat, roic_median, epv_sur_cours);

CREATE TABLE IF NOT EXISTS notifications (ticker TEXT PRIMARY KEY, premiere TEXT, derniere TEXT, epv REAL, canal TEXT, motif TEXT, cours_signale REAL);
CREATE TABLE IF NOT EXISTS presets (nom TEXT PRIMARY KEY, filtres TEXT, tri TEXT, description TEXT, maj TEXT);
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, debut TEXT, fin TEXT, etape TEXT, statut TEXT, lignes_ecrites INTEGER, canari TEXT, message TEXT, detail TEXT);
