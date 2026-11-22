"""Offline regression tests: no CAN tools, board connection or motor actions."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('board_motion', Path(__file__).with_name('board-motion.py'))
motion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(motion)
TARGET = [1000, 1200, 1400, 1300, 1100, 1000]


class Clock:
    def __init__(self):
        self.now = 0.
        self.hook = lambda: None

    def monotonic(self):
        return self.now

    def time(self):
        return 1800000000. + self.now

    def sleep(self, seconds):
        self.now += seconds
        self.hook()


def feedback(node, value, fault=0):
    return '(1800000000.000000) can0 %03X#AA%02X%02X%02X00000000\n' % (
        0x101 + node, fault, value & 255, value >> 8)


class Bus:
    def __init__(self, clock):
        self.clock = clock
        self.sent = []
        self.pending = []
        self.closed = False
        self.fault_after = float('inf')
        self.missing_after = float('inf')
        self.position_override = None
        self.fallback = False

    def send_pose(self, pose, stamp):
        assert not self.closed
        self.sent.append((self.clock.now, list(pose)))
        for node, value in enumerate(pose):
            if node == 5 and self.clock.now >= self.missing_after:
                continue
            actual = self.position_override(node, value) if self.position_override else value
            fault = 8 if node == 4 and self.clock.now >= self.fault_after else 0
            self.pending.append(feedback(node, actual, fault))
        return motion.command_lines(pose, stamp)

    def read(self):
        lines, self.pending = self.pending, []
        return lines

    def emergency_open(self):
        self.fallback = True

    def close(self):
        self.closed = True


def make_runner(tmp_path, target=TARGET, settle=6):
    clock = Clock()
    bus = Bus(clock)
    run = tmp_path / 'handui-test'
    rows = [motion.OPEN + [1, 0], list(target) + [settle, 0]]
    runner = motion.Runner(str(run), rows, lambda: bus, clock, str(tmp_path / 'owner'))
    return runner, bus, clock, run


def status(path):
    return dict(line.split('=', 1) for line in (path / 'holding.status').read_text().splitlines())


def request_at(clock, run, seconds, name):
    def hook():
        if clock.now >= seconds:
            (run / name).touch()
    clock.hook = hook


def test_holds_over_35_seconds_without_return_or_growing_logs(tmp_path):
    runner, bus, clock, run = make_runner(tmp_path)
    snapshots = []
    def hook():
        if clock.now > 1 and not snapshots:
            snapshots.append(((run / 'can.log').read_bytes(), (run / 'final.log').read_bytes()))
            assert (run / 'exit-code').read_text().strip() == '0'
            assert status(run)['status'] == 'holding'
        if clock.now > 35:
            assert snapshots[0] == ((run / 'can.log').read_bytes(), (run / 'final.log').read_bytes())
            (run / 'handover-request').touch()
    clock.hook = hook
    assert runner.run() == 0
    assert len(bus.sent) >= 1750
    assert all(pose == TARGET for _, pose in bus.sent)
    assert status(run)['status'] == 'handover'
    assert bus.closed
    assert not (tmp_path / 'owner').exists()


def test_handover_exits_without_open_before_next_target(tmp_path):
    old, old_bus, clock, old_path = make_runner(tmp_path / 'old')
    request_at(clock, old_path, 1, 'handover-request')
    assert old.run() == 0
    assert old_bus.closed
    assert motion.OPEN not in [pose for _, pose in old_bus.sent]
    new_target = TARGET.copy()
    new_target[3] += 100
    new, new_bus, new_clock, new_path = make_runner(tmp_path / 'new', new_target)
    request_at(new_clock, new_path, 1, 'handover-request')
    assert new.run() == 0
    assert all(pose == new_target for _, pose in new_bus.sent)
    assert status(old_path)['status'] == 'handover'


@pytest.mark.parametrize('when', [.2, 2.])
def test_stop_opens_explicitly_and_never_resumes_old_target(tmp_path, when):
    runner, bus, clock, run = make_runner(tmp_path)
    request_at(clock, run, when, 'stop-request')
    assert runner.run() == 130
    targets = [pose for _, pose in bus.sent]
    first_open = targets.index(motion.OPEN)
    assert all(pose == motion.OPEN for pose in targets[first_open:])
    assert (run / 'exit-code').read_text().strip() == '130'
    assert status(run)['status'] == 'released'
    assert bus.closed


def test_explicit_open_finishes_without_background_hold(tmp_path):
    runner, bus, clock, run = make_runner(tmp_path, motion.OPEN)
    assert runner.run() == 0
    assert clock.now < 1
    assert status(run)['status'] == 'released'
    assert bus.closed


def test_fault_after_completion_stops_holding_and_is_not_success(tmp_path):
    runner, bus, clock, run = make_runner(tmp_path)
    bus.fault_after = 2
    assert runner.run() == 1
    result = status(run)
    assert result['status'] == 'error'
    assert 'fault status 8' in result['message']
    assert (run / 'exit-code').read_text().strip() == '0'  # Initial action succeeded.
    assert bus.sent[-1][1] == motion.OPEN
    assert bus.closed


def test_fault_before_full_feedback_exits_and_closes_sender(tmp_path):
    runner, bus, clock, run = make_runner(tmp_path)
    bus.fault_after = 0
    assert runner.run() == 1
    assert (run / 'exit-code').read_text().strip() == '1'
    assert status(run)['status'] == 'error'
    assert bus.closed
    assert (run / 'final.log').exists()
    assert not (tmp_path / 'owner').exists()


def test_one_stale_motor_cannot_hide_behind_other_five(tmp_path):
    runner, bus, clock, run = make_runner(tmp_path)
    bus.missing_after = 2
    assert runner.run() == 1
    assert 3.5 <= clock.now < 4.5
    assert status(run)['status'] == 'error'
    assert 'six motors' in status(run)['message']
    assert bus.sent[-1][1] == motion.OPEN


def test_lag_during_motion_is_allowed_but_persistent_idle_error_stops(tmp_path):
    runner, bus, clock, run = make_runner(tmp_path, settle=20)
    bus.position_override = lambda node, value: max(0, value - 200)
    assert runner.run() == 1
    assert clock.now > 3.5
    assert 'persistent position deviation' in status(run)['message']


def test_temporary_periodic_reset_recovers_without_false_stop(tmp_path):
    runner, bus, clock, run = make_runner(tmp_path)
    bus.position_override = lambda node, value: 0 if 2 <= clock.now < 2.1 else value
    request_at(clock, run, 4, 'handover-request')
    assert runner.run() == 0
    assert motion.OPEN not in [pose for _, pose in bus.sent]


def test_twelve_bit_feedback_and_nonzero_status():
    assert motion.decode_line('(1.000000) can0 103#AA0023A100000000') == (2, 0x123)
    with pytest.raises(RuntimeError, match='fault status 8'):
        motion.decode_line('(1.000000) can0 103#AA08230100000000')
    with pytest.raises(RuntimeError, match='Invalid AA'):
        motion.decode_line('(1.000000) can0 103#A501')
    assert motion.decode_line('(1.000000) can0 003#AA2301E8096403') is None


def test_only_seven_byte_position_frames_and_documented_limits():
    lines = motion.command_lines(TARGET, 1800000000.999999).splitlines()
    assert len(lines) == 6
    assert all(len(line.split('#')[1]) == 14 for line in lines)
    assert lines[0].startswith('(1800000000.999999) can0 001#AA')
    assert lines[1].startswith('(1800000001.000499) can0 002#AA')
    with pytest.raises(ValueError):
        motion.command_lines([2001, 0, 0, 0, 0, 0], 1)
    with pytest.raises(ValueError):
        motion.command_lines([0, -1, 0, 0, 0, 0], 1)


@pytest.mark.parametrize('bad', [
    '0 612 0 0 0 0 1 0\n',
    '0 612 0 0 0 0 1 0\n2001 0 0 0 0 0 6 0\n',
    '0 612 0 0 0 0 1 0\n0 -1 0 0 0 0 6 0\n',
    '0 612 0 0 0 0 1 0\n0 0 0 0 0 0 1001 0\n',
])
def test_invalid_plans_fail_before_a_bus_is_created(tmp_path, bad):
    path = tmp_path / 'plan'
    path.write_text(bad)
    with pytest.raises(ValueError):
        motion.read_plan(str(path))


def test_runtime_check_does_not_construct_can_bus(monkeypatch, capsys):
    monkeypatch.setattr(motion.os.path, 'isfile', lambda path: True)
    monkeypatch.setattr(motion.os, 'access', lambda path, mode: True)
    monkeypatch.setattr(motion.signal, 'SIGHUP', 1, raising=False)
    def forbidden():
        raise AssertionError('CAN must not be opened by --check')
    monkeypatch.setattr(motion, 'CanToolsBus', forbidden)
    assert motion.main(['--check']) == 0
    assert capsys.readouterr().out.strip() == '__HOLD_RUNTIME_OK__'


def test_runtime_check_exercises_parent_alias_without_creating_files(monkeypatch, capsys):
    probe = '/tmp/handui-' + '0' * 32
    monkeypatch.setattr(motion.signal, 'SIGHUP', 1, raising=False)
    monkeypatch.setattr(motion.os.path, 'realpath',
                        lambda path: '/var/volatile/tmp' if path == '/tmp' else
                        '/var/volatile/tmp/' + path.rsplit('/', 1)[-1])
    monkeypatch.setattr(motion.os.path, 'islink', lambda path: path == '/tmp')
    monkeypatch.setattr(motion.os.path, 'isfile', lambda path: True)
    monkeypatch.setattr(motion.os, 'access', lambda path, mode: True)
    monkeypatch.setattr(motion.os, 'makedirs', lambda *a, **kw: pytest.fail('--check must not create directories'))
    monkeypatch.setattr(motion, 'atomic_write', lambda *a: pytest.fail('--check must not write files'))
    assert motion.main(['--check']) == 0
    assert capsys.readouterr().out.strip() == '__HOLD_RUNTIME_OK__'


def test_runtime_check_rejects_missing_signal_before_can(monkeypatch):
    monkeypatch.setattr(motion.signal, 'SIGHUP', None, raising=False)
    with pytest.raises(RuntimeError, match='Required board signal'):
        motion.main(['--check'])


def test_preparation_failure_is_recoverable_without_opening_can(monkeypatch):
    writes = {}
    monkeypatch.delenv('HANDUI_LOCKED', raising=False)
    monkeypatch.setattr(motion.os.path, 'realpath', lambda path: path)
    monkeypatch.setattr(motion.os, 'makedirs', lambda *a, **kw: None)
    monkeypatch.setattr(motion, 'atomic_write', lambda path, text: writes.update({path: text}))
    run = '/tmp/handui-' + 'a' * 32
    assert motion.main([run, 'missing-plan']) == 1
    assert writes[motion.os.path.join(run, 'exit-code')] == '1\n'
    assert writes[motion.os.path.join(run, 'final.log')] == ''
    assert 'status=error' in writes[motion.os.path.join(run, 'holding.status')]


def test_system_tmp_parent_alias_is_allowed(tmp_path, monkeypatch):
    temp_root = str(tmp_path / 'tmp')
    canonical = str(tmp_path / 'var' / 'volatile' / 'tmp')
    run = temp_root + '/handui-' + 'b' * 32
    resolved = str(Path(canonical) / ('handui-' + 'b' * 32))
    original_realpath = motion.os.path.realpath
    monkeypatch.setattr(motion.os.path, 'realpath',
                        lambda path: {temp_root: canonical, run: resolved}.get(path, original_realpath(path)))
    monkeypatch.setattr(motion.os.path, 'islink', lambda path: path == temp_root)
    assert motion.validate_run_directory(run, temp_root) == run


def test_run_owned_symlink_is_rejected_without_diagnostic_writes(tmp_path, monkeypatch):
    run = '/tmp/handui-' + 'c' * 32
    writes = []
    monkeypatch.setattr(motion.os.path, 'islink', lambda path: path == run)
    monkeypatch.setattr(motion, 'atomic_write', lambda *args: writes.append(args))
    with pytest.raises(ValueError, match='itself'):
        motion.main([run, 'unused-plan'])
    assert not writes


def test_resolved_run_outside_canonical_tmp_is_rejected(monkeypatch):
    run = '/tmp/handui-' + 'd' * 32
    monkeypatch.setattr(motion.os.path, 'islink', lambda path: False)
    monkeypatch.setattr(motion.os.path, 'realpath',
                        lambda path: '/var/volatile/tmp' if path == '/tmp' else '/elsewhere/' + path.rsplit('/', 1)[-1])
    with pytest.raises(ValueError, match='outside'):
        motion.validate_run_directory(run)


def test_alias_preparation_error_records_terminal_state_without_can(tmp_path, monkeypatch):
    logical_run = '/tmp/handui-' + 'e' * 32
    safe_run = str(tmp_path / ('handui-' + 'e' * 32))
    monkeypatch.setattr(motion, 'validate_run_directory', lambda path: safe_run)
    monkeypatch.delenv('HANDUI_LOCKED', raising=False)
    monkeypatch.setattr(motion, 'CanToolsBus', lambda: pytest.fail('Preparation must not open CAN'))
    assert motion.main([logical_run, 'missing-plan']) == 1
    assert (Path(safe_run) / 'exit-code').read_text().strip() == '1'
    assert (Path(safe_run) / 'final.log').read_text() == ''
    assert (Path(safe_run) / 'can.log').read_text() == ''
    assert status(Path(safe_run))['status'] == 'error'


def test_invalid_lexical_run_path_never_writes_diagnostics(monkeypatch):
    writes = []
    monkeypatch.setattr(motion, 'atomic_write', lambda *args: writes.append(args))
    with pytest.raises(ValueError):
        motion.main(['/etc/handui-' + 'f' * 32, 'unused-plan'])
    assert not writes


def test_fallback_open_kills_queued_target_sender_first(monkeypatch):
    events = []
    class Player:
        def poll(self):
            return None
        def terminate(self):
            events.append('terminate')
        def wait(self, timeout):
            events.append('wait')
    bus = motion.CanToolsBus.__new__(motion.CanToolsBus)
    bus.player = Player()
    monkeypatch.setattr(motion.subprocess, 'run', lambda args, **kw: events.append(args))
    bus.emergency_open()
    assert events[:2] == ['terminate', 'wait']
    assert len(events[2:]) == 6
    assert all(event[:2] == ['cansend', 'can0'] for event in events[2:])
    assert events[2][2] == '001#AA0000E8096403'
    assert events[3][2] == '002#AA6402E8096403'
