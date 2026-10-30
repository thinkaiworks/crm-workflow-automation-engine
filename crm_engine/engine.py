"""Run rules against a CRM: evaluate, de-duplicate, apply (or dry-run), notify, report."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

from .rules import Action, Rule


class FiredLog:
    """Remembers which (rule, record) pairs already fired so tasks aren't duplicated on every run."""

    def __init__(self, path: str | None):
        self.path = Path(path) if path else None
        self.keys: set[str] = set()
        if self.path and self.path.exists():
            self.keys = set(json.loads(self.path.read_text()))

    def seen(self, rule: str, record_id: str) -> bool:
        return f"{rule}|{record_id}" in self.keys

    def mark(self, rule: str, record_id: str) -> None:
        self.keys.add(f"{rule}|{record_id}")

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(sorted(self.keys)))


@dataclass
class RunReport:
    evaluated: int = 0
    actions: list[Action] = field(default_factory=list)
    skipped_already_fired: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        by_kind: dict[str, int] = {}
        for a in self.actions:
            by_kind[a.kind] = by_kind.get(a.kind, 0) + 1
        return {"records_evaluated": self.evaluated, "actions": by_kind, "skipped_already_fired": self.skipped_already_fired, "errors": len(self.errors)}


def notify(message: str, url: str | None = None) -> None:
    url = url or os.getenv("SLACK_WEBHOOK_URL")
    if url:
        requests.post(url, json={"text": message}, timeout=15)


def run(crm, rules: list[Rule], *, dry_run: bool = True, fired_log: FiredLog | None = None, now: datetime | None = None) -> RunReport:
    now = now or datetime.now(timezone.utc)
    fired_log = fired_log or FiredLog(None)
    report = RunReport()
    cache: dict[str, list[dict]] = {}
    for rule in rules:
        records = cache.setdefault(rule.object, crm.fetch(rule.object))
        for record in records:
            report.evaluated += 1
            if rule.once and fired_log.seen(rule.name, str(record["id"])):
                report.skipped_already_fired += 1
                continue
            actions = rule.actions_for(record, now)
            if not actions:
                continue
            for a in actions:
                report.actions.append(a)
                if dry_run:
                    continue
                try:
                    if a.kind == "notify":
                        notify(a.payload["message"])
                    else:
                        crm.apply(a)
                except Exception as exc:  # keep going; one bad record shouldn't stop the run
                    report.errors.append(f"{rule.name} on {a.record_id}: {exc}")
            if not dry_run:
                fired_log.mark(rule.name, str(record["id"]))
    if not dry_run:
        fired_log.save()
    return report
