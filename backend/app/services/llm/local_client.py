"""012: Local LLM client wrapper (OpenAI-compatible endpoint).

Returns ``None`` when ``local_llm_enabled=False`` or the ``openai`` package
is not installed — callers gate on the return value so the pipeline degrades
gracefully to zero-LLM behaviour.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass  # openai.OpenAI is only used at runtime

logger = logging.getLogger(__name__)


def get_local_llm():
    """Return an ``openai.OpenAI`` client pointed at the local LLM, or *None*.

    Conditions that yield *None* (no error raised):
    - ``settings.local_llm_enabled`` is ``False``
    - The ``openai`` package is not importable (missing optional dep)
    """
    from app.config import settings

    if not settings.local_llm_enabled:
        return None

    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai package not installed — LLM gap filling disabled")
        return None

    return OpenAI(
        base_url=settings.local_llm_base_url,
        api_key=settings.local_llm_api_key,
        timeout=300.0,
    )
