"""A dependency-free REPL (plain ``input()`` — no prompt-toolkit).

Bare ``oa`` on a TTY drops here. Each line is tokenised with ``shlex`` and
dispatched back through the same Click group with ``standalone_mode=False`` so
per-command ``SystemExit`` / ``ClickException`` don't tear down the loop. The
shared ``ctx.obj`` carries config + flags across lines; ``_in_repl`` guards
against the root callback recursing back into the REPL.
"""

from __future__ import annotations

import shlex
from typing import Any

import click

_EXIT_WORDS = {"exit", "quit", ":q"}
_HELP_WORDS = {"help", "?"}


def run_repl(cli: click.Command, ctx: click.Context) -> None:
    obj: dict[str, Any] = ctx.obj
    obj["_in_repl"] = True
    click.echo("ontology-agent CLI — type a command, 'help', or 'exit' (Ctrl-D to quit).")

    while True:
        try:
            line = input("oa> ").strip()
        except EOFError:
            click.echo()
            break
        except KeyboardInterrupt:
            click.echo("^C", err=True)  # interrupt the current line, not the session
            continue

        if not line:
            continue
        if line in _EXIT_WORDS:
            break
        args = ["--help"] if line in _HELP_WORDS else _tokenize(line)
        if args is None:
            continue

        try:
            cli.main(args=args, prog_name="oa", standalone_mode=False, obj=obj)
        except SystemExit:
            pass  # --help / ctx.exit() from a command; envelope already printed
        except click.ClickException as exc:
            exc.show()
        except click.exceptions.Abort:
            click.echo("Aborted!", err=True)
        except KeyboardInterrupt:
            click.echo("^C", err=True)
        except Exception as exc:  # noqa: BLE001 — keep the REPL alive
            click.echo(f"error: {exc}", err=True)


def _tokenize(line: str) -> list[str] | None:
    try:
        return shlex.split(line)
    except ValueError as exc:
        click.echo(f"parse error: {exc}", err=True)
        return None
