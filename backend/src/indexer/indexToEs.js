import fs from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';
import Database from 'better-sqlite3';
import { Client } from '@elastic/elasticsearch';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const ES_URL = process.env.ELASTIC_URL || 'http://localhost:9200';
const INDEX = process.env.ES_INDEX || 'airports_v1';
const BATCH_SIZE = parseInt(process.env.BATCH_SIZE || '500', 10);
const RECREATE = process.argv.includes('--recreate');

async function ensureIndex(client, mappingPath) {
  const exists = await client.indices.exists({ index: INDEX });
  if (exists && RECREATE) {
    console.log('Deleting existing index', INDEX);
    await client.indices.delete({ index: INDEX });
  }
  const nowExists = await client.indices.exists({ index: INDEX });
  if (!nowExists) {
    const mapping = JSON.parse(await fs.readFile(mappingPath, 'utf8'));
    console.log('Creating index', INDEX);
    try {
      await client.indices.create({ index: INDEX, body: mapping });
    } catch (err) {
      console.warn('Index creation failed with original mapping, attempting sanitized fallback:', err.message || err);
      // Sanitize analyzers that may not be installed in this ES (kuromoji, ik_max_word, arabic)
      const sanitized = JSON.parse(JSON.stringify(mapping));
      const analyzers = sanitized.settings && sanitized.settings.analysis && sanitized.settings.analysis.analyzer ? sanitized.settings.analysis.analyzer : {};
      // Map unknown analyzers to en_analyzer
      const allowed = new Set(Object.keys(analyzers));
      function fixProp(object) {
        if (!object || typeof object !== 'object') return;
        for (const key of Object.keys(object)) {
          if (key === 'analyzer' && typeof object[key] === 'string') {
            if (!allowed.has(object[key])) object[key] = 'en_analyzer';
          } else {
            fixProp(object[key]);
          }
        }
      }
      fixProp(sanitized.mappings);
      console.log('Creating index with sanitized mapping');
      await client.indices.create({ index: INDEX, body: sanitized });
    }
  } else {
    console.log('Index exists, skipping create:', INDEX);
  }
}

function makeAirportDoc(row, cityMap) {
  const {
    airport_id, ident, iata_code, icao_code, type, name_official, name_norm,
    latitude, longitude, elevation_ft, iso_country, iso_region, municipality, has_scheduled_service, popularity_score
  } = row;

  const names = { en: name_official || name_norm || '' };
  const aliases = [];
  if (name_norm && name_norm !== name_official) aliases.push(name_norm);
  if (municipality) aliases.push(municipality);

  const doc = {
    entity_type: 'airport',
    airport_id,
    ident,
    iata_code,
    icao_code,
    names: { ...names, aliases },
    name_combined: [name_official, municipality].filter(Boolean).join(' '),
    names_all: (name_official || name_norm || '').toLowerCase(),
    region_name: municipality || iso_region,
    country_name: iso_country,
    location: (latitude != null && longitude != null) ? { lat: latitude, lon: longitude } : undefined,
    elevation_ft,
    popularity_score: (popularity_score != null) ? Number(popularity_score) : undefined,
    has_scheduled_service: !!(has_scheduled_service && String(has_scheduled_service).toLowerCase().startsWith('y')),
    city_id: row.city_id || null,
    associated_airports: row.city_id && cityMap[row.city_id] ? cityMap[row.city_id].airport_ids : []
  };
  return doc;
}

function makeCityDoc(cityRow, airportPopularityMap) {
  const { city_id, mac_code, name, iso_country, latitude, longitude, airport_ids, primary_airport_id, airport_count } = cityRow;
  const airportIds = Array.isArray(airport_ids) ? airport_ids : JSON.parse(airport_ids || '[]');
  // aggregate popularity as max of member airports
  let maxPop = null;
  for (const airportId of airportIds) {
    const popularity = airportPopularityMap[airportId];
    if (popularity != null) {
      if (maxPop == null || popularity > maxPop) maxPop = popularity;
    }
  }
  const doc = {
    entity_type: 'city',
    city_id,
    mac_code,
    name,
    country_name: iso_country,
    location: (latitude != null && longitude != null) ? { lat: latitude, lon: longitude } : undefined,
    associated_airports: airportIds,
    primary_airport_id,
    airport_count,
    popularity_score: maxPop != null ? Number(maxPop) : undefined,
    names_all: (name || '').toLowerCase(),
    name_combined: name
  };
  return doc;
}

