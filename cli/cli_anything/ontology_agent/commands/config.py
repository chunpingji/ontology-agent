"""``oa config`` — show / set / reset persistent CLI configuration."""

from __future__ import annotations

import click

from cli_anything.ontology_agent.commands._ctx import print_result
from cli_anything.ontology_agent.core.config import Config


@click.group()
def config() -> None:
    """CLI configuration (backend URL + caller identity)."""


@config.command("show")
@click.pass_context
def config_show(ctx: click.Context) -> None:
    """Print the current configuration (bearer token redacted)."""
    cfg: Config = ctx.obj["config"]
    print_result(ctx, cfg.redacted())


@config.command("set")
@click.option("--api-url", default=None, help="Backend base URL, e.g. http://localhost:8000")
@click.option("--user", default=None, help="X-User identity for dev gateways")
@click.option("--role", default=None, help="X-Role, e.g. senior_analyst")
@click.option("--verify-ssl/--no-verify-ssl", "verify_ssl", default=None, help="TLS verification")
@click.pass_context
def config_set(
    ctx: click.Context,
    api_url: str | None,
    user: str | None,
    role: str | None,
    verify_ssl: bool | None,
) -> None:
    """Update one or more configuration fields (persisted)."""
    cfg: Config = ctx.obj["config"]
    updated = cfg.update(api_url=api_url, user=user, role=role, verify_ssl=verify_ssl)
    ctx.obj["config"] = updated
    print_result(ctx, updated.redacted())


@config.command("reset")
@click.confirmation_option(prompt="Reset all configuration (including any saved token) to defaults?")
@click.pass_context
def config_reset(ctx: click.Context) -> None:
    """Restore default configuration."""
    fresh = ctx.obj["config"].reset()
    ctx.obj["config"] = fresh
    print_result(ctx, fresh.redacted())
