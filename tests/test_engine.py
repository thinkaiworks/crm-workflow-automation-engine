import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from crm_engine.adapters import HubSpotCRM, MemoryCRM
from crm_engine.engine import FiredLog, run
from crm_engine.rules import Action, check, load_rules, render

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)


def demo_crm():
    return MemoryCRM(json.loads((ROOT / "crm_engine" / "demo_records.json").read_text()))


def test_conditions():
    rec = {"status": "New", "score": 80, "created": "2026-10-20T00:00:00Z", "email": ""}
    assert check({"field": "status", "eq": "new"}, rec, NOW)
    assert check({"field": "score", "gt": 75}, rec, NOW)
    assert check({"field": "created", "older_than_days": 7}, rec, NOW)
    assert check({"field": "email", "exists": False}, rec, NOW)
    assert check({"any": [{"field": "status", "eq": "Lost"}, {"field": "score", "lt": 90}]}, rec, NOW)
    assert not check({"not": {"field": "status", "in": ["New", "Open"]}}, rec, NOW)


def test_hubspot_epoch_millis_dates():
    rec = {"last": 1761307200000}  # 2025-10-24
    assert check({"field": "last", "older_than_days": 30}, rec, NOW)


def test_render_placeholders():
    assert render("Call {firstname} {missing}", {"firstname": "Sara"}) == "Call Sara"


def test_demo_run_produces_expected_actions():
    report = run(demo_crm(), load_rules(ROOT / "rules.example.yaml"), dry_run=True, now=NOW)
    fired = {(a.rule, a.record_id) for a in report.actions}
    assert ("Hot new lead - call in 1 hour", "101") in fired
    assert ("New lead untouched for 3 days", "102") in fired
    assert ("Stalled in progress - 10 days no contact", "104") in fired
    assert ("Recently contacted - move to in progress", "103") in fired
    assert not any(a.record_id == "105" for a in report.actions)


def test_apply_updates_crm_and_never_double_fires(tmp_path):
    crm = demo_crm()
    rules = load_rules(ROOT / "rules.example.yaml")
    log = FiredLog(str(tmp_path / "fired.json"))
    first = run(crm, rules, dry_run=False, fired_log=log, now=NOW)
    assert crm.records["102"]["hs_lead_status"] == "ATTEMPTED_TO_CONTACT"
    assert len(crm.tasks) >= 3
    tasks_after_first = len(crm.tasks)

    second = run(crm, rules, dry_run=False, fired_log=FiredLog(str(tmp_path / "fired.json")), now=NOW)
    assert len(crm.tasks) == tasks_after_first
    assert second.skipped_already_fired > 0
    assert first.summary()["errors"] == 0


def test_duplicate_rule_names_rejected(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text("rules:\n  - {name: a, when: {field: x, eq: 1}, then: []}\n  - {name: a, when: {field: x, eq: 1}, then: []}\n")
    with pytest.raises(ValueError):
        load_rules(p)


class FakeResp:
    def __init__(self, data=None):
        self._data = data or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class FakeSession:
    def __init__(self):
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(("GET", url, params))
        if "after" not in params:
            return FakeResp({"results": [{"id": "1", "properties": {"firstname": "A"}}], "paging": {"next": {"after": "1"}}})
        return FakeResp({"results": [{"id": "2", "properties": {"firstname": "B"}}]})

    def post(self, url, json=None, timeout=None):
        self.calls.append(("POST", url, json))
        return FakeResp()

    def patch(self, url, json=None, timeout=None):
        self.calls.append(("PATCH", url, json))
        return FakeResp()


def test_hubspot_adapter_paginates_and_creates_task():
    s = FakeSession()
    hs = HubSpotCRM(token="x", session=s)
    rows = hs.fetch("contact")
    assert [r["id"] for r in rows] == ["1", "2"]
    hs.apply(Action("create_task", "1", "r", {"title": "Call", "due": "2026-10-30T12:00:00+00:00", "priority": "high"}))
    method, url, body = s.calls[-1]
    assert url.endswith("/crm/v3/objects/tasks")
    assert body["properties"]["hs_task_priority"] == "HIGH"
    assert body["associations"][0]["to"]["id"] == "1"
