# Airport Autocomplete — Fly Fairly

Production-quality airport and city autocomplete service built with Node.js + Express + Elasticsearch.

## Project Structure

```
airport-autocomplete/
├── backend/
│   └── src/
│       ├── index.js           # Express app entry point
│       ├── routes/            # API route handlers
│       ├── services/          # Business logic (ES client, query builder)
│       └── indexer/           # Bulk indexing logic
├── data/
│   └── etl/                   # Python ETL pipeline scripts
├── infra/
│   ├── docker-compose.yml     # Local ES + Redis + App
│   └── elasticsearch/
│       └── mappings.json      # ES index settings & mappings
├── tests/
│   └── eval/
│       ├── harness.js         # Golden query evaluation
│       └── regressions.json   # Test cases
├── package.json               # Dependencies
└── Plan.md                    # Architecture & implementation plan
```

## Quick Start

### Prerequisites
- Node.js 18+
- Docker & Docker Compose (for Elasticsearch)
- npm or yarn

### Install Dependencies

```bash
npm install
```

### Run the Data Pipeline (ETL)
Before indexing data into Elasticsearch, we must process the raw data and build the local SQLite database. Run these four Python scripts sequentially from the project root folder:

##### Step A: Download latest OurAirports data and ingest into SQLite database
```bash
python3 data/etl/ingest_ourairports.py
```

##### Step B: Normalize text and remove duplicate entries
```bash
python3 data/etl/normalize_dedupe.py
```

##### Step C: Group nearby airports to discover Multi-Airport Cities (MACs)
```bash
python3 data/etl/detect_macs_emit_cities.py
```

##### Step D: Calculate smart relevancy ranking and popularity scores
```bash
python3 data/etl/compute_popularity.py
```

### Start Local Elasticsearch

```bash
docker-compose -f infra/docker-compose.yml up -d
```

Verify ES is running:
```bash
curl http://localhost:9200
```

### Run Development Server

```bash
npm run dev
```

Server will start on `http://localhost:3000` using Node.js `--watch` mode.

Health check: `http://localhost:3000/health`

### Index Data to Elasticsearch

Ensure local Elasticsearch is running, then run the bulk indexer:

```bash
node backend/src/indexer/indexToEs.js
```

This will:
1. Load data from the SQLite staging DB (`data/staging/airports.db`)
2. Format airport and city documents
3. Bulk index them into the Elasticsearch index `airports_v1`

### Run Evaluation Harness

```bash
npm run eval
```

Tests golden queries against the API and compares results to baseline.

## API Endpoints

### GET `/health`
Health check endpoint.

**Response:**
```json
{
  "status": "ok",
  "timestamp": "2026-05-28T..."
}
```

### GET `/autocomplete`
Main autocomplete endpoint.

**Query Parameters:**
- `q` (required): Search query (e.g., "london", "JFK", "東京")
- `lang` (optional): BCP47 language code (e.g., "en", "ja", "zh", "ar")
- `lat` (optional): Latitude for geo-distance boost
- `lng` (optional): Longitude for geo-distance boost
- `limit` (optional): Max results, default 8
- `type` (optional): Filter by type ("airport", "city", "both"), default "both"

**Response:**
```json
{
  "query": "london",
  "results": [
    {
      "id": "uuid...",
      "entity_type": "airport",
      "iata_code": "LHR",
      "icao_code": "EGLL",
      "name": "London Heathrow",
      "city": "London",
      "region": "England",
      "country": "United Kingdom",
      "location": { "lat": 51.47, "lng": -0.46 },
      "popularity_score": 9.2,
      "has_scheduled_service": true
    },
    ...
  ],
  "latency_ms": 45
}
```

## Configuration

Set environment variables in `.env`:

```
PORT=3000
ES_HOST=localhost
ES_PORT=9200
ES_INDEX=airports
LOG_LEVEL=debug
```

## Implementation Plan

See [Plan.md](./Plan.md) for detailed architecture, decisions, and step-by-step implementation guide.

### Timeline (3–5 hours condensed)
- **Hour 0–1:** Project scaffold, docker-compose, ES mapping ✓ (in progress)
- **Hour 1–2:** ETL: ingest OurAirports, normalize, compute popularity
- **Hour 2–3:** Index to ES, implement `/autocomplete` with exact-code short-circuit
- **Hour 3–4:** Build evaluation harness, test golden queries
- **Hour 4–5:** Write approach memo, prepare demo

## Troubleshooting

### ES Connection Fails
- Verify `docker-compose up -d` and `curl http://localhost:9200`
- Check ES logs: `docker logs <container-id>`
- Restart: `docker-compose down && docker-compose up -d`

### "Cannot find module" errors
- Run `npm install` again
- Delete `node_modules` and reinstall if issues persist

### Memory Issues (macOS)
- Reduce ES heap: Edit `docker-compose.yml` ES_JAVA_OPTS to `-Xms512m -Xmx512m`
- Use OpenSearch as lightweight alternative

## Key Decisions

1. **Search Engine:** Elasticsearch (self-hosted) with fallback support for OpenSearch
2. **Backend:** Node.js + Express (prototype) with JavaScript (ESM)
3. **Database:** SQLite for prototype, PostgreSQL+PostGIS for production
4. **Data Source:** OurAirports canonical + Wikidata enrichment
5. **Indexing:** Per-language `search_as_you_type` fields + `rank_feature` for popularity
6. **Ranking:** Exact-code short-circuit → multi-clause `function_score` → region dict for disambiguation

## Production Notes

For production:
- Replace SQLite with PostgreSQL + PostGIS
- Add Redis caching for hot queries
- Implement Prometheus metrics and monitoring
- Expand multilingual analyzers (currently using `names_all` fallback)
- Add geospatial decay and user personalization signals
- Consider hosted Elasticsearch (Elastic Cloud, Amazon OpenSearch Service)
