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
    function formatHit(hit) {
      const source = hit.source || hit._source || {};
      const base = { id: hit.id, score: hit.score };
      if (source.entity_type === 'city' || source.name) {
        return {
          ...base,
          kind: 'city',
          city_id: source.city_id,
          name: source.name || source.name_combined || (source.names && source.names.en) || '',
          airport_count: source.airport_count ?? (Array.isArray(source.associated_airports) ? source.associated_airports.length : undefined),
          associated_airports: source.associated_airports || [],
          location: source.location,
          popularity_score: source.popularity_score
        };
      }
      return {
        ...base,
        kind: 'airport',
        airport_id: source.airport_id,
        ident: source.ident,
        iata_code: source.iata_code || null,
        icao_code: source.icao_code || null,
        name: (source.names && source.names.en) || source.name_combined || source.names_all || '',
        aliases: (source.names && source.names.aliases) || [],
        city_id: source.city_id,
        country: source.country_name,
        location: source.location,
        popularity_score: source.popularity_score
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
