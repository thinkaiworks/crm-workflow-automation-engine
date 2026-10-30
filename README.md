# CRM Workflow Automation Engine

**Make sure no lead ever sits untouched in your CRM again.** Write your sales process as simple rules ("if a new lead hasn't been contacted in 3 days, mark it and create an overdue task"), and this engine checks every lead in **HubSpot** or **Zoho CRM** on a schedule, updates statuses, creates follow-up tasks, adds notes and pings Slack.

No per-seat workflow add-ons, no clicking through 40 workflow screens. Your whole follow-up process lives in one readable file.

Built by [ThinkAIWorks](https://thinkaiworks.com).

## Example rules

```yaml
- name: Hot new lead - call in 1 hour
  when:
    all:
      - { field: hs_lead_status, eq: NEW }
      - { field: lead_score, gt: 75 }
      - { field: notes_last_contacted, exists: false }
  then:
    - create_task: { title: "Call hot lead {firstname} {lastname}", due_in_hours: 1, priority: high }
    - notify: "🔥 Hot lead: {firstname} {lastname}, score {lead_score}"

- name: New lead untouched for 3 days
  when:
    all:
      - { field: hs_lead_status, eq: NEW }
      - { field: createdate, older_than_days: 3 }
  then:
    - set: { hs_lead_status: ATTEMPTED_TO_CONTACT }
    - create_task: { title: "Overdue: first touch for {firstname}", due_in_hours: 4, priority: high }
```

**Conditions:** `eq`, `ne`, `in`, `not_in`, `exists`, `contains`, `gt`, `lt`, `older_than_days`, `newer_than_days`, combined with `all` / `any` / `not`.
**Actions:** `set` (update fields), `create_task`, `add_note`, `notify` (Slack).

## Quick start

```bash
pip install -r requirements.txt
python -m crm_engine --crm demo          # dry run on sample leads
```

Output:

```
[Hot new lead - call in 1 hour] create_task  record 101: Call hot lead Sara Mills
[New lead untouched for 3 days] update       record 102: {'hs_lead_status': 'ATTEMPTED_TO_CONTACT'}
[Stalled in progress - 10 days no contact] create_task  record 104: Re-engage Mia: send case study + book call
...
```

Go live:

```bash
cp .env.example .env   # add HubSpot token or Zoho OAuth details
python -m crm_engine --crm hubspot --rules rules.example.yaml          # preview
python -m crm_engine --crm hubspot --rules rules.example.yaml --apply  # write
```

Run it every 15 minutes with cron, GitHub Actions, or an n8n Schedule node.

## Built for real accounts

- **Dry run by default.** Nothing is written until you add `--apply`.
- **Never double-fires.** Each rule fires once per record, so tasks don't pile up on every run.
- **Keeps going on errors.** One bad record is logged, the rest still run.
- **Full pagination** for HubSpot (cursor) and Zoho (page/more_records).
- **Zoho data centres** (US/EU/IN/AU) set by environment variable.

## Tests

```bash
pytest -q
```

## Stack

Python · HubSpot CRM v3 API · Zoho CRM v7 API · YAML · Slack webhooks
