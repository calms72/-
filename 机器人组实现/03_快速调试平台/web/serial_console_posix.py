"""Exclusive 115200 8N1 access to the confirmed controller's Linux console."""
import fcntl
import os
import re
import select
import termios
import time
from pathlib import Path

from board_identity import expected_console


class Console:
    ansi = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b.")

    def __init__(self, port, log_path):
        self.login_name, _, self.shell = expected_console()
        self.log = Path(log_path).open("a", encoding="utf-8")
        self.fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            settings = termios.tcgetattr(self.fd)
            settings[0] = settings[1] = settings[3] = 0
            settings[2] &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB |
                             termios.HUPCL | getattr(termios, "CRTSCTS", 0))
            settings[2] |= termios.CS8 | termios.CLOCAL | termios.CREAD
            settings[4] = settings[5] = termios.B115200
            settings[6][termios.VMIN] = settings[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, settings)
        except BaseException:
            os.close(self.fd)
            self.log.close()
            raise

    def close(self):
        os.close(self.fd)
        self.log.close()

    def send(self, text):
        data = (text + "\r").encode()
        while data:
            n = os.write(self.fd, data)
            data = data[n:]

    def receive(self, timeout=6):
        data = bytearray()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.fd], [], [], min(.1, max(0, deadline-time.monotonic())))
            if ready:
                chunk = os.read(self.fd, 32768)
                if chunk:
                    data.extend(chunk)
            cleaned = self.ansi.sub("", data.decode(errors="replace")).replace("\r", "")
            if self.shell.search(cleaned) or re.search(r"(?:login:|Password:)\s*$", cleaned):
                break
        text = self.ansi.sub("", data.decode(errors="replace")).replace("\r", "")
        self.log.write(text + "\n")
        self.log.flush()
        return text

    def login(self):
        self.send("")
        output = self.receive()
        if re.search(r"login:\s*$", output):
            self.send(self.login_name)
            output += self.receive()
        if not self.shell.search(output):
            raise RuntimeError("未识别到已验证开发板的登录控制台，请检查串口或登录状态。")

    def command(self, text, timeout=8):
        self.log.write("COMMAND: " + text + "\n")
        self.send(text)
        result = self.receive(timeout)
        if not self.shell.search(result):
            raise RuntimeError("开发板未及时返回控制台提示符，动作不会自动重发。")
        return result
