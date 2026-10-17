"""AI investigation provider interface (placeholder; implementations arrive in Phase 6).

Contract (ARCHITECTURE.md §8): providers are pure functions of their input, never touch the
database, and return validated, typed output. The loosely typed `Mapping` signatures below are
replaced by Pydantic models (`InvestigationContext`, `ProviderResult`) in Phase 6.
"""

from collections.abc import Mapping
from typing import Protocol, runtime_checkable


@runtime_checkable
class InvestigationProvider(Protocol):
    name: str
    model_id: str

    def analyze(self, context: Mapping[str, object], *, timeout_s: float) -> Mapping[str, object]:
        """Analyze a bounded, redacted incident context and return a structured analysis."""
        ...
