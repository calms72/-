"""准备姿态 -> 一批固定抓取目标 -> A0验收。默认仅预览。"""
import argparse
import json
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import paperbox_grasp as protocol

ROOT = Path(__file__).resolve().parents[1]
READY, GRASP = protocol.READY, protocol.GRASP
ORDER = (3, 4, 5, 6, 2)
QUERY = ('timeout 1 candump -L can0 & bg_pid=$!; sleep .15; bg_ok=0; '
         'for bg_node in 001 002 003 004 005 006; do cansend can0 "$bg_node#A0" || { bg_ok=1; break; }; sleep .05; done; '
         'wait "$bg_pid"; bg_rc=$?; test "$bg_ok" = 0 && test "$bg_rc" = 124')


def batch_command(targets):
    packets = [f'{node:03X}#' + bytes((0xAA, targets[node-1] & 255, targets[node-1] >> 8,
                                      0xE8, 0x09, 0x64, 0x03)).hex().upper() for node in ORDER]
    return ('timeout 3 candump -L can0 & bg_pid=$!; sleep .15; bg_ok=0; '
            'for bg_frame in ' + ' '.join(packets) + '; do '
            'cansend can0 "$bg_frame" || { bg_ok=1; break; }; sleep .08; done; '
            'if test "$bg_ok" = 0; then sleep .25; '
            'for bg_node in 001 002 003 004 005 006; do cansend can0 "$bg_node#A0" || { bg_ok=1; break; }; sleep .05; done; fi; '
            'wait "$bg_pid"; bg_rc=$?; test "$bg_ok" = 0 && test "$bg_rc" = 124'), packets


