#!/usr/bin/env python3
"""Feedback-monitored position holder for the board's small Python image.

CAN I/O uses existing can-utils, without socket, fcntl or json. The shell entry
point keeps descriptor 9 locked. Importing and --check never access CAN.
"""
import os
import re
import sys
import time
import subprocess
import selectors
import signal

OPEN = [0, 612, 0, 0, 0, 0]
LIMITS = [2000, 4095, 4095, 4095, 4095, 4095]
PERIOD = 0.02
FEEDBACK_TIMEOUT = 1.5
POSITION_TOLERANCE = 80
OWNER = '/tmp/handui-motion-owner'
FRAME = re.compile(r'^\([0-9]+\.[0-9]+\)\s+can0\s+([0-9A-Fa-f]{3})#([0-9A-Fa-f]+)$')


def atomic_write(path, text):
    temporary = path + '.new'
    with open(temporary, 'w', encoding='utf-8') as out:
        out.write(text)
    os.replace(temporary, path)


def checked_pose(values):
    if len(values) != 6 or any(type(v) is not int or not 0 <= v <= cap
                               for v, cap in zip(values, LIMITS)):
        raise ValueError('Six positions within the documented motor limits are required')
    return list(values)


def read_plan(path):
    with open(path, encoding='ascii') as source:
        rows = [line.split() for line in source if line.strip()]
    if not 2 <= len(rows) <= 65:
        raise ValueError('Plan needs an initial pose and 1-64 target poses')
    result = []
    for row in rows:
        if len(row) != 8 or any(not token.isdigit() for token in row):
            raise ValueError('Each plan row needs eight nonnegative integers')
        values = [int(token) for token in row]
        checked_pose(values[:6])
        if not 1 <= values[6] <= 100 or not 0 <= values[7] <= 300:
            raise ValueError('Plan wait or hold duration is outside its limits')
        result.append(values)
    if sum(row[6] + row[7] for row in result[1:]) > 3000:
        raise ValueError('A motion plan cannot exceed five minutes')
    return result


def command_lines(pose, stamp):
    checked_pose(pose)
    base = round(stamp * 1000000)
    lines = []
    for i, value in enumerate(pose):
        seconds, micros = divmod(base + i * 500, 1000000)
        lines.append('(%d.%06d) can0 %03X#AA%02X%02XE8096403\n' %
                     (seconds, micros, i + 1, value & 255, value >> 8))
    return ''.join(lines)


def decode_line(line):
    match = FRAME.fullmatch(line.strip())
    if not match:
        return None
    node = int(match[1], 16) - 0x101
    if not 0 <= node < 6:
        return None
    try:
        payload = bytes.fromhex(match[2])
    except ValueError:
        raise RuntimeError('Malformed feedback for motor %d' % (node + 1))
    if len(payload) != 8 or payload[0] != 0xAA:
        raise RuntimeError('Invalid AA feedback for motor %d' % (node + 1))
    if payload[1] != 0:
        raise RuntimeError('Motor %d reported fault status %d' % (node + 1, payload[1]))
    return node, payload[2] | ((payload[3] & 15) << 8)


