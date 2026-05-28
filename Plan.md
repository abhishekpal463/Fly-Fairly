# Airport Autocomplete Plan — Node.js + Express + Elasticsearch (Updated)

Date: 2026-05-28

Overview
--------
This document is the complete step-by-step plan for implementing a production-quality airport and city autocomplete service for Fly Fairly. It incorporates critical fixes for multi-airport cities (MACs), popularity-based ranking, simplified ETL lineage, modern Elasticsearch features (`search_as_you_type` and `rank_feature`), and per-language multifields to avoid analyzer collisions.

Goals
-----
- Provide sub-50ms autocomplete responses at high QPS for global users.
- Correctly handle IATA/ICAO codes, city names, regions/states, aliases, typos, diacritics, and multi-script queries (English, Chinese, Japanese, Arabic).
- Avoid runtime aggregations by modeling MACs as first-class documents.
- Prevent false positives like the "Florida" trap via precomputed popularity scoring.
- Keep the prototype ETL simple and deterministic (OurAirports canonical + Wikidata enrichment).

Decisions (summary)
-------------------
- Backend: Node.js + Express (prototype) with TypeScript.
- Search engine: Elasticsearch (self-hosted) or OpenSearch as an OSS alternative.
- Prototype DB: SQLite for local development; migrate to PostgreSQL + PostGIS for production.
- Canonical data source: OurAirports (airport rows, iso_region); Wikidata only for multilingual labels/enrichment.
- Autocomplete: `search_as_you_type` fields per language; `rank_feature` for popularity; `entity_type` to distinguish `airport` and `city` (MAC) documents.

High-level Step-by-step Implementation
-----------------------------------

1) Project bootstrap
	 - Create a repository scaffold (Node + TypeScript): `backend/` for API and `data/` for ETL artifacts.
	 - Install dependencies (example):

```bash
mkdir airport-autocomplete && cd airport-autocomplete
npm init -y
npm install express @elastic/elasticsearch better-sqlite3 axios ioredis zod
npm install -D typescript ts-node-dev jest supertest @types/express @types/jest
npx tsc --init
```

2) Local infra (docker-compose)
	 - Compose services for local prototyping: Elasticsearch (or OpenSearch), the Node app, and Redis (optional cache).
	 - **ISSUE FIX:** ES heap of 1GB may be insufficient on macOS; reduce to 512MB or use OpenSearch (OSS, lighter). kuromoji, ik, and arabic analyzers require plugins—either install in container or fall back to `names_all` field (ascii-folded text).
	 - Minimal `docker-compose.yml` snippet (conceptual):

```yaml
version: '3.7'
services:
	elasticsearch:
		image: docker.elastic.co/elasticsearch/elasticsearch:8.10.0
		environment:
			- discovery.type=single-node
			- xpack.security.enabled=false
			- "ES_JAVA_OPTS=-Xms1g -Xmx1g"
		ulimits:
			memlock:
				soft: -1
				hard: -1
		volumes:
			- esdata:/usr/share/elasticsearch/data
		ports:
			- "9200:9200"

	app:
		build: ./backend
		depends_on:
			- elasticsearch
		ports:
			- "3000:3000"

volumes:
	esdata:
```

3) Data acquisition (canonical ingestion)
	 - Source: OurAirports CSV (use as single canonical source for airports).
		 - Use `iso_region` (e.g., `US-FL`, `US-HI`, `CA-ON`) to map to clean region/state names during ETL.
	 - Enrichment: Query Wikidata by `iata` or `icao` to fetch multilingual labels (`label:ja`, `label:zh`, `label:ar`) and known `annual_passengers` when present.
	 - **ISSUE FIX:** Wikidata `annual_passengers` is sparse (~20% coverage). Document fallback strategy in step 6. For prototype, accept missing data and use fallbacks (population, scheduled_service flag).
	 - DO NOT ingest GeoNames or OSM in the prototype — they cause noisy deduplication.

4) ETL: cleansing, canonicalization, dedupe
	 - Steps:
		 a) Parse OurAirports CSV into a staging table (SQLite for prototype).
		 b) Normalize text fields: Unicode NFKC, trim, lower-case, store original form in `name_official`.
		 c) Deduplicate by priority: `icao` (unique) → `iata` → exact lat/lon match → geospatial nearest neighbor (<= 0.5 km) + name similarity threshold.
	 	 **ISSUE FIX:** 0.5 km threshold is conservative to avoid false merges. Maintain manual exceptions list for known reassignments. Log all deduped pairs for review.
		 d) Filter: remove `heliport`, `seaplane` and `closed` or `military-only` if flagged; keep `has_scheduled_service` candidates.\t \t **ISSUE FIX:** `has_scheduled_service` is updated quarterly in OurAirports. Use as hint but don't strictly enforce to avoid missing regional/charter airports.		 e) Map `iso_region` to `region_name` (state/province) for region queries like "Florida".

	 - Implementation notes:
		 - Use Python (`pandas`) or Node streaming for ETL; if Python, use `pandas` + `phonenumbers` + `pycountry` for codes.
		 - Save canonical rows to `airports` table with `airport_id` (UUID), `iata_code`, `icao_code`, `city_name`, `region_name`, `country_code`, `location` (lat/lon), `has_scheduled_service`.

