# ui-react

Vite + React + TypeScript + Tailwind + shadcn/ui SPA for RTIA. Mounted at `/`
by FastAPI in production; runs on Vite's dev server at `http://127.0.0.1:5173`
during development with API calls proxied to `http://127.0.0.1:8000`.

## Quick start

```bash
cd ui-react
npm install
npm run dev     # dev server with proxy to FastAPI
npm run build   # writes dist/ — served by FastAPI at /
```

Override the proxy target with `RTIA_API_ORIGIN=http://127.0.0.1:9000 npm run dev`.

## Adding shadcn/ui components

```bash
npx shadcn@latest add button
```

The CLI reads `components.json` and writes to `src/components/ui/`.

## Layout

```
ui-react/
├── index.html
├── package.json
├── vite.config.ts
├── tailwind.config.js
├── postcss.config.js
├── components.json          # shadcn/ui config
├── tsconfig*.json
└── src/
    ├── main.tsx             # React entrypoint
    ├── App.tsx              # Landing (US-16 hello-world)
    ├── index.css            # Tailwind layers + CSS vars
    └── lib/utils.ts         # cn() helper shadcn components expect
```
