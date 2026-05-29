import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const cases = JSON.parse(fs.readFileSync(path.join(__dirname, 'regressions.json'), 'utf8'));
const baseUrl = process.env.AUTOCOMPLETE_URL || 'http://localhost:3000/autocomplete';

function formatResult(result) {
  if (!result) return '∅';
  const iata = result.iata_code || result.ident || '';
  const name = result.name || result.city || 'unknown';
  return `${name} (${iata || 'no-iata'})`;
}

function naiveScore(query, result) {
  const haystacks = [
    result.name || '',
    result.city || '',
    result.ident || '',
    result.iata_code || '',
    result.icao_code || '',
    ...(Array.isArray(result.aliases) ? result.aliases : [])
  ].map((value) => String(value || '').toLowerCase());

  const needle = String(query || '').toLowerCase();
  let score = 0;

  if (haystacks.some((value) => value === needle)) score += 100;
  if (haystacks.some((value) => value.includes(needle))) score += 40;
  if (haystacks.some((value) => value.startsWith(needle))) score += 20;
  if (haystacks.some((value) => value.includes(needle.slice(0, 3)))) score += 5;

  return score;
}

function naiveRank(query, results) {
  return [...results]
    .map((result) => ({ result, score: naiveScore(query, result) }))
    .sort((a, b) => b.score - a.score || (a.result.score || 0) - (b.result.score || 0));
}

function containsExpected(value, expected) {
  if (!expected) return true;
  const text = String(value || '').toLowerCase();
  return text.includes(String(expected).toLowerCase());
}

async function fetchResults(query) {
  const url = new URL(baseUrl);
  url.searchParams.set('q', query);
  url.searchParams.set('limit', '5');

  const response = await fetch(url.toString(), { headers: { accept: 'application/json' } });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${await response.text()}`);
  }

  const payload = await response.json();
  return payload;
}

async function main() {
  console.log('Airport autocomplete eval harness');
  console.log(`Base URL: ${baseUrl}`);
  console.log('');

  let passed = 0;
  let total = 0;
  let reciprocalRankSum = 0;

  for (const testCase of cases) {
    total += 1;
    const payload = await fetchResults(testCase.query);
    const results = Array.isArray(payload.results) ? payload.results : [];
    const liveTop = results[0] || null;
    const naive = naiveRank(testCase.query, results);
    const naiveTop = naive[0]?.result || null;

    const expectedOk = containsExpected(liveTop?.name || '', testCase.mustContain)
      || containsExpected(liveTop?.iata_code || '', testCase.mustContain)
      || containsExpected(liveTop?.ident || '', testCase.mustContain);

    const hitRank = results.findIndex((result) =>
      containsExpected(result.name || '', testCase.mustContain)
      || containsExpected(result.iata_code || '', testCase.mustContain)
      || containsExpected(result.ident || '', testCase.mustContain)
    );
    const reciprocalRank = hitRank >= 0 ? 1 / (hitRank + 1) : 0;
    reciprocalRankSum += reciprocalRank;

    if (expectedOk) passed += 1;

    console.log(`- ${testCase.query}`);
    console.log(`  ${testCase.description}`);
    console.log(`  live top-1: ${formatResult(liveTop)}`);
    console.log(`  naive top-1: ${formatResult(naiveTop)}`);
    console.log(`  rr@1: ${reciprocalRank.toFixed(2)} | pass: ${expectedOk ? 'yes' : 'no'}`);
    console.log('');
  }

  console.log('Summary');
  console.log(`  cases: ${total}`);
  console.log(`  passed: ${passed}`);
  console.log(`  mrr: ${(reciprocalRankSum / total).toFixed(3)}`);
  console.log('');
  console.log('Tip: start the app with `npm run dev` or `npm start` before running this harness.');
}

main().catch((error) => {
  console.error('Eval harness failed:', error);
  process.exitCode = 1;
});
