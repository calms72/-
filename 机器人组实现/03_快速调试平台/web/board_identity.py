"""Read the expected local board identity only before a hardware connection."""
import os
import re


def expected_console():
    username = os.environ.get("HANDUI_BOARD_USER", "")
    hostname = os.environ.get("HANDUI_BOARD_HOST", "")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", username):
        raise ValueError("实机连接前请设置 HANDUI_BOARD_USER 为授权的板端登录账户。")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", hostname):
        raise ValueError("实机连接前请设置 HANDUI_BOARD_HOST 为串口提示符显示的主机名。")
    prompt = re.compile(
        r"(?:^|\n)" + re.escape(username + "@" + hostname)
        + r":[^\r\n#$]*[#$][ \t]*"
    )
    return username, hostname, prompt
