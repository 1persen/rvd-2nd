"""Deployment state transitions using temporary releases, never a real server."""
import io
import fcntl
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch, Mock

import auto_deploy as deployer


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.previous = self.root / 'releases/previous'
        self.previous.mkdir(parents=True)
        (self.previous / 'migrations').mkdir()
        (self.previous / 'alembic.ini').write_text('configuration')
        (self.root / 'current').symlink_to(self.previous)
        self.state = self.root / 'state'
        self.state.mkdir()
        self.backups = self.root / 'backups'
        self.backups.mkdir()
        for name, value in [('ROOT', self.root), ('STATE', self.state), ('BACKUPS', self.backups)]:
            mock = patch.object(deployer, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        self.sha = 'a' * 40
        self.release = self.root / 'releases' / ('git-' + self.sha)

    def extract(self, sha, release):
        (release / 'migrations').mkdir()
        (release / 'alembic.ini').write_text('configuration')

    def fake_command(self, args, **kwargs):
        if 'pg_dump' in args:
            Path(args[-1]).write_bytes(b'backup')

    def test_success_activates_exact_revision_and_repeat_is_noop(self):
        with patch.object(deployer, 'extract_release', side_effect=self.extract), \
             patch.object(deployer, 'build_release') as build, \
             patch.object(deployer, 'run', side_effect=self.fake_command), \
             patch.object(deployer, 'restart') as restart, \
             patch.object(deployer, 'check_health'):
            deployer.deploy(self.sha)
            self.assertEqual((self.root / 'current').resolve(), self.release)
            self.assertEqual((self.release / '.git-revision').read_text().strip(), self.sha)
            deployer.deploy(self.sha)
            build.assert_called_once()
            restart.assert_called_once()

    def test_failed_build_preserves_live_release_and_does_not_retry_same_commit(self):
        with patch.object(deployer, 'extract_release', side_effect=self.extract), \
             patch.object(deployer, 'build_release', side_effect=RuntimeError('tests failed')) as build, \
             patch.object(deployer, 'restart') as restart:
            with self.assertRaisesRegex(RuntimeError, 'tests failed'):
                deployer.deploy(self.sha)
            self.assertEqual((self.root / 'current').resolve(), self.previous)
            self.assertFalse(self.release.exists())
            with self.assertRaisesRegex(RuntimeError, 'Previously failed revision'):
                deployer.deploy(self.sha)
            build.assert_called_once()
            restart.assert_not_called()

    def test_failed_health_restores_previous_release(self):
        with patch.object(deployer, 'extract_release', side_effect=self.extract), \
             patch.object(deployer, 'build_release'), \
             patch.object(deployer, 'run', side_effect=self.fake_command), \
             patch.object(deployer, 'restart') as restart, \
             patch.object(deployer, 'check_health', side_effect=[RuntimeError('unhealthy'), None]):
            with self.assertRaisesRegex(RuntimeError, 'unhealthy'):
                deployer.deploy(self.sha)
            self.assertEqual((self.root / 'current').resolve(), self.previous)
            self.assertEqual(restart.call_count, 2)
            self.assertFalse(self.release.exists())

    def test_migration_change_stops_before_build_or_restart(self):
        (self.previous / 'migrations/old.py').write_text('old migration')
        with patch.object(deployer, 'extract_release', side_effect=self.extract), \
             patch.object(deployer, 'build_release') as build, \
             patch.object(deployer, 'restart') as restart:
            with self.assertRaisesRegex(RuntimeError, 'Migration changes'):
                deployer.deploy(self.sha)
            build.assert_not_called()
            restart.assert_not_called()
            self.assertEqual((self.root / 'current').resolve(), self.previous)

    def test_archive_rejects_symlinks(self):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w') as archive:
            link = tarfile.TarInfo('escape')
            link.type = tarfile.SYMTYPE
            link.linkname = '/etc'
            archive.addfile(link)
        with patch.object(deployer, 'run', return_value=Mock(stdout=data.getvalue())):
            with self.assertRaisesRegex(ValueError, 'links'):
                deployer.extract_release(self.sha, self.release)

    def test_backup_failure_never_switches_production(self):
        with patch.object(deployer, 'extract_release', side_effect=self.extract), \
             patch.object(deployer, 'build_release'), \
             patch.object(deployer, 'run', side_effect=RuntimeError('backup failed')), \
             patch.object(deployer, 'restart') as restart:
            with self.assertRaisesRegex(RuntimeError, 'backup failed'):
                deployer.deploy(self.sha)
            self.assertEqual((self.root / 'current').resolve(), self.previous)
            restart.assert_not_called()

    def test_active_lock_prevents_a_second_deployment(self):
        with (self.state / 'lock').open('w') as lock, patch.object(deployer, 'fetch_main') as fetch:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            deployer.main()
            fetch.assert_not_called()

    def test_pruning_preserves_current_previous_and_manual_releases(self):
        for index in range(8):
            path = self.root / 'releases' / f'git-{index}'
            path.mkdir()
            (path / '.git-revision').write_text(str(index))
        current = self.root / 'releases/git-0'
        deployer.activate(current)
        deployer.prune_releases(self.previous)
        self.assertTrue(current.exists())
        self.assertTrue(self.previous.exists())
        self.assertLess(len(list((self.root / 'releases').glob('git-*'))), 8)


if __name__ == '__main__':
    unittest.main()
