"""Output formatting.

Two modes, chosen by the global ``--json`` flag stored in ``ctx.obj["json"]``:

* human — best-effort tables / key-value blocks (for people at a terminal);
* JSON — a **versioned envelope** (for agents):
      {"schema_version":"1","ok":true,"command":"report.generate",
       "data":{…},"context":{"api_url":"…"}}
      {"schema_version":"1","ok":false,"command":"…",
       "error":{"code":"…","message":"…","detail":…},"context":{…}}
  Success envelopes go to stdout; error envelopes go to stderr.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import click

SCHEMA_VERSION = "1"


# ---------------------------------------------------------------------- #
# Envelope plumbing
# ---------------------------------------------------------------------- #


def _command_name(ctx: click.Context | None) -> str:
    if ctx is None:
        return ""
    parts = (ctx.command_path or "").split()
    return ".".join(parts[1:]) if len(parts) > 1 else (parts[0] if parts else "")


def _context(ctx: click.Context | None) -> dict[str, Any]:
    obj = getattr(ctx, "obj", None) or {}
    cfg = obj.get("config")
    out: dict[str, Any] = {}
    if cfg is not None:
        out["api_url"] = getattr(cfg, "api_url", None)
    return out


def _json_mode(ctx: click.Context | None) -> bool:
    obj = getattr(ctx, "obj", None) or {}
    return bool(obj.get("json"))


def _dump(payload: Any, *, stream: Any) -> None:
    print(json.dumps(payload, indent=2, default=str, ensure_ascii=False), file=stream)


def print_result(ctx: click.Context | None, data: Any) -> None:
    """Emit a success result — JSON envelope or human-readable view."""
    if _json_mode(ctx):
        _dump(
            {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "command": _command_name(ctx),
                "data": data,
                "context": _context(ctx),
            },
            stream=sys.stdout,
        )
    else:
        print_human(data)


def print_error(
    ctx: click.Context | None,
    message: str,
    *,
    code: str = "error",
    detail: Any = None,
) -> None:
    """Emit an error — JSON envelope on stderr, or ``Error: …`` line."""
    if _json_mode(ctx):
        error: dict[str, Any] = {"code": code, "message": message}
        if detail is not None:
            error["detail"] = detail
        _dump(
            {
                "schema_version": SCHEMA_VERSION,
                "ok": False,
                "command": _command_name(ctx),
                "error": error,
                "context": _context(ctx),
            },
            stream=sys.stderr,
        )
    else:
        print(f"Error: {message}", file=sys.stderr)


# ---------------------------------------------------------------------- #
# Human formatting (ported from the infilake CLI-Anything reference)
# ---------------------------------------------------------------------- #


def print_human(data: Any) -> None:
    if data is None:
        print("(no output)")
        return
    if isinstance(data, list):
        if not data:
            print("(empty list)")
            return
        if isinstance(data[0], dict):
            cols = _collect_columns(data)
            rows = [[_fmt(d.get(c)) for c in cols] for d in data]
            print_table(cols, rows)
            return
        for item in data:
            print(f"- {_fmt(item)}")
        return
    if isinstance(data, dict):
        print_kv(data)
        return
    print(_fmt(data))


def print_kv(data: dict, *, indent: int = 0) -> None:
    pad = "  " * indent
    keys = list(data.keys())
    max_key = max((len(str(k)) for k in keys), default=0)
    for k in keys:
        v = data[k]
        key = str(k).ljust(max_key)
        if isinstance(v, dict) and v:
            print(f"{pad}{key}:")
            print_kv(v, indent=indent + 1)
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            print(f"{pad}{key}: ({len(v)} items)")
            for i, item in enumerate(v):
                print(f"{pad}  [{i}]")
                print_kv(item, indent=indent + 2)
        else:
            print(f"{pad}{key}: {_fmt(v)}")


def print_table(columns: list[str], rows: list[list[Any]], *, max_col_width: int = 60) -> None:
    if not columns:
        print("(no columns)")
        return
    widths = [min(len(c), max_col_width) for c in columns]
    str_rows: list[list[str]] = []
    for row in rows:
        cells = []
        for i, v in enumerate(row):
            s = _fmt(v)
            if len(s) > max_col_width:
                s = s[: max_col_width - 1] + "…"
            cells.append(s)
            if i < len(widths):
                widths[i] = min(max(widths[i], len(s)), max_col_width)
        str_rows.append(cells)
    sep = "  "
    print(sep.join(c.ljust(widths[i]) for i, c in enumerate(columns)))
    print(sep.join("-" * widths[i] for i in range(len(columns))))
    for cells in str_rows:
        print(sep.join((cells[i] if i < len(cells) else "").ljust(widths[i]) for i in range(len(columns))))


def _collect_columns(items: list[dict]) -> list[str]:
    seen: dict[str, None] = {}
    for item in items:
        for k in item.keys():
            seen.setdefault(k, None)
    return list(seen.keys())


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str, ensure_ascii=False)
    return str(v)
