import csv
import imaplib
import io
import json
import os
import re
import secrets
import smtplib
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.header import decode_header
from email.message import EmailMessage
from pathlib import Path
from threading import Thread
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, URL, create_engine, func, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

DATABASE_URL = URL.create(
    "postgresql+psycopg",
    username=os.environ["POSTGRES_USER"],
    password=os.environ["POSTGRES_PASSWORD"],
    host=os.getenv("POSTGRES_HOST", "db"),
    port=int(os.getenv("POSTGRES_PORT", "5432")),
    database=os.environ["POSTGRES_DB"],
)
ADMIN_KEY = os.environ["ADMIN_KEY"]
EMAIL_INGEST_KEY = os.environ["EMAIL_INGEST_KEY"]
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8080").rstrip("/")
SMTP_ENABLED = os.getenv("SMTP_ENABLED", "false").lower() == "true"
SMTP_HOST = os.getenv("SMTP_HOST", "mailpit")
SMTP_PORT = int(os.getenv("SMTP_PORT", "1025"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_STARTTLS = os.getenv("SMTP_STARTTLS", "false").lower() == "true"
SMTP_SSL = os.getenv("SMTP_SSL", "false").lower() == "true"
MAIL_FROM = os.getenv("MAIL_FROM", "it-service@localhost")
UPLOAD_DIR = Path("/app/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(20), default="portal")
    requester_name: Mapped[str] = mapped_column(String(160))
    requester_email: Mapped[str] = mapped_column(String(254), index=True)
    entity: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    department: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    category: Mapped[str] = mapped_column(String(120))
    assignment_group: Mapped[str] = mapped_column(String(120), index=True)
    ticket_type: Mapped[str] = mapped_column(String(30))
    priority: Mapped[str] = mapped_column(String(5), default="P3")
    status: Mapped[str] = mapped_column(String(30), default="New", index=True)
    subject: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    approval_name: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    approval_email: Mapped[Optional[str]] = mapped_column(String(254), nullable=True)
    attachments: Mapped[str] = mapped_column(Text, default="[]")
    email_message_id: Mapped[Optional[str]] = mapped_column(String(300), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    notification_recipient: Mapped[Optional[str]] = mapped_column(String(254), nullable=True)

class TicketReply(Base):
    __tablename__ = "ticket_replies"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    author: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class TicketEvent(Base):
    __tablename__ = "ticket_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    actor: Mapped[str] = mapped_column(String(160))
    field: Mapped[str] = mapped_column(String(80))
    old_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class CategoryRoute(Base):
    __tablename__ = "category_routes"
    category: Mapped[str] = mapped_column(String(120), primary_key=True)
    recipient_email: Mapped[Optional[str]] = mapped_column(String(254), nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class RoutingSettings(Base):
    __tablename__ = "routing_settings"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    fallback_email: Mapped[Optional[str]] = mapped_column(String(254), nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

CATALOGUE = [
    ("SAP – Request", "SAP", "Request", "SAP Team", True),
    ("SAP – Change", "SAP", "Change", "SAP Team", True),
    ("SAP – Incident", "SAP", "Incident", "SAP Team", False),
    ("SAP – Non-Standard Change/Request", "SAP", "Change/Request", "SAP Team", True),
    ("MDM – Standard Request", "MDM", "Request", "MDM Team", True),
    ("MDM – Standard Change", "MDM", "Change", "MDM Team", True),
    ("MDM – Incident", "MDM", "Incident", "MDM Team", False),
    ("MDM – Non-Standard Change/Request", "MDM", "Change/Request", "MDM Team", True),
    ("Digital Workplace – Standard Request", "Digital Workplace", "Request", "Digital Workplace", True),
    ("Digital Workplace – Standard Change", "Digital Workplace", "Change", "Digital Workplace", True),
    ("Digital Workplace – Incident", "Digital Workplace", "Incident", "Digital Workplace", False),
    ("Digital Workplace – Non-Standard Change/Request", "Digital Workplace", "Change/Request", "Digital Workplace", True),
    ("Digital Infrastructure – Standard Request", "Digital Infrastructure", "Request", "Infrastructure", True),
    ("Digital Infrastructure – Standard Change", "Digital Infrastructure", "Change", "Infrastructure", True),
    ("Digital Infrastructure – Incident", "Digital Infrastructure", "Incident", "Infrastructure", False),
    ("Digital Infrastructure – Non-Standard Change/Request", "Digital Infrastructure", "Change/Request", "Infrastructure", True),
    ("AI/Data – Standard Request", "AI/Data", "Request", "Data & Analytics", True),
    ("AI/Data – Standard Change", "AI/Data", "Change", "Data & Analytics", True),
    ("AI/Data – Incident", "AI/Data", "Incident", "Data & Analytics", False),
    ("AI/Data – Non-Standard Change/Request", "AI/Data", "Change/Request", "Data & Analytics", True),
    ("Enterprise Applications – Standard Request", "Enterprise Apps", "Request", "Enterprise Apps", True),
    ("Enterprise Applications – Standard Change", "Enterprise Apps", "Change", "Enterprise Apps", True),
    ("Enterprise Applications – Incident", "Enterprise Apps", "Incident", "Enterprise Apps", False),
    ("Enterprise Applications – Non-Standard Change/Request", "Enterprise Apps", "Change/Request", "Enterprise Apps", True),
]
CATALOGUE_BY_NAME = {item[0]: item for item in CATALOGUE}
VALID_PRIORITIES = {"P1", "P2", "P3", "P4"}
VALID_STATUSES = {"New", "In Progress", "Waiting for User", "Resolved", "Closed"}
VALID_GROUPS = {item[3] for item in CATALOGUE} | {"Service Desk"}

def db_session():
    with SessionLocal() as db:
        yield db

def admin(x_admin_key: Optional[str] = Header(None)):
    if not x_admin_key or not secrets.compare_digest(x_admin_key, ADMIN_KEY):
        raise HTTPException(401, "Staff authentication required")

def ticket_dict(ticket: Ticket):
    return {c.name: getattr(ticket, c.name) for c in Ticket.__table__.columns}

def detail_dict(db: Session, ticket: Ticket):
    result = ticket_dict(ticket)
    result["replies"] = [
        {c.name: getattr(reply, c.name) for c in TicketReply.__table__.columns}
        for reply in db.scalars(select(TicketReply).where(TicketReply.ticket_id == ticket.id).order_by(TicketReply.created_at)).all()
    ]
    result["events"] = [
        {c.name: getattr(event, c.name) for c in TicketEvent.__table__.columns}
        for event in db.scalars(select(TicketEvent).where(TicketEvent.ticket_id == ticket.id).order_by(TicketEvent.created_at)).all()
    ]
    return result

def valid_email(value: str):
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value or ""))

def get_routing_recipient(db: Session, category: str):
    route = db.get(CategoryRoute, category)
    if route and route.recipient_email and valid_email(route.recipient_email):
        return route.recipient_email
    settings = db.get(RoutingSettings, 1)
    if settings and settings.fallback_email and valid_email(settings.fallback_email):
        return settings.fallback_email
    return None

def send_email(recipient: str, subject: str, body: str):
    if not SMTP_ENABLED:
        return False
    if not valid_email(recipient):
        print(f"SMTP skipped for invalid recipient on subject: {subject[:80]}", flush=True)
        return False
    message = EmailMessage()
    message["From"] = MAIL_FROM
    message["To"] = recipient
    message["Subject"] = subject.replace("\r", " ").replace("\n", " ")[:300]
    message.set_content(body)
    try:
        smtp_class = smtplib.SMTP_SSL if SMTP_SSL else smtplib.SMTP
        with smtp_class(SMTP_HOST, SMTP_PORT, timeout=10) as client:
            if SMTP_STARTTLS and not SMTP_SSL:
                client.starttls()
            if SMTP_USERNAME:
                client.login(SMTP_USERNAME, SMTP_PASSWORD)
            client.send_message(message)
        return True
    except Exception as error:
        print(f"SMTP delivery failed: {error}", flush=True)
        return False

def send_ticket_created(ticket: Ticket):
    return send_email(
        ticket.requester_email,
        f"[{ticket.number}] Ticket received: {ticket.subject}",
        f"Hello {ticket.requester_name},\n\n"
        f"Your IT ticket has been created.\n\n"
        f"Ticket: {ticket.number}\nStatus: {ticket.status}\nPriority: {ticket.priority}\n"
        f"Assigned to: {ticket.assignment_group}\nCategory: {ticket.category}\n\n"
        f"Description:\n{ticket.description}\n\n"
        "Please keep the ticket number in the subject when replying.\n\n"
        "IT & Digital Transformation",
    )

def send_ticket_reply(ticket: Ticket, reply: TicketReply):
    return send_email(
        ticket.requester_email,
        f"[{ticket.number}] Update: {ticket.subject}",
        f"Hello {ticket.requester_name},\n\n"
        f"{reply.author} added an update to your IT ticket:\n\n{reply.body}\n\n"
        f"Current status: {ticket.status}\nPriority: {ticket.priority}\n"
        f"Assigned to: {ticket.assignment_group}\n\n"
        f"Ticket reference: {ticket.number}\n\nIT & Digital Transformation",
    )

def send_routing_notification(ticket: Ticket, reason: str = "New ticket"):
    if not ticket.notification_recipient:
        return False
    return send_email(
        ticket.notification_recipient,
        f"[{ticket.number}] [{ticket.priority}] {ticket.category}: {ticket.subject}",
        f"{reason}\n\n"
        f"Ticket: {ticket.number}\nRequester: {ticket.requester_name} <{ticket.requester_email}>\n"
        f"Entity / location: {ticket.entity or '—'}\nDepartment: {ticket.department or '—'}\n"
        f"Status: {ticket.status}\nPriority: {ticket.priority}\nCategory: {ticket.category}\n"
        f"Assignment group: {ticket.assignment_group}\n\nDescription:\n{ticket.description}\n\n"
        f"Open the staff console: {APP_BASE_URL}/admin.html\n\nIT & Digital Transformation",
    )

def next_number(db: Session):
    # Serialise number allocation inside PostgreSQL. A simple row count can
    # produce the same number for two requests arriving at the same time.
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": 81415183})
    last_id = db.scalar(select(func.max(Ticket.id))) or 0
    return f"IT-{datetime.now():%Y%m%d}-{last_id + 1:06d}"

def create_ticket(db: Session, *, requester_name, requester_email, subject, description, category="Unclassified", source="email", entity=None, department=None, priority="P3", approval_name=None, approval_email=None, attachment_names="[]", email_message_id=None):
    config = CATALOGUE_BY_NAME.get(category)
    ticket_type = config[2] if config else "Request"
    group = config[3] if config else "Service Desk"
    ticket = Ticket(
        number=next_number(db), source=str(source)[:20],
        requester_name=str(requester_name).strip()[:160], requester_email=str(requester_email).strip()[:254],
        entity=str(entity).strip()[:100] if entity else None, department=str(department).strip()[:100] if department else None,
        category=category[:120], assignment_group=group, ticket_type=ticket_type, priority=priority,
        subject=subject[:300], description=description,
        approval_name=str(approval_name).strip()[:160] if approval_name else None,
        approval_email=str(approval_email).strip()[:254] if approval_email else None,
        attachments=attachment_names, email_message_id=email_message_id,
        notification_recipient=get_routing_recipient(db, category),
    )
    db.add(ticket)
    db.flush()
    db.add(TicketEvent(ticket_id=ticket.id, actor=ticket.requester_name, field="created", new_value=ticket.status))
    if ticket.notification_recipient:
        db.add(TicketEvent(ticket_id=ticket.id, actor="Automatic routing", field="notification_recipient", new_value=ticket.notification_recipient))
    db.commit()
    db.refresh(ticket)
    return ticket

app = FastAPI(title="ITIL Lite")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def startup():
    Base.metadata.create_all(engine)
    # create_all does not add columns to an existing database. This small,
    # idempotent migration preserves current tickets when upgrading.
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ"))
        connection.execute(text("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS notification_recipient VARCHAR(254)"))
    with SessionLocal() as db:
        for name, *_ in CATALOGUE:
            if not db.get(CategoryRoute, name):
                db.add(CategoryRoute(category=name))
        if not db.get(RoutingSettings, 1):
            db.add(RoutingSettings(id=1))
        db.commit()
    if os.getenv("IMAP_ENABLED", "false").lower() == "true":
        Thread(target=imap_loop, daemon=True).start()

@app.get("/api/health")
def health(db: Session = Depends(db_session)):
    db.execute(select(1))
    return {"status": "ok"}

@app.get("/api/catalogue")
def catalogue():
    return [{"name": n, "area": a, "type": t, "group": g, "requires_approval": approval} for n, a, t, g, approval in CATALOGUE]

@app.post("/api/tickets")
async def submit_ticket(
    requester_name: str = Form(...), requester_email: str = Form(...), entity: str = Form(""), department: str = Form(""), category: str = Form(...), priority: str = Form("P3"), subject: str = Form(...), description: str = Form(...), approval_name: str = Form(""), approval_email: str = Form(""), files: list[UploadFile] = File(default=[]), db: Session = Depends(db_session)
):
    if category not in CATALOGUE_BY_NAME or priority not in VALID_PRIORITIES:
        raise HTTPException(400, "Invalid category or priority")
    if not requester_name.strip() or not requester_email.strip() or not subject.strip() or not description.strip():
        raise HTTPException(400, "Name, email, subject, and description are required")
    config = CATALOGUE_BY_NAME[category]
    if config[4] and (not approval_name.strip() or not approval_email.strip()):
        raise HTTPException(400, "Approver name and email are required for this request")
    attachment_names = []
    for upload in files:
        if upload.filename:
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", upload.filename)[-180:]
            stored = f"{int(time.time() * 1000)}_{secrets.token_hex(4)}_{safe}"
            content = await upload.read()
            if len(content) > 10 * 1024 * 1024:
                raise HTTPException(400, "Each attachment must be 10 MB or smaller")
            (UPLOAD_DIR / stored).write_bytes(content)
            attachment_names.append({"original": upload.filename, "stored": stored})
    ticket = create_ticket(db, requester_name=requester_name.strip(), requester_email=requester_email.strip(), entity=entity.strip() or None, department=department.strip() or None, category=category, priority=priority, subject=subject.strip(), description=description.strip(), approval_name=approval_name.strip() or None, approval_email=approval_email.strip() or None, attachment_names=json.dumps(attachment_names), source="portal")
    email_sent = send_ticket_created(ticket)
    routing_email_sent = send_routing_notification(ticket)
    return {"number": ticket.number, "assignment_group": ticket.assignment_group, "status": ticket.status, "email_sent": email_sent, "routing_email_sent": routing_email_sent, "routing_configured": bool(ticket.notification_recipient)}

@app.get("/api/tickets", dependencies=[Depends(admin)])
def list_tickets(status: Optional[str] = None, group: Optional[str] = None, db: Session = Depends(db_session)):
    query = select(Ticket).order_by(Ticket.created_at.desc())
    if status: query = query.where(Ticket.status == status)
    if group: query = query.where(Ticket.assignment_group == group)
    return [ticket_dict(t) for t in db.scalars(query).all()]

@app.get("/api/tickets/{number}", dependencies=[Depends(admin)])
def get_ticket(number: str, db: Session = Depends(db_session)):
    ticket = db.scalar(select(Ticket).where(Ticket.number == number))
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    return detail_dict(db, ticket)

@app.patch("/api/tickets/{number}", dependencies=[Depends(admin)])
def update_ticket(number: str, body: dict, db: Session = Depends(db_session)):
    ticket = db.scalar(select(Ticket).where(Ticket.number == number))
    if not ticket: raise HTTPException(404, "Ticket not found")
    actor = body.get("actor", "IT Support")
    if not isinstance(actor, str) or not actor.strip():
        raise HTTPException(400, "A staff name is required")
    actor = actor.strip()[:160]
    updates = {field: body[field].strip() for field in ("status", "assignment_group", "priority", "description", "category") if isinstance(body.get(field), str) and body[field].strip()}
    if "status" in updates and updates["status"] not in VALID_STATUSES:
        raise HTTPException(400, "Invalid status")
    if "assignment_group" in updates and updates["assignment_group"] not in VALID_GROUPS:
        raise HTTPException(400, "Invalid assignment group")
    if "priority" in updates and updates["priority"] not in VALID_PRIORITIES:
        raise HTTPException(400, "Invalid priority")
    if "category" in updates and updates["category"] not in CATALOGUE_BY_NAME:
        raise HTTPException(400, "Invalid category")
    if "description" in body and "description" not in updates:
        raise HTTPException(400, "Description cannot be empty")
    if len(updates.get("description", "")) > 50000:
        raise HTTPException(400, "Description must be 50,000 characters or fewer")
    category_changed = "category" in updates and updates["category"] != ticket.category
    if category_changed:
        category_config = CATALOGUE_BY_NAME[updates["category"]]
        updates["ticket_type"] = category_config[2]
        updates["assignment_group"] = category_config[3]
        updates["notification_recipient"] = get_routing_recipient(db, updates["category"])
    for field, value in updates.items():
        old_value = getattr(ticket, field)
        if old_value == value:
            continue
        setattr(ticket, field, value)
        db.add(TicketEvent(ticket_id=ticket.id, actor=actor, field=field, old_value=old_value, new_value=value))
    if "status" in updates:
        if updates["status"] in {"Resolved", "Closed"} and not ticket.resolved_at:
            ticket.resolved_at = datetime.now(timezone.utc)
        elif updates["status"] not in {"Resolved", "Closed"}:
            ticket.resolved_at = None
    db.commit(); db.refresh(ticket)
    result = detail_dict(db, ticket)
    if category_changed and body.get("notify_route", True):
        result["routing_email_sent"] = send_routing_notification(ticket, f"Ticket reclassified by {actor}")
    return result

@app.post("/api/tickets/{number}/replies", dependencies=[Depends(admin)])
def add_reply(number: str, body: dict, db: Session = Depends(db_session)):
    ticket = db.scalar(select(Ticket).where(Ticket.number == number))
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    author = body.get("author")
    reply_body = body.get("body")
    if not isinstance(author, str) or not author.strip():
        raise HTTPException(400, "A staff name is required")
    if not isinstance(reply_body, str) or not reply_body.strip():
        raise HTTPException(400, "Reply cannot be empty")
    if len(reply_body.strip()) > 20000:
        raise HTTPException(400, "Reply must be 20,000 characters or fewer")
    reply = TicketReply(ticket_id=ticket.id, author=author.strip()[:160], body=reply_body.strip())
    db.add(reply)
    if ticket.status == "New":
        old_status = ticket.status
        ticket.status = "In Progress"
        db.add(TicketEvent(ticket_id=ticket.id, actor=reply.author, field="status", old_value=old_status, new_value=ticket.status))
    db.commit()
    result = detail_dict(db, ticket)
    result["email_sent"] = send_ticket_reply(ticket, reply)
    return result

@app.get("/api/email/status", dependencies=[Depends(admin)])
def email_status():
    return {"enabled": SMTP_ENABLED, "host": SMTP_HOST, "port": SMTP_PORT, "from": MAIL_FROM, "starttls": SMTP_STARTTLS, "ssl": SMTP_SSL}

@app.post("/api/email/test", dependencies=[Depends(admin)])
def test_email(body: dict):
    recipient = body.get("recipient")
    if not isinstance(recipient, str) or not valid_email(recipient.strip()):
        raise HTTPException(400, "Enter a valid test recipient email")
    sent = send_email(
        recipient.strip(),
        "ITIL Lite SMTP test",
        "This is a successful SMTP test from ITIL Lite.\n\n"
        f"Application: {APP_BASE_URL}\n\nIT & Digital Transformation",
    )
    if not sent:
        raise HTTPException(503, "SMTP test message could not be delivered. Check the application logs and SMTP settings.")
    return {"status": "sent", "recipient": recipient.strip()}

@app.get("/api/routing", dependencies=[Depends(admin)])
def get_routing(db: Session = Depends(db_session)):
    settings = db.get(RoutingSettings, 1)
    routes = {route.category: route for route in db.scalars(select(CategoryRoute)).all()}
    return {
        "fallback_recipient": settings.fallback_email if settings else None,
        "fallback_updated_by": settings.updated_by if settings else None,
        "routes": [
            {
                "category": name, "area": area, "type": ticket_type, "group": group,
                "recipient": routes[name].recipient_email if name in routes else None,
                "effective_recipient": (routes[name].recipient_email if name in routes and routes[name].recipient_email else (settings.fallback_email if settings else None)),
                "uses_fallback": not bool(routes[name].recipient_email if name in routes else None),
                "updated_by": routes[name].updated_by if name in routes else None,
            }
            for name, area, ticket_type, group, _ in CATALOGUE
        ],
    }

@app.patch("/api/routing/fallback", dependencies=[Depends(admin)])
def update_fallback(body: dict, db: Session = Depends(db_session)):
    recipient = body.get("recipient")
    actor = body.get("actor")
    if not isinstance(recipient, str) or not valid_email(recipient.strip()):
        raise HTTPException(400, "A valid Service Desk fallback email is required")
    if not isinstance(actor, str) or not actor.strip():
        raise HTTPException(400, "A staff name is required")
    settings = db.get(RoutingSettings, 1) or RoutingSettings(id=1)
    settings.fallback_email = recipient.strip()[:254]
    settings.updated_by = actor.strip()[:160]
    db.add(settings)
    db.commit()
    return get_routing(db)

@app.patch("/api/routing/category", dependencies=[Depends(admin)])
def update_category_route(body: dict, db: Session = Depends(db_session)):
    category = body.get("category")
    recipient = body.get("recipient", "")
    actor = body.get("actor")
    if not isinstance(category, str) or category not in CATALOGUE_BY_NAME:
        raise HTTPException(400, "Invalid category")
    if not isinstance(recipient, str) or (recipient.strip() and not valid_email(recipient.strip())):
        raise HTTPException(400, "Enter a valid recipient email or leave it blank to use the fallback")
    if not isinstance(actor, str) or not actor.strip():
        raise HTTPException(400, "A staff name is required")
    route = db.get(CategoryRoute, category) or CategoryRoute(category=category)
    route.recipient_email = recipient.strip()[:254] or None
    route.updated_by = actor.strip()[:160]
    db.add(route)
    db.commit()
    return get_routing(db)

@app.get("/api/reports/summary", dependencies=[Depends(admin)])
def report_summary(db: Session = Depends(db_session)):
    tickets = db.scalars(select(Ticket)).all()
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    open_tickets = [t for t in tickets if t.status not in {"Resolved", "Closed"}]
    completed = [t for t in tickets if t.status in {"Resolved", "Closed"}]
    recent = sum(t.created_at >= week_ago for t in tickets)
    completed_recently = [t for t in completed if (t.resolved_at or t.updated_at) >= week_ago]
    resolution_hours = [
        ((t.resolved_at or t.updated_at) - t.created_at).total_seconds() / 3600
        for t in completed if (t.resolved_at or t.updated_at) >= t.created_at
    ]
    age_buckets = Counter()
    for ticket in open_tickets:
        age_days = (now - ticket.created_at).total_seconds() / 86400
        bucket = "Under 24 hours" if age_days < 1 else "1–3 days" if age_days < 4 else "4–7 days" if age_days < 8 else "Over 7 days"
        age_buckets[bucket] += 1
    daily_opened = []
    for days_ago in range(6, -1, -1):
        day = (now - timedelta(days=days_ago)).date()
        daily_opened.append({"date": day.isoformat(), "count": sum(t.created_at.date() == day for t in tickets)})
    oldest_open = sorted(open_tickets, key=lambda ticket: ticket.created_at)[:5]
    return {
        "generated_at": now,
        "total": len(tickets),
        "open": len(open_tickets),
        "completed": len(completed),
        "completion_rate": round((len(completed) / len(tickets) * 100), 1) if tickets else 0,
        "by_status": Counter(t.status for t in tickets),
        "by_group": Counter(t.assignment_group for t in tickets),
        "by_priority": Counter(t.priority for t in tickets),
        "by_category": Counter(t.category for t in tickets),
        "by_type": Counter(t.ticket_type for t in tickets),
        "by_source": Counter(t.source for t in tickets),
        "age_buckets": age_buckets,
        "opened_last_7_days": recent,
        "completed_last_7_days": len(completed_recently),
        "open_high_priority": sum(t.priority in {"P1", "P2"} for t in open_tickets),
        "average_resolution_hours": round(sum(resolution_hours) / len(resolution_hours), 1) if resolution_hours else None,
        "daily_opened": daily_opened,
        "oldest_open": [{"number": t.number, "subject": t.subject, "priority": t.priority, "status": t.status, "created_at": t.created_at} for t in oldest_open],
    }

@app.get("/api/reports/tickets.csv", dependencies=[Depends(admin)])
def export_csv(db: Session = Depends(db_session)):
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(["Number", "Created", "Source", "Requester", "Email", "Category", "Group", "Type", "Priority", "Status", "Routing Recipient", "Subject"])
    for t in db.scalars(select(Ticket).order_by(Ticket.created_at.desc())):
        writer.writerow([t.number, t.created_at, t.source, t.requester_name, t.requester_email, t.category, t.assignment_group, t.ticket_type, t.priority, t.status, t.notification_recipient, t.subject])
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=itil-tickets.csv"})

@app.post("/api/email/intake")
def email_intake(payload: dict, x_ingest_key: Optional[str] = Header(None), db: Session = Depends(db_session)):
    if not x_ingest_key or not secrets.compare_digest(x_ingest_key, EMAIL_INGEST_KEY): raise HTTPException(401, "Invalid ingest key")
    sender = payload.get("from"); subject = payload.get("subject"); body = payload.get("body", "")
    if not isinstance(sender, str) or not isinstance(subject, str) or not isinstance(body, str):
        raise HTTPException(400, "from, subject, and body must be strings")
    sender = sender.strip(); subject = subject.strip(); body = body.strip()
    if not sender or not subject: raise HTTPException(400, "from and subject are required")
    message_id = payload.get("message_id")
    if message_id is not None and not isinstance(message_id, str):
        raise HTTPException(400, "message_id must be a string")
    if message_id and len(message_id) > 300:
        raise HTTPException(400, "message_id must be 300 characters or fewer")
    if message_id and db.scalar(select(Ticket).where(Ticket.email_message_id == message_id)): return {"status": "duplicate"}
    category = next((name for name, area, *_ in CATALOGUE if f"[{area.lower()}]" in subject.lower()), "Unclassified")
    ticket = create_ticket(db, requester_name=payload.get("name") or sender.split("@")[0], requester_email=sender, subject=subject, description=body or "(No message body)", category=category, source="email", email_message_id=message_id)
    return {"number": ticket.number, "assignment_group": ticket.assignment_group, "routing_email_sent": send_routing_notification(ticket)}

def decode(value):
    return "".join((part.decode(enc or "utf-8", errors="replace") if isinstance(part, bytes) else part) for part, enc in decode_header(value or ""))

def imap_loop():
    while True:
        try:
            with imaplib.IMAP4_SSL(os.environ["IMAP_HOST"], int(os.getenv("IMAP_PORT", "993"))) as client:
                client.login(os.environ["IMAP_USERNAME"], os.environ["IMAP_PASSWORD"]); client.select(os.getenv("IMAP_MAILBOX", "INBOX"))
                _, ids = client.search(None, "UNSEEN")
                for uid in ids[0].split():
                    _, data = client.fetch(uid, "(RFC822)"); msg = message_from_bytes(data[0][1])
                    sender = msg.get("Reply-To") or msg.get("From", "")
                    match = re.search(r"<([^>]+)>", sender); sender = match.group(1) if match else sender
                    body = ""
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain" and not part.get_filename(): body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace"); break
                    with SessionLocal() as db:
                        if not db.scalar(select(Ticket).where(Ticket.email_message_id == msg.get("Message-ID"))):
                            email_intake({"from": sender, "name": decode(msg.get("From")).split("<")[0].strip(), "subject": decode(msg.get("Subject")), "body": body, "message_id": msg.get("Message-ID")}, EMAIL_INGEST_KEY, db)
                    client.store(uid, "+FLAGS", "\\Seen")
        except Exception as error: print(f"IMAP poll failed: {error}", flush=True)
        time.sleep(60)

@app.get("/", include_in_schema=False)
def portal_home():
    return FileResponse("static/legacy-portal.html")

app.mount("/", StaticFiles(directory="static", html=True), name="static")
