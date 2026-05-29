import express from 'express';
import { autocomplete } from '../services/searchClient.js';

const router = express.Router();

router.get('/', async (req, res): Promise<void> => {
  const q = String(req.query.q || '');
  if (!q) {
    res.status(400).json({ error: 'Missing q parameter' });
    return;
  }

  const size = parseInt(String(req.query.limit || '8'), 10) || 8;
  const type = String(req.query.type || 'both') as any;
  const lat = req.query.lat ? Number(req.query.lat) : undefined;
  const lon = req.query.lng ? Number(req.query.lng) : undefined;

  try {
    const hits = await autocomplete({ q, size, lat, lon, type });
    const results = hits.map((h: any) => ({ id: h.id, score: h.score, ...h.source }));
    res.json({ query: q, results });
  } catch (err: any) {
    console.error('Autocomplete error', err);
    res.status(500).json({ error: 'Search backend error' });
  }
});

export default router;
