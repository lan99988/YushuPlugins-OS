import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from devflow.__main__ import CapabilityProbe, main
from devflow.capabilities import DEFAULT_POLICY
from devflow.engine import Store


def catalog(version='new', supported=True):
    return {'command': 'fake-codex', 'version': version, 'models': [
        {'model': model, 'supportedReasoningEfforts': [{'reasoningEffort': effort} for effort in efforts]}
        for model, efforts in ([('gpt-6-luna', ['max']), ('gpt-6.1-sol', ['medium', 'high', 'max'])]
                              if supported else [('gpt-5.6-sol', ['high'])])]}


class FakeClock:
    def __init__(self, callback=lambda now: None):
        self.now = 0
        self.sleeps = []
        self.callback = callback

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        if self.now > 7200:
            raise AssertionError('watch did not terminate within fake deadline')
        self.callback(self.now)


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.store = Store(self.root / 'state/state.db')
        self.store.initialize([{'id': 'a', 'deps': [], 'allowed_paths': ['a/'],
                               'tests': [[sys.executable, '-V']], 'risk': 'low', 'prompt': 'work'}])
        self.old_capabilities = catalog('old', supported=False)
        self.store.set_meta('capabilities', self.old_capabilities)
        self.store.set_meta('models_policy', DEFAULT_POLICY)
        self.store.set_meta('initialization_error', 'blocked_capability: gpt-6-luna unavailable in CLI model/list')
        self.store.control('blocked_capability')
        self.before = self.store.tasks()
        self.clock = FakeClock()
        self.probes = []
        self.closed = []

    def tearDown(self):
        self.tmp.cleanup()

    def invoke(self, *command):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main(['--repo', str(self.repo), '--state', str(self.root / 'state'), *command])
        return code, output.getvalue(), errors.getvalue()

    def context(self, results, delay=0):
        test = self

        class Probe:
            def __init__(self, repo):
                self.started = test.clock.time()
                test.probes.append(self.started)
                self.result_value = results.pop(0)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                test.closed.append(self.started)

            def poll(self):
                if test.clock.time() < self.started + delay:
                    return None
                value = self.result_value
                return (None, value) if isinstance(value, Exception) else (copy.deepcopy(value), None)

        return Probe

    def run_watch(self, results, delay=0, on_coordinator=None):
        with patch('devflow.__main__.time', self.clock, create=True), \
                patch('devflow.__main__.CapabilityProbe', self.context(results, delay), create=True), \
                patch('devflow.__main__.Coordinator') as coordinator:
            if on_coordinator:
                coordinator.side_effect = on_coordinator
            code, output, errors = self.invoke('run', '--watch')
        return code, coordinator, output, errors

    def test_waits_thirty_minutes_between_probes_and_dispatches_only_verified_catalog(self):
        def coordinator(repo, store, workers, publish):
            self.assertEqual(self.clock.time(), 3600)
            self.assertEqual(store.meta('control'), 'running')
            self.assertEqual(store.meta('capabilities')['version'], 'supported')
            self.assertEqual(store.meta('capabilities')['selected'][0], ['gpt-6-luna', 'max', None])
            self.assertEqual(store.tasks(), self.before)
            from unittest.mock import Mock
            return Mock()

        code, coordinator_mock, _, errors = self.run_watch(
            [catalog('unsupported', False), catalog('supported')], on_coordinator=coordinator)
        self.assertEqual(code, 0, errors)
        self.assertEqual(self.probes, [1800, 3600])
        self.assertLessEqual(max(self.clock.sleeps), 5)
        coordinator_mock.assert_called_once()
        self.assertIsNone(self.store.meta('initialization_error'))
        self.assertFalse(self.store.meta('capability_watch')['waiting'])

    def test_failed_probe_preserves_queue_and_old_evidence_until_stop(self):
        snapshots = []

        def control(now):
            if now == 1805:
                snapshots.append((self.store.meta('control'), self.store.meta('initialization_error'),
                                  self.store.meta('capability_watch')))
                self.store.control('stopped')

        self.clock.callback = control
        code, coordinator, _, errors = self.run_watch([OSError('network unavailable')])
        self.assertEqual(code, 0, errors)
        coordinator.assert_not_called()
        self.assertEqual(self.probes, [1800])
        self.assertEqual(self.store.tasks(), self.before)
        self.assertEqual(self.store.meta('capabilities'), self.old_capabilities)
        self.assertEqual(snapshots[0][0], 'blocked_capability')
        self.assertIn('network unavailable', snapshots[0][1])
        self.assertEqual(snapshots[0][2]['next_probe_at'], 3600)
        self.assertEqual(self.store.meta('control'), 'stopped')

    def test_pause_defers_probe_until_resume_without_dispatch(self):
        def control(now):
            if now == 10:
                self.store.control('paused')
            if now == 1900:
                self.assertEqual(self.probes, [])
                self.store.control('running')

        self.clock.callback = control
        code, coordinator, _, errors = self.run_watch([catalog()])
        self.assertEqual(code, 0, errors)
        self.assertEqual(self.probes, [1900])
        coordinator.assert_called_once()

    def test_stop_during_slow_probe_closes_context_within_five_seconds(self):
        self.clock.callback = lambda now: self.store.control('stopped') if now == 1805 else None
        code, coordinator, _, errors = self.run_watch([catalog()], delay=30)
        self.assertEqual(code, 0, errors)
        coordinator.assert_not_called()
        self.assertEqual(self.clock.time(), 1805)
        self.assertEqual(self.closed, [1800])
        self.assertEqual(self.store.meta('control'), 'stopped')
        self.assertEqual(self.store.meta('capabilities'), self.old_capabilities)
        self.assertEqual(self.store.tasks(), self.before)

    def test_capability_success_during_pause_does_not_resume_user_control(self):
        def control(now):
            if now == 1805:
                self.store.control('paused')
            if now == 1820:
                self.assertEqual(self.store.meta('control'), 'paused')
                self.assertEqual(self.store.meta('capabilities')['version'], 'new')
                self.store.control('running')

        self.clock.callback = control
        code, coordinator, _, errors = self.run_watch([catalog()], delay=10)
        self.assertEqual(code, 0, errors)
        self.assertEqual(self.probes, [1800])
        self.assertEqual(self.clock.time(), 1820)
        coordinator.assert_called_once()

    def test_one_shot_blocked_run_reports_precise_reason_without_probe_or_coordinator(self):
        with patch('devflow.capabilities.probe_cli') as probe, patch('devflow.__main__.Coordinator') as coordinator:
            code, _, errors = self.invoke('run')
        self.assertEqual(code, 1)
        self.assertIn('gpt-6-luna unavailable', errors)
        probe.assert_not_called()
        coordinator.assert_not_called()
        self.assertEqual(self.store.tasks(), self.before)

    def test_watch_exits_for_stopped_or_spec_without_probe(self):
        for control in ('stopped', 'blocked_spec'):
            with self.subTest(control=control):
                self.store.control(control)
                code, coordinator, _, errors = self.run_watch([])
                self.assertEqual(code, 0, errors)
                coordinator.assert_not_called()
        self.assertEqual(self.probes, [])

    def test_restart_keeps_persisted_probe_deadline(self):
        self.store.set_meta('capability_watch', {'waiting': True, 'next_probe_at': 200,
                                               'last_probe_at': -1600, 'probing': False})
        code, coordinator, _, errors = self.run_watch([catalog()])
        self.assertEqual(code, 0, errors)
        self.assertEqual(self.probes, [200])
        coordinator.assert_called_once()

    def test_resume_cannot_bypass_probe_failure_with_stale_usable_evidence(self):
        self.store.set_meta('capabilities', catalog('stale'))
        self.store.set_meta('capability_watch', {'waiting': True, 'next_probe_at': 1800,
                                               'last_probe_at': 0, 'probing': False})
        self.invoke('resume')
        self.assertEqual(self.store.meta('control'), 'blocked_capability')
        self.clock.callback = lambda now: self.store.control('stopped') if now == 5 else None
        code, coordinator, _, errors = self.run_watch([])
        self.assertEqual(code, 0, errors)
        coordinator.assert_not_called()
        self.assertEqual(self.probes, [])


class LauncherWatchTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows launcher')
    def test_launches_hidden_waiter_for_capability_block_but_exits_for_stop_or_spec(self):
        script = Path(__file__).resolve().parents[1] / 'devflow/install_autostart.ps1'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / 'repo'
            module = repo / 'devflow'
            module.mkdir(parents=True)
            (module / '__init__.py').write_text('')
            state = root / 'state'
            state.mkdir()
            record = root / 'launched.json'
            # Native Python runs a fake status module only. Start-Process is replaced,
            # so neither model turns nor scheduled task registrations can occur.
            harness = root / 'harness.ps1'
            harness.write_text('''param($Script,$State,$Record)
function Start-Process {
    param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,[switch]$PassThru,$RedirectStandardOutput,$RedirectStandardError)
    @{Arguments=$ArgumentList;WindowStyle=$WindowStyle} | ConvertTo-Json | Set-Content -LiteralPath $Record
    $child = [PSCustomObject]@{ExitCode=0}
    $child | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value {}
    return $child
}
& $Script -Mode Launch -State $State
''', encoding='utf-8')
            config = {'Repo': str(repo), 'Python': sys.executable, 'Codex': sys.executable,
                      'Gh': sys.executable, 'TaskName': 'Devflow-Test-' + root.name}
            (state / 'scheduler.json').write_text(json.dumps(config), encoding='utf-8')
            for control in ('blocked_capability', 'stopped', 'blocked_spec'):
                with self.subTest(control=control):
                    record.unlink(missing_ok=True)
                    (module / '__main__.py').write_text('print(' + repr(json.dumps({'control': control})) + ')')
                    result = subprocess.run(['powershell', '-NoProfile', '-File', str(harness),
                                             '-Script', str(script), '-State', str(state), '-Record', str(record)],
                                            capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if control == 'blocked_capability':
                        launch = json.loads(record.read_text(encoding='utf-8-sig'))
                        self.assertEqual(launch['WindowStyle'], 'Hidden')
                        self.assertIn('"--watch"', launch['Arguments'])
                    else:
                        self.assertFalse(record.exists())


class ProbeContextTests(unittest.TestCase):
    def test_contained_catalog_inherits_worker_process_group(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from devflow import capabilities
        for contained in (False, True):
            with self.subTest(contained=contained):
                process = Mock()
                process.stdin = io.StringIO()
                process.stdout = io.StringIO('\n'.join(json.dumps(value) for value in [
                    {'id': 1, 'result': {}}, {'id': 2, 'result': {'data': [], 'nextCursor': None}}]))
                with patch.object(capabilities, 'os', SimpleNamespace(name='posix')), \
                        patch.object(capabilities.subprocess, 'Popen', return_value=process) as popen:
                    self.assertEqual(capabilities.probe_catalog('fake-codex', contained=contained), [])
                self.assertEqual(popen.call_args.kwargs.get('start_new_session', False), not contained)
                process.terminate.assert_called_once()

    def test_context_returns_catalog_from_ordinary_worker_process(self):
        from unittest.mock import Mock
        runner = Mock()
        runner.run.return_value = json.dumps(catalog())
        with patch('devflow.__main__.CommandRunner', return_value=runner):
            with CapabilityProbe(Path.cwd()) as probe:
                self.assertTrue(probe.done.wait(2))
                result, error = probe.poll()
        self.assertIsNone(error)
        self.assertEqual(result, catalog())
        argv = runner.run.call_args.args[0]
        self.assertEqual(argv[0], sys.executable)
        self.assertIn('devflow.probe_worker', argv[2])
        self.assertEqual(runner.run.call_args.kwargs['timeout'], 180)
        runner.cancel.assert_not_called()

    def test_context_cancels_inflight_worker_without_waiting_for_protocol_timeout(self):
        from unittest.mock import Mock
        entered, cancelled = threading.Event(), threading.Event()
        runner = Mock()

        def run(*args, **kwargs):
            entered.set()
            if not cancelled.wait(2):
                raise AssertionError('context failed to cancel worker')
            raise RuntimeError('cancelled')

        runner.run.side_effect = run
        runner.cancel.side_effect = cancelled.set
        with patch('devflow.__main__.CommandRunner', return_value=runner):
            with CapabilityProbe(Path.cwd()) as probe:
                self.assertTrue(entered.wait(2))
                self.assertIsNone(probe.poll())
            self.assertTrue(cancelled.wait(2))
            self.assertTrue(probe.done.wait(2))
        runner.cancel.assert_called()

    def test_worker_only_probes_capabilities_with_contained_runner(self):
        from devflow.probe_worker import main as worker_main
        with patch('devflow.probe_worker.probe_cli', return_value=catalog()) as probe, \
                patch('devflow.probe_worker.CommandRunner') as runner, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(worker_main([str(Path.cwd())]), 0)
        self.assertTrue(runner.return_value.contained)
        probe.assert_called_once_with(runner.return_value, Path.cwd().resolve(), contained=True)
        self.assertEqual(json.loads(output.getvalue()), catalog())

    def test_init_saves_explicit_policy_even_when_probe_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / 'repo'
            repo.mkdir()
            policy = {'levels': [{'model': 'gpt-6.1-sol', 'effort': 'high'}] * 4}
            policy_path = root / 'policy.json'
            policy_path.write_text(json.dumps(policy))
            manifest = root / 'tasks.json'
            manifest.write_text(json.dumps({'version': 1, 'tasks': [
                {'id': 'a', 'allowed_paths': ['a/'], 'tests': [[sys.executable, '-V']]}]}))
            with patch('devflow.__main__.git', return_value=''), \
                    patch('devflow.capabilities.probe_cli', side_effect=OSError('CLI offline')), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(['--repo', str(repo), '--state', str(root / 'state'), 'init',
                             '--manifest', str(manifest), '--models-policy', str(policy_path)])
            store = Store(root / 'state/state.db')
            self.assertEqual(code, 1)
            self.assertEqual(store.meta('models_policy'), policy)
            self.assertEqual(store.meta('control'), 'blocked_capability')
            self.assertEqual(store.task('a')['status'], 'pending')
