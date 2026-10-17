"""TracePilot demo sample service: deterministic synthetic traffic for demos and tests."""

from sample_service.scenarios import SCENARIOS, Scenario, generate_events

__all__ = ["SCENARIOS", "Scenario", "generate_events"]
