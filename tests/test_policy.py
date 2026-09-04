import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from agent.policy import SafetyLevel, classify

BLOCKED_COMMANDS = [
    "rm -rf /",
    "rm -rf /*",
    "sudo rm -rf /",
    "mkfs.ext4 /dev/sda1",
    "dd if=/dev/zero of=/dev/sda",
    ":(){ :|:& };:",
    "chmod -R 777 /",
    "echo hi > /dev/sda",
    "shutdown -h now",
    "reboot",
    "history -c",
    "iptables -F",
    "DROP DATABASE prod;",
    "userdel -r root",
]

SAFE_COMMANDS = [
    "ls -la /var/log",
    "cat /etc/os-release",
    "tail -n 100 /var/log/nginx/error.log",
    "head -20 file.txt",
    "grep error /var/log/syslog",
    "find / -name '*.conf' -type f",
    "ps aux",
    "df -h",
    "du -sh /var/log",
    "free -m",
    "uname -a",
    "whoami",
    "pwd",
    "hostname",
    "uptime",
    "systemctl status nginx",
    "systemctl --failed",
    "journalctl -u nginx -n 50",
    "netstat -tulpn",
    "ss -tulpn",
    "id",
    "env",
    "echo hello",
    "which python3",
    "nginx -t",
    "curl -I https://example.com",
]

CONFIRM_COMMANDS = [
    "apt install nginx",
    "systemctl restart nginx",
    "vim /etc/nginx/nginx.conf",
    "rm /var/log/old.log",
    "useradd newuser",
    "kill -9 1234",
    "pip install requests",
    "docker restart mycontainer",
]


@pytest.mark.parametrize("command", BLOCKED_COMMANDS)
def test_blocked_commands(command):
    result = classify(command)
    assert result.level == SafetyLevel.BLOCKED, f"expected BLOCKED for: {command}"


@pytest.mark.parametrize("command", SAFE_COMMANDS)
def test_safe_commands(command):
    result = classify(command)
    assert result.level == SafetyLevel.SAFE, f"expected SAFE for: {command}"


@pytest.mark.parametrize("command", CONFIRM_COMMANDS)
def test_confirm_commands(command):
    result = classify(command)
    assert result.level == SafetyLevel.CONFIRM, f"expected CONFIRM for: {command}"


# A chained command is only as safe as its most dangerous segment. Classifying
# by the first word alone would auto-run the tail of `echo hi; systemctl ...`.

CHAINED_CONFIRM = [
    "echo hi; systemctl restart nginx",
    "ls -la && apt install nginx",
    "cat /etc/hosts || useradd bob",
    "nginx -t && systemctl reload nginx",
    "ls $(rm -rf /tmp/cache)",
    "echo `pip install requests`",
]

CHAINED_BLOCKED = [
    "echo x; rm -rf /",
    "df -h && mkfs.ext4 /dev/sdb1",
    "uptime; shutdown -h now",
    "ls | grep foo; history -c",
]

CHAINED_SAFE = [
    "df -h && free -m",
    "grep error /var/log/syslog | head -20",
    "cat /etc/os-release; uname -a",
    "ps aux | grep nginx | wc -l",
]


@pytest.mark.parametrize("command", CHAINED_CONFIRM)
def test_chained_command_inherits_its_worst_segment(command):
    assert classify(command).level == SafetyLevel.CONFIRM


@pytest.mark.parametrize("command", CHAINED_BLOCKED)
def test_a_destructive_segment_blocks_the_whole_chain(command):
    assert classify(command).level == SafetyLevel.BLOCKED


@pytest.mark.parametrize("command", CHAINED_SAFE)
def test_a_chain_of_read_only_commands_stays_safe(command):
    assert classify(command).level == SafetyLevel.SAFE


def test_the_reason_names_the_offending_segment():
    reason = classify("echo hi; systemctl restart nginx").reason
    assert "systemctl restart nginx" in reason


def test_split_segments_unpacks_operators_and_substitutions():
    from agent.policy import split_segments

    assert split_segments("ls -la && df -h") == ["ls -la", "df -h"]
    assert split_segments("echo $(whoami)") == ["whoami", "echo"]
    assert split_segments("a | b; c & d") == ["a", "b", "c", "d"]
