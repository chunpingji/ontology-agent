"""``oa`` — root command group and entrypoint.

Thin Click client over the ontology-agent backend, in the HKUDS CLI-Anything
spirit: ``--json`` on every command for agents, a REPL when run bare on a TTY,
no hidden fallbacks. The Report Center's "生成风险评估报告" action lives at
``oa report generate``.
"""

from __future__ import annotations

import sys
from dataclasses import replace

import click

from cli_anything.ontology_agent import VERSION
from cli_anything.ontology_agent.commands.auth import auth
from cli_anything.ontology_agent.commands.config import config
from cli_anything.ontology_agent.commands.docs import docs
from cli_anything.ontology_agent.commands.report import report
from cli_anything.ontology_agent.core.config import Config
from cli_anything.ontology_agent.core.errors import EXIT_INTERRUPT
from cli_anything.ontology_agent.utils.repl import run_repl


@click.group(invoke_without_command=True)
@click.option("--json", "json_out", is_flag=True, help="Emit a machine-readable JSON envelope (agent mode)")
@click.option("--api-url", default=None, help="Backend base URL (overrides config/env for this run)")
@click.option("--user", default=None, help="X-User identity (overrides config/env for this run)")
@click.option("--role", default=None, help="X-Role (overrides config/env for this run)")
@click.option("--token", default=None, help="Bearer token (overrides config/env for this run)")
@click.option("-v", "--verbose", is_flag=True, help="Log HTTP requests to stderr")
@click.version_option(VERSION, "-V", "--version", prog_name="oa")
@click.pass_context
def cli(
    ctx: click.Context,
    json_out: bool,
    api_url: str | None,
    user: str | None,
    role: str | None,
    token: str | None,
    verbose: bool,
) -> None:
    """ontology-agent CLI — risk-assessment reports from uploaded documents."""
    if ctx.obj is None:
        ctx.obj = {}

    # Build config once (first entry). REPL re-dispatch reuses the same obj, so
    # the session's identity/URL stays stable; only --json can be upgraded per line.
    if "config" not in ctx.obj:
        cfg = Config.load()
        overrides = {
            k: v
            for k, v in {"api_url": api_url, "user": user, "role": role, "token": token}.items()
            if v is not None
        }
        if overrides:
            cfg = replace(cfg, **overrides)
        ctx.obj["config"] = cfg
        ctx.obj["json"] = json_out
        ctx.obj["verbose"] = verbose
    elif json_out:
        ctx.obj["json"] = True

    if ctx.invoked_subcommand is None and not ctx.obj.get("_in_repl"):
        if sys.stdin.isatty() and sys.stdout.isatty():
            run_repl(cli, ctx)
        else:
            click.echo(ctx.get_help())


cli.add_command(auth)
cli.add_command(config)
cli.add_command(docs)
cli.add_command(report)


def main() -> None:
    """Console-script entrypoint (``oa`` / ``ontology-agent``)."""
    try:
        cli.main(prog_name="oa")
    except KeyboardInterrupt:
        click.echo(err=True)
        sys.exit(EXIT_INTERRUPT)


if __name__ == "__main__":
    main()
