"""Linux USB serial discovery and read-only handshake for the verified MYiR board."""
import os
import re
from pathlib import Path

from serial_console import Console
from board_identity import expected_console

VID = '04b4'
PID = '0002'


def read(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ''


def port_exists(port):
    """Check a serial port without opening a port already in use by this app."""
    if os.name != 'nt':
        return Path(port).exists()
    if not re.fullmatch(r'COM[1-9]\d*', port, re.IGNORECASE):
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r'HARDWARE\DEVICEMAP\SERIALCOMM') as key:
            index = 0
            while True:
                try:
                    _, name, _ = winreg.EnumValue(key, index)
                except OSError:
                    return False
                if name.upper() == port.upper():
                    return True
                index += 1
    except OSError:
        return False


def usb_parent(path):
    for parent in (path, *path.parents):
        if (parent / 'idVendor').exists() and (parent / 'idProduct').exists():
            return parent
    return None


def scan_serial(sys_root=Path('/sys'), dev_root=Path('/dev')):
    ports = []
    for tty in sorted((sys_root / 'class/tty').glob('tty*')):
        if not re.fullmatch(r'tty(?:ACM|USB)\d+', tty.name):
            continue
        device = (tty / 'device').resolve()
        parent = usb_parent(device)
        vendor = read(parent / 'idVendor') if parent else ''
        product_id = read(parent / 'idProduct') if parent else ''
        node = dev_root / tty.name
        ports.append({'port': str(node), 'vid': vendor, 'pid': product_id,
            'product': read(parent / 'product') if parent else '',
            'serial_number': read(parent / 'serial') if parent else '',
            'usb_path': str(parent) if parent else '', 'recognized': vendor == VID and product_id == PID,
            'readable': os.access(node, os.R_OK), 'writable': os.access(node, os.W_OK)})
    return ports


def scan_usb(sys_root=Path('/sys')):
    result = []
    for path in sorted((sys_root / 'bus/usb/devices').glob('*')):
        if read(path / 'idVendor') != VID or read(path / 'idProduct') != PID:
            continue
        control = path.parent / (path.name + ':1.0')
        data = path.parent / (path.name + ':1.1')
        link = control / 'driver'
        result.append({'usb_path': str(path), 'product': read(path / 'product'),
            'driver': link.resolve().name if link.exists() else None,
            'cdc_acm': read(control / 'bInterfaceClass') == '02' and
                       read(control / 'bInterfaceSubClass') == '02' and read(data / 'bInterfaceClass') == '0a'})
    return result


def select_port(port='auto'):
    if port != 'auto':
        if os.name == 'nt':
            if not re.fullmatch(r'COM[1-9]\d*', port, re.IGNORECASE):
                raise ValueError('Windows 串口必须是 COM 后接数字。')
        elif not re.fullmatch(r'/dev/tty(?:ACM|USB)\d+', port):
            raise ValueError('串口必须是 /dev/ttyACM数字 或 /dev/ttyUSB数字。')
        if not port_exists(port):
            raise FileNotFoundError('串口不存在：' + port)
        return port
    if os.name == 'nt':
        raise ValueError('Windows 请用 --serial COM数字 指定已核对的开发板串口。')
    matches = [p for p in scan_serial() if p['recognized']]
    if len(matches) > 1:
        raise RuntimeError('发现多个同型号串口，请用 --serial /dev/ttyACM数字 指定。')
    if not matches:
        devices = scan_usb()
        if devices:
            raise RuntimeError('已发现 Cypress USB 设备，但没有串口。请检查系统是否加载 cdc_acm 驱动。')
        raise RuntimeError('没有发现开发板 USB 串口，请检查 USB 连接和开发板供电。')
    return matches[0]['port']


def handshake(port='auto', log_path=None, configure_can=False):
    _, hostname, _ = expected_console()
    port = select_port(port)
    log_path = Path(log_path or Path(__file__).parent / 'data/handshake.log')
    log_path.parent.mkdir(parents=True, exist_ok=True)
    c = Console(port, log_path)
    try:
        c.login()
        model_response = c.command("printf '\\n__MODEL__'; cat /proc/device-tree/model; printf '\\n'")
        match = re.search(r'\n__MODEL__([^\n]*)', model_response)
        model = (match[1] if match else '').replace('\x00', '').strip()
        if 'MYiR PLC STM32MP257x' not in model:
            raise RuntimeError('握手失败：不是已验证的 MYiR STM32MP257 开发板。')
        status = c.command('ip -details -statistics link show can0')
        ready = 'LOWER_UP' in status and 'bitrate 1000000' in status
        if not ready and configure_can and re.search(r'state DOWN\b', status):
            status = c.command('ip link set can0 type can bitrate 1000000 && ip link set can0 up && ip -details -statistics link show can0')
            ready = 'LOWER_UP' in status and 'bitrate 1000000' in status
        if not ready:
            raise RuntimeError('串口登录成功，但 can0 未处于 1 Mbps 工作状态；请先核对板端接口配置。')
        return {'port': port, 'baudrate': 115200, 'format': '8N1', 'hostname': hostname,
                'board_model': model, 'can_interface': 'can0', 'can_bitrate': 1000000,
                'can_ready': True, 'motor_commands_sent': False, 'can_status': status}
    finally:
        c.close()
