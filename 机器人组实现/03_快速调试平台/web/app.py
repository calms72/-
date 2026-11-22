"""Local dexterous-hand dashboard. Only documented CAN position frames are sent."""
import argparse
import base64
import copy
import hashlib
import json
import math
import os
import re
import secrets
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request, Response
from serial_console import Console
from device_connection import select_port, handshake, port_exists

ROOT = Path(__file__).resolve().parent
OPEN = [0, 612, 0, 0, 0, 0]
LIMITS = [2000, 4095, 4095, 4095, 4095, 4095]
NAMES = ['拇指侧摆', '拇指弯曲', '食指', '中指', '无名指', '小指']
GESTURES = [
    {'id': 'open', 'name': '自然张开', 'hint': '回到初始手姿', 'positions': OPEN},
    {'id': 'grasp', 'name': '圆柱抓握', 'hint': '已实机验证的抓握姿态', 'positions': [2000, 0, 1600, 1400, 1200, 1500]},
    {'id': 'fist', 'name': '轻握拳', 'hint': '保留行程余量', 'positions': [0, 1800, 2400, 2400, 2200, 2200]},
    {'id': 'peace', 'name': 'V 手势', 'hint': '食指与中指伸展', 'positions': [0, 1600, 0, 0, 2200, 2200]},
    {'id': 'point', 'name': '指向', 'hint': '食指伸展', 'positions': [0, 1600, 0, 2200, 2200, 2200]},
    {'id': 'thumb', 'name': '拇指伸展', 'hint': '四指收拢', 'positions': [0, 612, 2200, 2200, 2000, 2000]},
]


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)


def integer(value, lo, hi, label):
    if type(value) is not int or not lo <= value <= hi:
        raise ValueError(f'{label}必须是 {lo}–{hi} 范围内的整数。')
    return value


def positions(values):
    if not isinstance(values, list) or len(values) != 6:
        raise ValueError('位置必须包含六个关节。')
    return [integer(v, 0, cap, NAMES[i]) for i, (v, cap) in enumerate(zip(values, LIMITS))]


def validate_frames(frames):
    if not isinstance(frames, list) or not 1 <= len(frames) <= 64:
        raise ValueError('动作需要 1–64 个姿态。')
    result = []
    for frame in frames:
        if not isinstance(frame, dict):
            raise ValueError('动作姿态格式不正确。')
        result.append({'positions': positions(frame.get('positions')),
                       'duration_ms': integer(frame.get('duration_ms', 1200), 200, 10000, '到位等待时间'),
                       'hold_ms': integer(frame.get('hold_ms', 0), 0, 30000, '停留时间')})
    if sum(f['duration_ms'] + f['hold_ms'] for f in result) > 300000:
        raise ValueError('单次动作总时长不能超过五分钟。')
    return result


def decode_feedback(text):
    latest = {}
    for match in re.finditer(r'can0\s+(10[1-6])#([A-F0-9]+)\b', text):
        payload = bytes.fromhex(match[2])
        if len(payload) == 8 and payload[0] == 0xAA and payload[1] == 0:
            latest[int(match[1], 16) - 0x101] = int.from_bytes(payload[2:4], 'little') & 0xFFF
        else:
            latest.pop(int(match[1], 16) - 0x101, None)
    if set(latest) != set(range(6)):
        raise RuntimeError('未收到六个节点完整的 AA 位置反馈。')
    return [latest[i] for i in range(6)]


