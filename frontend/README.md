# Probability Watch Frontend

Next.js dashboard for the Event Intelligence Platform.

## Development

```bash
npm install
npm run dev
```

The development server runs at `http://localhost:3000`. API calls go through
`/api/*` and are proxied to FastAPI by `next.config.ts`; set `API_ORIGIN` when
the backend is not on `http://localhost:8000`.

## Production Export

```bash
npm run build
```

The build exports static files to `frontend/out`. FastAPI serves that directory
from the site root (`/`), while JSON APIs remain under `/api`.

## Quality Checks

```bash
npm run lint
npm run build
```
