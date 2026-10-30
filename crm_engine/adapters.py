"""CRM adapters. Each one fetches records as flat dicts and applies Actions.

- MemoryCRM: in-memory, for demos and tests
- HubSpotCRM: HubSpot CRM v3 (private app token)
- ZohoCRM:    Zoho CRM v7 (OAuth refresh token)
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import requests

from .rules import Action, parse_dt


class MemoryCRM:
    def __init__(self, records: list[dict]):
        self.records = {str(r["id"]): dict(r) for r in records}
        self.tasks: list[dict] = []
        self.notes: list[dict] = []

    def fetch(self, object_type: str) -> list[dict]:
        return [r for r in self.records.values() if r.get("object", "contact") == object_type]

    def apply(self, a: Action) -> None:
        if a.kind == "update":
            self.records[a.record_id].update(a.payload)
        elif a.kind == "create_task":
            self.tasks.append({"record_id": a.record_id, **a.payload})
        elif a.kind == "add_note":
            self.notes.append({"record_id": a.record_id, **a.payload})


class HubSpotCRM:
    BASE = "https://api.hubapi.com"
    OBJECTS = {"contact": "contacts", "deal": "deals", "company": "companies"}
    PROPS = {
        "contact": ["firstname", "lastname", "email", "phone", "hs_lead_status", "lifecyclestage", "hubspot_owner_id",
                    "notes_last_contacted", "hs_last_sales_activity_timestamp", "createdate", "num_contacted_notes"],
        "deal": ["dealname", "dealstage", "amount", "closedate", "hubspot_owner_id", "notes_last_contacted", "createdate", "hs_lastmodifieddate"],
    }

    def __init__(self, token: str | None = None, session: requests.Session | None = None):
        self.token = token or os.environ["HUBSPOT_TOKEN"]
        self.s = session or requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})

    def fetch(self, object_type: str) -> list[dict]:
        obj = self.OBJECTS[object_type]
        props = ",".join(self.PROPS.get(object_type, []))
        out, after = [], None
        while True:
            params = {"limit": 100, "properties": props}
            if after:
                params["after"] = after
            r = self.s.get(f"{self.BASE}/crm/v3/objects/{obj}", params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            for row in data.get("results", []):
                out.append({"id": row["id"], "object": object_type, **row.get("properties", {})})
            after = data.get("paging", {}).get("next", {}).get("after")
            if not after:
                return out

    def apply(self, a: Action) -> None:
        if a.kind == "update":
            obj = "contacts"  # rules default to contacts; extend for deals as needed
            self.s.patch(f"{self.BASE}/crm/v3/objects/{obj}/{a.record_id}", json={"properties": a.payload}, timeout=30).raise_for_status()
        elif a.kind == "create_task":
            due = parse_dt(a.payload["due"]) or datetime.now(timezone.utc)
            body = {
                "properties": {
                    "hs_task_subject": a.payload["title"],
                    "hs_task_body": a.payload.get("body", ""),
                    "hs_task_status": "NOT_STARTED",
                    "hs_task_priority": "HIGH" if a.payload.get("priority") == "high" else "MEDIUM",
                    "hs_timestamp": int(due.timestamp() * 1000),
                },
                "associations": [{"to": {"id": a.record_id}, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 204}]}],
            }
            self.s.post(f"{self.BASE}/crm/v3/objects/tasks", json=body, timeout=30).raise_for_status()
        elif a.kind == "add_note":
            body = {
                "properties": {"hs_note_body": a.payload["body"], "hs_timestamp": int(datetime.now(timezone.utc).timestamp() * 1000)},
                "associations": [{"to": {"id": a.record_id}, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}]}],
            }
            self.s.post(f"{self.BASE}/crm/v3/objects/notes", json=body, timeout=30).raise_for_status()


class ZohoCRM:
    MODULES = {"contact": "Leads", "deal": "Deals"}

    def __init__(self, session: requests.Session | None = None):
        self.s = session or requests.Session()
        self.accounts = os.getenv("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.com")
        self.api = os.getenv("ZOHO_API_URL", "https://www.zohoapis.com")
        self._refresh()

    def _refresh(self) -> None:
        r = self.s.post(f"{self.accounts}/oauth/v2/token", params={
            "refresh_token": os.environ["ZOHO_REFRESH_TOKEN"], "client_id": os.environ["ZOHO_CLIENT_ID"],
            "client_secret": os.environ["ZOHO_CLIENT_SECRET"], "grant_type": "refresh_token"}, timeout=30)
        r.raise_for_status()
        self.s.headers.update({"Authorization": f"Zoho-oauthtoken {r.json()['access_token']}"})

    def fetch(self, object_type: str) -> list[dict]:
        module = self.MODULES[object_type]
        fields = "First_Name,Last_Name,Email,Phone,Lead_Status,Lead_Source,Owner,Last_Activity_Time,Created_Time"
        out, page = [], 1
        while True:
            r = self.s.get(f"{self.api}/crm/v7/{module}", params={"fields": fields, "per_page": 200, "page": page}, timeout=30)
            if r.status_code == 204:
                return out
            r.raise_for_status()
            data = r.json()
            for row in data.get("data", []):
                out.append({"id": row["id"], "object": object_type, **{k.lower(): v for k, v in row.items() if k != "id"}})
            if not data.get("info", {}).get("more_records"):
                return out
            page += 1

    def apply(self, a: Action) -> None:
        if a.kind == "update":
            fields = {k.title(): v for k, v in a.payload.items()}  # lead_status -> Lead_Status
            self.s.put(f"{self.api}/crm/v7/Leads/{a.record_id}", json={"data": [fields]}, timeout=30).raise_for_status()
        elif a.kind == "create_task":
            task = {"Subject": a.payload["title"], "Description": a.payload.get("body", ""), "Due_Date": a.payload["due"][:10],
                    "Priority": "High" if a.payload.get("priority") == "high" else "Normal", "What_Id": {"id": a.record_id}, "$se_module": "Leads"}
            self.s.post(f"{self.api}/crm/v7/Tasks", json={"data": [task]}, timeout=30).raise_for_status()
        elif a.kind == "add_note":
            note = {"Note_Content": a.payload["body"], "Parent_Id": {"module": {"api_name": "Leads"}, "id": a.record_id}}
            self.s.post(f"{self.api}/crm/v7/Notes", json={"data": [note]}, timeout=30).raise_for_status()