class Manager:
    def __init__(self, data_dir=None, port='auto', simulate=False):
        self.data = Path(data_dir or ROOT / 'data')
        self.data.mkdir(parents=True, exist_ok=True)
        self.port = port
        self.auto_port = port == 'auto'
        self.simulate = simulate
        self.lock = threading.RLock()
        self.io_lock = threading.Lock()
        self.connected = False
        self.message = '点击连接，读取开发板状态。'
        self.pose = OPEN.copy()
        self.desired_pose = OPEN.copy()
        self.confirmed_at = None
        self.calibration = [{'zero_raw': 0, 'end_raw': cap, 'end_angle': 90., 'calibrated': False} for cap in LIMITS]
        self.job = None
        self.recording = None
        self.recent = []
        self.pending = None
        self.runner_installed = False
        self.holding = False
        self.holding_job = None
        self._hold_monitor = None
        self._hold_monitor_job = None
        if (self.data / 'calibration.json').exists():
            self.calibration = json.loads((self.data / 'calibration.json').read_text())
        if (self.data / 'state.json').exists():
            state = json.loads((self.data / 'state.json').read_text())
            self.pose = positions(state['positions'])
            self.desired_pose = positions(state.get('desired_positions', state['positions']))
            self.confirmed_at = state.get('confirmed_at')
            self.pending = state.get('pending')
            self.holding_job = state.get('holding_job')
        for path in (self.data / 'sessions').glob('*/result.json'):
            try:
                self.recent.append(json.loads(path.read_text()))
            except (ValueError, OSError):
                continue
        self.recent.sort(key=lambda item: item.get('finished_at', ''))
        self.recent = self.recent[-12:]
        if not (self.data / 'state.json').exists() and self.recent and self.recent[-1].get('status') == 'done':
            self.desired_pose = self.recent[-1]['frames'][-1]['positions'].copy()

    def persist(self):
        write_json(self.data / 'state.json', {'positions': self.pose, 'desired_positions': self.desired_pose, 'confirmed_at': self.confirmed_at, 'pending': self.pending, 'holding_job': self.holding_job})

    def install_runner(self, console):
        python_program = (ROOT / 'board-motion.py').read_bytes()
        shell_program = (ROOT / 'board-motion.sh').read_bytes()
        query_program = (ROOT / 'board-query.sh').read_bytes()
        deployed = console.command('sha256sum /tmp/handui-motion.py /tmp/handui-motion.sh /tmp/handui-query.sh 2>/dev/null')
        hashes = dict((path, digest) for digest, path in re.findall(
            r'^([a-f0-9]{64})\s+(/tmp/handui-(?:motion\.(?:py|sh)|query\.sh))\s*$', deployed, re.M))
        if hashes.get('/tmp/handui-motion.py') != hashlib.sha256(python_program).hexdigest():
            self.upload(console, '/tmp/handui-motion.py', python_program)
        check = console.command("python3 /tmp/handui-motion.py --check")
        if '\n__HOLD_RUNTIME_OK__\n' not in check:
            raise RuntimeError('开发板不支持持续保持程序；未发送运动指令。')
        if hashes.get('/tmp/handui-motion.sh') != hashlib.sha256(shell_program).hexdigest():
            self.upload(console, '/tmp/handui-motion.sh', shell_program)
        if hashes.get('/tmp/handui-query.sh') != hashlib.sha256(query_program).hexdigest():
            self.upload(console, '/tmp/handui-query.sh', query_program)
        syntax = console.command("sh -n /tmp/handui-motion.sh && sh -n /tmp/handui-query.sh && printf '\\n__HOLD_SHELL_OK__\\n'")
        if '\n__HOLD_SHELL_OK__\n' not in syntax:
            raise RuntimeError('板端控制脚本语法检查失败，未发送动作。')
        self.runner_installed = True

    def console(self, log):
        c = Console(self.port, log)
        try:
            c.login()
        except BaseException:
            c.close()
            raise
        return c

    @staticmethod
    def upload(c, path, content):
        encoded = base64.b64encode(content).decode()
        c.command(': > ' + path + '.b64')
        for i in range(0, len(encoded), 300):
            c.command("printf '%s' '" + encoded[i:i+300] + "' >> " + path + '.b64')
        output = c.command('base64 -d ' + path + '.b64 > ' + path + " && printf '\\n__UPLOAD_OK__\\n'")
        if '\n__UPLOAD_OK__\n' not in output:
            raise RuntimeError('动作程序上传失败，未启动运动。')

    @staticmethod
    def fetch(c, path, timeout=15):
        output = c.command("printf '\\n__DATA_BEGIN__\\n'; base64 " + path + "; printf '\\n__DATA_END__\\n'", timeout)
        match = re.search(r'\n__DATA_BEGIN__\n([A-Za-z0-9+/=\s]*)\n__DATA_END__\n', output)
        if not match:
            raise RuntimeError('日志回收失败；不会自动重发动作。')
        return base64.b64decode(match[1]).decode()

    def connect(self):
        with self.lock:
            if self.busy():
                raise RuntimeError('动作正在执行，请等待完成。')
        if self.simulate:
            self.connected = True
            self.message = '模拟模式：不发送任何硬件指令。'
            return self.state()
        with self.io_lock, self.lock:
            if self.busy():
                raise RuntimeError('动作正在执行，请等待完成。')
            self.connected = False
            if self.auto_port:
                self.port = select_port()
            handshake(self.port, self.data / 'handshake.log')
            c = self.console(self.data / 'connection.log')
            try:
                result = c.command('cat /proc/device-tree/model; printf "\\n"; ip -details link show can0')
                if 'MYiR PLC STM32MP257x' not in result or 'bitrate 1000000' not in result or 'LOWER_UP' not in result:
                    raise RuntimeError('开发板型号或 can0 的 1 Mbps 配置与已验证设备不符。')
                self.install_runner(c)
                if self.holding_job and not self.pending:
                    info = self.hold_status(c, self.holding_job)
                    if info.get('status') == 'holding':
                        self.pose = info['positions']
                        self.desired_pose = info['target']
                        self.confirmed_at = now()
                        self.holding = True
                    elif info.get('status') in ('released', 'handover'):
                        self.holding = False
                        self.holding_job = None
                    else:
                        self.holding = False
                        self.holding_job = None
                        self.persist()
                        raise RuntimeError(info.get('message') or '上次保持已异常结束，请检查后重新连接。')
                self.connected = True
                self.message = '已应用，正在保持；点击自然张开或停止并张开可释放。' if self.holding else '开发板已连接，CAN 1 Mbps；位置显示为最近一次确认值。'
                if self.pending:
                    self.job = copy.deepcopy(self.pending)
                    self.job.pop('finished_at', None)
                    self.job['status'] = 'running'
                    self.job['message'] = '正在回收前一次动作的状态，不会重发。'
                    self.job['_cancel'] = threading.Event()
                    threading.Thread(target=self.worker, args=(self.job, True), daemon=True).start()
                elif self.holding_job:
                    self.start_hold_monitor(self.holding_job)
            finally:
                c.close()
        return self.state()

    def busy(self):
        return self.job is not None and self.job['status'] in ('preparing', 'running', 'stopping')

    def recordings(self):
        records = []
        for path in sorted((self.data / 'recordings').glob('*.json'), reverse=True):
            try:
                item = json.loads(path.read_text())
                records.append({k: item[k] for k in ('id', 'name', 'created_at', 'frames')})
            except (ValueError, KeyError):
                continue
        return sorted(records, key=lambda item: item['created_at'], reverse=True)

    def state(self):
        with self.lock:
            if not self.simulate and self.port != 'auto' and not port_exists(self.port):
                self.connected = False
                self.message = '串口设备已断开，请重新连接开发板。'
            job = {k: v for k, v in self.job.items() if not k.startswith('_')} if self.job else None
            record = None
            if self.recording:
                record = {'name': self.recording['name'], 'frames': self.recording['frames'],
                          'elapsed_ms': int((time.monotonic() - self.recording['_started']) * 1000)}
            return copy.deepcopy({'app_id': 'dexterous-hand-ui', 'connected': self.connected, 'simulate': self.simulate, 'port': self.port, 'auto_serial': self.auto_port,
                'message': self.message, 'positions': self.pose, 'desired_positions': self.desired_pose, 'confirmed_at': self.confirmed_at,
                'joints': [{'id': i+1, 'name': n, 'limit': LIMITS[i], **self.calibration[i]} for i, n in enumerate(NAMES)],
                'gestures': GESTURES, 'motion_mode': 'direct', 'job': job, 'busy': self.busy(), 'holding': self.holding, 'recording': record,
                'recordings': self.recordings(), 'recent': self.recent[-12:]})

    def start(self, label, frames):
        frames = validate_frames(frames)
        with self.lock:
            if not self.connected:
                raise ConnectionError('开发板未连接，请先连接。')
            if self.pending and not self.busy():
                raise RuntimeError('前一次动作状态尚未回收，请重新连接。')
            if self.busy():
                raise RuntimeError('正在执行动作，请等待完成或点击停止并张开。')
            if self.recording and len(self.recording['frames']) + len(frames) > 64:
                raise ValueError('录制已达到 64 个姿态，请先保存。')
            if self.recording:
                total = sum(f['duration_ms'] + f['hold_ms'] for f in self.recording['frames'])
                required = sum(f['duration_ms'] + f['hold_ms'] for f in frames)
                if total + required > 300000:
                    raise ValueError('录制已达到五分钟的回放长度，请先保存。')
            jid = uuid.uuid4().hex
            job = {'id': jid, 'label': label, 'frames': frames, 'motion_mode': 'direct', 'status': 'preparing', 'progress': 0,
                   'started_at': now(), 'message': '正在准备板端动作程序…',
                   'board_dir': '/tmp/handui-' + jid,
                   'initial_positions': [min(cap, max(0, p)) for p, cap in zip(self.pose, LIMITS)],
                   '_previous_desired': self.desired_pose.copy(),
                   '_cancel': threading.Event(), '_launched': False}
            self.job = job
            self.desired_pose = frames[-1]['positions'].copy()
            self.pending = {k: v for k, v in job.items() if not k.startswith('_')}
            self.persist()
            if self.recording:
                gap = min(30000, int((time.monotonic() - self.recording['_last_end']) * 1000))
                total = sum(f['duration_ms'] + f['hold_ms'] for f in self.recording['frames'])
                required = sum(f['duration_ms'] + f['hold_ms'] for f in frames)
                # Keep the saved record playable, even after a long idle period.
                gap = min(gap, max(0, 300000 - total - required))
                self.recording['frames'][-1]['hold_ms'] = min(30000, self.recording['frames'][-1]['hold_ms'] + gap)
            threading.Thread(target=self.worker, args=(job,), daemon=True).start()
            return {'job_id': jid}

    def plan(self, job):
        rows = [' '.join(map(str, job['initial_positions'])) + ' 1 0']
        for frame in job['frames']:
            target = frame['positions']
            settle = max(6, math.ceil(frame['duration_ms']/100))
            rows.append(' '.join(map(str, target)) + f" {settle} {math.ceil(frame['hold_ms']/100)}")
        return '\n'.join(rows) + '\n'

    @staticmethod
    def job_state_command(job):
        directory = '/tmp/handui-' + job['id']
        if not re.fullmatch(r'[a-f0-9]{32}', job['id']) or job['board_dir'] != directory:
            raise RuntimeError('动作任务标识无效，未重发动作。')
        pid = job.get('board_pid')
        pid = pid if type(pid) is int and pid > 0 else 0
        return 'sh /tmp/handui-query.sh state ' + job['id'] + ' ' + str(pid)

    def worker(self, job, resume=False):
        folder = self.data / 'sessions' / job['id']
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if resume:
                job['_launched'] = True
            if self.simulate:
                self.simulated(job)
                return
            if not resume:
                with self.io_lock:
                    c = self.console(folder / 'serial.log')
                    try:
                        if not self.runner_installed:
                            self.install_runner(c)
                        plan = self.plan(job)
                        (folder / 'plan.txt').write_text(plan)
                        self.upload(c, job['board_dir'] + '.plan', plan.encode())
                        job['_launched'] = True
                        # The helper owns the background job, so the interactive
                        # serial shell never emits job-completion notices mid-input.
                        result = c.command('sh /tmp/handui-query.sh start ' + job['id'])
                        launched = re.search(r'\n__STARTED__(\d+)\n', result)
                        if not launched:
                            raise RuntimeError('启动结果不确定，请重连回收状态，不会重发。')
                        with self.lock:
                            job['board_pid'] = int(launched[1])
                            self.pending = {k: v for k, v in job.items() if not k.startswith('_')}
                            self.persist()
                    finally:
                        c.close()
            with self.lock:
                job.update(status='running', message='已发送最终目标，等待电机到位并确认反馈。')
            deadline = time.monotonic() + 560
            sent_stop = False
            while time.monotonic() < deadline:
                with self.io_lock:
                    c = self.console(folder / 'serial.log')
                    try:
                        result = c.command(self.job_state_command(job))
                        match = re.search(r'\n__STATE__(running|missing|\d+):(\d+)\n', result)
                        if not match:
                            raise RuntimeError('无法读取板端动作状态，动作不会重发。')
                        if match[1] == 'missing':
                            # A runner may be between exec and writing its result.
                            # Confirm the absence twice before resolving it as failed.
                            time.sleep(.1)
                            result = c.command(self.job_state_command(job))
                            match = re.search(r'\n__STATE__(running|missing|\d+):(\d+)\n', result)
                            if not match:
                                raise RuntimeError('无法确认板端任务状态，动作不会重发。')
                        if match[1] == 'missing':
                            try:
                                console_text = self.fetch(c, job['board_dir'] + '/console.log')
                            except RuntimeError as exc:
                                console_text = '板端进程已退出，启动日志不可回收：' + str(exc)
                            (folder / 'board-console.log').write_text(console_text)
                            self.finish(job, 1, self.pose.copy(), '', feedback_confirmed=False,
                                        error_message='板端任务已退出且未生成执行结果，已解除等待；未自动重发动作。')
                            return
                        if match[1] == 'running' and job['_cancel'].is_set() and not sent_stop:
                            c.command('touch ' + job['board_dir'] + '/stop-request')
                            sent_stop = True
                        job['progress'] = min(95, round(int(match[2]) / max(1, len(job['frames'])) * 95))
                        if match[1] != 'running':
                            code = int(match[1])
                            final = self.fetch(c, job['board_dir'] + '/final.log')
                            feedback_confirmed = True
                            try:
                                pose = decode_feedback(final)
                            except RuntimeError:
                                if code == 0:
                                    raise
                                # An early failure can have partial feedback. It is terminal,
                                # so do not leave it pending or repeatedly replay/recover it.
                                pose = self.pose.copy()
                                feedback_confirmed = False
                            (folder / 'final.log').write_text(final)
                            raw = self.fetch(c, job['board_dir'] + '/can.log', timeout=120)
                            (folder / 'can.log').write_text(raw)
                            console_text = self.fetch(c, job['board_dir'] + '/console.log')
                            (folder / 'board-console.log').write_text(console_text)
                            self.finish(job, code, pose, raw, feedback_confirmed=feedback_confirmed)
                            monitor = self.holding_job if self.holding else None
                            if monitor:
                                self.start_hold_monitor(monitor)
                            return
                    finally:
                        c.close()
                time.sleep(.35)
            raise RuntimeError('板端动作超时，请检查开发板。')
        except Exception as exc:
            with self.lock:
                job.update(status='error', message=str(exc), finished_at=now())
                self.message = str(exc)
                self.connected = False
                # Preserve pending: reconnect collects the existing job, never relaunches it.
                self.pending = {k: v for k, v in job.items() if not k.startswith('_')} if job.get('_launched') else None
                if not job.get('_launched') and '_previous_desired' in job:
                    self.desired_pose = job['_previous_desired'].copy()
                self.persist()
                write_json(folder / 'result.json', {k: v for k, v in job.items() if not k.startswith('_')})
                if not job.get('_launched') and self.holding_job:
                    self.start_hold_monitor(self.holding_job)

    def simulated(self, job):
        job.update(status='running', message='模拟动作，不发送硬件指令。')
        for index, frame in enumerate(job['frames']):
            if job['_cancel'].wait(.25):
                self.finish(job, 130, OPEN.copy(), '')
                return
            job['progress'] = round((index+1)/len(job['frames'])*95)
        self.finish(job, 0, job['frames'][-1]['positions'].copy(), '')

    @staticmethod
    def hold_status(console, hold):
        directory = '/tmp/handui-' + hold['id']
        if not re.fullmatch(r'[a-f0-9]{32}', hold['id']) or hold['board_dir'] != directory:
            raise RuntimeError('保持任务标识无效，未重发动作。')
        output = console.command('sh /tmp/handui-query.sh hold ' + hold['id'])
        block = re.search(r'\n__HOLD_BEGIN__\n(.*?)\n__HOLD_END__\n', output, re.S)
        board_now = re.search(r'\n__BOARD_NOW__(\d+)\n', output)
        if not block or not board_now:
            raise RuntimeError('无法读取持续保持状态；未重发动作。')
        info = dict(line.split('=', 1) for line in block[1].splitlines() if '=' in line)
        if info.get('status') == 'holding':
            age = int(board_now[1]) - float(info.get('updated_at', '0'))
            if '\n__HOLD_ALIVE__\n' not in output or not -1 <= age <= 4:
                raise RuntimeError('保持进程已停止或反馈过期，请检查设备；未自动重发。')
            values = [int(value) for value in info['positions'].split(',')]
            if len(values) != 6 or any(not 0 <= value <= 4095 for value in values):
                raise RuntimeError('保持位置反馈无效；未自动重发。')
            info['positions'] = values
            info['target'] = positions([int(value) for value in info['target'].split(',')])
        return info

    def start_hold_monitor(self, hold):
        with self.lock:
            if self._hold_monitor_job is hold and self._hold_monitor and self._hold_monitor.is_alive():
                return
            self._hold_monitor_job = hold
            self._hold_monitor = threading.Thread(target=self.monitor_holding, args=(hold,), daemon=True)
            self._hold_monitor.start()

    def monitor_holding(self, hold):
        while True:
            with self.lock:
                if self.holding_job is not hold:
                    return
                if self.busy() and self.job['id'] != hold['id']:
                    return
            terminal_error = False
            try:
                folder = self.data / 'sessions' / hold['id']
                folder.mkdir(parents=True, exist_ok=True)
                with self.io_lock:
                    with self.lock:
                        if self.holding_job is not hold:
                            return
                    c = self.console(folder / 'holding-serial.log')
                    try:
                        info = self.hold_status(c, hold)
                    finally:
                        c.close()
                with self.lock:
                    if self.holding_job is not hold:
                        return
                    status = info.get('status')
                    if status == 'holding':
                        self.holding = True
                        self.pose = info['positions']
                        self.confirmed_at = now()
                        if not self.busy():
                            self.desired_pose = info['target'].copy()
                            self.message = '已应用，正在保持；点击自然张开或停止并张开可释放。'
                    elif status in ('released', 'handover'):
                        self.holding = False
                        self.holding_job = None
                        if not self.busy():
                            self.message = '已张开。' if status == 'released' else '保持已交接给下一次操作。'
                        self.persist()
                        return
                    else:
                        terminal_error = True
                        raise RuntimeError(info.get('message') or '持续保持异常，已停止刷新，请检查设备。')
            except Exception as exc:
                with self.lock:
                    if self.holding_job is not hold:
                        return
                    if self.busy() and self.job['id'] != hold['id']:
                        return
                    self.holding = False
                    if terminal_error:
                        self.holding_job = None
                    self.connected = False
                    self.message = str(exc)
                    self.persist()
                return
            time.sleep(1)

    def finish(self, job, code, pose, raw, feedback_confirmed=True, error_message=None):
        with self.lock:
            self.pose = pose
            self.desired_pose = (job['frames'][-1]['positions'] if code == 0 else OPEN).copy()
            if not feedback_confirmed:
                self.desired_pose = job.get('_previous_desired', self.pose).copy()
            if feedback_confirmed:
                self.confirmed_at = now()
            self.pending = None
            self.holding = code == 0 and job['frames'][-1]['positions'] != OPEN
            if code == 0:
                self.holding_job = {'id': job['id'], 'board_dir': job['board_dir']} if self.holding and not self.simulate else None
            elif code == 130:
                self.holding_job = None
            else:
                self.connected = False
            state = 'done' if code == 0 else ('stopped' if code == 130 else 'error')
            text = ('已应用，正在保持；点击自然张开或停止并张开可释放。' if self.holding else '动作完成，已收到六个关节位置反馈。') if code == 0 else ('已停止动作并张开。' if code == 130 else '动作异常，请查看反馈。')
            if error_message:
                text = error_message
            if self.simulate:
                text = '模拟动作完成。' if code == 0 else '模拟动作已停止并张开。'
            if not feedback_confirmed:
                text += '未获得完整的新鲜反馈，位置仍为上次确认值。'
            job.update(status=state, progress=100, message=text, finished_at=now(), final_positions=pose, exit_code=code)
            job['feedback_confirmed'] = feedback_confirmed
            expected = OPEN if code != 0 else job['frames'][-1]['positions']
            job['max_position_error_raw'] = max(abs(a-b) for a, b in zip(pose, expected))
            if code == 130 and job['max_position_error_raw'] > 80:
                job.update(status='error', message='动作已停止，张开位置存在明显偏差，请查看反馈。')
                text = job['message']
            job['feedback_frames'] = len(re.findall(r'can0\s+10[1-6]#', raw))
            self.message = text
            if self.recording and code == 0:
                self.recording['frames'].extend(copy.deepcopy(job['frames']))
                self.recording['_last_end'] = time.monotonic()
            result = {k: v for k, v in job.items() if not k.startswith('_')}
            self.recent = [item for item in self.recent if item['id'] != job['id']]
            self.recent.append(result)
            self.persist()
            write_json(self.data / 'sessions' / job['id'] / 'result.json', result)

    def stop(self):
        with self.lock:
            if self.busy():
                self.job['_cancel'].set()
                self.job.update(status='stopping', message='正在请求板端停止并张开…')
                return {'stopping': True}
        return self.start('停止并张开', [{'positions': OPEN, 'duration_ms': 1200}])

    def record_start(self, name):
        with self.lock:
            if not self.connected or self.busy() or self.recording:
                raise RuntimeError('请连接开发板，并等待当前动作或录制结束。')
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
                raise ValueError('动作名称需要 1–40 个字符。')
            t = time.monotonic()
            self.recording = {'id': uuid.uuid4().hex, 'name': name.strip(), 'created_at': now(),
                'frames': [{'positions': self.pose.copy(), 'duration_ms': 600, 'hold_ms': 0}],
                'calibration': copy.deepcopy(self.calibration), 'motion_mode': 'direct', 'simulate': self.simulate, '_started': t, '_last_end': t}

    def record_save(self):
        with self.lock:
            if self.busy() or not self.recording:
                raise RuntimeError('请等待动作完成后保存录制。')
            record = {k: v for k, v in self.recording.items() if not k.startswith('_')}
            validate_frames(record['frames'])
            write_json(self.data / 'recordings' / (record['id'] + '.json'), record)
            self.recording = None
            return record

    def record(self, rid):
        if not re.fullmatch(r'[a-f0-9]{32}', rid):
            raise ValueError('动作编号无效。')
        path = self.data / 'recordings' / (rid + '.json')
        if not path.exists():
            raise FileNotFoundError('动作记录不存在。')
        return json.loads(path.read_text())

    def calibrate(self, values):
        if self.busy() or self.recording:
            raise RuntimeError('动作或录制期间不能修改角度映射。')
        if not isinstance(values, list) or len(values) != 6:
            raise ValueError('需要六个关节的映射设置。')
        result = []
        for i, item in enumerate(values):
            if not isinstance(item, dict):
                raise ValueError('标定格式无效。')
            a = integer(item.get('zero_raw'), 0, LIMITS[i], '零度位置')
            b = integer(item.get('end_raw'), 0, LIMITS[i], '末端位置')
            angle = item.get('end_angle')
            if type(angle) not in (int, float) or not math.isfinite(angle) or not 1 <= angle <= 180 or a == b:
                raise ValueError('末端角度应为 1–180°，两个原始位置必须不同。')
            if type(item.get('calibrated', False)) is not bool:
                raise ValueError('实测标记必须是布尔值。')
            result.append({'zero_raw': a, 'end_raw': b, 'end_angle': float(angle), 'calibrated': item.get('calibrated', False)})
        with self.lock:
            self.calibration = result
            write_json(self.data / 'calibration.json', result)


