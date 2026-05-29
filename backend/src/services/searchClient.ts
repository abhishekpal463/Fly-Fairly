import { Client } from '@elastic/elasticsearch';

const ES_URL = process.env.ELASTIC_URL || process.env.ES_URL || 'http://localhost:9200';
const INDEX = process.env.ES_INDEX || 'airports_v1';

const client = new Client({ node: ES_URL });

export type AutocompleteOptions = {
  q: string;
  size?: number;
  lat?: number;
  lon?: number;
  type?: 'airport' | 'city' | 'both';
};

export async function autocomplete(opts: AutocompleteOptions) {
  const { q, size = 8, lat, lon, type = 'both' } = opts;

  // Basic query: use multi_match on search_as_you_type fields and fallback to names_all
  const must: any[] = [];
  const should: any[] = [];

  should.push({
    multi_match: {
      query: q,
      type: 'bool_prefix',
      fields: [
        'names.en',
        'names.aliases',
        'name_combined',
        'names_all'
      ]
    }
  });

  // Filter by entity_type if requested
  const filter: any[] = [];
  if (type === 'airport') filter.push({ term: { entity_type: 'airport' } });
  if (type === 'city') filter.push({ term: { entity_type: 'city' } });

  // Simple bool query with SHOULD clauses; we avoid using `rank_feature` in functions here.
  const body: any = {
    size,
    query: {
      bool: {
        filter,
        must,
        should,
        minimum_should_match: 1
      }
    }
  };

  // Add explicit prefix matches for IATA/ICAO codes (keywords)
  const code = q.trim().toUpperCase();
  if (code.length > 0 && code.length <= 4) {
    body.query.bool.should.push({ prefix: { iata_code: { value: code, boost: 6 } } });
    body.query.bool.should.push({ prefix: { icao_code: { value: code, boost: 4 } } });
  }

  // Optional geo distance scoring
  if (lat != null && lon != null) {
    body.sort = [
      {
        _geo_distance: {
          location: { lat, lon },
          order: 'asc',
          unit: 'km'
        }
      }
    ];
  }

    // DEBUG: log the request body for troubleshooting
    console.debug('ES query body:', JSON.stringify(body));
    const response: any = await client.search({ index: INDEX, body });
    const hits = (response && response.body && response.body.hits && response.body.hits.hits)
      || (response && response.hits && response.hits.hits)
      || [];
    console.debug('ES hits count:', hits.length);
  return hits.map((h: any) => ({ id: h._id, score: h._score, source: h._source }));
}

export default client;
