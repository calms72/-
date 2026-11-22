"""Pure offline tests. Fake Console/clock only; no real port or CLI --run."""
import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

import paperbox_grasp_simple as m

HERE = Path(__file__).resolve().parent
BOOT = '00000000-0000-4000-8000-000000000001'


class FakeClock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t
    def sleep(self, seconds): self.t += seconds


class FakeConsole:
    def __init__(self, clock, positions, **options):
        self.clock, self.positions, self.options = clock, list(positions), options
        self.requests, self.controls, self.query_rounds = [], [], 0
        self.closed = False
        self.stamp = 1.0

    def login(self): pass
    def close(self): self.closed = True

    def frame(self, node, data):
        self.stamp += .001
        return f'({self.stamp:.6f}) can0 {node:03X}#{data.hex().upper()}\n'

    def info(self, node, status=0, velocity=0):
        p = self.positions[node-1]
        v = velocity & 4095
        return bytes((0xA0, status, p & 255, (p >> 8) | ((v & 15) << 4), v >> 4, 0, 0, 0))

    def command(self, wire, timeout=8):
        self.clock.t += .05
        self.requests.append(wire)
        response = ''
        sends = re.findall(r'\bcansend\s+\S+\s+(?:"[^"]*"|[^\s;]+)', wire)
        if len(sends)!=len(re.findall(r'\bcansend\b',wire)):
            raise AssertionError('unrecognized cansend syntax')
        if 'hostname;' in wire:
            if sends: raise AssertionError('identity must not send any frame')
            response = ('myd-ld25x-rt\nMYiR PLC STM32MP257x Evaluation Board 2\n' + BOOT +
                        '\n4: can0: <NOARP,UP,LOWER_UP,ECHO> mtu 16\n bitrate 1000000\n')
            if self.options.get('plc'): response += 'SIMPLE_PLC_ACTIVE\n'
        elif 'for bg_frame in ' in wire:
            if (sends!=['cansend can0 "$bg_frame"','cansend can0 "$bg_node#A0"'] or
                    wire.count('for bg_frame in ')!=1 or wire.count('for bg_node in ')!=1):
                raise AssertionError('unexpected batch sends or extra loop')
            planned = re.search(r'for bg_frame in ([^;]+);', wire)[1].split()
            for packet in planned:
                ident, raw = packet.split('#')
                node, payload = int(ident,16), bytes.fromhex(raw)
                self.controls.append((node,payload))
                if len(payload)!=7 or payload[0]!=0xAA or payload[3:]!=bytes.fromhex('E8096403'):
                    raise AssertionError('unexpected control frame')
                response += self.frame(node, payload)
                reply = bytearray(self.info(node, status=8 if self.options.get('aa_fault') and node==3 else 0))
                reply[0] = 0xAA
                response += self.frame(node+0x100, bytes(reply))
                if not self.options.get('stalled'):
                    self.positions[node-1] = int.from_bytes(payload[1:3], 'little')
            # The whole remote loop was already submitted. A lost USB result
            # cannot establish whether any remaining frame was cancelled.
            if self.options.get('usb'):
                raise OSError('fake USB result unknown after all five accepted targets')
            response += self.query()
            if self.options.get('extra_command'):
                response += self.frame(1, b'\xA0')
            if self.options.get('bad_rc'):
                return wire+'\n'+response+'\n__PB_SIMPLE_RC__2\nroot@myd-ld25x-rt:~# '
        elif re.search(r'for \w+ in 001 002 003 004 005 006;', wire):
            if sends!=['cansend can0 "$bg_node#A0"'] or wire.count('for bg_node in ')!=1:
                raise AssertionError('query may only send the six A0 requests')
            response = self.query()
        elif wire.startswith('timeout 1 candump -L can0;'):
            if sends: raise AssertionError('passive read must not send frames')
            self.clock.t += 1
        else:
            raise AssertionError('unrecognized fake request: '+wire)
        return wire+'\n'+response+'\n__PB_SIMPLE_RC__0\nroot@myd-ld25x-rt:~# '

    def query(self):
            response = ''
            self.query_rounds += 1
            self.clock.t += 1
            for node in range(1, 7):
                response += self.frame(node, b'\xA0')
                initial = self.query_rounds == 1
                fault = self.options.get('initial_fault') if initial else self.options.get('fault')
                moving = self.options.get('initial_moving') if initial else self.options.get('moving')
                if not (self.controls and self.options.get('missing') and node == 6):
                    response += self.frame(node+0x100, self.info(node, status=8 if fault and node==3 else 0,
                                                               velocity=1 if moving and node==3 else 0))
            if self.controls and self.options.get('unknown_ack'):
                response += self.frame(0x103, b'\xA5\x01')
            return response


