"""Open the native serial implementation only for a requested hardware session."""
import os


def Console(port, log_path):
    if os.name == "nt":
        from serial_console_windows import Console as NativeConsole
    else:
        from serial_console_posix import Console as NativeConsole
    return NativeConsole(port, log_path)
