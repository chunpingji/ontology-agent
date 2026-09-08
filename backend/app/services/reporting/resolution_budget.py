"""A shared hard budget across binding traversal and repeated execution scopes."""

from dataclasses import dataclass

from app.services.reporting.template_v2 import ReportingError


@dataclass
class ResolutionBudget:
    limit: int
    spent: int = 0

    def consume(self, count=1):
        if self.spent + count > self.limit:
            raise ReportingError("RESOLUTION_BUDGET_EXCEEDED")
        self.spent += count
