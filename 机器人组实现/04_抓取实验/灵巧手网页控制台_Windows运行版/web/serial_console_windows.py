"""Exclusive 115200 8N1 access to the confirmed controller's Windows COM port."""
import re
import secrets
import time
from pathlib import Path

import win32con
import win32file


class Console:
    # Background PLC logs can follow a valid prompt without another newline.
    shell = re.compile(r"(?:^|\n)root@myd-ld25x-rt:[^\r\n#]*#[ \t]*")
    ansi = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b.")

    def __init__(self, port, log_path):
        if not isinstance(port, str) or not re.fullmatch(r"COM[1-9]\d*", port, re.I):
            raise ValueError("串口必须是 COM 加数字，例如 COM12。")
        device_path = '\\\\.\\' + port.upper()
        self.log = Path(log_path).open("a", encoding="utf-8")
        self.handle = None
        try:
            self.handle = win32file.CreateFile(
                device_path,
                win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                0,  # No sharing: one process owns the console.
                None,
                win32con.OPEN_EXISTING,
                0,
                None,
            )
            dcb = win32file.GetCommState(self.handle)
            win32file.BuildCommDCB("baud=115200 parity=N data=8 stop=1", dcb)
            win32file.SetCommState(self.handle, dcb)
            # ReadFile returns after at most 100 ms; writes have a finite timeout.
            win32file.SetCommTimeouts(self.handle, (0, 0, 100, 0, 1000))
        except BaseException:
            try:
                if self.handle is not None:
                    win32file.CloseHandle(self.handle)
            finally:
                self.log.close()
            raise

    def close(self):
        handle, self.handle = self.handle, None
        try:
            if handle is not None:
                win32file.CloseHandle(handle)
        finally:
            self.log.close()

    def send(self, text):
        data = (text + "\r").encode()
        deadline = time.monotonic() + max(2.0, len(data) / 11520 + 1.0)
        while data:
            status, count = win32file.WriteFile(self.handle, data)
            if status:
                raise OSError(f"串口写入失败，Windows 状态码 {status}。")
            if count:
                data = data[count:]
            elif time.monotonic() >= deadline:
                raise TimeoutError("串口写入超时。")

    @classmethod
    def has_prompt(cls, text, marker=None):
        if cls.has_secondary_prompt(text):
            return False
        if marker is not None:
            completed = re.search(r"(?m)^" + re.escape(marker) + r"\n", text)
            if not completed:
                return False
            text = text[completed.end():]
        return cls.shell.search(text) is not None

    @staticmethod
    def has_secondary_prompt(text):
        # Only a standalone final PS2 prompt proves unfinished shell input.
        # A '>' inside a log line or followed by other output is insufficient.
        return re.search(r"(?:^|\n)> (?:\n)?\Z", text) is not None

    def receive(self, timeout=6, marker=None):
        data = bytearray()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status, chunk = win32file.ReadFile(self.handle, 32768)
            if status:
                raise OSError(f"串口读取失败，Windows 状态码 {status}。")
            if chunk:
                data.extend(chunk)
            cleaned = self.ansi.sub("", data.decode(errors="replace")).replace("\r", "")
            if self.has_prompt(cleaned, marker) or (marker is None and (
                    self.has_secondary_prompt(cleaned) or re.search(r"(?:login:|Password:)\s*$", cleaned))):
                break
        text = self.ansi.sub("", data.decode(errors="replace")).replace("\r", "")
        self.log.write(text + "\n")
        self.log.flush()
        return text

    def login(self):
        deadline = time.monotonic() + 8
        cleared_secondary = False
        for _ in range(3):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self.send("")
            output = self.receive(min(2, remaining))
            if self.has_prompt(output):
                return
            if re.search(r"login:\s*$", output):
                self.send("root")
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    output = self.receive(min(2, remaining))
                if self.has_prompt(output):
                    return
            if self.has_secondary_prompt(output):
                if cleared_secondary:
                    raise RuntimeError("控制台未同步：未完成的 shell 输入仍未清除，请检查控制台；不会自动重发动作。")
                # This clears only the positively identified unfinished input.
                # Never complete its braces or replay the command that left it.
                cleared_secondary = True
                self.send("\x03")
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    output = self.receive(min(2, remaining))
                if self.has_prompt(output):
                    return
                if self.has_secondary_prompt(output):
                    raise RuntimeError("控制台未同步：未完成的 shell 输入仍未清除，请检查控制台；不会自动重发动作。")
            if re.search(r"Password:\s*$", output):
                break
        raise RuntimeError("未识别到已验证开发板的 root 控制台，请检查串口或登录状态。")

    def command(self, text, timeout=8):
        self.log.write("COMMAND: " + text + "\n")
        nonce = secrets.token_hex(12)
        marker = '__HANDUI_DONE_' + nonce + '__'
        # Split the marker in the source so echoed commands cannot complete a read.
        sync = "printf '\\n%s%s\\n' '__HANDUI_DONE_' '" + nonce + "__'"
        # Parse the whole group before executing it. Sending sync on a second
        # command line lets its terminal echo interleave with the first result
        # (including base64 file contents).
        body = text.rstrip()
        if body.endswith(';'):
            body = body[:-1]
        self.send('{ ' + body + '; ' + sync + '; }')
        result = self.receive(timeout, marker=marker)
        if self.has_secondary_prompt(result):
            raise RuntimeError("控制台未同步：检测到未完成的 shell 输入；本次命令结果未知，不会自动重发动作。")
        if not self.has_prompt(result, marker):
            raise RuntimeError("开发板未及时返回控制台提示符，动作不会自动重发。")
        return re.sub(r"(?m)^" + re.escape(marker) + r"\n", "", result, count=1)
