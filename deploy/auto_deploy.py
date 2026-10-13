#!/usr/bin/env python3
"""Pull public main, validate an isolated release, then atomically activate it."""
import fcntl
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tarfile
import time
import urllib.error
import urllib.request

ROOT = Path('/srv/rvd')
STATE = Path('/var/lib/rvd-deploy')
BACKUPS = Path('/var/backups/rvd')
REPOSITORY = 'https://github.com/1persen/rvd-2nd.git'


def log(message, **fields):
    print(json.dumps({'service': 'rvd-auto-deploy', 'message': message, **fields}), flush=True)


def run(args, *, cwd=None, timeout=60, capture=False):
    return subprocess.run(args, cwd=cwd, check=True, timeout=timeout,
                          stdout=subprocess.PIPE if capture else None)


def fetch_main():
    repo = STATE / 'repository.git'
    if not repo.exists():
        run(['git', 'init', '--bare', str(repo)])
    run(['git', '-C', str(repo), 'fetch', '--depth=1', REPOSITORY, 'refs/heads/main'])
    sha = run(['git', '-C', str(repo), 'rev-parse', 'FETCH_HEAD'], capture=True).stdout.decode().strip()
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('Invalid main revision')
    return sha


def extract_release(sha, release):
    data = run(['git', '-C', str(STATE / 'repository.git'), 'archive', sha], capture=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive.getmembers():
            if not (member.isfile() or member.isdir()):
                raise ValueError('Release archives must not contain links or special files')
            if Path(member.name).parts[0] in ('.venv', '.git-revision'):
                raise ValueError('Release archive contains reserved deployment files')
        archive.extractall(release, filter='data')


def schema_files(release):
    paths = [release / 'alembic.ini', *(release / 'migrations').rglob('*.py')]
    return {str(p.relative_to(release)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def build_release(release):
    run(['chown', '-R', 'rvd-build:rvd-build', str(release)])
    user = ['runuser', '-u', 'rvd-build', '--']
    python = str(release / '.venv/bin/python')
    run(user + ['python3', '-m', 'venv', str(release / '.venv')])
    run(user + [python, '-m', 'pip', 'install', '--require-hashes', '-r', 'requirements.lock'],
        cwd=release, timeout=240)
    run(user + [python, '-m', 'pip', 'check'], cwd=release)
    run(user + ['npm', 'ci', '--prefix', 'deploy/assets', '--ignore-scripts', '--no-audit', '--no-fund'],
        cwd=release, timeout=180)
    run(user + ['sh', 'scripts/build_css.sh'], cwd=release)
    run(user + ['node', '--test', *map(str, sorted((release / 'tests').glob('*.test.cjs')))],
        cwd=release, timeout=180)
    run(user + [python, '-m', 'unittest', 'discover', '-s', 'deploy', '-p', 'test_auto_deploy.py'],
        cwd=release)
    # Only the isolated test environment reaches candidate code, under an unprivileged user.
    run(['bash', '-c', 'set -ea; source /etc/rvd/loadtest.env; set +a; '
         'exec runuser -u rvd-build -- "$@"', '--', python, '-m', 'pytest', 'tests', '-q'],
        cwd=release, timeout=180)
    run(['chown', '-R', 'root:rvd', str(release)])
    run(['chmod', '-R', 'a+rX,go-w', str(release)])


def activate(release):
    pending = ROOT / 'current-next'
    pending.unlink(missing_ok=True)
    pending.symlink_to(release)
    pending.replace(ROOT / 'current')


def restart():
    run(['systemctl', 'restart', 'rvd'], timeout=45)


def check_health():
    for _ in range(20):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3) as response:
                if response.status == 200:
                    break
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    else:
        raise RuntimeError('Application health check failed')
    for path in ('/', '/events', '/leaderboard', '/admin/login'):
        with urllib.request.urlopen('http://127.0.0.1:8000' + path, timeout=10) as response:
            if response.status != 200:
                raise RuntimeError('Public route health check failed')
    try:
        urllib.request.urlopen('http://127.0.0.1:8000/api/v1/admin/dashboard', timeout=10).close()
    except urllib.error.HTTPError as error:
        if error.code == 401:
            return
        raise
    raise RuntimeError('Anonymous admin request was not rejected')


def prune_releases(previous):
    releases = sorted((p for p in (ROOT / 'releases').glob('git-*')
                       if p.is_dir() and not p.is_symlink()), key=lambda p: p.stat().st_mtime, reverse=True)
    protected = {previous, (ROOT / 'current').resolve(), *releases[:5]}
    for path in releases:
        if path not in protected and (path / '.git-revision').exists():
            shutil.rmtree(path)
    backups = sorted(BACKUPS.glob('rvd-auto-*.dump'), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in backups[10:]:
        path.unlink()


def deploy(sha):
    previous = (ROOT / 'current').resolve(strict=True)
    revision = previous / '.git-revision'
    if revision.exists() and revision.read_text().strip() == sha:
        return
    attempted = STATE / 'last-attempt'
    if attempted.exists() and attempted.read_text().strip() == sha:
        raise RuntimeError(f'Previously failed revision {sha}; submit a fix or inspect the journal before retrying')
    attempted.write_text(sha + '\n')
    release = ROOT / 'releases' / ('git-' + sha)
    switched = False
    try:
        release.mkdir()
        extract_release(sha, release)
        if schema_files(previous) != schema_files(release):
            raise RuntimeError('Migration changes require a reviewed manual deployment')
        log('Validating candidate', revision=sha)
        build_release(release)
        backup = BACKUPS / ('rvd-auto-' + sha + '.dump')
        run(['runuser', '-u', 'postgres', '--', 'pg_dump', '-Fc', '-d', 'rvd', '-f', str(backup)], timeout=120)
        backup.chmod(0o600)
        run(['runuser', '-u', 'postgres', '--', 'pg_restore', '--list', str(backup)], capture=True)
        (release / '.git-revision').write_text(sha + '\n')
        switched = True
        activate(release)
        restart()
        check_health()
    except BaseException:
        if switched:
            log('Rolling back', revision=sha, previous=str(previous))
            activate(previous)
            restart()
            check_health()
        if release.exists() and (ROOT / 'current').resolve() != release:
            shutil.rmtree(release)
        raise
    log('Deployment healthy', revision=sha, release=str(release))
    prune_releases(previous)


def main():
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STATE / 'lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        deploy(fetch_main())


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise InterruptedError('Deployment interrupted')

    signal.signal(signal.SIGTERM, interrupted)
    try:
        main()
    except Exception as error:
        log('Deployment failed; inspect journal before retry', error=str(error))
        raise SystemExit(1)
