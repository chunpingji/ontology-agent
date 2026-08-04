# `oa` — ontology-agent CLI

An agent-native command-line client (in the [HKUDS **CLI-Anything**](https://github.com/HKUDS/CLI-Anything)
spirit) for the ontology-agent backend. It mirrors the **Report Center** page's
**"生成风险评估报告"** (generate risk-assessment report) action: take an uploaded
document, resolve its extraction job, generate the risk-assessment `.docx`, and
save it locally.

Design principles (from CLI-Anything):

- **Thin client over the real backend** — every command is a direct call to a
  documented API endpoint; no reimplementation of business logic.
- **`--json` on every command** — a versioned envelope for agents; human tables
  otherwise.
- **REPL when bare** — running `oa` on a TTY drops into an interactive shell.
- **No silent fallbacks** — typed errors and stable exit codes.

## Install

```bash
cd cli
pip install -e .            # or: uv pip install -e .
oa --help
```

Requires Python ≥ 3.11 and just two runtime deps (`click`, `requests`).

## Configure

Precedence: **CLI flag > env var > config file > default**.

```bash
oa config set --api-url http://localhost:8000 --user analyst --role senior_analyst
oa config show             # bearer token is redacted
```

Config lives at `$XDG_CONFIG_HOME/ontology-agent-cli/config.json` (else
`~/.config/…`), written `0600`. Env vars: `OA_API_URL`, `OA_USER`, `OA_ROLE`,
`OA_TOKEN`.

Identity mirrors the frontend exactly: `X-User`/`X-Role` on every request, plus
`Authorization: Bearer` when a token is present. A dev backend
(`auth_required=false`) trusts the headers, so login is optional.

```bash
oa auth login --username analyst --password-stdin <<<'…'   # stores a 12h token
oa auth whoami
oa auth logout
```

## The primary flow

```bash
# From a document IRI (exactly what the Report Center button does):
oa report generate 'http://…/document/abc' -o ./out/

# …or straight from an extraction job id:
oa report generate --job-id 1a2b3c -o report.docx

# Async is the server default: the CLI polls until done, then downloads. Opt out:
oa report generate --job-id 1a2b3c --no-wait        # prints {report_id,status}; poll later
```

`generate` writes atomically and refuses to overwrite without `--force`. It is
**not idempotent** — each successful call creates a new report server-side.

## Other commands

```bash
oa docs list [--phase IRI]                     # documents + their resolved job ids
oa report list [--job-id JOB]                   # a job's reports, or a fan-out across docs
oa report status   <JOB_ID> <REPORT_ID>
oa report download <JOB_ID> [REPORT_ID] [-o PATH] [--force]   # omit id → latest completed
oa report delete   <JOB_ID> <REPORT_ID> [--yes]      # needs senior_analyst
oa report coverage <JOB_ID> [--template-id ID] [--fail-on-missing]
```

## Exit codes

| code | meaning | | code | meaning |
|--|--|--|--|--|
| 0 | ok | | 7 | precondition (HTTP 422) |
| 1 | unexpected | | 8 | conflict (HTTP 409) |
| 2 | usage | | 9 | network / server / other HTTP |
| 3 | config | | 10 | report generation failed |
| 4 | auth (401) | | 11 | wait timed out (report keeps generating) |
| 5 | forbidden (403) | | 130 | interrupted |
| 6 | not found (404) | | | |

On `--wait-timeout`, exit `11` still prints the `report_id` so you can resume
with `oa report download`.

## JSON envelope

```json
{ "schema_version": "1", "ok": true, "command": "report.generate",
  "data": { "path": "…", "bytes": 12345, "sha256": "…", "report_id": "…" },
  "context": { "api_url": "http://localhost:8000" } }
```

Errors go to stderr with `"ok": false` and `error.{code,message,detail}`.

## Tests

```bash
cd cli
pip install -e '.[test]'
pytest                     # stubbed HttpClient; no live backend needed
```
