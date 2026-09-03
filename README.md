# ITIL Lite

Small, self-hosted ITIL ticketing for an organisation of about 400 people. It has:

- an anonymous internal self-service portal;
- automatic category-to-assignment-group routing;
- a local staff console protected by a single administrator key;
- a ticket workspace with editable descriptions, IT replies, and an auditable activity timeline;
- PostgreSQL data storage and attachment storage in Docker volumes;
- an ITIL-oriented report view, print view, dashboard reporting, and CSV export; and
- email intake via a protected webhook, plus optional IMAP polling.

## Quick start (PowerShell)

Prerequisites: Docker Desktop with Docker Compose, running in Linux-container mode.

```powershell
Copy-Item .env.example .env
notepad .env
docker compose up --build -d
docker compose ps
```

Open `http://localhost:8080`. The employee page uses the original Biomedia portal layout and submits directly to the ticket API. The staff console is at `http://localhost:8080/admin.html`; enter the value of `ADMIN_KEY` from `.env` and your staff display name.

## Local SMTP testing

The Docker stack includes Mailpit, a local SMTP capture service. It accepts application email without sending anything to the internet.

1. Start the stack with `docker compose up --build -d`.
2. Open the staff console and use **Send SMTP test**, or submit a ticket from the employee portal with any valid-looking email address.
3. Open `http://localhost:8025` to inspect the captured message, headers, and content.
4. Post an IT reply from **Work ticket** and refresh Mailpit to see the requester update.

Mailpit's browser and SMTP ports are bound to `127.0.0.1`, so they are available only from the Docker host. Do not use Mailpit as a production mail relay.

To use an approved corporate SMTP relay later, update these `.env` values and recreate the app container:

```dotenv
SMTP_ENABLED=true
SMTP_HOST=smtp.company.example
SMTP_PORT=587
SMTP_USERNAME=approved-service-account
SMTP_PASSWORD=replace-with-the-service-account-password
SMTP_STARTTLS=true
SMTP_SSL=false
MAIL_FROM=it-service@company.example
```

Your mail administrator should supply the correct host, port, encryption mode, account, and sender address. Keep `.env` out of source control.

## Working tickets

In the staff console, select **Work ticket** to:

- update status, priority, category, assignment group, and description;
- post IT replies and troubleshooting notes; and
- review the ticket's replies and field-change history in one timeline.

Posting the first reply automatically moves a new ticket to **In Progress**. Resolving or closing a ticket records its completion time for reporting; reopening it clears that completion time.

Changing a category automatically applies that category's ticket type and recommended assignment group, records the reclassification in the activity timeline, and sends a notification to the newly effective routing mailbox.

## Category email routing

Open **Email routing** in the staff console to configure:

- a required Service Desk fallback recipient; and
- an optional dedicated notification recipient for each catalogue category.

Blank category recipients inherit the Service Desk fallback. When a portal or email-intake ticket is created, the requester confirmation and the IT routing notification are sent separately. The effective routing recipient is saved on the ticket for audit purposes and is included in CSV exports.

For local Mailpit testing, addresses such as `data@itteam.example` and `infra@itteam.example` are sufficient because Mailpit captures rather than externally delivers them. Replace them with approved corporate distribution lists or shared mailboxes before production use.

## ITIL report

Select **ITIL report** in the staff console for a printable operational view covering:

- open backlog and high-priority exposure;
- tickets opened and completed in the last seven days;
- completion rate and average resolution time;
- status, assignment-group, and ticket-type distribution;
- category distribution;
- open-ticket aging; and
- the oldest open tickets requiring attention.

These are operational indicators rather than contractual SLA targets. Use them in regular service reviews and improvement discussions.

## Automated Docker test

From this project folder, run:

```powershell
.\scripts\Test-ITILLite.ps1
```

The test builds and starts the stack, waits for its health endpoint, then verifies portal submission, SMTP capture, automatic routing, staff access, description updates, replies, audit history, status updates, reports, email intake, and duplicate-email protection. It intentionally leaves two clearly labelled smoke-test tickets in the local database and keeps the stack running so you can continue testing in the browser.

If PowerShell blocks local scripts, use this one-time invocation instead:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\Test-ITILLite.ps1
```

The same portal remains available at `http://localhost:8080/legacy-portal.html`. A standalone copy is also included as `Existing_Portal_Integrated.html` for publishing to your existing portal platform. If publishing it somewhere else (such as SharePoint), set `window.ITIL_TICKETING_API` near the bottom of that file to your reachable HTTPS ITIL URL, for example `https://itil.company.example/api/tickets`.

To stop the service without removing data:

```powershell
docker compose down
```

To apply a code update:

```powershell
docker compose up --build -d
```

## Email tickets

Email cannot be received by a container without a mailbox or mail gateway. This package supports both safe integration paths:

1. **Recommended for Microsoft 365:** configure a Power Automate/Logic Apps flow or future Microsoft Graph integration to POST the sender, subject and body to `POST /api/email/intake`, including `X-Ingest-Key: <EMAIL_INGEST_KEY>`. See the JSON example below.
2. **Generic IMAP mailbox:** set `IMAP_ENABLED=true` and the IMAP values in `.env`; the application polls the mailbox and converts each unread email into a ticket.

Webhook example:

```json
{
  "from": "jane@example.com",
  "name": "Jane Tan",
  "subject": "[Digital Workplace] Cannot access SharePoint",
  "body": "I receive Access Denied when opening the finance site.",
  "message_id": "optional-provider-message-id"
}
```

Subject tags select a catalogue group: `[SAP]`, `[MDM]`, `[Digital Workplace]`, `[Digital Infrastructure]`, `[AI/Data]`, and `[Enterprise Apps]`. Untagged email goes to `Service Desk` for triage. Email attachments are deliberately not fetched by the webhook; use the portal for sensitive/large files until a Graph mail connector is enabled.

## Security and production notes

- Change all `.env` secrets before deployment; never commit `.env`.
- Put the application behind HTTPS/reverse proxy and restrict portal access to your corporate network/VPN.
- Back up PostgreSQL with `docker compose exec db pg_dump -U itil itil > itil-backup.sql`.
- The current staff key is intentionally simple, as requested. Replace it with Entra ID/OIDC later; the API is already separated from the UI to make that change contained.
