"""GUI entry point for ssh-agent.

Usage:
    python gui.py --host HOST --user USER [--password PASS] [--key KEYFILE]

Requires the "gui" extra: `uv sync --extra gui`.
"""
from __future__ import annotations

import argparse
import getpass
import sys

from dotenv import load_dotenv

from core.logger import SessionLogger
from core.ssh_session import SSHSession
from ui.main_window import run_app

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="ssh-agent GUI")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", default=None)
    parser.add_argument("--key", dest="key_filename", default=None)
    parser.add_argument("--log", default="session_log.jsonl")
    parser.add_argument("--session", default=None,
                        help="ادامهٔ یک سشن قبلی با همین شناسه")
    args = parser.parse_args()

    password = args.password
    if password is None and args.key_filename is None:
        password = getpass.getpass("Password: ")

    session = SSHSession(
        host=args.host,
        port=args.port,
        username=args.user,
        password=password,
        key_filename=args.key_filename,
    )
    logger = SessionLogger(args.log)
    run_app(session, logger, session_id=args.session)


if __name__ == "__main__":
    sys.exit(main())
