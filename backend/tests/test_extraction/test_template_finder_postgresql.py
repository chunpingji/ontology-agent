"""Same Finder identity and fence contracts on a dedicated disposable PostgreSQL DB."""

from tests.test_api.test_template_finder import finder_setup  # noqa: F401
from tests.test_extraction.test_template_finder_execution import (  # noqa: F401
    test_concurrent_replay_starts_only_one_worker,
    test_expired_worker_cannot_publish_over_new_generation,
)
from tests.test_reporting.test_reporting_postgresql import db  # noqa: F401
