# Sales Navigator - presentation tier

Next.js 16 (App Router) + TypeScript + Tailwind CSS 4. Every page is a client component that talks to the FastAPI application tier through `src/lib/api.ts`.

```bash
npm install
npm run dev                       # http://localhost:3000
```

The API URL comes from `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`).

Other commands:

```bash
npx tsc --noEmit && npm run lint && npm run build
```

| Path | What lives there |
|---|---|
| `src/app/*/page.tsx` | One file per screen (see the route table in `docs/HANDOVER.md` §1.3) |
| `src/components/ui.tsx` | Shared UI primitives (Button, Card, Badge, Stat, ...) |
| `src/components/AppShell.tsx` | Authenticated layout: sidebar, mobile nav, quota meter |
| `src/lib/` | API client, session, types, formatting (₹, dates, labels) |
| `src/app/globals.css` | Design tokens (light + dark). Re-skin here |

Full documentation: [../docs/HANDOVER.md](../docs/HANDOVER.md).
