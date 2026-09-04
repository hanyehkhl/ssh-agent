"""CLI entry point for ssh-agent.

Usage:
    python cli.py --host HOST --user USER [--password PASS] [--key KEYFILE]
    python cli.py --list-sessions

The terminal UX follows agno's `print_response`: a live status line while the
agent works, one panel per tool call colored by its safety level, and a final
answer panel with the run's metrics.
"""
from __future__ import annotations

import argparse
import getpass
import sys

from dotenv import load_dotenv
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table
from rich.text import Text

from agent.events import (
    AgentContent,
    RunCompleted,
    RunError,
    ToolCallCompleted,
    ToolCallStarted,
)
from agent.loop import Agent
from agent.session import SessionStore
from agent.tools import ToolContext
from core.logger import SessionLogger
from core.ssh_session import SSHSession

load_dotenv()

# Persian output needs UTF-8; the Windows console still defaults to a legacy
# code page, which would raise UnicodeEncodeError on the first prompt.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

console = Console()

SAFETY_STYLES = {"SAFE": "green", "CONFIRM": "yellow", "BLOCKED": "red"}
BANNER = "[bold cyan]ssh-agent[/] — دستیار SSH فارسی‌زبان با حلقهٔ ایجنتیک و موتور ایمنی"


def confirm_in_terminal(command: str, why: str, reason: str) -> bool:
    console.print(Panel(
        Group(
            Text(f"$ {command}", style="bold white"),
            Text(f"دلیل مدل: {why}", style="dim"),
            Text(f"چرا تأیید لازم است: {reason}", style="dim"),
        ),
        title="[bold yellow]نیاز به تأیید[/]",
        border_style="yellow",
    ))
    return Confirm.ask("  اجرا شود؟", default=False, console=console)


def print_sessions(store: SessionStore) -> int:
    sessions = store.list_sessions()
    if not sessions:
        console.print("[dim]هیچ سشن ذخیره‌شده‌ای پیدا نشد.[/]")
        return 0
    table = Table(title="سشن‌های ذخیره‌شده", header_style="bold cyan")
    table.add_column("session id")
    table.add_column("آخرین به‌روزرسانی")
    table.add_column("نوبت‌ها", justify="right")
    table.add_column("اولین پیام", overflow="ellipsis", max_width=48)
    for item in sessions:
        table.add_row(
            item["session_id"], item["updated_at"][:19].replace("T", " "),
            str(item["turns"]), item["first_message"],
        )
    console.print(table)
    return 0


def render_run(agent: Agent, user_text: str):
    """Consume one run's event stream and render it to the terminal."""
    status = console.status("[dim]ایجنت در حال کار...[/]", spinner="dots")
    running = False

    def spinner(on: bool):
        # Panels must not be printed while the spinner holds the live region,
        # so every print is bracketed by stopping and restarting it.
        nonlocal running
        if on and not running:
            status.start()
        elif not on and running:
            status.stop()
        running = on

    spinner(True)
    try:
        for event in agent.run(user_text):
            if isinstance(event, ToolCallStarted):
                status.update(f"[dim]در حال اجرای[/] [bold]{event.command}[/]")
            elif isinstance(event, ToolCallCompleted):
                spinner(False)
                style = SAFETY_STYLES.get(event.safety_level, "white")
                body = event.result if len(event.result) < 1500 else event.result[:1500] + "\n…"
                console.print(Panel(
                    Text(body),
                    title=f"[{style}][{event.safety_level}][/] $ {event.command}",
                    border_style=style,
                ))
                spinner(True)
            elif isinstance(event, AgentContent):
                spinner(False)
                console.print(Markdown(event.text))
                spinner(True)
            elif isinstance(event, RunCompleted):
                spinner(False)
                console.print(Panel(
                    Markdown(event.content),
                    title="[bold cyan]ایجنت[/]",
                    subtitle=f"[dim]{event.metrics.summary()}[/]",
                    border_style="cyan",
                ))
            elif isinstance(event, RunError):
                spinner(False)
                console.print(Panel(event.message, title="[bold red]خطا[/]", border_style="red"))
    finally:
        spinner(False)


def main():
    parser = argparse.ArgumentParser(description="ssh-agent CLI")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--user")
    parser.add_argument("--password", default=None)
    parser.add_argument("--key", dest="key_filename", default=None)
    parser.add_argument("--log", default="session_log.jsonl")
    parser.add_argument("--session", default=None,
                        help="ادامهٔ یک سشن قبلی با همین شناسه (یا ساخت سشن جدید با این نام)")
    parser.add_argument("--list-sessions", action="store_true",
                        help="نمایش سشن‌های ذخیره‌شده و خروج")
    parser.add_argument("--no-store", action="store_true",
                        help="تاریخچهٔ گفتگو روی دیسک ذخیره نشود")
    args = parser.parse_args()

    store = SessionStore()
    if args.list_sessions:
        return print_sessions(store)
    if not args.host or not args.user:
        parser.error("--host and --user are required (or use --list-sessions)")

    password = args.password
    if password is None and args.key_filename is None:
        password = getpass.getpass("Password: ")

    console.print(BANNER)
    with console.status(f"[dim]اتصال به {args.user}@{args.host}:{args.port} ...[/]"):
        session = SSHSession(
            host=args.host, port=args.port, username=args.user,
            password=password, key_filename=args.key_filename,
        )
    logger = SessionLogger(args.log)
    ctx = ToolContext(session=session, logger=logger, confirm_fn=confirm_in_terminal)
    agent = Agent(ctx, session_id=args.session, store=None if args.no_store else store)

    console.print(
        f"[green]متصل شد.[/] سشن: [bold]{agent.session_id}[/]  "
        "[dim](خروج: exit / quit)[/]\n"
    )
    try:
        while True:
            try:
                user_input = console.input("[bold]شما>[/] ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if user_input.lower() in ("exit", "quit", "خروج"):
                break
            if not user_input:
                continue
            render_run(agent, user_input)
    finally:
        session.close()
        console.print(f"[dim]سشن {agent.session_id} بسته شد.[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
