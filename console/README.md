# Console

React 19, TypeScript, TanStack Query and Router, Tailwind 4 (tokens in `src/theme/tokens.css`), Motion.

- `npm run dev`: demo mode, reads `public/demo.json` (a recorded simulated morning, `make demo`).
- `VITE_API_URL=http://localhost:8000 VITE_API_TOKEN=demo-trader-token npm run dev`: live mode against the service.
- `npm test`, `npm run build`.

Feature folders: `features/desk` (inbox, email with evidence, lines and offers), `features/evaluation`,
`features/models`, `features/tools`; shared states in `shared/`.
