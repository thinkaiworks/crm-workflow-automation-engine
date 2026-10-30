"""CLI: python -m crm_engine --crm demo|hubspot|zoho --rules rules.yaml [--apply]"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .adapters import HubSpotCRM, MemoryCRM, ZohoCRM
from .engine import FiredLog, run
from .rules import load_rules


def main() -> None:
    p = argparse.ArgumentParser(description="Run CRM automation rules")
    p.add_argument("--crm", choices=["demo", "hubspot", "zoho"], default="demo")
    p.add_argument("--rules", default="rules.example.yaml")
    p.add_argument("--apply", action="store_true", help="Actually write to the CRM (default is a dry run)")
    p.add_argument("--state", default="data/fired.json", help="Where to remember which rules already fired")
    args = p.parse_args()

    if args.crm == "demo":
        records = json.loads(Path(__file__).with_name("demo_records.json").read_text())
        crm = MemoryCRM(records)
        now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    else:
        crm = HubSpotCRM() if args.crm == "hubspot" else ZohoCRM()
        now = None

    report = run(crm, load_rules(args.rules), dry_run=not args.apply, fired_log=FiredLog(args.state if args.apply else None), now=now)
    mode = "APPLIED" if args.apply else "DRY RUN (nothing written, add --apply to go live)"
    print(f"\n{mode}\n")
    for a in report.actions:
        detail = a.payload.get("title") or a.payload.get("message") or a.payload.get("body") or a.payload
        print(f"  [{a.rule}] {a.kind:<12} record {a.record_id}: {detail}")
    print("\nSummary:", json.dumps(report.summary()))
    for e in report.errors:
        print("  ERROR", e)


if __name__ == "__main__":
    main()
