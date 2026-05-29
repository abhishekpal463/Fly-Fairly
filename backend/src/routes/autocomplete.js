import express from 'express';
import { autocomplete, searchByCode } from '../services/searchClient.js';

const router = express.Router();

router.get('/', async (req, res) => {
  const q = String(req.query.q || '');
  if (!q) {
    res.status(400).json({ error: 'Missing q parameter' });
    return;
  }

  const size = parseInt(String(req.query.limit || '8'), 10) || 8;
  const type = String(req.query.type || 'both');
  const lat = req.query.lat ? Number(req.query.lat) : undefined;
  const lon = (req.query.lon || req.query.lng) ? Number(req.query.lon || req.query.lng) : undefined;

  try {
    // Short-circuit exact IATA/ICAO codes (2-4 alnum chars)
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
    res.json({ query: q, results });
  } catch (err) {
    console.error('Autocomplete error', err);
    res.status(500).json({ error: 'Search backend error' });
  }
});

export default router;
