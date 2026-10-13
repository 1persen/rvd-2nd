# Raja Voting Digital

General-event voting platform built with FastAPI, Jinja2, Alpine.js, PostgreSQL and Redis. The ivory/navy/gold public and organizer interfaces include the RV logo, event discovery, participant search, live rankings and a gold/silver/bronze top-three podium.

Production: https://rajavotedigital.my.id. This source combines the approved UI with the backend already running on Tencent. See [release verification](deploy/ivory-gold-20261010.md).

## Runtime

Use Python 3.12, PostgreSQL and the hash-locked dependencies in `requirements.lock`. SQLite is not a supported deployment or full-test target: migrations require PostgreSQL and scheduled-event comparisons require timezone-aware timestamps.

Copy `.env.example` to a private `.env`, set separate generated `SECRET_KEY` and `JWT_SECRET_KEY` values, and supply the database connection. Redis is required for shared cache, rate limiting and SSE across workers; `memory://` is only for a single-process local environment. Keep environment files, credentials and database files out of Git.

Install dependencies with `pip install --require-hashes -r requirements.lock`, apply `alembic upgrade head`, and start `uvicorn app.main:app`. Production uses the existing [systemd unit](deploy/rvd.service) and [Caddy configuration](deploy/Caddyfile).

Install the locked CSS build dependencies with `npm ci --prefix deploy/assets --ignore-scripts --no-audit --no-fund`, then build the Tailwind stylesheet with `sh scripts/build_css.sh` before starting production. It uses Tailwind 3.4.17; `app/static/css/tailwind.css` is generated and ignored by Git. The template selects the compiled stylesheet at startup and versions asset URLs. The CDN fallback is for development.

Tencent automatically polls [`1persen/rvd-2nd`](https://github.com/1persen/rvd-2nd) `main` every minute and deploys tested application revisions. See [automatic deployment and recovery](deploy/auto-deploy.md); schema migrations require a reviewed manual deployment.

Nothing is seeded automatically. Admin accounts are provisioned separately. Demo seeding is for disposable development databases only, never production; the sample event's fixed schedule must be adjusted before testing current voting windows.

## Routes

| Route | Behavior |
| --- | --- |
| `/` | Featured event, searchable discovery and live top-three preview |
| `/events` | Event catalog and open-voting filter |
| `/events?event_id=...` | Participant search, pagination, rankings and supporters |
| `/leaderboard?event_id=...` | Event-specific live leaderboard over SSE |
| `/events/{event_id}/vote/{team_id}` | Checkout, closed-voting or payment-unavailable state |
| `/admin/login` | Admin login using a JWT bearer token |
| `/admin` | Organizer dashboard, with navigation to events, transactions, audit logs, leaderboard and settings |
| `/health` | Application health response |

## Payment and vote integrity

Production gateway integration remains deferred. With no Midtrans key and `MIDTRANS_MOCK_MODE=false`, checkout shows an unavailable notice, invoice creation fails closed, unsigned webhooks are rejected and mock simulation is hidden. Adding real keys alone is insufficient: the Snap/QRIS response contract still requires integration and sandbox verification.

The backend grants `qty` votes, independently of the rupiah amount, only after successful payment. Payment snapshots preserve the purchased quantity. Row locks, atomic increments and a unique vote log per payment prevent duplicate or lost votes. Idempotent invoice replay verifies that the key belongs to the same request. Status expiry does not overwrite concurrent settlement.

Admin routes validate bearer tokens; rendered database text is escaped. Public and API requests have rate limits. SSE releases database sessions before streaming and uses the Redis backplane with coalesced ranking updates. Anonymous discovery HTML has a bounded five-second per-worker cache; checkout, admin and APIs remain fresh.

## Validation

- `node --test tests/*.test.cjs`: 18 checks passed in the integrated workspace.
- Server Python suite: 70 tests passed against the isolated PostgreSQL test database. The suite refuses database names that do not end in `test` and creates real fixtures; never run it against production.
- Production browser checks: public HTTPS pages, exact logo asset, admin login, seven admin pages at 320/390/1440 pixels, keyboard navigation and dialogs.
- Application/migration SHA-256 comparison: 126 files matched the running Tencent release.

See [design status](DESIGN_PREVIEW.md), [handoff](HANDOFF.md), [deployment notes](deploy/README.md), and [machine-readable evidence](docs/tencent-ivory-gold-verification.json). Real payment processing, sustained/distributed load, physical devices and every CRUD/export operation were not retested for this visual release.
