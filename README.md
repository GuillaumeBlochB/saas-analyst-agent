# SaaS Analyst Agent

Un agent IA qui répond à des questions de gestion sur une entreprise SaaS en enquêtant dans son API de facturation, accompagné d'une suite d'évaluations qui mesure si ses réponses sont justes.

Posez-lui *« Le churn a empiré cet été. Quels clients perdons-nous, et pourquoi ? »* : il interroge les indicateurs, les ventile, descend jusqu'aux enregistrements, puis rend un court rapport où chaque constat cite les appels d'API qui le justifient.

## Exemple d'exécution

Exécution réelle avec `claude-sonnet-5-5`, effort `medium`. L'agent rédige son rapport dans la langue de la question, ici l'anglais.

```
$ analyst ask "How dependent are we on our largest customers?"
   c1  list_customers({"sort":"mrr_desc","limit":25,"status":"active"})  -> 4494 chars
   c2  query_metric({"metric":"mrr","start":"2026-09","end":"2026-09","group_by":"segment"})  -> 254 chars
   c3  query_metric({"metric":"active_customers","start":"2026-09","end":"2026-09","group_by":"segment"})  -> 261 chars

# We depend heavily on one account. Halvorsen Freight is about 17% of MRR, and the top 10 customers are about 38% of the $351K total from only 420 customers.

## Findings
- Total MRR at 2026-09 is $351,468. Enterprise is $270,794 (77%) from 44 customers. Mid-market is $55,080 from 120 customers and SMB is $25,594 from 256 customers. [c2, c3]
- Halvorsen Freight (cus_0070, US, outbound) pays $60,000/month, about 17.1% of MRR. That is roughly 6.7 times the next largest account, Juniper Biotech at $9,000. [c1, c2]
- The top 10 active customers total about $134,500 MRR, about 38% of the total. All of them are enterprise. Accounts ranked 2 to 10 are each $7,500–$9,000, so beyond Halvorsen the concentration is moderate. [c1, c2]
- The top 25 accounts are all enterprise. Many are outbound-sourced and signed in 2025–2026, so the large-account base is young. [c1]

## Recommendations
- Treat Halvorsen Freight as a key account: assign an executive sponsor, review its contract term and renewal date, and track its usage and health.
- Model the revenue impact of losing Halvorsen, which would remove about 17% of MRR.
- Keep growing the mid-tier enterprise accounts at $5–9K and mid-market, so that no single account carries the business.

## Caveats
- The top-10 share is computed from the listed MRR values against the segment MRR total. Small rounding differences are possible.
- I did not look at contract terms, renewal dates, or Halvorsen's invoice and payment history, because the data doesn't include contract terms. I also did not track concentration over time.
- The segment MRR total may include small differences from per-customer MRR.

completed | 3 tool calls | 2 turns | 10441 in / 1449 out tokens | $0.032 | 11.02s | trace: runs\20261009T135954Z-5f40ddc9.jsonl
```

## Résultats des évaluations

Une exécution de la suite avec `claude-sonnet-5-5`, effort `medium` :

```
$ analyst eval
PASS  churn_spike             6 calls   $0.041    13.7s
PASS  payment_incident        8 calls   $0.047    17.1s
PASS  revenue_concentration   4 calls   $0.035    12.0s
PASS  channel_quality         3 calls   $0.055    17.4s
PASS  mrr_lookup              1 calls   $0.015     3.6s

5/5 passed, total cost $0.19
```

## Pourquoi ce projet

La plupart des démos d'agents montrent qu'un agent *sait* appeler des outils. Celle-ci est construite pour montrer s'il *arrive à la bonne conclusion*, et à quel coût :

- **Une vérité connue.** L'entreprise, Nimbus, est fictive. Ses données sont générées de façon déterministe, avec quatre anomalies plantées volontairement : chaque question d'évaluation a donc une réponse vérifiable.
- **Une boucle d'agent écrite à la main.** Une centaine de lignes sur l'API Messages de Claude, sans framework d'agents : chaque décision du harnais est lisible dans [agent.py](src/saas_analyst/agent.py).
- **Des citations imposées par le code.** L'agent doit rendre sa réponse via un outil `submit_report`. Un rapport qui cite un appel d'outil inexistant ou en échec est rejeté et renvoyé pour correction.
- **Des budgets que le modèle ne peut pas dépasser.** Un budget d'appels d'outils et une limite de tours sont appliqués par le code. Une fois le budget épuisé, l'agent est sommé de rendre ce qu'il a établi.
- **Des traces complètes.** Chaque exécution écrit un fichier JSONL avec tous les tours du modèle, les appels d'outils, leurs résultats, les tokens et le coût.

## Fonctionnement

```
question ──> boucle d'agent ──(appels d'outils)──> outils de facturation ──HTTP──> API mock
                 │                                                                   │
                 │<──────────── résultats, chacun marqué d'un call_id ───────────────┘
                 │
                 └──> submit_report ──> contrôle des citations ──> rapport + trace
```

