import os
from pathlib import Path

import pytest

import device_connection as device


@pytest.mark.skipif(os.name == 'nt', reason='Linux sysfs test needs directory symlinks')
def test_scanner_follows_usb_parent_and_does_not_select_other_devices(tmp_path):
    sys_root = tmp_path / 'sys'
    dev_root = tmp_path / 'dev'
    dev_root.mkdir()
    tty_root = sys_root / 'class/tty'
    tty_root.mkdir(parents=True)
    for index, vendor in enumerate(['04b4', '1234']):
        usb = sys_root / 'devices' / str(index)
        usb.mkdir(parents=True)
        (usb / 'idVendor').write_text(vendor)
        (usb / 'idProduct').write_text('0002')
        (usb / 'product').write_text('USB Serial')
        interface = usb / 'interface'
        interface.mkdir()
        tty = tty_root / f'ttyACM{index}'
        tty.mkdir()
        (tty / 'device').symlink_to(interface, target_is_directory=True)
        (dev_root / tty.name).touch()
    ports = device.scan_serial(sys_root, dev_root)
    assert len(ports) == 2
    assert [p['recognized'] for p in ports] == [True, False]


@pytest.mark.skipif(os.name == 'nt', reason='Windows requires an explicit COM port')
def test_auto_selection_refuses_ambiguous_adapters(monkeypatch):
    monkeypatch.setattr(device, 'scan_serial', lambda: [
        {'recognized': True, 'port': '/dev/ttyACM0'},
        {'recognized': True, 'port': '/dev/ttyACM1'}])
    with pytest.raises(RuntimeError, match='多个'):
        device.select_port()


@pytest.mark.skipif(os.name != 'nt', reason='Windows COM selection')
def test_windows_explicit_port_selection(monkeypatch):
    monkeypatch.setattr(device, 'port_exists', lambda port: port == 'COM7')
    assert device.select_port('COM7') == 'COM7'
    with pytest.raises(FileNotFoundError):
        device.select_port('COM8')


def test_handshake_checks_model_and_can_without_motor_commands(monkeypatch, tmp_path):
    monkeypatch.setenv('HANDUI_BOARD_USER', 'operator')
    monkeypatch.setenv('HANDUI_BOARD_HOST', 'board-test')
    commands = []
    class FakeConsole:
        def __init__(self, port, log):
            assert port == '/dev/ttyACM0'
        def login(self):
            pass
        def command(self, command):
            commands.append(command)
            if 'device-tree/model' in command:
                return '\n__MODEL__MYiR PLC STM32MP257x Evaluation Board 2\n'
            return 'can0: <UP,LOWER_UP> state UP bitrate 1000000'
        def close(self):
            pass
    monkeypatch.setattr(device, 'select_port', lambda port: '/dev/ttyACM0')
    monkeypatch.setattr(device, 'Console', FakeConsole)
    result = device.handshake(log_path=tmp_path/'handshake.log')
    assert result['motor_commands_sent'] is False
    assert result['can_ready'] is True
    assert all('cansend' not in c and 'ip link set' not in c for c in commands)
