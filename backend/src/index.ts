import express from 'express';

const app = express();
const PORT = process.env.PORT || 3000;

// Middleware
app.use(express.json());

// Health check
app.get('/health', (_req, res) => {
  res.json({ status: 'ok', timestamp: new Date().toISOString() });
});

// Placeholder: /autocomplete endpoint
app.get('/autocomplete', (req, res): void => {
  const { q, lang: _lang, lat: _lat, lng: _lng, limit: _limit = 8, type: _type = 'both' } = req.query;
  
  if (!q) {
    res.status(400).json({ error: 'Missing query parameter: q' });
    return;
  }

  // TODO: Implement autocomplete logic
  res.json({
    query: q,
    results: [],
    message: 'Autocomplete endpoint not yet implemented'
  });
});

// Error handling
app.use((_err: any, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
  res.status(500).json({ error: 'Internal server error' });
});

// Start server
app.listen(PORT, () => {
  console.log(`✓ Server running on http://localhost:${PORT}`);
  console.log(`✓ Health check: http://localhost:${PORT}/health`);
  console.log(`✓ Autocomplete: http://localhost:${PORT}/autocomplete?q=london`);
});

export default app;
