# Screener mondial — entrepôt D1 d'un serveur d'analyse d'actions

Ce dépôt alimente une base Cloudflare D1 (`screener`) d'environ 5 700 sociétés
cotées sur 26 places, et calcule pour chacune la rentabilité du capital, un
verdict de moat et trois regards de cherté. Le serveur d'analyse (worker
`donnees-portefeuille`, non public) interroge cette base par son outil
`screener`.

**Le screener trie, il ne conclut jamais.** Ses chiffres diffèrent volontairement
de l'analyse unitaire : seuil de rentabilité forfaitaire de 9 % au lieu d'un
WACC, pas de TTM, fenêtre de 12 exercices aux États-Unis et 5 ailleurs.

## Une seule définition des calculs

`src/noyau.js` est **généré** depuis le worker par `outils/generer_noyau.mjs`
(analyse acorn, fermeture transitive des dépendances, refus de toute fonction
qui toucherait au réseau, au KV ou à l'environnement). ROIC, capital investi,
profil de société, EPV, EVA, moat et moteur DCF deux phases sont donc ceux du
serveur. Ne jamais l'éditer à la main :

```sh
node outils/generer_noyau.mjs <worker.js>     # écrit src/noyau.js
node src/test_noyau.mjs                        # verdicts attendus, avant tout commit
```

## Chaîne d'étapes

| Workflow | Script | Cadence | Écrit |
|---|---|---|---|
| A univers | `incr2_vaneck.py`, `incr3_univers_us.py` | dimanche | `societe` (VanEck, déposants SEC) |
| B comptes | `incr4_comptes.py` | samedi, 12 derniers exercices clos | `comptes2` (SEC frames, dette par familles) |
| H univers international | `univers_intl.py --ecrire` | mensuel | `societe` (places d'origine avant places mixtes) |
| I comptes internationaux | `incr8_comptes_intl.py` | toutes les 2 h, par lots | `comptes2` (StockAnalysis, devise des comptes) |
| P profils internationaux | `incr12_profil_intl.py` | 4 fois par jour | `societe` : siège, ISIN, PEA ; cotations secondaires |
| M secteurs internationaux | `incr11_secteur_intl.py` | toutes les 6 h | `societe.secteur` |
| K sièges | `incr9_siege.py` | dimanche | `societe` : siège réel et code SIC des déposants SEC |
| C métriques | `incr5_metriques.mjs` → `incr6_prix.py` → `incr7_valorisation.mjs` | quotidien | `metriques` |
| L discord | `incr10_discord.py` | exception 17 h lun.-sam., hebdo dimanche 18 h | `notifications` |
| W travaux lourds | `lourd/lourd.mjs` | quotidien + à la demande du worker | KV du worker (voir plus bas) |
| F tests | `test_noyau.mjs`, `test_places.py`, `tests/test_chaine.mjs` | à chaque push dans `src/`, `tests/`, `outils/` | rien |

Chaque étape journalise son passage dans `runs` (statut, canari, compteurs).
Le message Discord hebdomadaire lit la fraîcheur **et** le dernier canari de
chaque étape : une étape en échec répété y apparaît en rouge.

### Ce que calcule l'étage métriques (`incr5`)

- ROIC retenu, organique, persistance au-dessus de 9 % ; profil de société du
  noyau. Exclusions : `financier`, `float`, `incoherent`, **`incomplet`**
  (postes manquants — ce n'est pas un modèle économique), **`hors_perimetre`**
  (SPAC et fonds cotés, par code SIC).
- Verdict de moat **propre** du noyau (`moatPropre`), celui de l'outil `moat`.
- Croissance démontrée du cadre : CAGR du FCF, le plus bas des fenêtres 5 ans
  et longue (repli sur le BPA).
- Drapeau `cyclique` : au moins deux replis de l'EBIT de 25 % ou plus d'un
  exercice au suivant, ou une activité de matières premières.
- Plancher EPV et brique EVA du noyau.

### Ce que calcule l'étage valorisation (`incr7`)

- **Seuil N** : `croissanceImplicite` du noyau — dix ans puis 2,5 % —, au
  taux-obstacle de l'enveloppe (10 % PEA, 11,5 % CTO), sur la valeur
  d'entreprise, depuis le plus bas du FCF du dernier exercice et de sa médiane
  sur trois. Bandes du cadre sur N ÷ croissance démontrée : vert ≤ 0,80 ·
  orange ≤ 1,00 · jaune ≤ 1,20 · rouge au-delà. Stocké dans
  `croissance_implicite` (nom historique), `n_ratio`, `bande_n`.
- EPV, EVA, concordance, part de trésorerie, rendement FCF, PER — tous
  rapportés à la capitalisation **exprimée dans la devise des comptes** (change
  de référence BCE du jour).
- `capi_eur` : plancher commun de taille (300 M€ par défaut dans l'outil et
  dans Discord).

## Places, identité et éligibilité PEA

`src/places.py` est la table unique des codes StockAnalysis (vérifiés sur une
page de cotation par place). Une société est identifiée par son **ISIN** :
deux tickers pour un même ISIN font une cotation primaire et une secondaire, et
la secondaire sort de la collecte. L'éligibilité PEA est **présumée** sur le
pays de l'ISIN (siège social de l'émetteur), le siège administratif en repli ;
elle ne remplace jamais la liste du courtier, et la source le dit.

## Déclenchement à l'heure

Le cron de GitHub part avec plusieurs heures de retard. Le worker Cloudflare
déclenche les workflows à l'heure par `workflow_dispatch`, selon sa variable
`DECLENCHEURS` :

```json
[{"wf":"W-lourd.yml","h":2},
 {"wf":"C-metriques.yml","h":16},
 {"wf":"L-discord.yml","h":17,"jours":[1,2,3,4,5,6],"inputs":{"mode":"exception"}},
 {"wf":"L-discord.yml","h":18,"jours":[0],"inputs":{"mode":"hebdo"}}]
```

Les `schedule` de GitHub restent en **filet** : une garde (`src/garde.py`)
les saute si l'étape a déjà réussi dans la fenêtre prévue.

## Travaux lourds du serveur (W)

Le plan gratuit de Cloudflare accorde 10 ms de processeur par appel ; l'index
EDGAR `companyfacts`, l'ingestion ESEF et les pages StockAnalysis en demandent
bien davantage. `src/lourd/lourd.mjs` télécharge le code du worker déployé,
l'exécute tel quel dans Node et écrit dans le même espace KV. Les journaux
publics ne portent que des comptes, jamais un ticker ; le détail des erreurs
reste en KV (`lourd:journal`). Tant que la délégation n'est pas activée côté
worker (`LOURD=github`), le passage tourne en ESSAI et n'écrit aucun compte.

## Secrets

| Secret | Usage |
|---|---|
| `CF_ACCOUNT_ID` | compte Cloudflare |
| `CF_API_TOKEN` | D1 (lecture/écriture) ; pour W : Workers Scripts lecture + Workers KV écriture |
| `CF_D1_SCREENER_ID` | base `screener` |
| `CF_KV_ID` | espace KV du worker (W seulement) |
| `SEC_UA` | User-Agent déclaré à la SEC |
| `DISCORD_WEBHOOK` | canal de notification |

## Règle de travail

Toute modification arrive par une branche et une demande de fusion ; les tests
(F) tournent dessus, et `main` exige qu'ils soient verts (règle de protection
de branche). Les étapes planifiées lisent toujours `main`.

## Base

Schéma réel dans `sql/001_schema.sql`. Chaque script crée lui-même les colonnes
qu'il écrit. Budget d'écritures : `PLAFOND_ECRITURES` (100 000 lignes par jour
sur le plan gratuit de D1, index compris).