def create_app(manager=None):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 128 * 1024
    manager = manager or Manager()
    app.config['MANAGER'] = manager
    token = secrets.token_urlsafe(32)

    @app.before_request
    def protect():
        if request.method == 'POST' and not secrets.compare_digest(request.headers.get('X-Control-Token', ''), token):
            return jsonify(error='请从本地控制页面执行操作。'), 403

    @app.after_request
    def no_cache(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'"
        return response

    @app.errorhandler(Exception)
    def error(exc):
        if not request.path.startswith('/api/'):
            raise exc
        status = 400 if isinstance(exc, (ValueError, TypeError)) else (404 if isinstance(exc, FileNotFoundError) else 409)
        if isinstance(exc, (OSError, ConnectionError)):
            manager.connected = False
            manager.message = str(exc)
            status = 503
        return jsonify(error=str(exc)), status

    def body():
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise ValueError('需要 JSON 对象。')
        return value

    @app.get('/')
    def index():
        return render_template('index.html', control_token=token)

    @app.get('/api/state')
    def state():
        return jsonify(manager.state())

    @app.post('/api/connect')
    def connect():
        return jsonify(manager.connect())

    @app.post('/api/joint')
    def joint():
        data = body()
        node = integer(data.get('id'), 1, 6, '关节编号') - 1
        cal = manager.calibration[node]
        angle = data.get('angle')
        if type(angle) not in (int, float) or not math.isfinite(angle) or not 0 <= angle <= cal['end_angle']:
            raise ValueError('角度超出当前映射范围。')
        pose = manager.desired_pose.copy()
        pose[node] = round(cal['zero_raw'] + angle / cal['end_angle'] * (cal['end_raw']-cal['zero_raw']))
        return jsonify(manager.start(NAMES[node] + '独立控制', [{'positions': pose, 'duration_ms': data.get('duration_ms', 1200)}]))

    @app.post('/api/gesture')
    def gesture():
        data = body()
        gid = data.get('id')
        if gid == 'grasp-cycle':
            frames = [{'positions': GESTURES[1]['positions'], 'duration_ms': 2400, 'hold_ms': 2000},
                      {'positions': OPEN, 'duration_ms': 2400}]
            return jsonify(manager.start('抓握 → 保持 → 张开', frames))
        item = next((g for g in GESTURES if g['id'] == gid), None)
        if not item:
            raise ValueError('手势不存在。')
        return jsonify(manager.start(item['name'], [{'positions': item['positions'], 'duration_ms': data.get('duration_ms', 1600)}]))

    @app.post('/api/stop')
    def stop():
        return jsonify(manager.stop())

    @app.post('/api/calibration')
    def calibration():
        manager.calibrate(body().get('joints'))
        return jsonify(ok=True)

    @app.post('/api/recordings/start')
    def recording_start():
        manager.record_start(body().get('name'))
        return jsonify(ok=True)

    @app.post('/api/recordings/save')
    def recording_save():
        return jsonify(manager.record_save())

    @app.post('/api/recordings/<rid>/play')
    def recording_play(rid):
        record = manager.record(rid)
        if record.get('simulate') and not manager.simulate:
            raise ValueError('模拟记录不能直接在真实设备上回放。')
        return jsonify(manager.start('回放：' + record['name'], record['frames']))

    @app.get('/api/recordings/<rid>/export')
    def export(rid):
        record = manager.record(rid)
        response = Response(json.dumps(record, ensure_ascii=False, indent=2), mimetype='application/json')
        response.headers['Content-Disposition'] = f'attachment; filename="action-{rid[:8]}.json"'
        return response

    @app.get('/api/recordings/<rid>/markdown')
    def markdown(rid):
        record = manager.record(rid)
        safe_name = record['name'].replace('\n', ' ').replace('\r', ' ')
        lines = ['# 动作记录：' + safe_name, '', '记录时间：' + record['created_at'], '',
                 '位置顺序：拇指侧摆、拇指弯曲、食指、中指、无名指、小指。单位为驱动器原始位置。', '',
                 '模式：' + ('模拟，未发送硬件指令。' if record.get('simulate') else '真实开发板控制。'), '',
                 '运动方式：持续刷新每个姿态的最终位置，由电机控制器连续运动到位，正常完成后保持最后姿态直到下一次操作。', '',
                 '| 序号 | 六关节位置 | 到位等待（ms） | 停留时间（ms） |', '| --- | --- | ---: | ---: |']
        for i, f in enumerate(record['frames']):
            lines.append(f"| {i+1} | `{f['positions']}` | {f['duration_ms']} | {f['hold_ms']} |")
        lines += ['', 'duration_ms 保留旧记录字段名，当前回放将其用作到位等待时间，不再插值发送中间位置。正常完成后持续保持最后目标；若序列最后为自然张开，则完成后释放保持。', '', '角度映射快照：', '', '```json', json.dumps(record['calibration'], ensure_ascii=False, indent=2), '```', '']
        response = Response('\n'.join(lines), mimetype='text/markdown; charset=utf-8')
        response.headers['Content-Disposition'] = f'attachment; filename="action-{rid[:8]}.md"'
        return response

    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--serial', default='auto')
    parser.add_argument('--simulate', action='store_true')
    args = parser.parse_args()
    data_dir = ROOT / ('data-simulation' if args.simulate else 'data')
    app = create_app(Manager(data_dir=data_dir, port=args.serial, simulate=args.simulate))
    app.run(host='127.0.0.1', port=args.port, threaded=True, use_reloader=False)
