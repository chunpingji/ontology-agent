"""Preview/apply the explicitly authorized HRS-5592 draft specialization, without app startup."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-hash", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from app.db import SessionLocal
    from app.services.reporting.batch_demo_template import specialize

    with SessionLocal() as db:
        result = specialize(
            db, expected_hash=args.expected_hash, actor=args.actor, apply=args.apply,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
