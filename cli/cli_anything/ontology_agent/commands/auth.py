"""``oa auth`` — login / whoami / logout.

Login stores a 12h bearer token (``POST /api/auth/login``). With a dev backend
(``auth_required=false``) login is optional — X-User/X-Role suffice.
"""

from __future__ import annotations

import sys

import click

from cli_anything.ontology_agent.commands._ctx import build_client, handle, print_result
from cli_anything.ontology_agent.core import auth as auth_mod
from cli_anything.ontology_agent.core import session as session_mod
from cli_anything.ontology_agent.core.config import Config


@click.group()
def auth() -> None:
    """Authentication: login / whoami / logout."""


@auth.command("login")
@click.option("--username", default=None, help="Username (prompts if omitted)")
@click.option("--password", default=None, help="Password (INSECURE on shared hosts; prefer --password-stdin)")
@click.option("--password-stdin", is_flag=True, help="Read the password from stdin")
@click.pass_context
def auth_login(
    ctx: click.Context,
    username: str | None,
    password: str | None,
    password_stdin: bool,
) -> None:
    """Log in and persist a bearer token."""
    cfg: Config = ctx.obj["config"]

    if not username:
        username = click.prompt("Username", type=str)
    if password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    elif not password:
        password = click.prompt("Password", hide_input=True)

    result = handle(ctx, auth_mod.login, build_client(ctx), username, password)
    updated = session_mod.apply_login(
        cfg,
        token=result["token"],
        username=result.get("username", username),
        role=result.get("role"),
    )
    ctx.obj["config"] = updated
    print_result(
        ctx,
        {
            "logged_in": True,
            "username": result.get("username", username),
            "role": result.get("role"),
            "display_name": result.get("display_name"),
        },
    )


@auth.command("whoami")
@click.pass_context
def auth_whoami(ctx: click.Context) -> None:
    """Show the identity the backend resolves for the current credentials."""
    data = handle(ctx, auth_mod.whoami, build_client(ctx))
    print_result(ctx, data)


@auth.command("logout")
@click.pass_context
def auth_logout(ctx: click.Context) -> None:
    """Clear the locally stored bearer token (server token is stateless)."""
    cfg: Config = ctx.obj["config"]
    updated = session_mod.clear_token(cfg)
    ctx.obj["config"] = updated
    print_result(ctx, {"logged_out": True})