5) MAC detection and emit `city` documents
	 - Group airports into metro clusters deterministically during ETL and emit a separate `city`/`metro` document for each cluster.
		 - Heuristics: same `municipality` OR same `iso_region` + within X km + known hub lists.
		 - **ISSUE FIX:** OurAirports lacks IATA city codes. For v1: (a) curate mapping for top metros, OR (b) infer from primary airport, OR (c) skip. Document choice in memo.
	 - City doc fields: `entity_type: city`, `city_iata` if applicable (LON, NYC, TYO), `names.{en,ja,zh,ar}`, `associated_airports` (array of IATA codes), `location` (centroid), `annual_passengers` (sum), `population` (if available), `popularity_score`.

6) Popularity / importance scoring
	 - Compute `popularity_score` at ETL time:
		 - If `annual_passengers` available: score = log1p(annual_passengers)
		 - Else fallback: score = log1p(city_population) * scheduled_service_flag (if available) or 1.0 (neutral)
		 - **ISSUE FIX:** Document all score sources in ETL logs. For missing data, default to 1.0 (neutral boost). Verify score distribution (median, P75) to sanity-check results.
		 - Normalize/log-scale and document the normalization function; store raw numeric to index as ES `rank_feature`.
		 - **ISSUE FIX:** `rank_feature` queries do NOT use `field_value_factor`. Use `rank_feature` function in `function_score` query (see step 9 example).

7) Elasticsearch mapping (recommended)
	 - Use `search_as_you_type` fields per language and a fallback `names.all` (ASCII-folded) field.
	 - Use `rank_feature` for `popularity_score` and `geo_point` for `location`.
	 - Example mapping (abbreviated):

```json
{
	"settings": {
		"analysis": {
			"analyzer": {
				"en_analyzer": { "type": "custom", "tokenizer": "standard", "filter": ["lowercase","asciifolding"] },
				"ascii_analyzer": { "type": "custom", "tokenizer": "standard", "filter": ["lowercase","asciifolding"] }
			}
		}
	},
	"mappings": {
		"properties": {
			"entity_type": { "type": "keyword" },
			"iata_code": { "type": "keyword" },
			"icao_code": { "type": "keyword" },
			"names": {
				"properties": {
					"en":{"type":"search_as_you_type","analyzer":"en_analyzer","max_shingle_size":3},
					"ja":{"type":"search_as_you_type","analyzer":"kuromoji","max_shingle_size":3},
					"zh":{"type":"search_as_you_type","analyzer":"ik_max_word","max_shingle_size":3},
					"ar":{"type":"search_as_you_type","analyzer":"arabic","max_shingle_size":3},
					"aliases":{"type":"search_as_you_type","analyzer":"ascii_analyzer","max_shingle_size":3}
				}
			},
			"name_combined": { "type": "search_as_you_type", "analyzer": "en_analyzer", "max_shingle_size": 3 },
			"names_all": { "type": "text", "analyzer": "ascii_analyzer" },
			"region_name": { "type": "search_as_you_type", "analyzer": "en_analyzer" },
			"country_name": { "type": "keyword" },
			"location": { "type": "geo_point" },
			"associated_airports": { "type": "keyword" },
			"annual_passengers": { "type": "long" },
			"population": { "type": "long" },
			"popularity_score": { "type": "rank_feature", "positive_score_impact": true },
			"has_scheduled_service": { "type": "boolean" }
		}
	}
}
```

Notes:
- Do not encode boosting in mappings via deprecated `boost` settings; do boosting at query-time using `function_score` and `rank_feature`.
- **ISSUE FIX:** For prototype with resource limits: install analyzer plugins via Docker, OR fall back to `names_all` for CJK/Arabic (reduced precision), OR use OpenSearch (bundles plugins).
- Ensure language analyzers (kuromoji, ik, arabic) are installed on the cluster or fall back to `names_all`.
- **INDEX SIZE:** Each `search_as_you_type` field generates 3 subfields (2-gram, 3-gram, prefix). If index > 2 GB, reduce to 2-gram or use `edge_ngram` instead.

8) Indexing pipeline
	 - Implement a bulk indexer that reads canonical rows and emits two document flavors into the same index:
		 - `entity_type: airport` documents for each airport
		 - `entity_type: city` documents for macros (MACs)
	 - Bulk index in batches (5k–10k docs) and verify `refresh`/`replicas` settings for local dev.

