# Hackbots Harness (Phase 2)

Local control plane for the hackbot agents. Stack: **Bun + `serve` + `bun:sqlite` + React**
(React is bundled by Bun at server startup — no separate build step).

## Run locally

```sh
cd ~/Projects/hackbots-starter/harness
bun install
bun run dev
# open http://127.0.0.1:13337
```

Environment (optional): `HOST`, `PORT` (default `13337`), `DATA_DIR`,
`INGEST_TOKEN`, `DISCORD_WEBHOOK_URL`, `DASHBOARD_TOKEN`.
Everything binds to `127.0.0.1` by default. The SQLite DB and auto-generated
ingest token live in `hackbots.db` under `DATA_DIR`.

Set `DASHBOARD_TOKEN` to require a token on all `/api/*` calls except health and
ingest. Then open `http://127.0.0.1:13337/?token=<secret>` once — the UI stores
it in localStorage and attaches it to later requests. `deploy/Caddyfile` also
has a commented HTTP Basic auth block for exposure beyond localhost.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | health check |
| GET | `/api/bootstrap` | targets + findings + runs + settings |
| POST | `/api/targets` | add target |
| DELETE | `/api/targets/:id` | delete target and cascade its findings |
| GET | `/api/findings?search=&severity=&status=&kind=&target_id=` | filtered findings |
| PATCH | `/api/findings/:id` | inline edit |
| POST | `/api/findings/:id/resolve` | mark resolved, fire Discord webhook |
| GET/PATCH | `/api/settings` | webhook, threshold, token, eval endpoint |
| POST | `/api/runs`, PATCH `/api/runs/:id` | track bot runs |
| POST | `/api/ingest` | **Bearer token** — workers report findings here |

`POST /api/ingest` body:
```json
{"title":"Exposed .git","severity":"high","kind":"exposure","detail":"...","source":"js-hunter","target_id":1,"run_id":"run_abc"}
```
Findings at or above `severity_threshold` auto-post to the Discord webhook on arrival.

## Wire Phase 1 into it

```sh
cd ~/Projects/hackbots-starter
INGEST_TOKEN=<copy from harness Settings> \
  python3 harness/scripts/ingest_from_report.py lab/sample-app/all_endpoints.txt \
  --target local-lab --severity low
```

Then in Maestro, tell `js-hunter` / `caido-fingerprinter` to write results to a text
file and run the bridge — or add `/api/ingest` as an MCP/HTTP tool later.

## Deploy template

`deploy/Caddyfile` (TLS reverse proxy) and `deploy/hackbots.service` (systemd).
These are templates for an authorized server; review paths, user, and token handling
before use. Do not expose the harness publicly without authentication in front of it.