class CanToolsBus:
    """One paced canplayer and one nonblocking candump listener."""
    def __init__(self):
        self.selector = selectors.DefaultSelector()
        self.pending = b''
        self.diagnostics = b''
        self.reader = subprocess.Popen(['candump', '-L', 'can0'], stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, bufsize=0)
        self.player = None
        try:
            self.player = subprocess.Popen(['canplayer', '-g', '0', '-x'], stdin=subprocess.PIPE,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                           bufsize=0)
            for pipe in (self.reader.stdout, self.reader.stderr, self.player.stderr):
                os.set_blocking(pipe.fileno(), False)
                self.selector.register(pipe, selectors.EVENT_READ)
            os.set_blocking(self.player.stdin.fileno(), False)
        except BaseException:
            self.close()
            raise

    def send_pose(self, pose, stamp):
        if self.player.poll() is not None:
            raise RuntimeError('canplayer exited before the holder completed')
        text = command_lines(pose, stamp)
        data = text.encode('ascii')
        try:
            written = os.write(self.player.stdin.fileno(), data)
        except BlockingIOError:
            raise RuntimeError('CAN sender cannot keep up; old targets will not be queued')
        if written != len(data):
            raise RuntimeError('CAN sender accepted an incomplete target batch')
        return text

    def read(self):
        lines = []
        for key, _ in self.selector.select(0):
            chunk = os.read(key.fd, 65536)
            if not chunk:
                self.selector.unregister(key.fileobj)
                if key.fileobj is self.reader.stdout:
                    raise RuntimeError('candump stopped during motor control')
                continue
            if key.fileobj is self.reader.stdout:
                self.pending += chunk
                if len(self.pending) > 262144:
                    raise RuntimeError('CAN feedback reader fell behind')
                while b'\n' in self.pending:
                    line, self.pending = self.pending.split(b'\n', 1)
                    lines.append(line.decode('ascii', errors='replace') + '\n')
            else:
                self.diagnostics = (self.diagnostics + chunk)[-2048:]
        if self.diagnostics:
            raise RuntimeError('CAN tool error: ' + self.diagnostics.decode('utf-8', errors='replace').strip())
        if self.reader.poll() is not None or self.player.poll() is not None:
            raise RuntimeError('A CAN tool exited during motor control')
        return lines

    def emergency_open(self):
        # A blocked/broken stream may still contain old targets. Stop that
        # sender before the fallback OPEN so it cannot undo the release.
        if self.player is not None and self.player.poll() is None:
            self.player.terminate()
            try:
                self.player.wait(timeout=.4)
            except subprocess.TimeoutExpired:
                self.player.kill()
                self.player.wait(timeout=.4)
        for node, value in enumerate(OPEN, 1):
            frame = '%03X#AA%02X%02XE8096403' % (node, value & 255, value >> 8)
            subprocess.run(['cansend', 'can0', frame], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=1, check=True)

    def close(self):
        # Owned sender must be gone before its inherited exclusive lock closes.
        for process in (getattr(self, 'player', None), getattr(self, 'reader', None)):
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=.4)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=.4)
                for name in ('stdin', 'stdout', 'stderr'):
                    pipe = getattr(process, name, None)
                    if pipe is not None:
                        pipe.close()
        self.selector.close()


class StopRequested(Exception):
    pass


class HandoverRequested(Exception):
    pass


