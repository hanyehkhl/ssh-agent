"""The SSH toolkit the agent is given.

Each tool is a plain typed method decorated with ``@tool``; the JSON schema
the model sees is derived from the signature and docstring by
`agent.toolkit`, so there is no second copy of the schema to keep in sync.

All tools share one persistent SSHSession, so shell state (cwd, exported env
vars) survives across calls within a session.
"""
from __future__ import annotations

from agent.events import ToolCallCompleted, ToolCallStarted
from agent.policy import SafetyLevel, classify
from agent.toolkit import Toolkit, tool
from core.logger import SessionLogger
from core.ssh_session import SSHSession


class ToolContext:
    """Holds the live session, logger, and a confirm callback.

    confirm_fn(command, why, reason) -> bool
    """

    def __init__(self, session: SSHSession, logger: SessionLogger, confirm_fn):
        self.session = session
        self.logger = logger
        self.confirm_fn = confirm_fn


class SSHToolkit(Toolkit):
    """Read, diagnose and (with human approval) change the remote server."""

    def __init__(self, ctx: ToolContext, on_event=None):
        self.ctx = ctx
        # on_event(event) lets the agent loop stream tool progress to a UI
        # without any frontend having to wrap or patch this class.
        self.on_event = on_event or (lambda event: None)
        super().__init__()

    def _emit(self, event):
        self.on_event(event)

    def _execute(self, command: str, why: str, level: SafetyLevel,
                 tool_name: str = "run_command", max_output_bytes: int = 8000) -> str:
        """Run an already-classified command, log it, and report the result."""
        result = self.ctx.session.run(command, max_output_bytes=max_output_bytes)
        self.ctx.logger.log_tool_call(
            command, why, level.value, result["exit_code"], result["output"], True
        )

        header = f"exit_code={result['exit_code']}"
        if result["truncated"]:
            header += " (output truncated)"
        if result["timed_out"]:
            header += " (WARNING: command may still be running, timed out waiting for prompt)"
        text = f"{header}\n{result['output']}"
        self._emit(ToolCallCompleted(
            tool_name=tool_name, command=command, result=text, safety_level=level.value
        ))
        return text

    def _run_readonly(self, command: str, why: str, tool_name: str,
                      max_output_bytes: int = 8000) -> str:
        """Run a command the tool itself built, so it is read-only by construction.

        The dedicated tools (read_file, tail_log, system_facts) compose their
        own command from the model's arguments rather than taking a command
        line, so they skip the classifier — but they still announce themselves,
        or the operator would watch a blank screen while system_facts runs.
        """
        self._emit(ToolCallStarted(tool_name=tool_name, command=command, why=why))
        return self._execute(command, why, SafetyLevel.SAFE, tool_name=tool_name,
                             max_output_bytes=max_output_bytes)

    @tool
    def run_command(self, command: str, why: str) -> str:
        """Run a shell command on the connected SSH server.

        The command is classified by the safety policy first: read-only
        commands run immediately, state-changing ones ask the human operator,
        and destructive ones are refused outright.

        Args:
            command: The exact shell command to run.
            why: One short sentence, in Persian, explaining why this command is
                needed right now. Shown to the human operator for CONFIRM and
                recorded in the session log.
        """
        checked = classify(command)
        self._emit(ToolCallStarted(tool_name="run_command", command=command, why=why))

        if checked.level == SafetyLevel.BLOCKED:
            self.ctx.logger.log_tool_call(command, why, checked.level.value, None, "", False)
            text = (
                f"BLOCKED: این دستور اجرا نشد. دلیل: {checked.reason}\n"
                "یک راه امن‌تر برای رسیدن به همین هدف پیشنهاد بده."
            )
            self._emit(ToolCallCompleted(
                tool_name="run_command", command=command, result=text, safety_level="BLOCKED"
            ))
            return text

        if checked.level == SafetyLevel.CONFIRM and not self.ctx.confirm_fn(
            command, why, checked.reason
        ):
            self.ctx.logger.log_tool_call(command, why, checked.level.value, None, "", False)
            text = (
                "CONFIRM_DENIED: کاربر اجازهٔ اجرای این دستور را نداد. "
                "راه دیگری امتحان کن یا از کاربر اطلاعات بیشتری بخواه."
            )
            self._emit(ToolCallCompleted(
                tool_name="run_command", command=command, result=text, safety_level="CONFIRM"
            ))
            return text

        return self._execute(command, why, checked.level)

    @tool
    def read_file(self, path: str, max_bytes: int = 8000) -> str:
        """Read a text file from the remote server (read-only, always SAFE).

        Args:
            path: Absolute or relative path to the file.
            max_bytes: Maximum number of bytes to return.
        """
        return self._run_readonly(
            f"cat -- {path!r}", "خواندن فایل", "read_file", max_output_bytes=max_bytes
        )

    @tool
    def system_facts(self) -> str:
        """Gather baseline facts about the remote system.

        Reports OS, failed services, disk usage and memory. Call this once at
        the start of a session to orient yourself before diagnosing a problem.
        """
        commands = [
            "uname -a",
            "cat /etc/os-release 2>/dev/null || true",
            "systemctl --failed --no-pager 2>/dev/null || true",
            "df -h",
            "free -m",
        ]
        parts = []
        for cmd in commands:
            output = self._run_readonly(cmd, "جمع‌آوری اطلاعات پایه سیستم", "system_facts")
            parts.append(f"$ {cmd}\n{output}")
        return "\n\n".join(parts)

    @tool
    def tail_log(self, unit_or_path: str, lines: int = 100) -> str:
        """Show the last N lines of a systemd unit's journal or a log file.

        Args:
            unit_or_path: A systemd unit name (e.g. "nginx") or a file path
                (e.g. "/var/log/nginx/error.log"). Unit names are detected by
                the absence of a leading "/".
            lines: Number of lines to show from the end.
        """
        if unit_or_path.startswith("/"):
            cmd = f"tail -n {int(lines)} -- {unit_or_path!r}"
        else:
            cmd = f"journalctl -u {unit_or_path} -n {int(lines)} --no-pager"
        return self._run_readonly(cmd, "بررسی لاگ‌ها", "tail_log")
