"""Safety tests: all paths, processes and command results are temporary or fake."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import clean


class CleanerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.home = self.base / 'home'
        self.layout = clean.Layout(self.home, self.base / 'tmp', self.base / 'approved.json', self.base / 'proc')
        self.calls = []
        self.layout.proc.mkdir()
        self.layout.temp.mkdir()
        self.cache = self.layout.roots['go-build']
        self.file = self.make(self.cache / 'ab' / ('ab' + '1' * 62 + '-d'), b'disposable Go cache data')
        self.processes = clean.Processes()

    def make(self, path, content=b''):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def runner(self, args, **kwargs):
        self.calls.append(args)
        if args[:3] == ['go', 'env', 'GOCACHE']:
            return subprocess.CompletedProcess(args, 0, str(self.cache) + '\n', '')
        if args[0] == 'jp-gate':
            return subprocess.CompletedProcess(args, 0, 'idle\n', '')
        if args[0] in ('lsof', 'fuser'):
            return subprocess.CompletedProcess(args, 1, '', '')
        if args[0] == 'python3':
            return subprocess.CompletedProcess(args, 0, 'recorded cleaner\n', '')
        raise AssertionError(f'unexpected external command: {args}')

    def reader(self, _):
        return self.processes

    def approve(self):
        self.layout.approvals.write_text(json.dumps({'version': 1, 'approved': {'go-build': 1}}))

    def apply(self, runner=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return clean.apply(self.layout, runner or self.runner, self.reader)

    def test_default_scan_is_read_only(self):
        before = sorted(str(p) for p in self.base.rglob('*'))
        categories, _ = clean.scan(self.layout, self.runner, self.reader)
        self.assertEqual(sorted(str(p) for p in self.base.rglob('*')), before)
        self.assertEqual(len(categories[2].candidates), 1)
        self.assertFalse(self.layout.approvals.exists())
        self.assertFalse(any(c[0] in ('jp-gate', 'python3', 'lsof', 'fuser') for c in self.calls))

    def test_unapproved_apply_cannot_delete(self):
        self.assertEqual(self.apply(), 0)
        self.assertTrue(self.file.exists())
        self.assertEqual(self.calls[-1][0], 'python3')
        self.assertIn('deleted=0,refused=0,freed_bytes=0', self.calls[-1])

    def test_approved_apply_deletes_only_recognized_cache_file(self):
        keep = self.make(self.cache / 'README', b'keep me')
        self.approve()
        size = self.file.stat().st_blocks * 512
        self.assertEqual(self.apply(), 0)
        self.assertFalse(self.file.exists())
        self.assertTrue(keep.exists())
        self.assertIn(f'deleted=1,refused=0,freed_bytes={size}', self.calls[-1])
        self.assertEqual(sum(call[0] == 'jp-gate' for call in self.calls), 2)
        self.assertTrue(any(call[0] == 'lsof' for call in self.calls))
        self.assertTrue(any(call[0] == 'fuser' for call in self.calls))

    def test_gate_rejects_busy_empty_extra_text_failure_and_missing(self):
        self.approve()
        for code, out, err in ((0, 'busy', ''), (0, '', ''), (0, 'idle\nextra', ''),
                               (1, 'idle', ''), (0, 'idle', 'warning')):
            def gate(args, **kw):
                return subprocess.CompletedProcess(args, code, out, err) if args[0] == 'jp-gate' else self.runner(args, **kw)
            with self.subTest(out=out, code=code, err=err), self.assertRaises(clean.Refusal):
                self.apply(gate)
            self.assertTrue(self.file.exists())
            self.assertIn('--outcome', self.calls[-1])
            self.assertIn('failed', self.calls[-1])
        def missing(args, **kw):
            if args[0] == 'jp-gate':
                raise FileNotFoundError('missing gate')
            return self.runner(args, **kw)
        with self.assertRaises(FileNotFoundError):
            self.apply(missing)
        self.assertTrue(self.file.exists())

    def test_gate_becomes_busy_before_unlink(self):
        self.approve()
        gates = 0
        def changing(args, **kw):
            nonlocal gates
            if args[0] == 'jp-gate':
                gates += 1
                if gates > 1:
                    return subprocess.CompletedProcess(args, 0, 'busy', '')
            return self.runner(args, **kw)
        self.assertEqual(self.apply(changing), 1)
        self.assertTrue(self.file.exists())

    def test_open_handles_and_tool_errors_refuse(self):
        self.approve()
        for tool, code, out, err in (('lsof', 0, 'p123', ''), ('fuser', 0, '', ''),
                                     ('lsof', 1, '', 'permission denied'), ('fuser', 2, '', 'error')):
            def held(args, **kw):
                if args[0] == tool:
                    return subprocess.CompletedProcess(args, code, out, err)
                return self.runner(args, **kw)
            with self.subTest(tool=tool, code=code, err=err):
                self.assertEqual(self.apply(held), 1)
                self.assertTrue(self.file.exists())

    def test_incomplete_process_visibility_refuses(self):
        self.approve()
        self.processes.uncertain = 1
        self.assertEqual(self.apply(), 1)
        self.assertTrue(self.file.exists())

    def test_process_fd_map_or_command_reference_refuses(self):
        self.approve()
        for paths, commands in (({self.file}, []), (set(), [f'worker {self.file}']),
                                 (set(), ['go build ./...'])):
            self.processes.paths = paths
            self.processes.commands = commands
            self.assertEqual(self.apply(), 1)
            self.assertTrue(self.file.exists())

    def test_wrong_gocache_refuses(self):
        self.approve()
        def wrong(args, **kw):
            if args[0] == 'go':
                return subprocess.CompletedProcess(args, 0, str(self.base / 'elsewhere'), '')
            return self.runner(args, **kw)
        self.assertEqual(self.apply(wrong), 1)
        self.assertTrue(self.file.exists())

    def test_files_changed_during_checks_are_refused(self):
        self.approve()
        def changing(args, **kw):
            if args[0] == 'lsof':
                self.file.write_bytes(b'new useful data')
            return self.runner(args, **kw)
        self.assertEqual(self.apply(changing), 1)
        self.assertEqual(self.file.read_bytes(), b'new useful data')

    def test_git_repo_worktree_and_bare_repo_refused(self):
        for mode in ('repo', 'worktree', 'bare'):
            root = self.cache / 'ab'
            marker = root / '.git'
            if mode == 'repo':
                marker.mkdir()
            elif mode == 'worktree':
                marker.write_text('gitdir: somewhere')
            else:
                (root / 'HEAD').touch()
                (root / 'objects').mkdir()
                (root / 'refs').mkdir()
            with self.subTest(mode=mode), self.assertRaises(clean.Refusal):
                clean.guard(self.file, 'go-build', self.layout)
            if mode == 'repo':
                marker.rmdir()
            elif mode == 'worktree':
                marker.unlink()

    def test_all_hard_protections_even_when_category_root_matches(self):
        roots = [self.home / '.cache/pip', self.home / '.cache/uv',
                 self.layout.local / 'tools/python', self.layout.local / 'tools/pip-cache',
                 self.home / '.local/share/job-pilot', self.home / '.treehouse',
                 self.layout.firstmate / 'private', self.home / '.cache/ms-playwright',
                 self.layout.local / 'models', self.home / 'Qwen3',
                 self.home / '.venv', self.home / 'site-packages',
                 self.home / 'virtualenvs', self.home / 'pip', self.home / 'uv',
                 self.home / 'personal']
        for root in roots:
            target = self.make(root / 'ab' / self.file.name, b'never delete')
            original_roots = self.layout.roots
            # Force the rule to match a forbidden location: policy must still win.
            class Matching(clean.Layout):
                @property
                def roots(inner):
                    return {**original_roots, 'go-build': root}
            matched = Matching(self.home, self.layout.temp, self.layout.approvals, self.layout.proc)
            with self.subTest(root=root), self.assertRaises(clean.Refusal):
                clean.guard(target, 'go-build', matched)
            self.assertTrue(target.exists())

    def test_python_files_and_environment_markers_refused(self):
        for name in ('x.py', 'x.pyc', 'x.whl', 'requirements.txt', 'uv.lock',
                     'pyproject.toml', 'python3', '__pycache__'):
            target = self.make(self.cache / 'ab' / name)
            with self.subTest(name=name), self.assertRaises(clean.Refusal):
                clean.guard(target, 'go-build', self.layout)
        self.make(self.cache / 'pyvenv.cfg')
        with self.assertRaises(clean.Refusal):
            clean.guard(self.file, 'go-build', self.layout)

    def test_symlink_hardlink_and_directory_refused(self):
        alias = self.cache / 'ab' / ('ab' + '2' * 62 + '-d')
        alias.symlink_to(self.file)
        with self.assertRaises(clean.Refusal):
            clean.guard(alias, 'go-build', self.layout)
        alias.unlink()
        os.link(self.file, alias)
        with self.assertRaises(clean.Refusal):
            clean.guard(self.file, 'go-build', self.layout)
        alias.unlink()
        alias.mkdir()
        with self.assertRaises(clean.Refusal):
            clean.guard(alias, 'go-build', self.layout)
        alias.rmdir()
        directory = self.cache / 'ab'
        directory.rename(self.cache / 'elsewhere')
        directory.symlink_to(self.cache / 'elsewhere', target_is_directory=True)
        with self.assertRaises(clean.Refusal):
            clean.guard(self.file, 'go-build', self.layout)

    def test_wrong_owner_refused(self):
        real = Path.lstat
        def foreign(path):
            info = real(path)
            if path == self.file:
                values = list(info)
                values[4] = os.getuid() + 1
                return os.stat_result(values)
            return info
        with patch.object(Path, 'lstat', foreign), self.assertRaises(clean.Refusal):
            clean.guard(self.file, 'go-build', self.layout)

    def test_dropped_categories_can_never_be_approved(self):
        for name in set(clean.CATEGORIES) - clean.ENABLED:
            with self.subTest(name=name), self.assertRaises(clean.Refusal):
                clean.approve(self.layout, name, self.runner)
        self.assertFalse(self.layout.approvals.exists())

    def test_approve_persists_and_repeated_approval_is_noop(self):
        with patch.object(clean.Processes, 'read', self.reader), contextlib.redirect_stdout(io.StringIO()):
            clean.approve(self.layout, 'go-build', self.runner)
            before = self.layout.approvals.read_bytes()
            clean.approve(self.layout, 'go-build', self.runner)
        self.assertIn('go-build', clean.approvals_read(self.layout))
        self.assertEqual(self.layout.approvals.read_bytes(), before)
        self.assertEqual(self.layout.approvals.stat().st_mode & 0o777, 0o600)

    def test_malformed_unknown_and_linked_approval_refused(self):
        for data in ('{', '{}', '{"version":1,"approved":{"tmp":1}}',
                     '{"version":1,"approved":{"go-build":true}}'):
            self.layout.approvals.write_text(data)
            with self.subTest(data=data), self.assertRaises((ValueError, clean.Refusal)):
                clean.approvals_read(self.layout)
        self.layout.approvals.unlink()
        self.layout.approvals.symlink_to(self.file)
        with self.assertRaises(clean.Refusal):
            clean.approvals_read(self.layout)

    def test_audit_failure_is_explicit(self):
        def failed(args, **kw):
            if args[0] == 'python3':
                return subprocess.CompletedProcess(args, 1, '', 'cannot write')
            return self.runner(args, **kw)
        with self.assertRaisesRegex(clean.Refusal, 'audit record failed'):
            self.apply(failed)

    def test_measure_does_not_follow_links(self):
        outside = self.make(self.base / 'private', b'not to traverse')
        (self.cache / 'link').symlink_to(outside)
        size, files, errors = clean.measure(self.cache)
        self.assertEqual(files, 1)
        self.assertEqual(errors, 0)
        self.assertEqual(size, self.file.stat().st_blocks * 512)

    def test_process_visibility_reader_fails_closed(self):
        self.assertEqual(clean.Processes.read(self.base / 'missing').uncertain, 1)
        malformed = self.layout.proc / '123'
        malformed.mkdir()
        (malformed / 'stat').write_text('bad')
        (malformed / 'status').write_text(f'Name:\tunknown\nUid:\t{os.getuid()}\t{os.getuid()}\t{os.getuid()}\t{os.getuid()}\n')
        self.assertEqual(clean.Processes.read(self.layout.proc).uncertain, 1)

    def test_unreadable_other_uid_process_does_not_block_deletion(self):
        proc_dir = self.layout.proc / '501'
        proc_dir.mkdir()
        other_uid = os.getuid() + 1
        (proc_dir / 'status').write_text(f'Name:\tudev-worker\nUid:\t{other_uid}\t{other_uid}\t{other_uid}\t{other_uid}\n')
        result = clean.Processes.read(self.layout.proc)
        self.assertEqual(result.uncertain, 0)
        self.approve()
        self.reader = lambda _: result
        self.assertEqual(self.apply(), 0)
        self.assertFalse(self.file.exists())

    def test_unreadable_same_uid_allowlisted_process_does_not_block_deletion(self):
        proc_dir = self.layout.proc / '502'
        proc_dir.mkdir()
        uid = os.getuid()
        (proc_dir / 'status').write_text(f'Name:\tssh-agent\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n')
        result = clean.Processes.read(self.layout.proc)
        self.assertEqual(result.uncertain, 0)
        self.approve()
        self.reader = lambda _: result
        self.assertEqual(self.apply(), 0)
        self.assertFalse(self.file.exists())

    def test_unreadable_same_uid_allowlisted_sd_pam_process_does_not_block_deletion(self):
        proc_dir = self.layout.proc / '505'
        proc_dir.mkdir()
        uid = os.getuid()
        (proc_dir / 'status').write_text(f'Name:\t(sd-pam)\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n')
        result = clean.Processes.read(self.layout.proc)
        self.assertEqual(result.uncertain, 0)
        self.approve()
        self.reader = lambda _: result
        self.assertEqual(self.apply(), 0)
        self.assertFalse(self.file.exists())

    def test_unreadable_same_uid_unknown_process_blocks_deletion(self):
        proc_dir = self.layout.proc / '503'
        proc_dir.mkdir()
        uid = os.getuid()
        (proc_dir / 'status').write_text(f'Name:\tcustom-agent\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n')
        result = clean.Processes.read(self.layout.proc)
        self.assertEqual(result.uncertain, 1)
        self.approve()
        self.reader = lambda _: result
        self.assertEqual(self.apply(), 1)
        self.assertTrue(self.file.exists())

    def test_readable_process_referencing_target_blocks_deletion(self):
        process = self.layout.proc / '504'
        process.mkdir()
        fields = ['S', '1'] + ['0'] * 18
        (process / 'stat').write_text(f'{process.name} (worker) ' + ' '.join(fields))
        (process / 'status').write_text(f'Name:\tworker\nUid:\t{os.getuid()}\t{os.getuid()}\t{os.getuid()}\t{os.getuid()}\n')
        (process / 'cmdline').write_bytes(b'worker\0')
        (process / 'cwd').symlink_to(self.base)
        (process / 'fd').mkdir()
        (process / 'fd/3').symlink_to(self.file)
        (process / 'maps').write_text('')
        (self.layout.proc / 'uptime').write_text('50000.0 0.0')
        result = clean.Processes.read(self.layout.proc)
        self.assertEqual(result.uncertain, 0)
        self.assertIn(self.file, result.paths)
        self.approve()
        self.reader = lambda _: result
        self.assertEqual(self.apply(), 1)
        self.assertTrue(self.file.exists())

    def test_process_reader_collects_handles_maps_rss_and_orphan_hint(self):
        process = self.layout.proc / str(os.getpid() + 98765)
        process.mkdir()
        fields = ['S', '1'] + ['0'] * 18
        (process / 'stat').write_text(f'{process.name} (worker name) ' + ' '.join(fields))
        (process / 'status').write_text('VmRSS: 128 kB\n')
        (process / 'cmdline').write_bytes(b'worker\0private-argument\0')
        (self.layout.proc / 'uptime').write_text('50000.0 0.0')
        (process / 'cwd').symlink_to(self.base)
        (process / 'fd').mkdir()
        (process / 'fd/3').symlink_to(self.file)
        mapped = self.base / 'mapped library'
        (process / 'maps').write_text(f'0000-1000 r-xp 0000 00:00 123 {mapped}\n')
        result = clean.Processes.read(self.layout.proc)
        self.assertEqual(result.uncertain, 0)
        self.assertIn(self.file, result.paths)
        self.assertIn(mapped, result.paths)
        self.assertEqual(result.biggest[0]['rss'], 128 * 1024)
        self.assertTrue(result.biggest[0]['possible_orphan'])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            clean.display([], result, {})
        self.assertNotIn('private-argument', output.getvalue())

    def test_new_git_marker_and_parent_symlink_during_checks_refuse(self):
        self.approve()
        def new_repo(args, **kw):
            if args[0] == 'lsof':
                (self.cache / '.git').mkdir()
            return self.runner(args, **kw)
        self.assertEqual(self.apply(new_repo), 1)
        self.assertTrue(self.file.exists())
        (self.cache / '.git').rmdir()
        moved = self.cache / 'moved'
        def swap_parent(args, **kw):
            if args[0] == 'lsof':
                self.file.parent.rename(moved)
                (self.cache / 'ab').symlink_to(moved, target_is_directory=True)
            return self.runner(args, **kw)
        self.assertEqual(self.apply(swap_parent), 1)
        self.assertTrue((moved / self.file.name).exists())

    def test_live_reference_to_cache_directory_refuses(self):
        self.approve()
        self.processes.paths = {self.cache}
        self.assertEqual(self.apply(), 1)
        self.assertTrue(self.file.exists())

    def test_missing_handle_tool_refuses_without_unlink(self):
        self.approve()
        def missing(args, **kw):
            if args[0] == 'lsof':
                raise FileNotFoundError('missing lsof')
            return self.runner(args, **kw)
        self.assertEqual(self.apply(missing), 1)
        self.assertTrue(self.file.exists())

    def test_windows_is_report_only(self):
        def powershell(args, **kw):
            self.assertEqual(args[0], 'powershell.exe')
            self.assertNotIn('Remove-Item', args[-1])
            return subprocess.CompletedProcess(args, 0, '1024\n', '')
        self.assertIn('1.0 KiB', clean.windows_temp(powershell))

    def test_human_bytes_reviewed_qwen_edge_cases(self):
        for invalid in (True, -1, 1.2, '2'):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                clean.human_bytes(invalid)
        self.assertEqual(clean.human_bytes(0), '0 B')
        self.assertEqual(clean.human_bytes(1024), '1.0 KiB')
        self.assertEqual(clean.human_bytes(1024 ** 7), '1048576.0 PiB')

    def test_cli_unknown_argument_rejected_before_scan(self):
        with patch.object(clean, 'scan') as scan, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as exited:
                clean.main(['scan', '--ful'])
            self.assertEqual(exited.exception.code, 2)
            scan.assert_not_called()


class RealHandleToolTests(unittest.TestCase):
    """Run the real lsof and fuser so tool-version quirks cannot hide behind fakes."""

    @unittest.skipUnless(all(os.path.exists(p) for p in ('/usr/bin/lsof', '/usr/bin/fuser')),
                         'lsof and fuser required')
    def test_real_tools_pass_unused_file_and_catch_open_one(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'unused'
            path.write_bytes(b'x')
            clean.check_handles(path)  # must not refuse a file nobody holds
            with open(path, 'rb'):
                with self.assertRaises(clean.Refusal):
                    clean.check_handles(path)

    def test_relative_path_refused(self):
        def runner(args):
            return subprocess.CompletedProcess(args, 1, '', '')
        with self.assertRaises(clean.Refusal):
            clean.check_handles(Path('relative'), runner=runner)


if __name__ == '__main__':
    unittest.main()
