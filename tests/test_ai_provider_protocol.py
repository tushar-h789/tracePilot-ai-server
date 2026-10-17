from collections.abc import Mapping

from app.integrations.ai.provider import InvestigationProvider


class _Dummy:
    name = "dummy"
    model_id = "dummy-0"

    def analyze(self, context: Mapping[str, object], *, timeout_s: float) -> Mapping[str, object]:
        return {"summary": "n/a", "timeout_s": timeout_s, "keys": sorted(context)}


def test_protocol_is_structural() -> None:
    provider: InvestigationProvider = _Dummy()
    assert isinstance(provider, InvestigationProvider)
    assert not isinstance(object(), InvestigationProvider)
    assert provider.analyze({"incident": {}}, timeout_s=1.0)["keys"] == ["incident"]
