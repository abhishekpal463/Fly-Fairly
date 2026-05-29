import { autocomplete, searchByCode } from '../dist/services/searchClient.js';

async function run(q) {
  const size = 5;
  const type = 'both';
  const lat = undefined;
  const lon = undefined;
  try {
    const codeCandidate = q.trim();
    let hits = [];
    if (/^[A-Za-z0-9]{2,4}$/.test(codeCandidate)) {
      hits = await searchByCode(codeCandidate, size);
    }
    if (!hits || hits.length === 0) {
      hits = await autocomplete({ q, size, lat, lon, type });
    }
    function formatHit(h) {
      const src = h.source || h._source || {};
      const base = { id: h.id, score: h.score };
      if (src.entity_type === 'city' || src.name) {
        return {
          ...base,
          kind: 'city',
          city_id: src.city_id ?? src.city_id,
          name: src.name || src.name_combined || (src.names && src.names.en) || '',
          airport_count: src.airport_count ?? (Array.isArray(src.associated_airports) ? src.associated_airports.length : undefined),
          associated_airports: src.associated_airports || [],
          location: src.location,
          popularity_score: src.popularity_score
        };
      }
      return {
        ...base,
        kind: 'airport',
        airport_id: src.airport_id,
        ident: src.ident,
        iata_code: src.iata_code || null,
        icao_code: src.icao_code || null,
        name: (src.names && src.names.en) || src.name_combined || src.names_all || '',
        aliases: (src.names && src.names.aliases) || [],
        city_id: src.city_id,
        country: src.country_name,
        location: src.location,
        popularity_score: src.popularity_score
      };
    }
    const results = hits.map(formatHit);
    console.log('RESULTS', JSON.stringify(results.slice(0,5), null, 2));
  } catch (e) {
    console.error('SIM ERROR', e);
    process.exit(1);
  }
}

(async ()=>{
  await run('LON');
  await run('London');
  await run('Vancouver');
})();
