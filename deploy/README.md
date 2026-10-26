# Tencent deployment

Production runs at https://rajavotedigital.my.id. The current release combines the approved ivory/navy/gold design with the backend already deployed on Tencent. See [release verification and recovery](ivory-gold-20261010.md).

## Existing runtime

| Component | Configuration |
| --- | --- |
| Release | `/srv/rvd/current` → a validated `/srv/rvd/releases/git-<commit>` release |
| Service | `rvd.service`, dedicated `rvd` user, two uvicorn workers on loopback port 8000 |
| Python | 3.12, release-local `.venv`, dependencies from hash-verified `requirements.lock` |
| Database | PostgreSQL 16, loopback only, separate production and disposable test databases |
| Redis | Shared cache, rate limits and SSE backplane; separate test database |
| Environment | `/etc/rvd/production.env`, outside source control |
| Proxy | Caddy with HTTPS and unbuffered SSE; Cloudflare Full (strict) |
| Assets | Prebuilt Tailwind and five-minute static cache; versioned URLs after a rebuild/restart |

The checked-in [systemd unit](rvd.service) and [Caddy configuration](Caddyfile) describe the existing runtime. Database data, runtime configuration and credentials stay outside release directories. New installations must provision them separately; copying source does not create an admin account or populate the database.

For a release, validate the source and locked dependencies, build CSS, test against the isolated database, create a protected database backup, apply any required migrations, then switch the active release symlink and restart the service. Keep the previous release for rollback and verify health, public pages and protected admin access after switching. The event-presentation release added migration `d2f6a8c1e4b7`; the visual release before it required no new migration because production was already at `b7c8d9e0f1a2`.

## Event presentation: migration, storage and rollback

Migration `d2f6a8c1e4b7_add_event_presentation` is additive: it adds `events.template_key` (`VARCHAR(20) NOT NULL DEFAULT 'classic'`) and `events.theme_config` (`JSONB NOT NULL DEFAULT '{}'`). Existing rows are backfilled by the server defaults, so a short window where old code runs against the new schema is safe. Apply it through the existing exact-commit migration approval flow; do not run it by hand against production. Roll back application code before, not after, a destructive schema downgrade — leaving the two columns in place is the intended rollback.

Per-event branding assets (background, logo, poster, ornament, font) are optional. Without Storage the feature is disabled and the rest of the application is unaffected.

| Setting | Value |
| --- | --- |
| `SUPABASE_URL` | `https://<project>.supabase.co` (HTTPS only) |
| `SUPABASE_SECRET_KEY` | Server secret key `sb_secret_...`; never the publishable key or the database password |
| `SUPABASE_STORAGE_BUCKET` | `event-assets` |

Setup:

1. Create one **public** Storage bucket named `event-assets` (5 MB object limit; MIME `image/webp`, `image/svg+xml`, `font/woff2`). A public bucket is for branding only — never store private documents there.
2. Add the three variables to `/etc/rvd/production.env`. Leave all three empty to keep uploads disabled; a partial configuration fails at startup by design, so a typo cannot silently produce a half-working deployment.
3. No anon/authenticated INSERT/UPDATE/DELETE policies are needed: browsers never upload directly, and the backend secret key bypasses RLS. Public read is limited to objects whose URL is known.
4. Restart the service. `GET /health` must stay 200; uploads are reachable only from the authenticated event dialog.

The secret is sent only in the `apikey` header to the configured HTTPS origin, with redirects disabled. It must never appear in HTML, API responses, exception messages or logs; if one is exposed, rotate it in Supabase and update `/etc/rvd/production.env`.

Object keys are `events/{event_id}/{slot}-{uuid4}.{webp|svg|woff2}` and uploads use `x-upsert:false`, so a replaced asset always gets a new URL. A Save uploads new objects first, commits metadata, then deletes the replaced objects; a failed Save removes the objects it just uploaded. Soft-delete and archive never delete files. Cleanup failures are logged and do not roll back a successful Save, so a rare orphaned object can remain and should be removed from the Supabase dashboard.

Limits enforced server-side (the browser inputs are only a first pass): background/poster/logo JPEG, PNG or WebP up to 5 MiB and re-encoded to WebP; ornament SVG up to 512 KiB after sanitizing; font WOFF2 up to 1 MiB with a 40 MiB decoded-size cap. Total Save/preview body is capped at 17 MiB.

CSS: run `npm ci --prefix deploy/assets --ignore-scripts --no-audit --no-fund` and `sh scripts/build_css.sh` before switching the release. `app/static/css/tailwind.css` is generated and git-ignored; `invite.css` is versioned by mtime at startup, so a restart is required after changing either file.

Post-deploy verification (use test data, never production payments):

- `/health` returns 200 and an existing classic event still renders at `/invite/<slug>`.
- One event per template renders on a disposable test event; the draft returns 404 to anonymous visitors and opens in the admin preview.
- A real upload to the **test** bucket appears after Save and is removed when replaced.
- Archive or soft-delete a test event and confirm the invite page, public API and leaderboard stream stop serving it.

To regenerate the lock file after dependency changes: `uv pip compile requirements.txt --python-version 3.12 --python-platform x86_64-manylinux_2_28 --generate-hashes --output-file requirements.lock`.


## Verification and limits

The current release passed 70 Python checks on the isolated server database and production browser checks across desktop and mobile widths. The integrated workspace also passes 18 JavaScript tests. Details and file-hash evidence are in [the release report](../docs/tencent-ivory-gold-verification.json).

Production mock payments remain disabled. New invoices use Duitku; see the [current payment configuration runbook](auto-deploy.md#duitku-configuration). Without gateway credentials, checkout stays unavailable. Sandbox must be visibly labeled and tested on a dedicated test event; never run demo seeding or load tests against production.

The previous same-host restore drill succeeded. The fresh visual-release backup has a validated archive listing but was not independently restored. Scheduled/off-site backups are still outstanding. Earlier high-volume independent-connection tests exposed unresolved transport limits; this visual release does not claim a new throughput SLA or repeat sustained/distributed load testing.

## Vercel (Hobby) migration
- Entry point: api/index.py
- Config: vercel.json (maxDuration 10s)
- DB: Supabase pooler port 6543; DB_POOL_SIZE=1, DB_MAX_OVERFLOW=0
- Redis: Upstash rediss://
- Leaderboard polling 10s (no SSE)
- Migrations manual: alembic upgrade head
