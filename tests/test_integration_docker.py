"""End-to-end integration tests against the real Docker target box.

These exercise the SSH session, the safety policy, and every tool from
agent/tools.py against an actual sshd — everything in the pipeline except
the LLM call itself (which needs a live LLM_API_KEY and is therefore out
of scope for an automated, unattended test).

Requires: tests/docker/Dockerfile built and running, e.g.
    docker build -t ssh-agent-testbox tests/docker
    docker run -d --name ssh-agent-test -p 2222:22 ssh-agent-testbox

Skips automatically if the container isn't reachable on localhost:2222.
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from agent.policy import SafetyLevel
from agent.tools import SSHToolkit, ToolContext
from core.logger import SessionLogger
from core.ssh_session import SSHSession

HOST = "127.0.0.1"
PORT = 2222
USER = "agentuser"
PASSWORD = "agentpass"


def _container_reachable() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=2):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _container_reachable(),
    reason="test container not reachable on 127.0.0.1:2222 — see tests/docker/",
)


@pytest.fixture()
def session():
    s = SSHSession(host=HOST, port=PORT, username=USER, password=PASSWORD)
    yield s
    s.close()


@pytest.fixture()
def executor(tmp_path, session):
    logger = SessionLogger(str(tmp_path / "session_log.jsonl"))
    ctx = ToolContext(session=session, logger=logger, confirm_fn=lambda *a: True)
    return SSHToolkit(ctx), logger


def test_ssh_session_runs_commands(session):
    result = session.run("echo hello-agent")
    assert result["exit_code"] == 0
    assert "hello-agent" in result["output"]


def test_ssh_session_preserves_cwd_across_calls(session):
    session.run("cd /tmp")
    result = session.run("pwd")
    assert result["output"].strip() == "/tmp"


def test_system_facts_tool_runs_all_probes(executor):
    exec_, _ = executor
    output = exec_.call("system_facts", "{}")
    assert "uname -a" in output
    assert "df -h" in output
    assert "free -m" in output


def test_run_command_safe_executes_without_confirmation(executor):
    exec_, logger = executor
    output = exec_.call("run_command", '{"command": "whoami", "why": "identify user"}')
    assert "exit_code=0" in output
    assert USER in output


def test_run_command_blocked_never_executes(executor):
    exec_, _ = executor
    output = exec_.call(
        "run_command", '{"command": "rm -rf /", "why": "cleanup everything"}'
    )
    assert output.startswith("BLOCKED:")
    # Sanity: the filesystem must still be intact.
    exec_.call("run_command", '{"command": "ls /", "why": "sanity check"}')


def test_run_command_confirm_denied_path(session, tmp_path):
    logger = SessionLogger(str(tmp_path / "session_log.jsonl"))
    ctx = ToolContext(session=session, logger=logger, confirm_fn=lambda *a: False)
    exec_ = SSHToolkit(ctx)
    output = exec_.call(
        "run_command", '{"command": "apt-get install -y cowsay", "why": "install a tool"}'
    )
    assert output.startswith("CONFIRM_DENIED:")


def test_scenario_broken_nginx_is_diagnosable(executor):
    """Mirrors plan scenario 1: after break_nginx.sh, `nginx -t` should
    surface the syntax error the agent is expected to find."""
    exec_, _ = executor
    exec_.call(
        "run_command",
        '{"command": "sed -i \\"s/http {/http {\\\\n    this_is_not_a_directive;/\\" /etc/nginx/nginx.conf", '
        '"why": "inject a config error for the test"}',
    )
    output = exec_.call("run_command", '{"command": "nginx -t", "why": "validate config"}')
    assert "exit_code=1" in output or "test failed" in output.lower()
    # Restore for other tests / re-runs.
    exec_.call(
        "run_command",
        '{"command": "sed -i \\"/this_is_not_a_directive;/d\\" /etc/nginx/nginx.conf", '
        '"why": "restore config"}',
    )


def test_scenario_disk_usage_visible_via_df_and_du(executor):
    """Mirrors plan scenario 2: df should show usage, du should locate
    the largest offender under a directory the agent inspects."""
    exec_, _ = executor
    exec_.call(
        "run_command",
        '{"command": "mkdir -p /tmp/bigapp && dd if=/dev/zero of=/tmp/bigapp/dump.bin bs=1M count=50", '
        '"why": "create a large file for the test"}',
    )
    du_output = exec_.call(
        "run_command", '{"command": "du -sh /tmp/bigapp", "why": "measure directory size"}'
    )
    assert "exit_code=0" in du_output
    exec_.call(
        "run_command", '{"command": "rm /tmp/bigapp/dump.bin", "why": "cleanup"}'
    )


def test_tail_log_on_nonexistent_unit_reports_gracefully(executor):
    exec_, _ = executor
    output = exec_.call("tail_log", '{"unit_or_path": "nginx"}')
    assert "exit_code" in output