class SimpleTests(unittest.TestCase):
    def directory(self):
        temporary = tempfile.TemporaryDirectory(prefix='offline-simple-test-', dir=HERE)
        Path(temporary.name).resolve().relative_to(HERE)
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def runner(self, action, root, positions=None, **options):
        clock = FakeClock(); created = []
        def factory(port, log):
            console = FakeConsole(clock, m.READY if positions is None else positions, **options)
            created.append(console)
            return console
        def invoke():
            with contextlib.redirect_stdout(io.StringIO()):
                return m.run(action, root, factory=factory, clock=clock, sleep=clock.sleep)
        return invoke, created

    def test_preview_never_connects_or_imports_driver(self):
        root = self.directory()/'not-created'
        def forbidden(*args): self.fail('offline preview opened a port')
        imported = set(sys.modules)
        for argv in ([], ['preview'], ['prepare'], ['grasp']):
            with self.subTest(argv=argv), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(m.main([*argv, '--state-dir', str(root)], factory=forbidden), 0)
                self.assertFalse(json.loads(output.getvalue())['opens_serial'])
        self.assertFalse(root.exists())
        self.assertNotIn('_paperbox_console', set(sys.modules)-imported)
        # An extra literal sender must not disappear inside a recognized branch.
        fake = FakeConsole(FakeClock(),m.READY)
        body,_ = m.batch_command(m.GRASP)
        for request in ('hostname; cansend can0 001#A5', m.QUERY+'; cansend can0 001#A5',
                        body+'; cansend can0 001#AA0000E8096403'):
            with self.subTest(unexpected=request), self.assertRaises(AssertionError):
                fake.command(request)
        self.assertEqual(fake.controls,[])

    def test_prepare_and_grasp_submit_one_batch_five_targets_without_markers(self):
        root = self.directory()
        for action, initial, target in (('prepare',m.GRASP,m.READY), ('grasp',m.READY,m.GRASP)):
            with self.subTest(action=action):
                invoke, created = self.runner(action,root,initial)
                result = invoke(); console = created[0]
                self.assertEqual(result['positions'],list(target))
                self.assertEqual(result['attempted_batches'],1)
                self.assertEqual([node for node,_ in console.controls],[3,4,5,6,2])
                self.assertEqual([int.from_bytes(raw[1:3],'little') for _,raw in console.controls],
                                 [target[node-1] for node in (3,4,5,6,2)])
                self.assertEqual(sum('for bg_frame in ' in request for request in console.requests),1)
                self.assertTrue(console.closed)
        self.assertFalse((root/'active.json').exists())
        self.assertFalse((root/'ready.json').exists())

    def test_status_only_reads_even_with_STOP_and_fault(self):
        root = self.directory(); (root/'STOP').write_text('stop subsequent motion',encoding='utf-8')
        positions = list(m.READY); positions[0] = 0
        invoke, created = self.runner('status',root,positions,plc=True,initial_fault=True)
        result = invoke()
        self.assertEqual(result['result'],'status_read')
        self.assertEqual(result['states'][2]['status'],8)
        self.assertEqual(created[0].controls,[])
        self.assertTrue(created[0].closed)

    def test_initial_fault_moving_pose_side_and_STOP_prevent_AA(self):
        for case in ('initial_fault','initial_moving','pose','side','plc','stop'):
            with self.subTest(case=case):
                root = self.directory(); initial = list(m.READY)
                if case=='pose': initial[2] += 31
                if case=='side': initial[0] -= 21
                if case=='stop': (root/'STOP').write_text('stop',encoding='utf-8')
                invoke, created = self.runner('grasp',root,initial,**{case:True})
                with self.assertRaises(m.protocol.GuardError): invoke()
                self.assertTrue(not created or created[0].controls==[])

    def test_USB_and_unknown_batch_result_never_retry_or_open(self):
        for option,count in (('usb',5),('bad_rc',5)):
            with self.subTest(option=option):
                root = self.directory(); invoke, created = self.runner('grasp',root,**{option:True})
                expected_error = OSError if option=='usb' else m.protocol.GuardError
                with self.assertRaises(expected_error): invoke()
                console = created[0]
                self.assertEqual(len(console.controls),count)
                self.assertEqual(len(set(node for node,_ in console.controls)),count)
                self.assertEqual(sum('for bg_frame in ' in request for request in console.requests),1)
                self.assertTrue(all(raw[0]==0xAA and int.from_bytes(raw[1:3],'little')==m.GRASP[node-1]
                                    for node,raw in console.controls))
                self.assertTrue(console.closed)

    def test_post_batch_fault_ACK_missing_extra_frame_stall_timeout_stop_without_resend(self):
        for option in ('fault','aa_fault','unknown_ack','missing','extra_command','stalled','moving'):
            with self.subTest(option=option):
                root = self.directory(); invoke, created = self.runner('grasp',root,**{option:True})
                with self.assertRaises(m.protocol.GuardError): invoke()
                console = created[0]
                self.assertEqual([node for node,_ in console.controls],[3,4,5,6,2])
                self.assertEqual(sum('for bg_frame in ' in request for request in console.requests),1)
                self.assertTrue(console.closed)
                report = json.loads(next(root.glob('simple-*/result.json')).read_text(encoding='utf-8'))
                self.assertFalse(report['automatic_retry_or_open'])
                self.assertEqual(report['result'],'stopped_without_retry_or_automatic_open')
                if option=='moving': self.assertGreater(console.query_rounds,2)


if __name__ == '__main__': unittest.main(verbosity=2)