9) API: `GET /autocomplete`
	 - Query parameters: `q` (string), `lang` (optional BCP47), `lat`, `lng` (optional), `limit` (default 8), `type` (airport|city|both).
	 - Request preprocessing:
		 - Unicode NFKC normalize
		 - Trim punctuation
		 - Detect script (basic Unicode range) to choose language field: if CJK characters present, prefer `names.zh`/`names.ja`; if Arabic script present, prefer `names.ar`; else prefer `names.en` + `names_all`.
		 - **ISSUE FIX:** Script detection is heuristic (may fail on mixed queries e.g., "Tokyo 東京"). Always allow explicit `lang` override. Always include `names_all` fallback in query.
	 - Query recipe (pseudocode):
		 1. Short-circuit exact-code lookup: if `q` looks like `^[A-Z]{3}$` (IATA) or `^[A-Z]{4}$` (ICAO), run `term` on `iata_code` / `icao_code` and return immediate hit with highest priority.
		 \t **ISSUE FIX:** Normalize input (NFKC, uppercase). Also check synthesized city IATA codes (if implemented in step 5). Check `entity_type: city` first, then fallback to airports.
		 2. Fallback multi-clause `function_score` query:
				- `must/should` clauses: `match_phrase_prefix` on `names.<detected_lang>` (boosted), `match_phrase_prefix` on `name_combined`, `match` on `names_all` (lower boost), `multi_match` fuzziness AUTO on `names_all` (even lower boost).
				- Use `field_value_factor` / `rank_feature` (`popularity_score`) with `boost_mode: multiply`.
				- Apply geo-distance `decay` (if lat/lng provided) to boost nearby airports/cities.
				- Filter out `is_closed = true` or `has_scheduled_service = false` if asked.

	 - Example `function_score` JSON (simplified):

```json
{
	"query": {
		"function_score": {
			"query": {
				"bool": {
					"should": [
						{ "match_phrase_prefix": { "names.en": { "query": "london", "boost": 4 } } },
						{ "match_phrase_prefix": { "name_combined": { "query": "london", "boost": 2 } } },
						{ "multi_match": { "query": "london", "fields": ["names_all"], "fuzziness": "AUTO", "boost": 0.5 } }
					]
				}
			},
			"field_value_factor": { "field": "popularity_score", "factor": 1, "modifier": "log1p", "missing": 1 },
			"boost_mode": "multiply"
		}
	}
}
```

10) Florida trap mitigation (concrete)
		- At ETL: map `iso_region` to `region_name` and attach the `region_name` to child airports.
		- At query-time: if the query token matches a known region/state (simple dictionary lookup for common states/regions), add a high-boost `term` clause on `region_name.keyword` and filter candidate airports by `country` when helpful. This ensures `Florida` surfaces MIA/MCO/TPA before `La Florida` (Chile) which lacks high `popularity_score` and region match.

11) Tests & evaluation harness
		- Create a curated set of golden queries (include the real failure cases listed) and expected top-1/top-3 results.
		- Metrics: precision@1/3/5, MRR, recall@10, false positive rate (FP that are unrelated), latency P50/P95.
		- Implement `tests/eval/harness.js` that: (a) indexes a test subset, (b) fires golden queries against the API and the naive baseline (substring), and (c) computes metrics and output diffs.

12) Observability & monitoring
		- Collect metrics: query latency, QPS, cache hit ratio, top failing queries, index refresh times.
		- Instrument API with metrics exporter (Prometheus) and track user search→click/booking conversion for long-term ranking signals.

13) Deliverables & files to create/update
		- `backend/package.json`, `backend/src/index.ts` (Express app)
		- `backend/src/routes/autocomplete.ts`
		- `backend/src/services/searchClient.ts` (ES query builder)
		- `backend/src/indexer/indexToEs.ts` (bulk indexer that emits `airport` and `city` docs)
		- `data/etl/ingest_ourairports.py` (canonical ingestion + Wikidata enrichment)
		- `infra/docker-compose.yml` (Elasticsearch + app + redis)
		- `infra/elasticsearch/mappings.json` (exact index settings and mappings)
		- `tests/eval/harness.js` and `tests/regressions.json` (golden queries)

14) Timeline (prototype milestones)
		- Day 1–3: Project scaffold, local ES, ingest OurAirports, simple index of airports.
		- Day 4–6: Implement MAC grouping (city docs), compute `popularity_score`, and index both docs.
		- Day 7–9: Build `GET /autocomplete` endpoint, implement exact-code short-circuit and `function_score` query.
		- Day 10–12: Build evaluation harness, run golden tests, and tune analyzers/boosts.

15) Future improvements (non-blocking)
		- Replace SQLite with Postgres + PostGIS; enable richer geospatial clustering.
		- Add user personalization signals and query logs to tune ranking.
		- Add Redis caching and RedisSearch hot-path for microsecond responses for the top N queries.
		- Expand multilingual coverage and add transliteration tables for CJK ↔ Latin.

Next actions (pick one)
- A) I will generate the exact `infra/elasticsearch/mappings.json` file now.
- B) I will scaffold `data/etl/ingest_ourairports.py` as a proof-of-concept extractor + enrichment script.
- C) I will scaffold `backend/src/routes/autocomplete.ts` with the query builder.

---

End of plan (updated with fixes: MACs first-class, popularity rank_feature, OurAirports canonical, search_as_you_type, per-language multifields).

