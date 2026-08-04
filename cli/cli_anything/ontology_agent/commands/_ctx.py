"""Shared helpers for command modules — client construction + error handling."""

from __future__ import annotations

from typing import Any

import click

from cli_anything.ontology_agent.core.client import HttpClient
from cli_anything.ontology_agent.core.config import Config
from cli_anything.ontology_agent.core.errors import CliError
from cli_anything.ontology_agent.core.output import print_error, print_result  # noqa: F401 — re-export


def build_client(ctx: click.Context) -> HttpClient:
    cfg: Config = ctx.obj["config"]
    verbose: bool = ctx.obj.get("verbose", False)
    return HttpClient(cfg, verbose=verbose)


def handle(ctx: click.Context, fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a domain function; translate exceptions into the unified error
    envelope + a stable exit code. On error this calls ``ctx.exit`` and does
    not return."""
    try:
        return fn(*args, **kwargs)
    except CliError as e:
        print_error(ctx, e.message, code=e.code, detail=e.detail)
        ctx.exit(e.exit_code)
    except click.exceptions.Abort:
        raise
    except Exception as e:  # noqa: BLE001 — last-resort mapping to exit 1
        print_error(ctx, f"未预期错误：{e}", code="unexpected")
        ctx.exit(1)
