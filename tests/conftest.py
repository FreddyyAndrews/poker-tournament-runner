import pytest


@pytest.fixture(autouse=True)
def _isolated_external_usage(tmp_path, monkeypatch):
    """Matches against external bots log their hands for quotas; keep tests'
    hands out of the real usage log (data/external/usage.jsonl)."""
    from arena import external
    monkeypatch.setattr(external, "USAGE_FILE", tmp_path / "external_usage.jsonl")


@pytest.fixture(autouse=True)
def proxy_state(monkeypatch):
    """Matches against external bots ask the outside-bot proxy (arena/proxy.py)
    whether each bot is on. Tests don't run one: they get this status, with
    both bots on, and can change it (tests/test_proxy.py runs a real proxy)."""
    from arena import external

    def upstream(title):
        return {"title": title, "allowed": True, "why": None, "in_flight": 0, "waiting": 0,
                "max_in_flight": 3, "daily_hands": 20000, "hands_today": 0}

    state = {"ok": True, "started": "test", "upstreams": {
        "slumbot": upstream("Slumbot"),
        "gto_wizard": {**upstream("GTO Wizard"), "daily_hands_per_bot": 5000}}}
    monkeypatch.setattr(external, "proxy_status", lambda: state)
    return state
