import * as svc from '../src/services/searchClient.js';

(async () => {
  try {
    console.log('calling wrapper');
    const hits = await svc.autocomplete({ q: 'Vancouver', size: 5 });
    console.log('wrapper returned count:', hits.length);
    console.log(JSON.stringify(hits.slice(0, 5), null, 2));
  } catch (e) {
    console.error('wrapper error', e);
  }
})();
