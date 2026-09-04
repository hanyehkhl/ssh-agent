"""Three-tier safety classifier for shell commands the agent wants to run.

SAFE    -> read-only, executed automatically.
CONFIRM -> mutates state, requires human approval before execution.
BLOCKED -> destructive/irreversible, never executed.

A command is split on shell separators and every segment is classified; the
whole command takes the most severe verdict of its parts. Without that split,
a chained command would be judged only by how it starts, so `echo hi; systemctl
restart nginx` would pass as read-only and run without asking anyone.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class SafetyLevel(str, Enum):
    SAFE = "SAFE"
    CONFIRM = "CONFIRM"
    BLOCKED = "BLOCKED"


@dataclass
class Classification:
    level: SafetyLevel
    reason: str


# Order matters: BLOCKED patterns are checked first, then SAFE, then default CONFIRM.

BLOCKED_PATTERNS = [
    r"\brm\s+.*-[a-zA-Z]*r[a-zA-Z]*f\b.*\s+/(\s|$)",   # rm -rf /
    r"\brm\s+.*-[a-zA-Z]*r[a-zA-Z]*f\b.*\s+/\*",        # rm -rf /*
    r"\bmkfs(\.\w+)?\b",
    r"\bdd\s+.*of=/dev/(sd|nvme|hd|xvd)\w*",
    r":\(\)\s*\{\s*:\|\:&\s*\}\s*;\s*:",                 # fork bomb
    r"\bchmod\s+-R\s+777\s+/(\s|$)",
    r">\s*/dev/sd\w*",
    r"\bshutdown\b|\breboot\b|\bpoweroff\b|\bhalt\b",
    r"history\s+-c\b",
    r"\biptables\s+-F\b",
    r"\bdrop\s+database\b",
    r"\buserdel\s+.*-r\b.*\broot\b",
]

SAFE_PATTERNS = [
    r"^\s*ls(\s|$)",
    r"^\s*cat\s",
    r"^\s*tail\s",
    r"^\s*head\s",
    r"^\s*grep\s",
    r"^\s*find\s.*(-name|-type)",
    r"^\s*ps(\s|$)",
    r"^\s*df(\s|$)",
    r"^\s*du(\s|$)",
    r"^\s*free(\s|$)",
    r"^\s*uname",
    r"^\s*whoami",
    r"^\s*pwd",
    r"^\s*hostname",
    r"^\s*uptime",
    r"^\s*systemctl\s+(status|is-active|is-enabled|list-units|--failed)",
    r"^\s*journalctl\b",
    r"^\s*netstat\b|^\s*ss\s",
    r"^\s*id(\s|$)",
    r"^\s*env(\s|$)",
    r"^\s*echo\s",
    r"^\s*which\s",
    r"^\s*nginx\s+-t\b",
    r"^\s*curl\s.*-I\b",
    # Read-only text filters and inspectors, which usually appear after a pipe.
    r"^\s*wc(\s|$)",
    r"^\s*sort(\s|$)",
    r"^\s*uniq(\s|$)",
    r"^\s*cut\s",
    r"^\s*tr\s",
    r"^\s*awk\s",
    r"^\s*column\s",
    r"^\s*jq(\s|$)",
    r"^\s*date(\s|$)",
    r"^\s*stat\s",
    r"^\s*lsof\b",
    r"^\s*ip\s+(a|addr|r|route|link|-s)\b",
    r"^\s*docker\s+(ps|logs|inspect|images|stats)\b",
]


# Shell operators that begin a new command: ; && || | & and newline. Also
# treated as segment starts are $(...) and `...` substitutions, since the
# command inside them runs too.
_SEGMENT_SPLIT_RE = re.compile(r"\|\||&&|;|\||\n|&")
_SUBSTITUTION_RE = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")

_SEVERITY = {SafetyLevel.SAFE: 0, SafetyLevel.CONFIRM: 1, SafetyLevel.BLOCKED: 2}


def split_segments(command: str) -> list[str]:
    """Break a command line into the individual commands it will run."""
    segments: list[str] = []
    remainder = command

    # Pull out command substitutions first, then split what is left. The
    # substitution body is itself a command and is classified on its own.
    for match in _SUBSTITUTION_RE.finditer(command):
        inner = match.group(1) or match.group(2) or ""
        if inner.strip():
            segments.extend(split_segments(inner))
    remainder = _SUBSTITUTION_RE.sub(" ", remainder)

    segments.extend(part.strip() for part in _SEGMENT_SPLIT_RE.split(remainder))
    return [segment for segment in segments if segment]


def classify(command: str) -> Classification:
    """Classify a whole command line by the most severe of its segments."""
    # Some destructive patterns span the operators themselves (the fork bomb
    # `:(){ :|:& };:` is nothing but operators), so the unsplit line is checked
    # against the BLOCKED set before it is broken apart.
    whole = _match_blocked(command.strip())
    if whole:
        return whole

    segments = split_segments(command)
    if len(segments) > 1:
        verdicts = [(classify_segment(s), s) for s in segments]
        worst, segment = max(verdicts, key=lambda pair: _SEVERITY[pair[0].level])
        if worst.level != SafetyLevel.SAFE:
            return Classification(
                worst.level, f"بخش «{segment}» از دستور: {worst.reason}"
            )
        return worst
    return classify_segment(command)


def _match_blocked(command: str) -> Classification | None:
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return Classification(
                SafetyLevel.BLOCKED,
                f"دستور با الگوی مخرب/غیرقابل‌بازگشت مطابقت دارد: {pattern}",
            )
    return None


def classify_segment(command: str) -> Classification:
    """Classify one single command (no shell operators)."""
    stripped = command.strip()

    blocked = _match_blocked(stripped)
    if blocked:
        return blocked

    for pattern in SAFE_PATTERNS:
        if re.search(pattern, stripped, re.IGNORECASE):
            return Classification(SafetyLevel.SAFE, "دستور فقط‌خواندنی است")

    return Classification(
        SafetyLevel.CONFIRM,
        "دستور می‌تواند وضعیت سیستم را تغییر دهد و نیاز به تأیید کاربر دارد",
    )
