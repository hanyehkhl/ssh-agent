"""Persistent SSH session wrapper around Paramiko.

Keeps a single interactive shell channel alive so state like `cd` and
exported env vars survives between commands issued by the agent.
"""
from __future__ import annotations

import re
import time

import paramiko

_PROMPT_MARKER = "__SSH_AGENT_DONE__"

# Strips ANSI/VT100 escape sequences (color codes, bracketed-paste toggles
# like ESC[?2004h/l, cursor movement, etc.) that bash writes to an
# interactive TTY but that are meaningless noise for a command's output.
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\r")

# The marker as it looks once the shell has actually run the command and
# expanded $? into a real exit code — as opposed to the shell's echo of the
# command line, where it is still the literal "$?".
_RESOLVED_MARKER_RE = re.compile(rf"{_PROMPT_MARKER}:(\d+)")


class SSHSession:
    def __init__(self, host: str, port: int, username: str, password: str | None = None,
                 key_filename: str | None = None, timeout: float = 10.0):
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            key_filename=key_filename,
            timeout=timeout,
        )
        # A wide pty stops the shell from hard-wrapping long command lines.
        # Wrapping used to split the completion marker across two lines, so
        # the echo cleanup below no longer recognised it and the echoed
        # command leaked into the captured output.
        self.shell = self.client.invoke_shell(width=1000, height=100)
        self.shell.settimeout(timeout)
        self._drain(initial=True)
        # Disable bash's bracketed-paste mode so it stops wrapping the
        # prompt in ESC[?2004h / ESC[?2004l on every command, and turn off
        # terminal echo so the command is not read back as part of its own
        # output in the first place.
        self.shell.send("bind 'set enable-bracketed-paste off' 2>/dev/null\n")
        self.shell.send("stty -echo 2>/dev/null\n")
        self._drain(read_timeout=0.5)

    def _drain(self, initial: bool = False, read_timeout: float = 2.0) -> str:
        """Read whatever is currently buffered on the channel."""
        buf = ""
        deadline = time.time() + read_timeout
        while time.time() < deadline:
            if self.shell.recv_ready():
                chunk = self.shell.recv(65536).decode("utf-8", errors="replace")
                buf += chunk
                deadline = time.time() + 0.3
            else:
                time.sleep(0.05)
        return buf

    def run(self, command: str, max_output_bytes: int = 8000, timeout: float = 20.0) -> dict:
        """Run a command on the persistent shell and capture stdout/stderr/exit code.

        Uses a marker echo to know when the command has finished and to
        recover the real exit code from `$?`.
        """
        marker = f"{_PROMPT_MARKER}:$?"
        full_cmd = f"{command}; echo {marker}"
        self.shell.send(full_cmd + "\n")

        output = ""
        deadline = time.time() + timeout
        match = None
        while time.time() < deadline:
            if self.shell.recv_ready():
                output += self.shell.recv(65536).decode("utf-8", errors="replace")
                # Wait for the marker with the exit code already substituted.
                # The bare marker is not enough: an interactive shell echoes
                # the command line back first, and that echo contains the
                # literal "__SSH_AGENT_DONE__:$?" — matching it would end the
                # read before the command has even run, returning empty output
                # and spilling the real output into the next command.
                match = _RESOLVED_MARKER_RE.search(output)
                if match:
                    break
            else:
                time.sleep(0.05)

        exit_code = int(match.group(1)) if match else -1

        # Strip ANSI escapes first, then drop the echoed command, the
        # marker line, and any shell prompt lines (e.g. "user@host:/tmp$").
        plain = _ANSI_ESCAPE_RE.sub("", output)
        cleaned_lines = []
        for line in plain.splitlines():
            stripped_line = line.strip()
            if not stripped_line:
                continue
            if _PROMPT_MARKER in line or stripped_line == full_cmd.strip():
                continue
            if re.match(r"^\S+@\S+:\S*\$\s*.*$", stripped_line):
                continue
            cleaned_lines.append(line)
        cleaned = "\n".join(cleaned_lines).strip()

        truncated = False
        if len(cleaned.encode("utf-8", errors="replace")) > max_output_bytes:
            cleaned = cleaned.encode("utf-8", errors="replace")[:max_output_bytes].decode(
                "utf-8", errors="replace"
            )
            truncated = True

        return {
            "command": command,
            "exit_code": exit_code,
            "output": cleaned,
            "truncated": truncated,
            "timed_out": match is None,
        }

    def close(self):
        try:
            self.shell.close()
        finally:
            self.client.close()
