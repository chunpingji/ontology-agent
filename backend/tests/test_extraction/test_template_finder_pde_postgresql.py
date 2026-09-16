"""PDE review CAS on an explicitly configured disposable PostgreSQL database."""

from tests.test_api.test_template_finder import finder_setup  # noqa: F401
from tests.test_api.test_template_finder_pde import pde_setup  # noqa: F401
from tests.test_extraction.test_template_finder_pde_concurrency import (  # noqa: F401
    test_different_owners_cannot_both_save_the_same_review_version,
)
from tests.test_reporting.test_reporting_postgresql import db  # noqa: F401
