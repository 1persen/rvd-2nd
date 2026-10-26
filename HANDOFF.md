# Raja Voting Digital handoff

## Current release

The approved ivory/navy/gold UI and the existing hardened backend are combined in this repository. Tencent now follows GitHub `main` through the [automatic deployment runner](deploy/auto-deploy.md). `/srv/rvd/current` selects the last validated `git-<commit>` release; `.git-revision` records its commit. The manual `20261010-voting-status` release remains available for recovery.

The voting-status follow-up corrects public labels for events marked open outside their scheduled window. It changes presentation only; event schedules and voting eligibility rules are preserved. See [verification and recovery](deploy/voting-status-20261010.md).

The visual release did not change the previously deployed Python backend, schema, dependency lock or checkout client. Compared with the older GitHub `main`, this branch also records those earlier production improvements: payment/vote integrity, concurrency controls, SSE resource handling, shared caching, middleware hardening and the associated regression tests.

## Architecture and contracts

- Routers handle HTTP and template context; services contain business logic; repositories perform database access.
- PostgreSQL migrations are the schema authority. Migration head is `d2f6a8c1e4b7` (adds `events.template_key` and `events.theme_config` for per-event invite templates); its parent `f1a2b3c4d5e6` adds `events.category`, and `b7c8d9e0f1a2` adds `teams.previous_rank`, a unique vote-log payment index and payment lookup indexes. Before applying the earlier revision elsewhere, resolve any historical duplicate vote-log payment IDs.
- Public visibility is limited to `PUBLISHED`, `VOTING_OPEN`, `VOTING_CLOSED` and `FINISHED`. Drafts/archives and soft-deleted events return 404 on `/invite/{slug}`, the public API, checkout, and the SSE stream; admins read every status through `/api/v1/admin/events*`.
- Per-event branding is optional: `classic`, `poster` and `cinematic` templates plus a strict `theme_config`. Raster/SVG/WOFF2 assets upload to the Supabase public bucket `event-assets` when `SUPABASE_URL`, `SUPABASE_SECRET_KEY` and `SUPABASE_STORAGE_BUCKET` are set; with all three empty, uploads return 503 while theme-only saves keep working.
- Admin authentication uses `/api/v1/auth/admin/login` and a signed JWT bearer token. Provision production accounts separately; no account or database snapshot belongs in Git.
- Successful payments grant the purchased quantity once, with an immutable snapshot. Event/payment locks and atomic updates preserve concurrent totals and ranks.
- SSE uses Redis to coordinate workers, releases request database sessions before streaming and coalesces updates. Discovery HTML is cached for five seconds per worker.
- `public_base.html`, `admin_base.html` and scoped CSS provide the visual system. Native checkout dialogs, authenticated exports and escaped admin table rendering are retained.

## Operations

Use the existing systemd/Caddy deployment configuration and hash-locked Python dependencies. Build Tailwind before starting a release. Runtime environment and database state live outside release directories. See [deployment notes](deploy/README.md) and [visual release evidence](deploy/ivory-gold-20261010.md).

The previous release and a fresh PostgreSQL backup remain available. No database or credential changes accompanied the visual rollout. Source was initially deployed directly; the integration branch records that running state for review.

## Remaining limits

Production gateway activation is deferred. Mock mode is disabled, and unavailable checkout must remain closed until the Snap/QRIS response contract is completed and tested. Queue enforcement and CAPTCHA remain unfinished. Some settings are stored administrative notes rather than implemented runtime controls; the UI says so. Project progress does not invent completion percentages.

Scheduled/off-site backups and the previously observed large independent-connection burst failures remain operational follow-ups. This PR does not claim to resolve them. Local SQLite previews are visual fixtures, not evidence of PostgreSQL concurrency or real gateway behavior.
