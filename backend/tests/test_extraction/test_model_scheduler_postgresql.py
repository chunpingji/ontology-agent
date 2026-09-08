"""Same request lifecycle tested with independent PostgreSQL transactions."""

import pytest

from tests.test_extraction.test_model_scheduler import (  # noqa: F401
    test_cancel_closes_http_before_releasing_slot_and_does_not_retry,
    test_fifo_capacity_and_expired_slot_fencing,
    test_pause_in_queue_never_sends_http,
    test_queue_time_uses_total_budget_even_before_http,
    test_retries_share_logical_id_and_total_deadline,
    test_shared_pool_caps_actual_http_across_jobs_and_model_consumers,
    test_total_deadline_covers_read_chunks_not_just_each_socket_read,
)
from tests.test_reporting.test_reporting_postgresql import db  # noqa: F401


@pytest.fixture
def request_db(request):
    return request.getfixturevalue("db").get_bind()
