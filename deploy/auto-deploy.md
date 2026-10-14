# Automatic Tencent deployment

`rvd-auto-deploy.timer` checks the public [`1persen/rvd-2nd`](https://github.com/1persen/rvd-2nd) repository's `main` branch every 60 seconds after the preceding check finishes. A merge normally reaches production within a few minutes, including build and validation time. This uses outbound HTTPS from Tencent, with no GitHub token, inbound webhook, SSH credential in GitHub, or Actions permission requirement. It follows `main`, so any direct push to that branch is also a deployment trigger; use reviewed pull requests.

## Release gates

GitHub Actions runs the `RVD checks` job for every pull request targeting `main` and every push to `main`. It validates the locked Python/Node dependencies, CSS build, migrations against an empty disposable PostgreSQL database, JavaScript tests, deployment tests, application tests, and Chromium navigation at 390/1440 px. Browser dependencies live separately in `tests/browser` and are not installed on production. No production credentials are available to CI.

The repository owner must enable a branch protection rule for `main`: require a pull request, require `RVD checks` from GitHub Actions, require the branch to be up to date, enforce the rule for administrators, and disallow force pushes and deletion. For a solo-maintained repository, a required review count of zero still requires a PR and passing checks without making self-authored changes impossible to merge. The workflow alone does not block merging; confirm the protection rule in GitHub Settings after the first successful run. GitHub's merge check and the server's release validation are separate gates; a green PR is not proof that production has updated.

The root-owned runner fetches an exact commit, rejects archives containing links, and creates `/srv/rvd/releases/git-<commit>`. Candidate dependencies, asset builds and tests run as `rvd-build`, which cannot read `/etc/rvd/production.env`. The runner loads only `/etc/rvd/loadtest.env` for application tests. That environment points to disposable PostgreSQL and Redis databases.

Every release installs Python dependencies using hash-verified `requirements.lock`, verifies dependency compatibility, installs CSS dependencies with `npm ci --ignore-scripts`, builds Tailwind, and runs JavaScript, deployment and application tests. It then makes release files root-owned, creates and validates a protected PostgreSQL backup, switches `/srv/rvd/current` atomically and restarts `rvd.service`. Health/public-page checks and anonymous admin API rejection must pass; otherwise the runner restores the previous release and checks it again. There is a brief service restart, not a zero-downtime rollout.

Changes to `alembic.ini` or migration Python files stop deployment before activation. Apply reviewed schema changes manually, validate both databases, and deploy the matching release before resuming automatic updates. Database restores are never automatic. Runtime secrets, gateway activation and operating-system configuration are not taken from Git. Changes to the root-owned deployment runner or systemd units also require an operator to rerun the installer from a reviewed checkout.

Five recent automatic releases plus any protected current/previous release are retained, together with ten `rvd-auto-*.dump` backups. Existing named manual releases and backups are not pruned. These backups are on the same host and do not replace off-site backups.

## Setup on the existing host

The source repository moved from `1stind/rvd` to `1persen/rvd-2nd`. The installed root-owned runner must be updated from this reviewed source to complete the switch; changing a local Git remote or merging this file alone does not update that installed copy. Existing application data, credentials, domain, backups and retained releases stay on Tencent.

From the reviewed checkout, run `sudo bash deploy/install_auto_deploy.sh`. It installs the runner and units, provisions the unprivileged build account, preserves startup compatibility for the current manual release, and enables the timer. It assumes the existing PostgreSQL/Redis setup, production/test environment files, Python 3.12 venv support, Node/npm, Git, systemd and legacy `/srv/rvd/venv` already exist. It does not provision credentials or a database.

Inspect the timer with `sudo systemctl status rvd-auto-deploy.timer`. Inspect deployment logs with `sudo journalctl -u rvd-auto-deploy.service -n 100 --no-pager`. The successful deployed commit is stored in `/srv/rvd/current/.git-revision`; verify it against GitHub `main`. A lock prevents concurrent deployments. The same successful revision does not trigger another build/restart.

## Failed releases and recovery

A failed revision is recorded in `/var/lib/rvd-deploy/last-attempt` and is not retried every minute. Subsequent polls continue to exit unsuccessfully for that revision, keeping the service failure visible without rebuilding it. Fix it through a new commit. For a transient failure, inspect the journal, stop the timer, remove that one marker, and start the service once before re-enabling the timer. Do not remove it without understanding the failure.

Stop `rvd-auto-deploy.timer` and let any active deployment finish before a manual rollback. Repoint `current` atomically to a verified retained release, restart `rvd`, and verify health. Keep the timer stopped while investigating; otherwise a subsequent main commit can replace the manually selected release. The runner does not update event schedules, seed data, change payment configuration, or run production payment tests.

## Verification

Deployment unit tests cover successful activation, repeat no-op, build and backup failure, health-check rollback, migration blocking, concurrent locking, archive links, and retention. They use temporary directories and mocked system/database commands; they do not deliberately break the live service. Application and browser checks are separate evidence. Physical hardware failures, a full backup restore, and real payment processing are not part of this automation check.
