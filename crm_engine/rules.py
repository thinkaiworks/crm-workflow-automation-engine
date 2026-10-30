"""Rule engine: plain-English YAML rules -> CRM actions.

A rule has a `when` block (conditions on a CRM record) and a `then` block (actions).
Conditions support: eq, ne, in, not_in, exists, contains, gt, lt, older_than_days, newer_than_days.
Combine with `all:` (AND) and `any:` (OR), nest as deep as you like.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import yaml


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):  # HubSpot sends epoch milliseconds
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    s = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def check(cond: dict, record: dict, now: datetime) -> bool:
    if "all" in cond:
        return all(check(c, record, now) for c in cond["all"])
    if "any" in cond:
        return any(check(c, record, now) for c in cond["any"])
    if "not" in cond:
        return not check(cond["not"], record, now)

    fieldname = cond["field"]
    value = record.get(fieldname)
    for op, expected in cond.items():
        if op == "field":
            continue
        if op == "eq" and str(value).lower() != str(expected).lower():
            return False
        if op == "ne" and str(value).lower() == str(expected).lower():
            return False
        if op == "in" and str(value).lower() not in {str(e).lower() for e in expected}:
            return False
        if op == "not_in" and str(value).lower() in {str(e).lower() for e in expected}:
            return False
        if op == "exists" and bool(value not in (None, "")) != bool(expected):
            return False
        if op == "contains" and str(expected).lower() not in str(value or "").lower():
            return False
        if op in ("gt", "lt"):
            v = _num(value)
            if v is None or (op == "gt" and not v > expected) or (op == "lt" and not v < expected):
                return False
        if op in ("older_than_days", "newer_than_days"):
            dt = parse_dt(value)
            if dt is None:
                return False
            age = now - dt
            if op == "older_than_days" and age < timedelta(days=expected):
                return False
            if op == "newer_than_days" and age > timedelta(days=expected):
                return False
    return True


def render(template: str, record: dict) -> str:
    """Fill {field} placeholders from the record; unknown fields render as blank."""
    return re.sub(r"\{(\w+)\}", lambda m: str(record.get(m.group(1)) or ""), template).strip()


@dataclass
class Action:
    kind: str  # update | create_task | add_note | notify
    record_id: str
    rule: str
    payload: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Idempotency key: the same rule never fires twice on the same record state."""
        return f"{self.rule}|{self.record_id}|{self.kind}|{sorted(self.payload.items())}"


@dataclass
class Rule:
    name: str
    when: dict
    then: list[dict]
    object: str = "contact"
    once: bool = True  # fire at most once per record

    def actions_for(self, record: dict, now: datetime) -> list[Action]:
        if not check(self.when, record, now):
            return []
        out: list[Action] = []
        rid = str(record["id"])
        for step in self.then:
            if "set" in step:
                out.append(Action("update", rid, self.name, {k: render(str(v), record) for k, v in step["set"].items()}))
            elif "create_task" in step:
                t = step["create_task"]
                due = now + timedelta(hours=t.get("due_in_hours", 24))
                out.append(Action("create_task", rid, self.name, {
                    "title": render(t["title"], record),
                    "body": render(t.get("body", ""), record),
                    "due": due.replace(microsecond=0).isoformat(),
                    "priority": t.get("priority", "normal"),
                }))
            elif "add_note" in step:
                out.append(Action("add_note", rid, self.name, {"body": render(step["add_note"], record)}))
            elif "notify" in step:
                out.append(Action("notify", rid, self.name, {"message": render(step["notify"], record)}))
            else:
                raise ValueError(f"Unknown action in rule '{self.name}': {step}")
        return out


def load_rules(path: str) -> list[Rule]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    rules = [Rule(**r) for r in data["rules"]]
    names = [r.name for r in rules]
    if len(names) != len(set(names)):
        raise ValueError("Rule names must be unique")
    return rules
