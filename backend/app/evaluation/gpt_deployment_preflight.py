"""Exercise the current production Harness Responses client against the GPT gateway."""

import argparse
import getpass
import json
from pathlib import Path

from app.config import settings
from app.services.document_harness.model import call_model, freeze_policy
from app.services.llm.model_runtime import model_scope


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--use-configured-key", action="store_true")
    args = parser.parse_args()
    case = json.loads(args.input.read_text())
    settings.local_llm_enabled = True
    if args.model:
        settings.local_llm_model = args.model
        settings.local_llm_model_revision = ""
    if args.base_url:
        settings.local_llm_base_url = args.base_url
    if not args.use_configured_key:
        settings.local_llm_api_key = getpass.getpass("Gateway API key: ")
    try:
        with model_scope(on_harness_event=lambda *_: None):
            answer = call_model("referent_selection", case["payload"], case["schema"],
                                freeze_policy())
        result = {
            "error": answer["error"], "status": answer["raw_response"]["response_status"],
            "model": settings.local_llm_model, "base_url": settings.local_llm_base_url,
            "usage": {name: answer["usage"].get(name) for name in (
                "input_tokens", "output_tokens",
            )},
            "verdict": (answer["output"] or {}).get("verdict"),
            "selected_partition_id": (answer["output"] or {}).get("selected_partition_id"),
        }
        print(json.dumps(result, ensure_ascii=False), flush=True)
    except Exception as exc:
        cause = exc.__cause__
        print(json.dumps({"exception_type": type(exc).__name__, "code": str(exc),
                          "cause_type": type(cause).__name__ if cause else None,
                          "http_status": getattr(cause, "status_code", None)},
                         ensure_ascii=False), flush=True)
        raise SystemExit(1) from None
    finally:
        if not args.use_configured_key:
            settings.local_llm_api_key = ""


if __name__ == "__main__":
    main()
