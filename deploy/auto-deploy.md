# Automatic Tencent deployment

`rvd-auto-deploy.timer` checks the public [`1persen/rvd-2nd`](https://github.com/1persen/rvd-2nd) repository's `main` branch every 60 seconds after the preceding check finishes. A merge normally reaches production within a few minutes, including build and validation time. This uses outbound HTTPS from Tencent, with no GitHub token, inbound webhook, SSH credential in GitHub, or Actions permission requirement. It follows `main`, so any direct push to that branch is also a deployment trigger; use reviewed pull requests.

## Release gates

GitHub Actions runs the `RVD checks` job for every pull request targeting `main` and every push to `main`. It validates the locked Python/Node dependencies, CSS build, migrations against an empty disposable PostgreSQL database, JavaScript tests, deployment tests, application tests, and Chromium layout/navigation at 320/390/820/1440 px. Browser dependencies live separately in `tests/browser` and are not installed on production. No production credentials are available to CI.

The repository owner must enable a branch protection rule for `main`: require a pull request, require `RVD checks` from GitHub Actions, require the branch to be up to date, enforce the rule for administrators, and disallow force pushes and deletion. For a solo-maintained repository, a required review count of zero still requires a PR and passing checks without making self-authored changes impossible to merge. The workflow alone does not block merging; confirm the protection rule in GitHub Settings after the first successful run. GitHub's merge check and the server's release validation are separate gates; a green PR is not proof that production has updated.

The root-owned runner fetches an exact commit, rejects archives containing links, and creates `/srv/rvd/releases/git-<commit>`. Candidate dependencies, asset builds and tests run as `rvd-build`, which cannot read `/etc/rvd/production.env`. The runner loads only `/etc/rvd/loadtest.env` for application tests. That environment points to disposable PostgreSQL and Redis databases.

Every release installs Python dependencies using hash-verified `requirements.lock`, verifies dependency compatibility, installs CSS dependencies with `npm ci --ignore-scripts`, builds Tailwind, and runs JavaScript, deployment and application tests. It then makes release files root-owned, creates and validates a protected PostgreSQL backup, switches `/srv/rvd/current` atomically and restarts `rvd.service`. Health/public-page checks and anonymous admin API rejection must pass; otherwise the runner restores the previous release and checks it again. There is a brief service restart, not a zero-downtime rollout.

Changes to `alembic.ini` or migration Python files require operator approval for the exact commit. The runner upgrades the isolated test database before application tests. For an approved release it validates a production backup, runs the migration as `rvd` with bounded lock/statement timeouts, and only then activates the release. Approval is removed after success. Database restores are never automatic. Runtime secrets, gateway activation and operating-system configuration are not taken from Git. Changes to the root-owned deployment runner or systemd units also require an operator to rerun the installer from a reviewed checkout.

Five recent automatic releases plus any protected current/previous release are retained, together with ten `rvd-auto-*.dump` backups. Existing named manual releases and backups are not pruned. These backups are on the same host and do not replace off-site backups.

## Setup on the existing host

The source repository moved from `1stind/rvd` to `1persen/rvd-2nd`. The installed root-owned runner must be updated from this reviewed source to complete the switch; changing a local Git remote or merging this file alone does not update that installed copy. Existing application data, credentials, domain, backups and retained releases stay on Tencent.

From the reviewed checkout, run `sudo bash deploy/install_auto_deploy.sh`. It installs the runner and units, provisions the unprivileged build account, preserves startup compatibility for the current manual release, and enables the timer. It assumes the existing PostgreSQL/Redis setup, production/test environment files, Python 3.12 venv support, Node/npm, Git, systemd and legacy `/srv/rvd/venv` already exist. It does not provision credentials or a database.

Inspect the timer with `sudo systemctl status rvd-auto-deploy.timer`. Inspect deployment logs with `sudo journalctl -u rvd-auto-deploy.service -n 100 --no-pager`. The successful deployed commit is stored in `/srv/rvd/current/.git-revision`; verify it against GitHub `main`. A lock prevents concurrent deployments. The same successful revision does not trigger another build/restart.

## Reviewed schema releases

The runner intentionally blocks unknown schema changes even when GitHub CI passes. Repository ownership does not change this policy. An operator must review migrations for compatibility with the currently running release, test an upgrade from its schema, and ensure that rollback of application code can safely leave the new schema in place. Destructive or incompatible migrations require a separate maintenance plan; do not approve them through this path.

For an additive migration release, stop `rvd-auto-deploy.timer` and wait for the deployment service to become inactive. Under `/var/lib/rvd-deploy/lock`, put the reviewed full 40-character main commit into root-only `/var/lib/rvd-deploy/approved-migration`. Remove `last-attempt` only if it records that same revision and the failure has been investigated. Start the deployment service once, inspect the journal, verify `.git-revision` and public health, then resume the timer. Approval for one commit does not authorize a subsequent commit. Never approve a revision before completing its review and checks.

The installed root-owned runner must first be updated from the reviewed version containing this approval support. A merge does not overwrite the installed runner. Partial/nontransactional DDL may remain after a failed migration or application rollback; inspect the database before retrying. The runner never restores a database automatically.

## Duitku configuration

New real invoices use Duitku; legacy Midtrans callbacks remain available for existing invoices. Set `DUITKU_ENVIRONMENT=production`, the merchant's enabled QRIS code in `DUITKU_PAYMENT_METHOD` (`SP`, `NQ`, or `SQ`), and the production `DUITKU_MERCHANT_CODE` / `DUITKU_API_KEY` in `/etc/rvd/production.env`. Keep `MIDTRANS_MOCK_MODE=false`. Without merchant credentials checkout returns 503 and Duitku callbacks return 403; no fabricated invoice or vote is created. Local mock payments are incompatible with Duitku production mode or credentials.

The checkout opens Duitku's HTTPS hosted payment page. Callbacks must match the merchant, signature, invoice amount, reference and gateway before granting votes, and retries are idempotent. Protocol source: [Duitku API documentation](https://docs.duitku.com/api/en/). Gateway availability, enabled merchant channels, credentials and a real payment/callback round trip must be verified separately before announcing payment readiness.

Duitku checkout requires a valid payer email (maximum 50 characters, matching the provider's documented validation limit). The form and invoice API reject missing or malformed emails before inquiry. Provider timeouts return HTTP 503 with a retryable availability message, without exposing provider responses or credentials. Inspect the payment ID and configured method in the warning log; an enabled channel can still be unavailable upstream. Check the provider transaction status before diagnosing a timed-out inquiry as definitively absent, and do not extend the request timeout beyond the database transaction timeout to mask an upstream outage.

## Failed releases and recovery

A failed revision is recorded in `/var/lib/rvd-deploy/last-attempt` and is not retried every minute. Subsequent polls continue to exit unsuccessfully for that revision, keeping the service failure visible without rebuilding it. Fix it through a new commit. For a transient failure, inspect the journal, stop the timer, remove that one marker, and start the service once before re-enabling the timer. Do not remove it without understanding the failure.

Stop `rvd-auto-deploy.timer` and let any active deployment finish before a manual rollback. Repoint `current` atomically to a verified retained release, restart `rvd`, and verify health. Keep the timer stopped while investigating; otherwise a subsequent main commit can replace the manually selected release. The runner does not update event schedules, seed data, change payment configuration, or run production payment tests.

## Verification

Deployment unit tests cover successful activation, repeat no-op, build and backup failure, health-check rollback, migration blocking, concurrent locking, archive links, and retention. They use temporary directories and mocked system/database commands; they do not deliberately break the live service. Application and browser checks are separate evidence. Physical hardware failures, a full backup restore, and real payment processing are not part of this automation check.

### Sandbox checkout

Keep merchant credentials in the server environment file; never commit them. Set `DUITKU_ENVIRONMENT=sandbox`, `MIDTRANS_MOCK_MODE=false`, and choose an enabled QRIS method from the merchant payment-method API. `NQ` is Nobu QRIS; availability depends on the merchant account.

The public site displays a sandbox notice before checkout, and invoice responses carry `is_sandbox` based on the saved, validated checkout URL. This label survives environment changes. Sandbox uses the real Duitku inquiry/signature path, not the application's local mock simulator. Use a dedicated test event so test votes do not affect a real competition.

Duitku's [official testing guide](https://docs.duitku.com/api/en/#testing) documents channel-specific test clients. An accepted inquiry proves invoice creation; it does not prove settlement or callback delivery. Channels without a documented simulator require provider support for settlement testing. Switch to production only with production credentials and a verified real payment/callback round trip.
