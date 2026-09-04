"""Unit tests for the parts of the session protocol that need no server."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.ssh_session import _PROMPT_MARKER, _ANSI_ESCAPE_RE, _RESOLVED_MARKER_RE


def test_the_echoed_command_line_does_not_end_the_read():
    # An interactive shell echoes the command back before running it. That
    # echo contains the marker with an unexpanded "$?" — treating it as the
    # end of the command returns empty output and spills the real output into
    # whatever runs next.
    echo = f"du -sh /tmp; echo {_PROMPT_MARKER}:$?"
    assert _PROMPT_MARKER in echo
    assert _RESOLVED_MARKER_RE.search(echo) is None


def test_the_resolved_marker_yields_the_exit_code():
    match = _RESOLVED_MARKER_RE.search(f"51M\t/tmp\n{_PROMPT_MARKER}:0\n")
    assert match and int(match.group(1)) == 0

    match = _RESOLVED_MARKER_RE.search(f"boom\n{_PROMPT_MARKER}:127\n")
    assert match and int(match.group(1)) == 127


def test_ansi_escapes_are_stripped_from_terminal_noise():
    noisy = "\x1b[?2004h\x1b[0;32mok\x1b[0m\r\n"
    assert _ANSI_ESCAPE_RE.sub("", noisy) == "ok\n"
