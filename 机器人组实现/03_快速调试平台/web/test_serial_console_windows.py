"""Offline serial synchronization tests; all Windows I/O is mocked."""
import importlib.util
import base64
import io
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


PROMPT = b'operator@board-test:~# '
NOISE = b'2020-01-01T00:00:00 sample background status\r\n'


@pytest.fixture
def serial(monkeypatch):
    class Clock:
        value = 0.0

        def monotonic(self):
            self.value += 0.05
            return self.value

    class Wire:
        def __init__(self):
            self.chunks = []
            self.writes = []
            self.reads = 0
            self.on_write = lambda data: None

        def write(self, handle, data):
            self.writes.append(data)
            self.on_write(data)
            return 0, len(data)

        def read(self, handle, size):
            self.reads += 1
            return 0, self.chunks.pop(0) if self.chunks else b''

    clock, wire = Clock(), Wire()
    monkeypatch.setitem(sys.modules, 'win32con', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'win32file', SimpleNamespace(WriteFile=wire.write, ReadFile=wire.read))
    spec = importlib.util.spec_from_file_location('offline_serial_windows', Path(__file__).with_name('serial_console_windows.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.time = SimpleNamespace(monotonic=clock.monotonic)
    monkeypatch.setattr(module.secrets, 'token_hex', lambda size: 'a1' * size)
    console = module.Console.__new__(module.Console)
    monkeypatch.setenv('HANDUI_BOARD_USER', 'operator')
    monkeypatch.setenv('HANDUI_BOARD_HOST', 'board-test')
    console.login_name, _, console.shell = module.expected_console()
    console.handle = object()
    console.log = io.StringIO()
    return console, wire, clock


def marker_from_write(data):
    suffix = re.search(rb"'__HANDUI_DONE_' '([a-f0-9]+)__'", data)[1]
    marker = b'__HANDUI_DONE_' + suffix + b'__'
    assert marker not in data, 'The full completion marker must never appear in an echo'
    return marker


def test_command_waits_for_its_own_marker_and_fresh_prompt(serial):
    console, wire, _ = serial

    def respond(data):
        marker = marker_from_write(data)
        wire.chunks = [
            PROMPT,  # Buffered prompt from an older command must not finish this read.
            data.replace(b'\n', b'\r\n'),
            b'\r\n__STATE__0:1\r\n',
            marker[:16], marker[16:] + b'\r\n',
            PROMPT,
        ]

    wire.on_write = respond
    output = console.command("printf '\\n__STATE__0:1\\n'")
    assert '\n__STATE__0:1\n' in output
    assert not wire.chunks
    assert len(wire.writes) == 1
    assert not re.search(r'(?m)^__HANDUI_DONE_[a-f0-9]+__$', output)


@pytest.mark.parametrize('before,after', [(NOISE, b''), (b'', NOISE), (NOISE, NOISE)])
def test_command_accepts_async_logs_before_or_after_prompt(serial, before, after):
    console, wire, _ = serial

    def respond(data):
        marker = marker_from_write(data)
        wire.chunks = [data + b'\r\nresult\r\n', marker + b'\r\n' + before + PROMPT + after]

    wire.on_write = respond
    assert '\nresult\n' in console.command('echo result')
    assert len(wire.writes) == 1


def test_echoed_marker_command_and_stale_prompt_do_not_complete(serial):
    console, wire, clock = serial

    def respond(data):
        marker_from_write(data)
        wire.chunks = [data + b'\r\n', PROMPT + NOISE]

    wire.on_write = respond
    with pytest.raises(RuntimeError, match='不会自动重发'):
        console.command('echo never-completed', timeout=0.5)
    assert len(wire.writes) == 1
    assert clock.value < 1


def test_marker_without_following_prompt_does_not_complete(serial):
    console, wire, _ = serial
    wire.on_write = lambda data: wire.chunks.extend([PROMPT + b'\r\n', marker_from_write(data) + b'\r\n' + NOISE])
    with pytest.raises(RuntimeError, match='不会自动重发'):
        console.command('echo incomplete', timeout=0.5)
    assert len(wire.writes) == 1


def test_unresponsive_command_times_out_without_resending(serial):
    console, wire, clock = serial
    with pytest.raises(RuntimeError, match='不会自动重发'):
        console.command('echo no-response', timeout=0.5)
    assert len(wire.writes) == 1
    assert wire.reads < 20
    assert clock.value < 1


def test_login_accepts_prompt_followed_by_unsolicited_log(serial):
    console, wire, _ = serial
    wire.on_write = lambda data: wire.chunks.extend([PROMPT + NOISE])
    console.login()
    assert wire.writes == [b'\r']


def test_login_can_request_a_new_prompt_after_noise(serial):
    console, wire, clock = serial

    def respond(data):
        if len(wire.writes) == 1:
            wire.chunks = [NOISE]
        else:
            wire.chunks = [PROMPT + NOISE]

    wire.on_write = respond
    console.login()
    assert wire.writes == [b'\r', b'\r']
    assert clock.value < 8.2


def test_login_handles_configured_username_without_password_input(serial):
    console, wire, _ = serial

    def respond(data):
        wire.chunks = [PROMPT + NOISE] if data == b'operator\r' else [b'board-test login: ']

    wire.on_write = respond
    console.login()
    assert wire.writes == [b'\r', b'operator\r']


def test_unresponsive_login_has_bounded_empty_retries(serial):
    console, wire, clock = serial
    with pytest.raises(RuntimeError, match='登录控制台'):
        console.login()
    assert wire.writes == [b'\r'] * 3
    assert clock.value < 8.2
    assert wire.reads < 150


def test_file_fetch_cannot_mix_second_command_echo_into_payload(serial):
    from app import Manager
    console, wire, _ = serial
    original = ''.join('(1.000000) can0 %03X#AA00610200000000\n' % node
                       for node in range(0x101, 0x107))
    encoded = base64.encodebytes(original.encode('ascii'))
    def respond(data):
        marker = marker_from_write(data)
        if b'\n' in data:
            # Real terminal behavior that broke the previous two-line sender:
            # echo of line two arrives after line one has begun writing data.
            first, second = data.split(b'\n', 1)
            wire.chunks = [first + b'\r\n', b'\r\n__DATA_BEGIN__\r\n', second + b'\r\n',
                           encoded, b'\r\n__DATA_END__\r\n', marker + b'\r\n' + PROMPT]
        else:
            wire.chunks = [PROMPT, data + b'\r\n', b'\r\n__DATA_BEGIN__\r\n',
                           encoded[:81], encoded[81:173], encoded[173:],
                           b'\r\n__DATA_END__\r\n', marker + b'\r\n' + PROMPT + NOISE]
    wire.on_write = respond
    assert Manager.fetch(console, '/tmp/handui-test/final.log') == original
    assert len(wire.writes) == 1
    assert wire.writes[0].startswith(b'{ ')
    assert b'\n' not in wire.writes[0]


def test_command_group_accepts_a_trailing_semicolon(serial):
    console, wire, _ = serial
    def respond(data):
        assert b';;' not in data
        wire.chunks = [data + b'\r\nresult\r\n', marker_from_write(data) + b'\r\n' + PROMPT]
    wire.on_write = respond
    assert '\nresult\n' in console.command('echo result;')


@pytest.mark.parametrize('initial', [b'> ', PROMPT + b'\r\n> '])
def test_login_clears_confirmed_secondary_input_once_and_rechecks_prompt(serial, initial):
    console, wire, _ = serial

    def respond(data):
        wire.chunks = [b'^C\r\n' + PROMPT + NOISE] if data == b'\x03\r' else [initial]

    wire.on_write = respond
    console.login()
    assert wire.writes == [b'\r', b'\x03\r']


def test_login_does_not_repeat_interrupt_when_secondary_prompt_remains(serial):
    console, wire, clock = serial
    wire.on_write = lambda data: wire.chunks.extend([b'\r\n> '])
    with pytest.raises(RuntimeError, match='控制台未同步'):
        console.login()
    assert wire.writes == [b'\r', b'\x03\r']
    assert clock.value < 8.2


@pytest.mark.parametrize('log', [b'pressure > limit\r\n', b'> log message\r\n',
                                 b'log prefix > ', b'> \r\ncontinuing log\r\n'])
def test_login_never_interrupts_unproven_secondary_prompt(serial, log):
    console, wire, _ = serial
    wire.on_write = lambda data: wire.chunks.extend([log])
    with pytest.raises(RuntimeError, match='登录控制台'):
        console.login()
    assert wire.writes == [b'\r'] * 3


def test_command_reports_secondary_prompt_as_unknown_without_interrupt_or_replay(serial):
    console, wire, _ = serial
    wire.on_write = lambda data: wire.chunks.extend([data + b'\r\n> '])
    with pytest.raises(RuntimeError, match='控制台未同步.*结果未知.*不会自动重发'):
        console.command('echo unfinished', timeout=0.5)
    assert len(wire.writes) == 1
    assert b'\x03' not in wire.writes[0]


def test_command_keeps_reading_past_payload_that_temporarily_looks_like_ps2(serial):
    console, wire, _ = serial

    def respond(data):
        wire.chunks = [data + b'\r\n> ', b'ordinary result\r\n',
                       marker_from_write(data) + b'\r\n' + PROMPT + NOISE]

    wire.on_write = respond
    assert '> ordinary result\n' in console.command('echo result')
    assert len(wire.writes) == 1


def test_completed_marker_before_a_new_secondary_prompt_is_not_a_fresh_prompt(serial):
    console, wire, _ = serial

    def respond(data):
        wire.chunks = [data + b'\r\n' + marker_from_write(data) + b'\r\n' + PROMPT + b'\r\n> ']

    wire.on_write = respond
    with pytest.raises(RuntimeError, match='控制台未同步'):
        console.command('echo incomplete', timeout=0.5)
    assert len(wire.writes) == 1
