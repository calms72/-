"""Fixed paper-box preset. Preview is offline; prepare/grasp need --run.

prepare: empty hand only. grasp: supported box, hands outside the closing gap.
Stop cancels later commands; it does not cancel a target already in the drive.
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRASP = (2000, 1562, 1710, 1709, 1101, 1008)
READY = (2000, 612, 0, 0, 0, 0)
ORDER = (3, 4, 5, 6, 2)
LIMITS = (2000, 4095, 4095, 4095, 4095, 4095)
TOLERANCE = 30
DRIFT = 20
DEADLINE = 50
CAPTURE = 60
FRAME = re.compile(r'^\(([0-9]+\.[0-9]+)\)\s+can0\s+([0-9A-Fa-f]{3})#([0-9A-Fa-f]+)\s*$', re.M)


class GuardError(RuntimeError):
    pass


def verify_preset():
    source = Path(__file__).with_name('纸盒抓取预设.json')
    profile = json.loads(source.read_text(encoding='utf-8-sig'))
    if profile.get('GRASP') != list(GRASP) or profile.get('READY') != list(READY) or profile.get('command_order') != list(ORDER):
        raise GuardError('预设文件与已认可的固定目标不一致，未发送动作。')
    return profile


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def frames(text, strict=False):
    result = []
    for line in text.replace('\r', '').splitlines():
        match = FRAME.fullmatch(line)
        if not match:
            if (strict and line.strip()) or re.match(r'^\([0-9]', line):
                raise GuardError('无法识别完整CAN记录。')
            continue
        node, payload = int(match[2], 16), bytes.fromhex(match[3])
        if node not in (*range(1, 7), *range(0x101, 0x107)) or not 1 <= len(payload) <= 8:
            raise GuardError('总线上出现未知节点或数据长度。')
        result.append((float(match[1]), node, payload))
    if strict and text and not text.endswith('\n'):
        raise GuardError('CAN记录末行不完整。')
    return result


def decode(node, data):
    if node not in range(0x101, 0x107) or len(data) != 8 or data[0] not in (0xA0, 0xAA):
        raise GuardError('收到非完整状态回复，不能作为正常状态使用。')
    motor = node - 0x100
    velocity = (data[3] >> 4) | (data[4] << 4)
    return {'node': motor, 'position': data[2] | ((data[3] & 15) << 8), 'status': data[1],
            'velocity': velocity if velocity < 2048 else velocity - 4096, 'raw': data.hex().upper()}


def check_states(states, reference=None, selected=None, moving=False):
    for state in states:
        index = state['node'] - 1
        if state['status'] or not 0 <= state['position'] <= LIMITS[index]:
            raise GuardError(f"第{index+1}路故障或位置越界：{state['status']}。")
        if state['velocity'] and not (moving and state['node'] == selected):
            raise GuardError('机构未静止或非目标轴在运动。')
        if reference is not None and state['node'] != selected and abs(state['position'] - reference[index]) > DRIFT:
            raise GuardError('非目标轴位置发生异常偏移。')


def snapshot(records, allow_fault=False):
    requests = [(node, data) for _, node, data in records if node <= 6]
    if requests != [(node, b'\xa0') for node in range(1, 7)]:
        raise GuardError('六路查询顺序不符或存在其他控制来源。')
    for _, node, data in records:
        if node >= 0x101:
            item = decode(node, data)
            if not allow_fault and (item['status'] or item['position'] > LIMITS[item['node'] - 1]):
                raise GuardError('查询窗口中出现故障或越界状态，未继续动作。')
    result = []
    for node in range(1, 7):
        sent = [stamp for stamp, ident, data in records if ident == node and data == b'\xa0']
        got = [(stamp, data) for stamp, ident, data in records if ident == node + 0x100 and len(data) == 8 and data[0] == 0xA0]
        if len(sent) != 1 or len(got) != 1 or not 0 <= got[0][0] - sent[0] <= .5:
            raise GuardError('状态回复缺失、重复或过期。')
        result.append(decode(node + 0x100, got[0][1]))
    return result


def console_factory(port, log):
    source = ROOT / '灵巧手网页控制台_Windows运行版' / 'web' / 'serial_console_windows.py'
    spec = importlib.util.spec_from_file_location('_paperbox_console', source)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module.Console(port, log)


class PresetRun:
    def __init__(self, action, state_dir, *, port='COM12', factory=console_factory, clock=time.monotonic, sleep=time.sleep):
        if action not in ('status', 'prepare', 'grasp') or not re.fullmatch(r'COM[1-9][0-9]*', port):
            raise ValueError('无效动作或串口。')
        self.action, self.state_dir, self.port = action, Path(state_dir).resolve(), port
        self.factory, self.clock, self.sleep = factory, clock, sleep
        self.tag = datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8]
        self.out = self.state_dir / self.tag
        self.active = self.state_dir / 'active.json'
        self.ready_file = self.state_dir / 'ready.json'
        self.stop_file = self.state_dir / 'STOP'
        self.remote = '/tmp/paperbox-preset-' + self.tag
        self.c = None; self.captured = False; self.capture_start = None; self.commands = []; self.counter = 0
        self.r = {'action': action, 'run_id': self.tag, 'attempts': [], 'queries': [],
                  'target': list(READY if action == 'prepare' else GRASP),
                  'fault_clear_sent': False, 'automatic_open_or_retry': False,
                  'single_final_target_per_axis': True, 'physical_grip_verified': False,
                  'raw_velocity_setting': 2536, 'raw_current_limit_setting': 868,
                  'low_speed_or_grip_force_calibration_claimed': False}

    def save(self):
        atomic_json(self.out / 'result.json', self.r)

    def check(self):
        if self.clock() - self.started >= DEADLINE or (self.action != 'status' and self.stop_file.exists()):
            raise GuardError('已停止后续指令，或达到运行时限。')

    def command(self, query, label, *, timeout=8, fresh=None):
        self.check(); self.counter += 1
        marker = '__PB_RC_' + uuid.uuid4().hex + '__'
        wire = query + '; pb_rc=$?; printf "\\n' + marker + '=%s\\n" "$pb_rc"'
        if len(wire) > 800 or '\n' in wire or '\r' in wire:
            raise GuardError('控制台命令超出范围。')
        stem = self.out / f'{self.counter:03d}-{label}'
        stem.with_suffix('.request.txt').write_text(query, encoding='utf-8')
        self.check()
        if fresh is not None and (not 0 <= self.clock() - fresh <= 2.5 or
                self.capture_start is None or self.clock() - self.capture_start >= 40):
            raise GuardError('状态或监听已过期，未发送该动作。')
        output = self.c.command(wire, timeout=min(timeout, DEADLINE - (self.clock() - self.started)))
        stem.with_suffix('.txt').write_text(output, encoding='utf-8')
        if re.findall(r'(?m)^' + marker + r'=([0-9]+)$', output) != ['0']:
            raise GuardError('串口命令结果未知或失败；不会重发动作。')
        self.check(); return output

    def identify(self):
        output = self.command('hostname; cat /proc/device-tree/model; printf "\\n"; cat /proc/sys/kernel/random/boot_id; '
                              'if pidof codesyscontrol; then echo PB_PLC_ACTIVE; fi; ip -details link show can0; '
                              'ps | grep -E "canplayer|hold_grip|contact_grip|handui-motion|index_follow|recovery_hold" | grep -v grep || true', 'identity')
        boot = re.findall(r'(?m)^([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$', output)
        if len(boot) != 1 or '\nmyd-ld25x-rt\n' not in output or 'MYiR PLC STM32MP257x Evaluation Board 2' not in output:
            raise GuardError('不是已确认的开发板。')
        if 'LOWER_UP' not in output or 'bitrate 1000000' not in output or 'mtu 16' not in output:
            raise GuardError('CAN连接未就绪。')
        if self.action != 'status' and (re.search(r'(?m)^PB_PLC_ACTIVE$', output) or
                re.search(r'(?m)^\s*\d+\s+\S+\s+.*\b(?:canplayer|hold_grip|contact_grip|handui-motion|index_follow|recovery_hold)\b', output)):
            raise GuardError('存在其他控制程序，未发送动作。')
        self.boot = boot[0]; self.r['boot_id'] = self.boot

    def read_all(self, label, reference=None, selected=None, moving=False):
        if self.captured:
            self.commands.extend((node, b'\xa0') for node in range(1, 7))
        began = self.clock()
        output = self.command('timeout 1 candump -L can0 & pb_pid=$!; sleep 0.15; pb_send=0; '
                              'for pb_node in 001 002 003 004 005 006; do cansend can0 "$pb_node#A0" || { pb_send=1; break; }; sleep 0.05; done; '
                              'wait "$pb_pid"; pb_listen=$?; test "$pb_send" = 0 && test "$pb_listen" = 124', label)
        records = frames(output)
        states = snapshot(records, allow_fault=self.action == 'status')
        if self.action != 'status':
            check_states([decode(node, data) for _, node, data in records if node >= 0x101], reference, selected, moving)
        self.r['queries'].append({'label': label, 'states': states}); self.save()
        return states, began

    def read_new_bus(self, reference, selected=None):
        # References change only after a commanded axis passes acceptance.
        # Old frames were audited under the previous reference; keep an offset.
        output = self.command('test "$(wc -l < ' + self.remote + '/can.log)" -le 1000 && cat ' + self.remote + '/can.log', 'bus-audit')
        records = frames(output)
        if [(node, data) for _, node, data in records if node <= 6] != self.commands:
            raise GuardError('发现其他请求或动作，停止后续指令。')
        if len(records) < self.audited:
            raise GuardError('CAN记录发生截断。')
        check_states([decode(node, data) for _, node, data in records[self.audited:] if node >= 0x101], reference, selected, moving=True)
        self.audited = len(records)

    def release_active(self):
        if json.loads(self.active.read_text(encoding='utf-8')).get('run_id') != self.tag:
            raise GuardError('运行标记所有者改变。')
        self.active.unlink()

    def run(self):
        self.state_dir.mkdir(parents=True, exist_ok=True); self.out.mkdir(exist_ok=False)
        self.started = self.clock(); owns_active = False
        try:
            if self.action != 'status':
                with self.active.open('x', encoding='utf-8') as stream:
                    json.dump({'run_id': self.tag, 'record': str(self.out)}, stream)
                    stream.flush(); os.fsync(stream.fileno())
                owns_active = True
            self.save(); self.check()
            self.c = self.factory(self.port, self.out / 'serial.log'); self.c.login(); self.identify()
            initial, _ = self.read_all('initial')
            if self.action == 'status':
                self.r.update({'result': 'status_read', 'states': initial}); self.save(); return self.r
            check_states(initial)
            if abs(initial[0]['position'] - GRASP[0]) > DRIFT:
                raise GuardError('拇指侧摆尚未就位；本程序不旋转侧摆，请先恢复预设。')
            if self.action == 'grasp':
                ready = json.loads(self.ready_file.read_text(encoding='utf-8'))
                if ready.get('state') != 'ready' or ready.get('boot_id') != self.boot or ready.get('target') != list(READY):
                    raise GuardError('没有本次启动的有效摆放准备记录。')
                if any(abs(item['position'] - goal) > TOLERANCE for item, goal in zip(initial, READY)):
                    raise GuardError('手指已离开摆放准备姿态。')
            atomic_json(self.ready_file, {'state': self.action + '_in_progress', 'boot_id': self.boot, 'run_id': self.tag})
            passive = frames(self.command('timeout 1 candump -L can0; pb_rc=$?; test "$pb_rc" = 124', 'passive'))
            if passive:
                raise GuardError('空闲CAN总线仍有数据，未开始预设动作。')
            self.command('umask 077; test ! -e ' + self.remote + ' && mkdir ' + self.remote +
                         ' && (timeout 60 candump -L can0 > ' + self.remote + '/can.log & echo $! > ' + self.remote + '/pid)', 'capture-start')
            self.captured = True; self.capture_start = self.clock(); self.audited = 0; self.sleep(.15)
            reference = [item['position'] for item in initial]
            goal_pose = READY if self.action == 'prepare' else GRASP
            for node in ORDER:
                self.read_new_bus(reference)
                states, began = self.read_all('before-' + str(node), reference)
                self.read_new_bus(reference)
                if abs(states[node - 1]['position'] - goal_pose[node - 1]) <= TOLERANCE:
                    self.read_new_bus(reference); continue
                target = goal_pose[node - 1]
                payload = bytes((0xAA, target & 255, target >> 8, 0xE8, 0x09, 0x64, 0x03))
                intent = {'node': node, 'target': target, 'payload': payload.hex().upper(), 'before': states[node - 1]}
                self.r['attempts'].append(intent); self.save()
                self.commands.append((node, payload))
                self.command('test "$(cat /proc/sys/kernel/random/boot_id)" = ' + self.boot +
                             ' && ! pidof codesyscontrol >/dev/null && '
                             'pb_cap_pid=$(cat ' + self.remote + '/pid) && '
                             'test "$(tr "\\000" " " </proc/$pb_cap_pid/cmdline)" = "timeout 60 candump -L can0 " && '
                             'cansend can0 ' + f'{node:03X}#' + payload.hex().upper(), 'motion-' + str(node), fresh=began)
                self.sleep(.25); settle_deadline = self.clock() + 4
                while True:
                    after, _ = self.read_all('after-' + str(node), reference, node, moving=True)
                    self.read_new_bus(reference, node)
                    selected = after[node - 1]
                    if selected['velocity'] == 0:
                        if abs(selected['position'] - target) > TOLERANCE:
                            raise GuardError('目标轴停在预设以外，可能接触或受阻；不补发、不加力。')
                        break
                    if self.clock() >= settle_deadline:
                        raise GuardError('目标轴未及时到位，不重发动作。')
                    self.sleep(.1)
                reference[node - 1] = selected['position']
                intent['after'] = after; self.save()
                print(json.dumps({'axis': node, 'target': target, 'actual': selected['position']}, ensure_ascii=False), flush=True)
            final, _ = self.read_all('final', reference)
            if any(abs(item['position'] - goal) > TOLERANCE for item, goal in zip(final, goal_pose)):
                raise GuardError('整组六路未达到预设范围。')
            self.read_new_bus(reference)
            self.command('pb_pid=$(cat ' + self.remote + '/pid); '
                         'test "$(tr "\\000" " " </proc/$pb_pid/cmdline)" = "timeout 60 candump -L can0 " && kill -TERM "$pb_pid"', 'capture-stop')
            fetched = self.command('printf "\\n__PB_RAW_BEGIN__\\n"; base64 ' + self.remote + '/can.log; printf "\\n__PB_RAW_END__\\n"', 'capture-fetch', timeout=15)
            match = re.search(r'\n__PB_RAW_BEGIN__\n([A-Za-z0-9+/=\s]*)\n__PB_RAW_END__\n', fetched)
            if not match:
                raise GuardError('CAN记录传输不完整。')
            raw = base64.b64decode(re.sub(r'\s', '', match[1]), validate=True).decode('ascii')
            records = frames(raw, strict=True)
            if [(node, data) for _, node, data in records if node <= 6] != self.commands or len(records) != self.audited:
                raise GuardError('最终总线记录与执行请求不符。')
            (self.out / 'can.log').write_text(raw, encoding='ascii')
            positions = [item['position'] for item in final]
            self.r.update({'result': 'ready' if self.action == 'prepare' else 'preset_reached_grip_unverified',
                           'positions': positions, 'finished_at': datetime.now().astimezone().isoformat()})
            self.save()
            atomic_json(self.ready_file, {'state': 'ready' if self.action == 'prepare' else 'consumed',
                        'boot_id': self.boot, 'run_id': self.tag, 'target': list(goal_pose), 'positions': positions})
            self.release_active(); owns_active = False
            return self.r
        except BaseException as error:
            self.r.update({'result': 'stopped_no_retry_no_automatic_open', 'error': repr(error)})
            try: self.save()
            except Exception: pass
            if owns_active and not self.r['attempts']:
                try: self.release_active()
                except Exception: pass
            raise
        finally:
            if self.c is not None:
                self.c.close()


def main(argv=None, factory=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', choices=('preview', 'status', 'prepare', 'grasp', 'stop', 'resume'), default='preview')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--port', default='COM12')
    parser.add_argument('--state-dir', type=Path, default=ROOT/'运行记录'/'纸盒预设抓取')
    args = parser.parse_args(argv)
    if args.action not in ('stop', 'resume'):
        try:
            verify_preset()
        except (OSError, ValueError, GuardError) as error:
            print('未继续动作：' + str(error), file=sys.stderr); return 1
    if args.action == 'preview' or (args.action in ('prepare', 'grasp') and not args.run):
        print(json.dumps({'mode': 'offline_preview', 'ready': READY, 'grasp': GRASP, 'axis_order': ORDER,
                          'side_swing_is_held_not_commanded': True, 'opens_serial': False}, ensure_ascii=False, indent=2)); return 0
    try:
        if args.action in ('stop', 'resume'):
            args.state_dir.mkdir(parents=True, exist_ok=True)
            stop = args.state_dir/'STOP'
            if args.action == 'stop':
                stop.write_text('stop later commands; drive targets may remain active\n', encoding='utf-8')
                print('已请求停止后续指令；已发送的电机目标可能仍在执行，需要时立即关闭总电源。')
            else:
                if (args.state_dir/'active.json').exists():
                    raise GuardError('仍有运行或结果待核实，不能解除停止。')
                stop.unlink(missing_ok=True); print('已解除后续指令停止标记。')
            return 0
        runner = PresetRun(args.action, args.state_dir, port=args.port, factory=factory or console_factory)
        result = runner.run()
        print(json.dumps({'result': result['result'], 'positions': result.get('positions'),
                          'states': result.get('states'), 'record': str(runner.out/'result.json')}, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print('未继续动作：' + str(error), file=sys.stderr); return 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
