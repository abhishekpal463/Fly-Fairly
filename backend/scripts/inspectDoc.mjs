import client from '../src/services/searchClient.js';

async function run() {
  try {
    const res = await client.search({ index: 'airports_v1', body: { query: { match_all: {} }, size: 1 } });
    console.log('response keys:', Object.keys(res || {}));
    console.log(JSON.stringify(res.body, null, 2));
  } catch (err) {
    console.error('ERR', err);
    process.exit(1);
  }
}

run();
