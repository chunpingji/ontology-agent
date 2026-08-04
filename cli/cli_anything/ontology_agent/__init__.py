"""``oa`` — an agent-native CLI over the ontology-agent backend, mirroring the
Report Center page's "生成风险评估报告" (generate risk-assessment report) flow.

CLI-Anything philosophy: a thin Click client over the real backend, ``--json``
on every command, REPL when invoked bare, no silent fallbacks.
"""

from __future__ import annotations

VERSION = "0.1.0"
