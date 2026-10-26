# Raja Voting Digital

General-event voting platform built with FastAPI, Jinja2, Alpine.js, PostgreSQL and Redis. The ivory/navy/gold public and organizer interfaces include the RV logo, event discovery, participant search, live rankings and a gold/silver/bronze top-three podium.

Production: https://rajavotedigital.my.id. This source combines the approved UI with the backend already running on Tencent. See [release verification](deploy/ivory-gold-20261010.md).

## Runtime

Use Python 3.12, PostgreSQL and the hash-locked dependencies in `requirements.lock`. SQLite is not a supported deployment or full-test target: migrations require PostgreSQL and scheduled-event comparisons require timezone-aware timestamps.

Copy `.env.example` to a private `.env`, set separate generated `SECRET_KEY` and `JWT_SECRET_KEY` values, and supply the database connection. Redis is required for shared cache, rate limiting and SSE across workers; `memory://` is only for a single-process local environment. Keep environment files, credentials and database files out of Git.

Install dependencies with `pip install --require-hashes -r requirements.lock`, apply `alembic upgrade head`, and start `uvicorn app.main:app`. Production uses the existing [systemd unit](deploy/rvd.service) and [Caddy configuration](deploy/Caddyfile).

Install the locked CSS build dependencies with `npm ci --prefix deploy/assets --ignore-scripts --no-audit --no-fund`, then build the Tailwind stylesheet with `sh scripts/build_css.sh` before starting production. It uses Tailwind 3.4.17; `app/static/css/tailwind.css` is generated and ignored by Git. The template selects the compiled stylesheet at startup and versions asset URLs. The CDN fallback is for development.

Tencent automatically polls [`1persen/rvd-2nd`](https://github.com/1persen/rvd-2nd) `main` every minute and deploys tested application revisions. See [automatic deployment and recovery](deploy/auto-deploy.md); schema migrations require operator approval for the exact commit before the runner applies them.

Nothing is seeded automatically. Admin accounts are provisioned separately. Demo seeding is for disposable development databases only, never production; the sample event's fixed schedule must be adjusted before testing current voting windows.

## Routes

| Route | Behavior |
| --- | --- |
| `/` | Featured event, searchable discovery and live top-three preview |
| `/events` | Event catalog and open-voting filter |
| `/invite/{event_slug}` | Public event portal; renders the stored `classic`/`poster`/`cinematic` template |
| `/events?event_id=...` | Legacy deep link: 302 redirect to the event's `/invite/` URL |
| `/leaderboard/{event_slug}` | Event-specific live leaderboard over SSE |
| `/events/{event_id}/vote/{team_id}` | Checkout, closed-voting or payment-unavailable state |
| `/admin/login` | Admin login using a JWT bearer token |
| `/admin` | Organizer dashboard, with navigation to events, transactions, audit logs, leaderboard and settings |
| `/health` | Application health response |

## Event templates and asset uploads

Each event stores one invite template (`classic`, `poster`, `cinematic`) and a validated `theme_config` (colors, background mode, overlay, component visibility). Unknown template values fall back to `classic`; unsafe legacy theme JSON is normalized on read instead of raising.

Uploads are optional. To enable them, create a **public** Supabase Storage bucket (default name `event-assets`) and set `SUPABASE_URL`, `SUPABASE_SECRET_KEY` (`sb_secret_...`, sent only in the `apikey` header) and `SUPABASE_STORAGE_BUCKET` in the server environment. A partial configuration fails at startup. With all three empty, the app still runs and theme-only saves work; requests that carry files return 503.

The backend validates and re-encodes every file before upload (JPEG/PNG/WebP → WebP with EXIF stripped, sanitized SVG ornaments, validated WOFF2 fonts with a decoded-size cap), stores a fresh object per change, and deletes replaced objects only after the metadata commit. Save and admin preview accept one `multipart/form-data` request; preview never writes to Storage or PostgreSQL. `requirements.lock` is generated with `uv pip compile requirements.txt --python-version 3.12 --python-platform x86_64-manylinux_2_28 --generate-hashes`; regenerate it whenever the pinned dependencies change.

## Payment and vote integrity

New real invoices use Duitku hosted QRIS checkout. Without Duitku credentials and with `MIDTRANS_MOCK_MODE=false`, checkout shows an unavailable notice, invoice creation fails closed, unsigned callbacks are rejected and mock simulation is hidden. Legacy Midtrans callbacks remain for existing invoices. Follow the [Duitku configuration runbook](deploy/auto-deploy.md#duitku-configuration); production payment readiness requires credentials, an enabled QRIS channel and a verified real payment/callback round trip.

The backend grants `qty` votes, independently of the rupiah amount, only after successful payment. Payment snapshots preserve the purchased quantity. Row locks, atomic increments and a unique vote log per payment prevent duplicate or lost votes. Idempotent invoice replay verifies that the key belongs to the same request. Status expiry does not overwrite concurrent settlement.

Admin routes validate bearer tokens; rendered database text is escaped. Public and API requests have rate limits. SSE releases database sessions before streaming and uses the Redis backplane with coalesced ranking updates. Anonymous discovery HTML has a bounded five-second per-worker cache; checkout, admin and APIs remain fresh.

## Validation

- `node --test tests/*.test.cjs`: 30 checks passed in the integrated workspace.
- Server Python suite: 155 passed against the isolated PostgreSQL test database. `tests/test_event_presentation.py` covers templates, visibility guards, uploads with a mocked Storage transport, and preview; `tests/test_event_assets_failures.py` covers partial Storage configuration, Storage 4xx/network failures with compensation, corrupt and oversized images, and preview validation errors. One POSIX-only load-test check is skipped on Windows because the `resource` module is unavailable. The suite refuses database names that do not end in `test` and creates real fixtures; never run it against production.
- Production browser checks: public HTTPS pages, exact logo asset, admin login, seven admin pages at 320/390/1440 pixels, keyboard navigation and dialogs.
- Application/migration SHA-256 comparison: 126 files matched the running Tencent release.

See [design status](DESIGN_PREVIEW.md), [handoff](HANDOFF.md), [deployment notes](deploy/README.md), and [machine-readable evidence](docs/tencent-ivory-gold-verification.json). Real payment processing, sustained/distributed load, physical devices and every CRUD/export operation were not retested for this visual release.
