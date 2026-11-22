import json
import re
import time

import pytest

from app import Manager, create_app, decode_feedback, validate_frames


@pytest.fixture
def control(tmp_path):
    manager = Manager(tmp_path, simulate=True)
    app = create_app(manager)
    app.config['TESTING'] = True
    client = app.test_client()
    token = re.search(r'name="control-token" content="([^"]+)"', client.get('/').text)[1]
    headers = {'X-Control-Token': token}
    return manager, client, headers


def wait(manager):
    deadline = time.monotonic() + 4
    while manager.busy() and time.monotonic() < deadline:
        time.sleep(.03)
    assert not manager.busy()


def test_write_requests_require_local_token(control):
    _, client, headers = control
    assert client.post('/api/connect', json={}).status_code == 403
    assert client.post('/api/connect', json={}, headers=headers).status_code == 200


@pytest.mark.parametrize('bad', [-1, 2001, True, '500', float('nan')])
def test_motor_limits_cannot_be_bypassed(bad):
    with pytest.raises(ValueError):
        validate_frames([{'positions': [bad, 0, 0, 0, 0, 0]}])


def test_joint_only_changes_selected_channel_and_record_persists(control):
    manager, client, headers = control
    client.post('/api/connect', json={}, headers=headers)
    assert client.post('/api/recordings/start', json={'name': '中指测试'}, headers=headers).status_code == 200
    original = manager.pose.copy()
    assert client.post('/api/joint', json={'id': 4, 'angle': 10, 'duration_ms': 600}, headers=headers).status_code == 200
    # A second command cannot replace an in-flight physical motion.
    assert client.post('/api/gesture', json={'id': 'fist'}, headers=headers).status_code == 409
    wait(manager)
    expected = original.copy(); expected[3] = round(4095 * 10 / 90)
    assert manager.pose == expected
    saved = client.post('/api/recordings/save', json={}, headers=headers)
    assert saved.status_code == 200
    record = saved.json
    assert record['frames'][-1]['positions'] == expected
    assert client.get(f"/api/recordings/{record['id']}/markdown").status_code == 200
    assert '中指测试' in client.get(f"/api/recordings/{record['id']}/markdown").text
    restarted = Manager(manager.data, simulate=True)
    assert restarted.pose == expected
    assert restarted.record(record['id'])['frames'] == record['frames']


def test_stop_cancels_playback_and_returns_open(control):
    manager, client, headers = control
    client.post('/api/connect', json={}, headers=headers)
    client.post('/api/gesture', json={'id': 'grasp-cycle'}, headers=headers)
    assert client.post('/api/stop', json={}, headers=headers).status_code == 200
    wait(manager)
    assert manager.pose == [0, 612, 0, 0, 0, 0]
    assert manager.job['status'] == 'stopped'


def test_invalid_angles_and_calibration_do_not_start_motion(control):
    manager, client, headers = control
    client.post('/api/connect', json={}, headers=headers)
    for angle in [-1, 91, '20', True, float('inf')]:
        assert client.post('/api/joint', json={'id': 3, 'angle': angle}, headers=headers).status_code == 400
    values = manager.calibration.copy()
    values[0] = {'zero_raw': 0, 'end_raw': 2500, 'end_angle': 90}
    assert client.post('/api/calibration', json={'joints': values}, headers=headers).status_code == 400
    assert manager.job is None


def test_feedback_decoder_does_not_treat_RTR_fault_response_as_position():
    good = '\n'.join(f'(1.0) can0 {0x101+i:03X}#AA00000000000000' for i in range(6))
    assert decode_feedback(good) == [0]*6
    with pytest.raises(RuntimeError):
        decode_feedback(good.replace('103#AA00000000000000', '103#A501'))
    with pytest.raises(RuntimeError):
        decode_feedback(good + '\n(1.1) can0 103#A501')


def test_direct_plan_contains_only_start_and_final_targets(control):
    manager, _, _ = control
    initial = [0, 612, 0, 0, 0, 0]
    target = [2000, 0, 1600, 1400, 1200, 1500]
    plan = manager.plan({'initial_positions': initial, 'frames': [
        {'positions': target, 'duration_ms': 1600, 'hold_ms': 2000},
        {'positions': initial, 'duration_ms': 600, 'hold_ms': 0}]})
    rows = [list(map(int, row.split())) for row in plan.splitlines()]
    assert len(rows) == 3
    assert [row[:6] for row in rows] == [initial, target, initial]
    assert rows[1][6:] == [16, 20]


def test_applied_target_is_kept_until_next_explicit_command(control, monkeypatch):
    manager, client, headers = control
    client.post('/api/connect', json={}, headers=headers)
    monkeypatch.setattr(manager, 'worker', lambda job: None)
    response = client.post('/api/joint', json={'id': 3, 'angle': 30}, headers=headers)
    assert response.status_code == 200
    # The UI receives the submitted target while the encoder still reports the old pose.
    target = [0, 612, 1365, 0, 0, 0]
    state = client.get('/api/state').json
    assert state['desired_positions'] == target
    assert state['positions'] != target
    manager.finish(manager.job, 0, target.copy(), '')
    for _ in range(3):
        state = client.get('/api/state').json
        assert state['holding'] is True
        assert state['desired_positions'] == target
        assert state['busy'] is False
    client.post('/api/joint', json={'id': 4, 'angle': 20}, headers=headers)
    assert manager.job['frames'][-1]['positions'] == [0, 612, 1365, 910, 0, 0]
    manager.finish(manager.job, 0, [0, 612, 1365, 910, 0, 0], '')
    client.post('/api/stop', json={}, headers=headers)
    assert manager.job['frames'][-1]['positions'] == [0, 612, 0, 0, 0, 0]
    manager.finish(manager.job, 0, [0, 612, 0, 0, 0, 0], '')
    assert client.get('/api/state').json['holding'] is False
    assert manager.holding_job is None