| Partie | Fichier | Rôle |
|---|---|---|
| Données | [dataset.py](src/saas_analyst/dataset.py) | Deux ans de clients, d'abonnements et de factures, avec les anomalies plantées |
| API mock | [api.py](src/saas_analyst/api.py) | Service FastAPI en lecture seule : listes paginées, endpoint d'indicateurs, format d'erreur unique |
| Outils | [tools.py](src/saas_analyst/tools.py) | Table déclarative des outils, partagée par l'agent et le serveur MCP |
| Agent | [agent.py](src/saas_analyst/agent.py) | La boucle, le prompt système, les budgets |
| Rapport | [report.py](src/saas_analyst/report.py) | Schéma de `submit_report` et validation des citations |
| Évaluations | [evals/](src/saas_analyst/evals/) | Cas, correcteurs déterministes, lanceur |
| Serveur MCP | [mcp_server.py](src/saas_analyst/mcp_server.py) | Les mêmes outils pour n'importe quel client MCP |

## Démarrage rapide

Il faut Python 3.11+ et une [clé API Claude](https://platform.claude.com/).

```bash
python -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env             # puis renseigner ANTHROPIC_API_KEY

pytest                           # aucune clé API nécessaire
analyst ask "How dependent are we on our largest customers?"
analyst eval                     # lance les 5 cas d'évaluation contre le modèle
```

Par défaut, l'API mock tourne dans le même processus : une seule commande suffit. Pour la lancer comme un service séparé :

```bash
analyst serve --port 8000
analyst ask "..." --api-url http://127.0.0.1:8000
```

Options : `--model` (le `.env.example` fourni sélectionne `claude-sonnet-5-5` ; sans réglage, `claude-opus-5-5`), `--effort` (de `low` à `max`, `medium` par défaut), `--max-tool-calls` (25 par défaut).

## Les évaluations

`analyst eval` lance chaque cas contre le modèle et affiche le verdict, le nombre d'appels d'outils, le coût et la durée. Les résultats sont enregistrés dans `eval_results/` et la trace de chaque exécution dans `runs/`.

| Cas | Question | Anomalie plantée que l'agent doit trouver |
|---|---|---|
| `churn_spike` | Quels clients perdons-nous, et pourquoi ? | Les clients du plan starter résilient pour raison de prix en juin et juillet 2026 |
| `payment_incident` | Y a-t-il un vrai problème d'encaissement ? | Les factures allemandes échouent à l'authentification de la carte depuis août 2026 |
| `revenue_concentration` | À quel point dépendons-nous de nos plus gros clients ? | Un seul client pèse environ 17 % du MRR |
| `channel_quality` | Quel canal amène les clients qui restent le moins ? | Les clients venus du paid social partent vite dans leurs premiers mois |
| `mrr_lookup` | Quel était le MRR fin septembre 2026 ? | Aucune : simple lecture, à faire en 4 appels d'outils au plus |

La notation est déterministe : chaque cas vérifie que le rapport nomme les bonnes entités et, le cas échéant, contient un chiffre proche de la valeur calculée sur les données. Un cas échoue aussi si l'exécution dépasse sa limite d'appels d'outils ou se termine sans rapport. Les valeurs attendues sont dérivées des données et non écrites en dur : elles suivent la graine du générateur.

`--repeat 3` mesure la régularité, `--case churn_spike` lance un seul cas.

## Choix de conception

- **Agréger côté serveur, pas dans la fenêtre de contexte.** `query_metric` renvoie une série mensuelle avec ventilation optionnelle : l'agent localise un problème en deux ou trois appels au lieu de parcourir des milliers de factures. Les listes renvoient `total_count`, donc compter ne demande jamais de paginer.
- **Les taux portent leur dénominateur.** Chaque taux est accompagné de `n`, et le prompt demande à l'agent de le vérifier, pour éviter les conclusions tirées d'une poignée de clients.
- **Les erreurs sont des instructions.** Entrée invalide, identifiant inconnu ou résultat trop volumineux reviennent sous forme d'erreurs qui disent quoi changer. Un résultat trop volumineux est refusé, jamais tronqué : l'agent ne raisonne pas sur des données partielles.
- **Lecture seule par construction.** L'API n'expose que des routes `GET`. L'agent ne peut rien modifier.
- **Une boucle testée sans le modèle.** La boucle reçoit le client Claude en paramètre. [test_agent.py](tests/test_agent.py) la pilote avec un client scripté pour vérifier le rejet des citations, l'épuisement du budget et la relance en l'absence de rapport.

## Utiliser les outils depuis un client MCP

```bash
analyst mcp
```

démarre un serveur MCP (stdio) qui expose les cinq outils de facturation. Exemple de configuration pour un client MCP :

```json
{ "mcpServers": { "nimbus-billing": { "command": "analyst", "args": ["mcp"] } } }
```

## Limites

- Les correcteurs repèrent des entités et des chiffres dans le texte du rapport. Ils ne jugent ni la qualité du raisonnement ni celle des recommandations, et un motif trop large peut valider un rapport juste pour de mauvaises raisons.
- Il n'y a pas encore de contrôle négatif : un cas où la prémisse de la question est fausse et où l'agent doit le dire.
- Les appels d'outils demandés dans un même tour sont exécutés l'un après l'autre, pas en parallèle.
- L'entreprise est simulée : un abonnement par client, pas de changement de plan, des remboursements rares et sans cause.
