"""Smoke tests: help for the root + each subgroup, version, bare non-TTY."""

from __future__ import annotations

from cli_anything.ontology_agent.oa_cli import cli


def test_root_help(runner):
    res = runner.invoke(cli, ["--help"])
    assert res.exit_code == 0
    for name in ("auth", "config", "docs", "report"):
        assert name in res.output


def test_report_help_lists_subcommands(runner):
    res = runner.invoke(cli, ["report", "--help"])
    assert res.exit_code == 0
    for name in ("generate", "list", "status", "download", "delete", "coverage"):
        assert name in res.output


def test_generate_help_has_job_and_wait_flags(runner):
    res = runner.invoke(cli, ["report", "generate", "--help"])
    assert res.exit_code == 0
    assert "--job-id" in res.output
    assert "--no-wait" in res.output


def test_subgroup_helps(runner):
    for group in ("auth", "config", "docs"):
        res = runner.invoke(cli, [group, "--help"])
        assert res.exit_code == 0, f"{group} --help failed"


def test_version(runner):
    res = runner.invoke(cli, ["--version"])
    assert res.exit_code == 0
    assert "0.1.0" in res.output


def test_bare_invocation_non_tty_prints_help(runner):
    # CliRunner streams are not TTYs → root prints help instead of REPL.
    res = runner.invoke(cli, [])
    assert res.exit_code == 0
    assert "report" in res.output