def test_hold_status_requires_live_process_and_fresh_feedback():
    hold = {'id': 'a' * 32, 'board_dir': '/tmp/handui-' + 'a' * 32}
    class Console:
        def __init__(self, age=0, alive=True):
            self.age = age
            self.alive = alive
        def command(self, command):
            assert 'cansend' not in command and 'canplayer' not in command
            return ('\n__HOLD_BEGIN__\nstatus=holding\npositions=0,612,500,0,0,0\ntarget=0,612,500,0,0,0\n'
                    f'updated_at={100 - self.age}\npid=123\n\n__HOLD_END__\n__BOARD_NOW__100\n' +
                    ('__HOLD_ALIVE__\n' if self.alive else ''))
    assert Manager.hold_status(Console(), hold)['positions'] == [0, 612, 500, 0, 0, 0]
    with pytest.raises(RuntimeError, match='停止或反馈过期'):
        Manager.hold_status(Console(age=6), hold)
    with pytest.raises(RuntimeError, match='停止或反馈过期'):
        Manager.hold_status(Console(alive=False), hold)


def test_feedback_decoder_rejects_drive_fault_and_masks_position_bits():
    good = '\n'.join(f'(1.0) can0 {0x101+i:03X}#AA0023F100000000' for i in range(6))
    assert decode_feedback(good) == [0x123] * 6
    with pytest.raises(RuntimeError):
        decode_feedback(good.replace('103#AA00', '103#AA08'))


def test_prelaunch_failure_does_not_replay_failed_target_on_next_joint(control, monkeypatch):
    manager, client, headers = control
    client.post('/api/connect', json={}, headers=headers)
    manager.simulate = False
    manager.runner_installed = True
    monkeypatch.setattr(manager, 'console', lambda log: (_ for _ in ()).throw(OSError('offline')))
    baseline = manager.desired_pose.copy()
    client.post('/api/joint', json={'id': 3, 'angle': 30}, headers=headers)
    wait(manager)
    assert manager.job['status'] == 'error'
    assert manager.pending is None
    assert manager.desired_pose == baseline
    manager.connected = True
    monkeypatch.setattr(manager, 'worker', lambda job: None)
    assert client.post('/api/joint', json={'id': 4, 'angle': 20}, headers=headers).status_code == 200
    assert manager.job['frames'][-1]['positions'][2] == baseline[2]


@pytest.mark.parametrize('logs_available', [True, False])
def test_dead_pending_runner_is_recovered_without_replaying_or_stopping(control, monkeypatch, logs_available):
    manager, _, _ = control
    manager.simulate = False
    manager.connected = True
    job = {'id': 'b' * 32, 'board_dir': '/tmp/handui-' + 'b' * 32,
           'label': '测试', 'status': 'running', 'frames': [
               {'positions': [0, 612, 500, 0, 0, 0], 'duration_ms': 1200, 'hold_ms': 0}],
           'initial_positions': manager.pose.copy(), 'started_at': '2026-11-11T00:00:00+08:00',
           '_cancel': __import__('threading').Event()}
    job['_cancel'].set()
    manager.job = job
    manager.pending = {k: v for k, v in job.items() if not k.startswith('_')}
    commands = []
    class Console:
        def command(self, command):
            commands.append(command)
            return '\n__STATE__missing:0\n'
        def close(self):
            pass
    monkeypatch.setattr(manager, 'console', lambda log: Console())
    def fetch(console, path, timeout=15):
        if not logs_available:
            raise RuntimeError('日志不存在')
        return 'startup failed before motion'
    monkeypatch.setattr(manager, 'fetch', fetch)
    original = manager.pose.copy()
    confirmed = manager.confirmed_at
    manager.worker(job, resume=True)
    assert not manager.busy()
    assert manager.pending is None
    assert manager.pose == original
    assert manager.confirmed_at == confirmed
    assert job['status'] == 'error'
    assert job['feedback_confirmed'] is False
    assert all('touch ' not in cmd and 'nohup ' not in cmd and 'cansend ' not in cmd for cmd in commands)


def test_job_liveness_requires_matching_runner_not_only_a_pid():
    job = {'id': 'c' * 32, 'board_dir': '/tmp/handui-' + 'c' * 32, 'board_pid': 123}
    command = Manager.job_state_command(job)
    assert command == 'sh /tmp/handui-query.sh state ' + 'c' * 32 + ' 123'
    assert len(command) < 100
    assert 'touch ' not in command


def test_reconnect_reuses_matching_board_program_without_slow_reupload(control, monkeypatch):
    import hashlib
    from app import ROOT
    manager, _, _ = control
    commands = []
    class Console:
        def command(self, command):
            commands.append(command)
            if command.startswith('sha256sum '):
                return '\n'.join(hashlib.sha256((ROOT / local).read_bytes()).hexdigest() + '  /tmp/' + remote
                                 for local, remote in [('board-motion.py', 'handui-motion.py'),
                                                       ('board-motion.sh', 'handui-motion.sh'),
                                                       ('board-query.sh', 'handui-query.sh')]) + '\n'
            if command.startswith('sh -n '):
                return '\n__HOLD_SHELL_OK__\n'
            return '\n__HOLD_RUNTIME_OK__\n'
    monkeypatch.setattr(manager, 'upload', lambda *args: pytest.fail('Identical programs must not be reuploaded'))
    manager.install_runner(Console())
    assert manager.runner_installed
    assert all('cansend' not in cmd and 'nohup ' not in cmd for cmd in commands)