def run(action, state_dir=None, *, port='COM12', factory=protocol.console_factory,
        clock=time.monotonic, sleep=time.sleep):
    if action not in ('prepare', 'grasp', 'status'):
        raise ValueError('无效动作。')
    protocol.verify_preset()
    state_dir = Path(state_dir or ROOT/'运行记录'/'纸盒预设抓取')
    output = state_dir / ('simple-' + datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8])
    output.mkdir(parents=True, exist_ok=False)
    report = {'action': action, 'attempted_batches': 0, 'queries': [], 'automatic_retry_or_open': False,
              'raw_velocity': 2536, 'raw_current_limit': 868, 'grip_force_calibrated': False,
              'actual_box_grip_verified': False, 'record': str(output/'result.json')}
    started = clock(); console = None; counter = 0

    def save():
        protocol.atomic_json(output/'result.json', report)

    def check():
        if clock()-started >= 20 or (action != 'status' and (state_dir/'STOP').exists()):
            raise protocol.GuardError('已取消后续指令或达到运行时限。')

    def command(body, label, timeout=8, fresh=None):
        nonlocal counter
        check(); counter += 1
        path = output/f'{counter:02d}-{label}'
        path.with_suffix('.request.txt').write_text(body, encoding='utf-8')
        if fresh is not None and not 0 <= clock()-fresh <= 2.5:
            raise protocol.GuardError('起始状态已过期，未提交抓取批次。')
        check()
        text = console.command(body + '; bg_result=$?; printf "\\n__PB_SIMPLE_RC__%s\\n" "$bg_result"', timeout=timeout)
        path.with_suffix('.txt').write_text(text, encoding='utf-8')
        if re.findall(r'(?m)^__PB_SIMPLE_RC__([0-9]+)$', text) != ['0']:
            raise protocol.GuardError('命令失败或结果未知；不重发。')
        check(); return text

    def read_states(label):
        began = clock()
        records = protocol.frames(command(QUERY, label))
        states = protocol.snapshot(records, allow_fault=action == 'status')
        if action != 'status' and label == 'before':
            protocol.check_states([protocol.decode(node, data) for _, node, data in records if node >= 0x101])
        report['queries'].append({'label': label, 'states': states}); save()
        return states, began

    def accepted(states, targets):
        # Motion may still be finishing. Faults and position limits always stop.
        for item in states:
            if item['status'] or item['position'] > protocol.LIMITS[item['node']-1]:
                raise protocol.GuardError('电机故障或位置越界，未继续动作。')
        if any(item['velocity'] for item in states):
            return False
        if any(abs(item['position']-goal) > 30 for item, goal in zip(states, targets)):
            raise protocol.GuardError('停在预设位置以外，可能受阻；不补发、不加力。')
        return True

    try:
        save(); check()
        console = factory(port, output/'serial.log'); console.login()
        identity = command('hostname; cat /proc/device-tree/model; printf "\\n"; cat /proc/sys/kernel/random/boot_id; '
                           'if pidof codesyscontrol; then echo SIMPLE_PLC_ACTIVE; fi; ip -details link show can0; '
                           'ps | grep -E "canplayer|hold_grip|contact_grip|handui-motion|index_follow|recovery_hold" | grep -v grep || true', 'identity')
        boots = re.findall(r'(?m)^([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$', identity)
        if len(boots) != 1 or '\nmyd-ld25x-rt\n' not in identity or 'MYiR PLC STM32MP257x Evaluation Board 2' not in identity:
            raise protocol.GuardError('开发板身份不符。')
        if 'LOWER_UP' not in identity or 'bitrate 1000000' not in identity or 'mtu 16' not in identity:
            raise protocol.GuardError('CAN未就绪。')
        if action != 'status' and (re.search(r'(?m)^SIMPLE_PLC_ACTIVE$', identity) or
                re.search(r'(?m)^\s*\d+\s+\S+\s+.*\b(?:canplayer|hold_grip|contact_grip|handui-motion|index_follow|recovery_hold)\b', identity)):
            raise protocol.GuardError('其他控制程序正在运行。')
        report['boot_id'] = boots[0]
        if action == 'status':
            states, _ = read_states('status')
            report.update({'result': 'status_read', 'states': states}); save(); return report
        if protocol.frames(command('timeout 1 candump -L can0; bg_rc=$?; test "$bg_rc" = 124', 'passive')):
            raise protocol.GuardError('总线仍有其他数据，未提交动作。')
        states, began = read_states('before')
        protocol.check_states(states)
        if abs(states[0]['position']-2000) > 20:
            raise protocol.GuardError('侧摆尚未就位；先空手恢复侧摆，本抓取程序不旋转侧摆。')
        if action == 'grasp' and any(abs(item['position']-goal)>30 for item, goal in zip(states, READY)):
            raise protocol.GuardError('起点不是准备姿态，未重复执行抓取。')
        targets = READY if action == 'prepare' else GRASP
        body, packets = batch_command(targets)
        report.update({'attempted_batches': 1, 'packets': packets, 'target': list(targets)})
        save()
        guarded = 'test "$(cat /proc/sys/kernel/random/boot_id)" = ' + boots[0] + ' && ! pidof codesyscontrol >/dev/null && { ' + body + '; }'
        text = command(guarded, 'one-batch', fresh=began)
        records = protocol.frames(text)
        expected = [(int(packet.split('#')[0], 16), bytes.fromhex(packet.split('#')[1])) for packet in packets]
        expected += [(node, b'\xa0') for node in range(1, 7)]
        if [(node, data) for _, node, data in records if node <= 6] != expected:
            raise protocol.GuardError('批次记录不完整或出现其他控制指令；不重发。')
        # Keep every MotorInfo, including AA faults; filter only outgoing AA requests.
        states = protocol.snapshot([item for item in records if item[1] >= 0x101 or item[2] == b'\xa0'])
        settle_deadline = clock()+4
        while not accepted(states, targets):
            if clock() >= settle_deadline:
                raise protocol.GuardError('到位超时，不重发预设。')
            sleep(.1); states, _ = read_states('settle')
        report.update({'result': 'ready' if action == 'prepare' else 'preset_reached_grip_unverified',
                       'positions': [item['position'] for item in states]})
        save(); return report
    except BaseException as error:
        report.update({'result': 'stopped_without_retry_or_automatic_open', 'error': repr(error)})
        save(); raise
    finally:
        if console is not None:
            console.close()


def main(argv=None, factory=protocol.console_factory):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='preview', choices=('preview', 'status', 'prepare', 'grasp', 'stop', 'resume'))
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--state-dir', type=Path, default=ROOT/'运行记录'/'纸盒预设抓取')
    parser.add_argument('--port', default='COM12')
    args = parser.parse_args(argv)
    if args.action == 'preview' or (args.action in ('prepare','grasp') and not args.run):
        print(json.dumps({'mode': 'offline_preview', 'ready': READY, 'grasp': GRASP,
                          'logic': '起点检查 -> 一次发送整组固定目标 -> A0到位检查', 'opens_serial': False}, ensure_ascii=False, indent=2)); return 0
    try:
        if args.action in ('stop', 'resume'):
            args.state_dir.mkdir(parents=True, exist_ok=True)
            if args.action == 'stop':
                (args.state_dir/'STOP').write_text('cancel later submissions; issued batch may still execute\n', encoding='utf-8')
                print('已取消后续提交；已发送批次仍可能执行，现场急停请关闭总电源。')
            else:
                (args.state_dir/'STOP').unlink(missing_ok=True)
                print('已解除停止标记；未启动动作。')
            return 0
        result = run(args.action, args.state_dir, port=args.port, factory=factory)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    except Exception as error:
        print('已停止：'+str(error), file=sys.stderr); return 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
