# Develop the web client

`web/` is a React and TypeScript client using Tailwind and Zustand. Its built
output, `web/dist`, is committed so a matching checkout can serve the browser
without Node. Node is required to change or rebuild it.

## Run the development client

From the repository root, start the browser-capable service in one terminal:

```sh
./sigil serve
```

In another terminal, install the locked web dependencies and start Vite:

```sh
cd web
npm ci
npm run dev
```

The development page is normally at `http://localhost:5173`; Vite proxies `/api`
and `/events` to the server. Use `serve`, not API-only `server`, so `/api/login`
is available. Sign in with the password printed by the service. Keep the server
local. `npm ci` also runs the tracked hook installer.

## Validate and rebuild

From `web/`, use the project's configured checks:

```sh
npm test
npm run typecheck
npm run lint
npm run format:check
npm run build
```

`npm run build` runs TypeScript checking and Vite, rewriting `web/dist` and its
`build.json`. To apply formatting deliberately, use `npm run format` before the
final checks. Include the matching bundle with source changes.

From the repository root, check bundle freshness without Node:

```sh
uv run python -m src.server.web.webbuild
```

The bundle records a source hash. The server refuses browser startup if it is
missing or stale. Python's implementation in `src/server/web/webbuild.py` and
Node's `web/scripts/source-hash.mjs` must agree; the server web-dist contract
tests compare them. Python tests don't replace the web client's own checks.

To view a rebuilt production page, restart `./sigil serve` normally and open its
printed address rather than Vite's. See the
[browser guide](../../usage/guides/browser.md) for the user-facing distinction
between `server` and `serve`.

---

[Documentation index](../../README.md) · [Technical documentation](../README.md)
