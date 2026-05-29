import { Client } from '@elastic/elasticsearch';
const ES_URL = process.env.ELASTIC_URL || process.env.ES_URL || 'http://localhost:9200';
const INDEX = process.env.ES_INDEX || 'airports_v1';
const client = new Client({ node: ES_URL });
export async function autocomplete(options) {
    const { q, size = 8, lat, lon, type = 'both' } = options;
    const qRaw = String(q || '').trim();
    const qUpper = qRaw.toUpperCase();
    const isCode = /^[A-Za-z0-9]{2,4}$/.test(qRaw);
    // Build SHOULD clauses
    const should = [];
    // Strong exact phrase match on combined name
    should.push({ match_phrase: { name_combined: { query: qRaw, boost: 8 } } });
    // search_as_you_type / prefix matching on names and aliases
    should.push({
        multi_match: {
            query: qRaw,
            type: 'bool_prefix',
            fields: ['names.en^3', 'names.aliases^2', 'name_combined^2']
        }
    });
    // Fuzzy fallback on the normalized names_all text field
    should.push({ match: { names_all: { query: qRaw, fuzziness: 'AUTO', operator: 'and', boost: 0.6 } } });
    // If query looks like a short code, prefer prefix matches on code fields
    if (isCode) {
        should.push({ prefix: { iata_code: { value: qUpper, boost: 12 } } });
        should.push({ prefix: { icao_code: { value: qUpper, boost: 10 } } });
    }
    // Filters
    const filter = [];
    if (type === 'airport')
        filter.push({ term: { entity_type: 'airport' } });
    if (type === 'city')
        filter.push({ term: { entity_type: 'city' } });
    const baseQuery = { bool: { should, minimum_should_match: 1 } };
    if (filter.length)
        baseQuery.bool.filter = filter;
    // Build function_score functions to boost desirable signals (no rank_feature scripting)
    const functions = [];
    if (isCode) {
        functions.push({ filter: { term: { iata_code: qUpper } }, weight: 20 });
        functions.push({ filter: { term: { icao_code: qUpper } }, weight: 18 });
    }
    // Boost airports with scheduled service and those with an IATA code
    functions.push({ filter: { term: { has_scheduled_service: true } }, weight: 1.25 });
    functions.push({ filter: { exists: { field: 'iata_code' } }, weight: 1.15 });
    // Geo proximity boost if provided
    if (lat != null && lon != null) {
        functions.push({ gauss: { location: { origin: `${lat},${lon}`, scale: '200km', decay: 0.5 } }, weight: 2 });
    }
    const body = {
        size,
        query: {
            function_score: {
                query: baseQuery,
                functions,
                score_mode: 'sum',
                boost_mode: 'multiply'
            }
        }
    };
    // Execute search
    const response = await client.search({ index: INDEX, body });
    const hits = (response && response.body && response.body.hits && response.body.hits.hits)
        || (response && response.hits && response.hits.hits)
        || [];
    return hits.map((hit) => ({ id: hit._id, score: hit._score, source: hit._source }));
}
export default client;
export async function searchByCode(code, size = 8) {
    const searchCode = String(code || '').trim().toUpperCase();
    if (!searchCode)
        return [];
    const body = {
        size,
        query: {
            bool: {
                should: [],
                minimum_should_match: 1
            }
        }
    };
    // prefer IATA (3) and ICAO (4) but try both
    body.query.bool.should.push({ term: { iata_code: { value: searchCode, boost: 6 } } });
    body.query.bool.should.push({ term: { icao_code: { value: searchCode, boost: 5 } } });
    const response = await client.search({ index: INDEX, body });
    const hits = (response && response.body && response.body.hits && response.body.hits.hits)
        || (response && response.hits && response.hits.hits)
        || [];
    return hits.map((hit) => ({ id: hit._id, score: hit._score, source: hit._source }));
}
//# sourceMappingURL=searchClient.js.map