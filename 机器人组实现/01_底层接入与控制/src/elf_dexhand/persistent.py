"""Persistent SSH transport for repeated low-latency CAN commands."""

from __future__ import annotations

import socket
import time
from pathlib import Path

import paramiko

from .connection import (
    BoardConfig,
    BoardConnectionError,
    CommandResult,
    RemoteCommandError,
)


class PersistentBoardConnection:
    """Reuse one authenticated SSH transport across multiple commands.

    This class supports the board's passwordless ``none`` authentication and
    configured or agent-provided public keys. It verifies the remote host key
    against OpenSSH's known_hosts file before attempting authentication.
    Use as a context manager so sockets close even when a command fails.
    """

    def __init__(self, config: BoardConfig, known_hosts: Path | None = None) -> None:
        """Store SSH settings without opening a network connection."""
        self.config = config
        self.known_hosts = known_hosts or Path.home() / ".ssh" / "known_hosts"
        self._transport: paramiko.Transport | None = None

    def __enter__(self) -> PersistentBoardConnection:
        """Connect and return this session; propagate authentication errors."""
        self.connect()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        """Close the SSH socket on normal or exceptional exit."""
        self.close()

    def _verify_host_key(self, transport: paramiko.Transport) -> None:
        """Require an exact match with the user's known_hosts entry."""
        if not self.known_hosts.is_file():
            raise BoardConnectionError(
                f"known_hosts file is missing: {self.known_hosts}; "
                "connect once with the system ssh client to verify this board"
            )
        keys = paramiko.HostKeys()
        try:
            keys.load(str(self.known_hosts))
        except (OSError, paramiko.SSHException) as exc:
            raise BoardConnectionError(
                f"cannot read known_hosts {self.known_hosts}: {exc}"
            ) from exc
        host = self.config.host
        if self.config.port != 22:
            host = f"[{host}]:{self.config.port}"
        expected = keys.lookup(host)
        actual = transport.get_remote_server_key()
        if expected is None or actual.get_name() not in expected:
            raise BoardConnectionError(
                f"host key for {host} is unknown; verify it with system ssh first"
            )
        if expected[actual.get_name()].asbytes() != actual.asbytes():
            raise BoardConnectionError(f"host key for {host} has changed")

    def _authenticate(self, transport: paramiko.Transport) -> None:
        """Try passwordless login, then a configured key or SSH-agent keys."""
        failures: list[str] = []
        try:
            transport.auth_none(self.config.user)
        except paramiko.SSHException as exc:
            failures.append(f"none: {exc}")
        if transport.is_authenticated():
            return

        keys: list[paramiko.PKey] = []
        if self.config.key_path is not None:
            if not self.config.key_path.is_file():
                raise BoardConnectionError(
                    f"SSH key does not exist: {self.config.key_path}"
                )
            for key_type in (
                paramiko.Ed25519Key,
                paramiko.RSAKey,
                paramiko.ECDSAKey,
            ):
                try:
                    keys.append(
                        key_type.from_private_key_file(str(self.config.key_path))
                    )
                    break
                except (paramiko.SSHException, OSError) as exc:
                    failures.append(f"{key_type.__name__}: {exc}")
        else:
            keys.extend(paramiko.Agent().get_keys())

        for key in keys:
            try:
                transport.auth_publickey(self.config.user, key)
            except paramiko.SSHException as exc:
                failures.append(f"public key {key.get_name()}: {exc}")
            if transport.is_authenticated():
                return
        detail = "; ".join(failures) or "no accepted authentication method"
        raise BoardConnectionError(f"SSH authentication failed: {detail}")

    def connect(self) -> None:
        """Open one verified, authenticated SSH transport to the board."""
        if self._transport is not None and self._transport.is_active():
            return
        transport: paramiko.Transport | None = None
        connection: socket.socket | None = None
        try:
            connection = socket.create_connection(
                (self.config.host, self.config.port),
                timeout=self.config.connect_timeout,
            )
            transport = paramiko.Transport(connection)
            transport.start_client(timeout=self.config.connect_timeout)
            self._verify_host_key(transport)
            self._authenticate(transport)
            transport.set_keepalive(10)
            self._transport = transport
        except (OSError, EOFError, paramiko.SSHException, BoardConnectionError) as exc:
            if transport is not None:
                transport.close()
            elif connection is not None:
                connection.close()
            if isinstance(exc, BoardConnectionError):
                raise
            raise BoardConnectionError(
                f"SSH connection to {self.config.host} failed: {exc}"
            ) from exc

    def close(self) -> None:
        """Close this session; a later run can reconnect explicitly."""
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def open_channel(self, command: str) -> paramiko.Channel:
        """Start one long-lived remote command and return its SSH channel.

        The caller owns the returned channel and must close it. This method
        verifies and authenticates the board before starting the command;
        remote process failures are reported when the caller reads its status.
        """
        if not command or not command.strip():
            raise ValueError("remote command cannot be empty")
        self.connect()
        transport = self._transport
        if transport is None:
            raise BoardConnectionError("SSH transport did not initialize")
        channel: paramiko.Channel | None = None
        try:
            channel = transport.open_session(timeout=self.config.connect_timeout)
            channel.exec_command(command)
            return channel
        except (OSError, EOFError, paramiko.SSHException) as exc:
            if channel is not None:
                channel.close()
            raise BoardConnectionError(f"SSH channel failed: {exc}") from exc

    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        """Execute a remote shell command and collect both output streams.

        Raises BoardConnectionError on transport failure or timeout and
        RemoteCommandError on a nonzero remote exit. The command has its normal
        remote side effects; callers must quote variable shell arguments.
        """
        if not command or not command.strip():
            raise ValueError("remote command cannot be empty")
        limit = timeout if timeout is not None else self.config.command_timeout
        if limit <= 0:
            raise ValueError("command timeout must be positive")
        self.connect()
        transport = self._transport
        if transport is None:
            raise BoardConnectionError("SSH transport did not initialize")
        try:
            channel = transport.open_session(timeout=self.config.connect_timeout)
            channel.exec_command(command)
            stdout = bytearray()
            stderr = bytearray()
            deadline = time.monotonic() + limit
            while True:
                while channel.recv_ready():
                    stdout.extend(channel.recv(65536))
                while channel.recv_stderr_ready():
                    stderr.extend(channel.recv_stderr(65536))
                if channel.exit_status_ready():
                    while channel.recv_ready():
                        stdout.extend(channel.recv(65536))
                    while channel.recv_stderr_ready():
                        stderr.extend(channel.recv_stderr(65536))
                    break
                if time.monotonic() >= deadline:
                    channel.close()
                    raise BoardConnectionError(
                        f"remote command timed out after {limit}s: {command}"
                    )
                time.sleep(0.01)
            code = channel.recv_exit_status()
            channel.close()
        except (OSError, EOFError, paramiko.SSHException) as exc:
            self.close()
            raise BoardConnectionError(f"SSH command failed: {exc}") from exc
        result = CommandResult(
            command=command,
            returncode=code,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )
        if code != 0:
            raise RemoteCommandError(
                f"remote command exited {code}: {command}\n{result.stderr.strip()}"
            )
        return result

    def check_connection(self) -> str:
        """Return the hostname after confirming this SSH session can run commands."""
        hostname = self.run("hostname").stdout.strip()
        if not hostname:
            raise BoardConnectionError("board returned an empty hostname")
        return hostname
