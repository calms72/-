"""Run commands on the Linux controller through the system SSH client."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class BoardConnectionError(RuntimeError):
    """The SSH client could not establish or maintain a board connection."""


class RemoteCommandError(RuntimeError):
    """A command ran on the board but returned a nonzero exit status."""


@dataclass(frozen=True)
class BoardConfig:
    """SSH settings for one board; no network operation occurs on creation."""

    host: str
    user: str = "root"
    port: int = 22
    key_path: Path | None = None
    connect_timeout: int = 8
    command_timeout: int = 20
    trust_on_first_use: bool = False

    def __post_init__(self) -> None:
        """Reject invalid addresses and timeouts before invoking SSH."""
        if not self.host or any(char.isspace() for char in self.host):
            raise ValueError("host must be a nonempty address without spaces")
        if not self.user or any(char.isspace() for char in self.user):
            raise ValueError("user must be a nonempty name without spaces")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if self.connect_timeout <= 0 or self.command_timeout <= 0:
            raise ValueError("SSH timeouts must be positive")

    @classmethod
    def from_environment(cls) -> BoardConfig:
        """Read BOARD_HOST, BOARD_USER, BOARD_PORT and BOARD_KEY.

        BOARD_HOST is required so a caller never contacts an unintended board.
        Invalid numeric settings raise ValueError; this method does not connect.
        """
        host = os.environ.get("BOARD_HOST", "")
        if not host:
            raise ValueError("set BOARD_HOST to the board's IP address or host name")
        key = os.environ.get("BOARD_KEY")
        return cls(
            host=host,
            user=os.environ.get("BOARD_USER", "root"),
            port=int(os.environ.get("BOARD_PORT", "22")),
            key_path=Path(key) if key else None,
        )


@dataclass(frozen=True)
class CommandResult:
    """Captured output from one completed remote command."""

    command: str
    returncode: int
    stdout: str
    stderr: str


class BoardClient(Protocol):
    """Minimum board operations required by a hand controller."""

    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        """Execute one remote command or raise a connection/command error."""
        ...

    def check_connection(self) -> str:
        """Return the hostname after confirming SSH login."""
        ...


class BoardConnection:
    """Execute noninteractive SSH commands using the host's OpenSSH client.

    Each call starts a new SSH process. Authentication uses the configured key,
    the user's SSH agent, or the board's supported passwordless method. Host
    keys are checked against known_hosts unless trust_on_first_use is enabled.
    """

    def __init__(self, config: BoardConfig, ssh_executable: str = "ssh") -> None:
        """Store configuration; connection is deferred until run or check."""
        self.config = config
        self.ssh_executable = ssh_executable

    def _ssh_argv(self, command: str) -> list[str]:
        """Build an argument vector without invoking a local shell."""
        executable = shutil.which(self.ssh_executable)
        if executable is None:
            raise BoardConnectionError(
                f"SSH executable {self.ssh_executable!r} was not found"
            )
        policy = "accept-new" if self.config.trust_on_first_use else "yes"
        argv = [
            executable,
            "-T",
            "-p",
            str(self.config.port),
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={self.config.connect_timeout}",
            "-o",
            f"StrictHostKeyChecking={policy}",
            "-o",
            "NumberOfPasswordPrompts=0",
        ]
        if self.config.key_path is not None:
            if not self.config.key_path.is_file():
                raise BoardConnectionError(
                    f"SSH key does not exist: {self.config.key_path}"
                )
            argv.extend(["-i", str(self.config.key_path), "-o", "IdentitiesOnly=yes"])
        argv.extend([f"{self.config.user}@{self.config.host}", command])
        return argv

    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        """Run a remote shell command and return its captured output.

        The command is sent to the board's shell, so callers must quote any
        variable arguments. Raises BoardConnectionError for SSH or timeout
        failures and RemoteCommandError for a nonzero remote exit status.
        This method may have the side effects of the supplied command.
        """
        if not command or not command.strip():
            raise ValueError("remote command cannot be empty")
        limit = timeout if timeout is not None else self.config.command_timeout
        if limit <= 0:
            raise ValueError("command timeout must be positive")
        argv = self._ssh_argv(command)
        try:
            completed = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=limit,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BoardConnectionError(
                f"SSH to {self.config.host} failed: {exc}"
            ) from exc
        result = CommandResult(
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        if result.returncode == 255:
            raise BoardConnectionError(
                f"SSH to {self.config.host} failed: {result.stderr.strip()}"
            )
        if result.returncode != 0:
            raise RemoteCommandError(
                f"remote command exited {result.returncode}: {command}\n"
                f"{result.stderr.strip()}"
            )
        return result

    def check_connection(self) -> str:
        """Log in and return the board hostname; propagate any SSH failure."""
        hostname = self.run("hostname").stdout.strip()
        if not hostname:
            raise BoardConnectionError("board returned an empty hostname")
        return hostname