class Runner:
    def __init__(self, run, rows, bus_factory=CanToolsBus, clock=time, owner=OWNER):
        self.run_dir, self.rows = run, rows
        self.bus_factory, self.clock, self.owner = bus_factory, clock, owner
        self.bus = None
        self.target = checked_pose(rows[1][:6])
        self.positions = [None] * 6
        self.latest = [None] * 6
        self.feedback_at = [None] * 6
        self.started = clock.monotonic()
        self.last_status = -10.0
        self.deviation_since = None
        self.completed = False
        self.log = None

    def path(self, name):
        return os.path.join(self.run_dir, name)

    def write(self, name, text):
        atomic_write(self.path(name), text)

    def status(self, status, message):
        values = ['?' if v is None else str(v) for v in self.positions]
        self.write('holding.status', 'status=%s\npositions=%s\ntarget=%s\nmessage=%s\nupdated_at=%.6f\npid=%d\n' %
                   (status, ','.join(values), ','.join(map(str, self.target)),
                    str(message).replace('\n', ' ').replace('\r', ' ')[:500],
                    self.clock.time(), os.getpid()))
        self.last_status = self.clock.monotonic()

    def read_feedback(self, ignore_fault=False):
        for line in self.bus.read():
            if self.log is not None:
                self.log.write(line)
            try:
                decoded = decode_line(line)
            except RuntimeError:
                if ignore_fault:
                    continue
                raise
            if decoded is not None:
                node, position = decoded
                self.positions[node] = position
                self.latest[node] = line
                self.feedback_at[node] = self.clock.monotonic()

    def fresh(self):
        now = self.clock.monotonic()
        return all(stamp is not None and now - stamp <= FEEDBACK_TIMEOUT
                   for stamp in self.feedback_at)

    def cycle(self, interrupts=True, recovering=False):
        tick = self.clock.monotonic()
        if interrupts:
            if os.path.exists(self.path('stop-request')):
                raise StopRequested()
            if os.path.exists(self.path('handover-request')):
                raise HandoverRequested()
        self.read_feedback(ignore_fault=recovering)
        if not recovering and tick - self.started > FEEDBACK_TIMEOUT and not self.fresh():
            raise RuntimeError('Fresh feedback from all six motors was lost for over 1.5 seconds')
        text = self.bus.send_pose(self.target, self.clock.time())
        if self.log is not None:
            self.log.write(text)
        self.read_feedback(ignore_fault=recovering)
        if self.completed and not recovering:
            if max(abs(a - b) for a, b in zip(self.positions, self.target)) > POSITION_TOLERANCE:
                if self.deviation_since is None:
                    self.deviation_since = tick
                elif tick - self.deviation_since > FEEDBACK_TIMEOUT:
                    raise RuntimeError('The held pose has a persistent position deviation')
            else:
                self.deviation_since = None
            if tick - self.last_status >= .5:
                self.status('holding', 'Holding the last target until the next operator command')
        self.clock.sleep(max(0, PERIOD - (self.clock.monotonic() - tick)))

    def wait(self, seconds):
        deadline = self.clock.monotonic() + seconds
        while self.clock.monotonic() < deadline:
            self.cycle()

    def snapshot(self):
        if not self.fresh():
            raise RuntimeError('Cannot confirm a final pose without fresh six-motor feedback')
        return ''.join(self.latest)

    def finish_initial(self, code):
        self.write('final.log', ''.join(line for line in self.latest if line))
        if self.log is not None:
            self.log.flush()
            self.log.close()
            self.log = None
        self.write('exit-code', str(code) + '\n')

    def recover(self):
        self.target = OPEN.copy()
        deadline = self.clock.monotonic() + .8
        try:
            while self.clock.monotonic() < deadline:
                self.cycle(interrupts=False, recovering=True)
        except Exception:
            try:
                self.bus.emergency_open()
            except Exception:
                pass

    def run(self):
        os.makedirs(self.run_dir, exist_ok=True)
        atomic_write(self.owner, self.run_dir + '\n')
        code = 0
        try:
            self.log = open(self.path('can.log'), 'w', encoding='ascii')
            self.bus = self.bus_factory()
            self.write('progress', '0\n')
            for index, row in enumerate(self.rows[1:], 1):
                self.target = row[:6]
                self.wait(row[6] * .1)
                self.write('point-%d.log' % index, self.snapshot())
                self.write('progress', str(index) + '\n')
                self.wait(row[7] * .1)
            self.snapshot()
            self.status('released' if self.target == OPEN else 'holding',
                        'Open pose applied' if self.target == OPEN else
                        'Holding the last target until the next operator command')
            self.finish_initial(0)
            self.completed = True
            if self.target != OPEN:
                while True:
                    self.cycle()
        except HandoverRequested:
            self.status('handover', 'Control passed to the next operator command without opening')
            if not self.completed:
                self.finish_initial(1)
        except StopRequested:
            self.recover()
            self.status('released', 'Stop requested; opening was attempted')
            if not self.completed:
                self.finish_initial(130)
            else:
                self.write('exit-code', '130\n')
            code = 130
        except Exception as error:
            if self.bus is not None:
                self.recover()
            detail = ('; opening was attempted and holding stopped' if self.bus is not None
                      else '; CAN holder could not start; no target was sent')
            self.status('error', str(error) + detail)
            if not self.completed:
                self.finish_initial(1)
            code = 1
        finally:
            if self.log is not None:
                self.log.close()
                self.log = None
            if self.bus is not None:
                self.bus.close()
            self.write('final-command', ' '.join(map(str, self.target)) + '\n')
            try:
                with open(self.owner, encoding='utf-8') as source:
                    same_owner = source.read().strip() == self.run_dir
                if same_owner:
                    os.unlink(self.owner)
            except FileNotFoundError:
                pass
        return code


