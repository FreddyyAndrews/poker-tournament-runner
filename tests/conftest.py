import pytest


@pytest.fixture(autouse=True)
def _isolated_external_usage(tmp_path, monkeypatch):
    """Matches against external bots log their hands for quotas; keep tests'
    hands out of the real usage log (data/external/usage.jsonl)."""
    from arena import external
    monkeypatch.setattr(external, "USAGE_FILE", tmp_path / "external_usage.jsonl")