async function bulkIndex(client, docs) {
  if (!docs.length) return;
  const body = [];
  for (const doc of docs) {
    const id = doc.entity_type + '_' + (doc.airport_id || doc.city_id);
    body.push({ index: { _index: INDEX, _id: id } });
    body.push(doc);
  }
  const response = await client.bulk({ refresh: false, body });
  if (response.body && response.body.errors) {
    const items = response.body.items || [];
    const errors = items.filter(item => item.index && item.index.error);
    console.error('Bulk index had errors:', errors.slice(0, 5));
    throw new Error('Bulk index errors');
  }
}

async function main() {
  const client = new Client({ node: ES_URL });
  try {
    await client.info();
  } catch (error) {
    console.error('Unable to connect to Elasticsearch at', ES_URL);
    console.error(error.message || error);
    process.exit(1);
  }

  const mappingPath = path.resolve(__dirname, '../../../infra/elasticsearch/mappings.json');
  await ensureIndex(client, mappingPath);

  // Open DB and read canonical rows
  const dbPath = path.resolve(__dirname, '../../../../data/database/airports.db');
  const dbDir = path.dirname(dbPath);
  try {
    await fs.mkdir(dbDir, { recursive: true });
  } catch (err) {
    // Directory already exists or can't be created
  }
  const db = new Database(dbPath, { readonly: true });

  const canonicalRows = db.prepare('SELECT airport_id, ident, iata_code, icao_code, type, name_official, name_norm, latitude, longitude, elevation_ft, iso_country, iso_region, municipality, has_scheduled_service, popularity_score, city_id FROM airports_canonical').all();
  console.log('Loaded canonical rows:', canonicalRows.length);

  // Build city map
  const cityRows = db.prepare('SELECT city_id, mac_code, name, iso_country, latitude, longitude, airport_ids, primary_airport_id, airport_count FROM airports_cities').all();
  console.log('Loaded city rows:', cityRows.length);
  const cityMap = {};
  for (const cityRow of cityRows) {
    try {
      cityRow.airport_ids = JSON.parse(cityRow.airport_ids || '[]');
    } catch (error) {
      cityRow.airport_ids = [];
    }
    cityMap[cityRow.city_id] = cityRow;
  }

  // Build airport popularity map
  const airportPopularityMap = {};
  for (const row of canonicalRows) {
    airportPopularityMap[row.airport_id] = row.popularity_score != null ? Number(row.popularity_score) : null;
  }

  // Index airports first
  let docs = [];
  let indexedAirportsCount = 0;
  for (const row of canonicalRows) {
    const doc = makeAirportDoc(row, cityMap);
    docs.push(doc);
    if (docs.length >= BATCH_SIZE) {
      await bulkIndex(client, docs);
      indexedAirportsCount += docs.length;
      docs = [];
    }
  }
  if (docs.length) {
    await bulkIndex(client, docs);
    indexedAirportsCount += docs.length;
  }
  console.log('Indexed airports total:', indexedAirportsCount);

  // Index cities
  docs = [];
  let indexedCitiesCount = 0;
  for (const cityRow of Object.values(cityMap)) {
    const doc = makeCityDoc(cityRow, airportPopularityMap);
    docs.push(doc);
    if (docs.length >= BATCH_SIZE) {
      await bulkIndex(client, docs);
      indexedCitiesCount += docs.length;
      docs = [];
    }
  }
  if (docs.length) {
    await bulkIndex(client, docs);
    indexedCitiesCount += docs.length;
  }
  console.log('Indexed cities total:', indexedCitiesCount);

  console.log('Bulk indexing complete');
  db.close();
}

main().catch(err => {
  console.error('Indexer error', err);
  process.exit(1);
});