def check_runtime():
    # Exercise the same read-only path and signal prerequisites as main before
    # deployment can be reported ready. Never create this probe directory.
    validate_run_directory('/tmp/handui-' + '0' * 32)
    for name in ('SIGHUP', 'SIGINT', 'SIGTERM'):
        if not isinstance(getattr(signal, name, None), int):
            raise RuntimeError('Required board signal is missing: ' + name)
    for tool in ('flock', 'canplayer', 'candump', 'cansend'):
        if not any(os.path.isfile(os.path.join(path, tool)) and
                   os.access(os.path.join(path, tool), os.X_OK)
                   for path in os.environ.get('PATH', '').split(os.pathsep)):
            raise RuntimeError('Required existing CAN tool is missing: ' + tool)
    print('__HOLD_RUNTIME_OK__', flush=True)


def validate_run_directory(path, temp_root='/tmp'):
    """Allow the system /tmp alias, but never follow a run-owned symlink."""
    prefix = temp_root.rstrip('/\\') + '/handui-'
    if not re.fullmatch(re.escape(prefix) + r'[0-9a-f]{32}', path):
        raise ValueError('Expected a validated /tmp/handui-<32 hex> directory')
    if os.path.islink(path):
        raise ValueError('The run directory itself must not be a symbolic link')
    # Yocto normally maps /tmp to /var/volatile/tmp. Compare canonical parents,
    # rather than rejecting every run because its system parent is an alias.
    parent = os.path.realpath(temp_root)
    resolved = os.path.realpath(path)
    if (os.path.normcase(os.path.dirname(resolved)) != os.path.normcase(parent)
            or os.path.basename(resolved) != os.path.basename(path)):
        raise ValueError('The run directory resolves outside the authorized temporary directory')
    return path


def main(argv):
    if argv == ['--check']:
        check_runtime()
        return 0
    if len(argv) != 2:
        raise ValueError('Expected a validated /tmp/handui-<32 hex> directory and plan')
    safe_run = None
    try:
        safe_run = validate_run_directory(argv[0])
        os.makedirs(safe_run, exist_ok=True)
        if os.environ.get('HANDUI_LOCKED') != '1':
            raise RuntimeError('Start through the flock-protected shell entry point')
        os.fstat(9)
        rows = read_plan(argv[1])
        run = Runner(argv[0], rows)
        def request_stop(signum, frame):
            atomic_write(run.path('stop-request'), 'signal\n')
        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, request_stop)
        return run.run()
    except Exception as error:
        if safe_run is None:
            # A rejected directory may point anywhere. Never follow it merely
            # to write diagnostics; the host can collect stderr/dead-PID state.
            raise
        # Report preparation failures to the waiting host without opening CAN.
        # An exec/import/plan failure must not leave a 560-second pending job.
        for name in ('final.log', 'can.log'):
            path = os.path.join(safe_run, name)
            if not os.path.exists(path):
                atomic_write(path, '')
        atomic_write(os.path.join(safe_run, 'holding.status'),
                     'status=error\npositions=?,?,?,?,?,?\ntarget=?,?,?,?,?,?\nmessage=%s\nupdated_at=%.6f\npid=%d\n' %
                     (str(error).replace('\n', ' ')[:500], time.time(), os.getpid()))
        atomic_write(os.path.join(safe_run, 'exit-code'), '1\n')
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
