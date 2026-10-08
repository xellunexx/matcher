# -*- coding: utf-8 -*-
"""TenderOps Canonical Observability & Telemetry Subsystem.

Provides structured event logging, correlation tracking, state transition journals,
diagnostic invariant checking, root-cause forensics, and report generation.
"""
from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import datetime
import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# Base paths
ROOT = Path(__file__).resolve().parent.parent
_BASE = os.environ.get("TENDEROPS_BASE") or str(ROOT)
_exe_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None
_res_base = _exe_dir if _exe_dir else Path(os.environ.get("TENDEROPS_WRITEBASE", _BASE))
DEFAULT_DB_PATH = Path(os.environ.get("TENDEROPS_DB_PATH", str(_res_base / "tenderops.sqlite3")))
LOGS_DIR = Path(_res_base) / "logs"
APP_LOG = LOGS_DIR / "application" / "app.log"
AUDIT_LOG = LOGS_DIR / "audit" / "audit.log"
ERRORS_LOG = LOGS_DIR / "errors" / "error.log"
DIAGNOSTICS_DIR = Path(_res_base) / "docs" / "diagnostics"

APP_VERSION = "2.6.0"
PIPELINE_VERSION = "1.4.2"
MATCHER_VERSION = "2.1.0"
PARSER_VERSION = "1.2.0"
SCHEMA_VERSION = "v2"

# ---------------- Severity & Event Types ----------------

class Severity:
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
    ALL = (DEBUG, INFO, WARNING, ERROR, CRITICAL)


class EventType:
    # Lifecycle
    APP_STARTED = "APP_STARTED"
    APP_INITIALIZED = "APP_INITIALIZED"
    SESSION_CREATED = "SESSION_CREATED"
    SESSION_RESET = "SESSION_RESET"
    SESSION_ENDED = "SESSION_ENDED"
    MODULE_ENTERED = "MODULE_ENTERED"
    MODULE_EXITED = "MODULE_EXITED"
    ROUTE_ENTERED = "ROUTE_ENTERED"
    ROUTE_EXITED = "ROUTE_EXITED"

    # Entities
    TENDER_CREATED = "TENDER_CREATED"
    TENDER_CONFIRMED = "TENDER_CONFIRMED"
    TENDER_LOADED = "TENDER_LOADED"
    TENDER_UPDATED = "TENDER_UPDATED"
    PROJECT_CREATED = "PROJECT_CREATED"
    PROJECT_LOADED = "PROJECT_LOADED"

    # Runs
    RUN_CREATED = "RUN_CREATED"
    RUN_STARTED = "RUN_STARTED"
    RUN_STAGE_STARTED = "RUN_STAGE_STARTED"
    RUN_STAGE_COMPLETED = "RUN_STAGE_COMPLETED"
    RUN_STAGE_FAILED = "RUN_STAGE_FAILED"
    RUN_COMPLETED = "RUN_COMPLETED"
    RUN_FAILED = "RUN_FAILED"
    RUN_CANCELLED = "RUN_CANCELLED"

    # Documents
    DOCUMENT_DISCOVERY_STARTED = "DOCUMENT_DISCOVERY_STARTED"
    DOCUMENT_DISCOVERED = "DOCUMENT_DISCOVERED"
    DOCUMENT_DOWNLOAD_STARTED = "DOCUMENT_DOWNLOAD_STARTED"
    DOCUMENT_DOWNLOADED = "DOCUMENT_DOWNLOADED"
    DOCUMENT_DOWNLOAD_FAILED = "DOCUMENT_DOWNLOAD_FAILED"
    DOCUMENT_UPLOADED = "DOCUMENT_UPLOADED"
    DOCUMENT_PARSED = "DOCUMENT_PARSED"
    DOCUMENT_PARSE_FAILED = "DOCUMENT_PARSE_FAILED"
    DOCUMENT_ASSOCIATED = "DOCUMENT_ASSOCIATED"
    DOCUMENT_ASSOCIATION_REJECTED = "DOCUMENT_ASSOCIATION_REJECTED"

    # KSS & Cost DB
    KSS_DISCOVERY_STARTED = "KSS_DISCOVERY_STARTED"
    KSS_DISCOVERY_COMPLETED = "KSS_DISCOVERY_COMPLETED"
    KSS_NOT_FOUND = "KSS_NOT_FOUND"
    PRICE_SOURCE_CREATED = "PRICE_SOURCE_CREATED"
    PRICE_SOURCE_ATTACHED = "PRICE_SOURCE_ATTACHED"
    PRICE_PARSE_STARTED = "PRICE_PARSE_STARTED"
    PRICE_PARSE_COMPLETED = "PRICE_PARSE_COMPLETED"
    PRICE_PARSE_FAILED = "PRICE_PARSE_FAILED"
    MATCH_STARTED = "MATCH_STARTED"
    MATCH_COMPLETED = "MATCH_COMPLETED"
    MATCH_FAILED = "MATCH_FAILED"
    PRICE_DECISION = "PRICE_DECISION"

    # Human Gate & Review
    HUMAN_GATE_CREATED = "HUMAN_GATE_CREATED"
    HUMAN_GATE_OPENED = "HUMAN_GATE_OPENED"
    HUMAN_GATE_RESOLVED = "HUMAN_GATE_RESOLVED"
    HUMAN_GATE_REJECTED = "HUMAN_GATE_REJECTED"
    REVIEW_STARTED = "REVIEW_STARTED"
    REVIEW_ITEM_OPENED = "REVIEW_ITEM_OPENED"
    REVIEW_ITEM_COMPLETED = "REVIEW_ITEM_COMPLETED"
    REVIEW_COMPLETED = "REVIEW_COMPLETED"

    # Generation
    GENERATION_REQUESTED = "GENERATION_REQUESTED"
    GENERATION_BLOCKED = "GENERATION_BLOCKED"
    GENERATION_AUTHORIZED = "GENERATION_AUTHORIZED"
    GENERATION_STARTED = "GENERATION_STARTED"
    GENERATION_COMPLETED = "GENERATION_COMPLETED"
    GENERATION_FAILED = "GENERATION_FAILED"

    # State transitions & Persistence
    STATE_TRANSITION = "STATE_TRANSITION"
    DB_INSERT = "DB_INSERT"
    DB_UPDATE = "DB_UPDATE"
    DB_DELETE = "DB_DELETE"
    DB_READ = "DB_READ"
    ARTIFACT_CREATED = "ARTIFACT_CREATED"
    ARTIFACT_REPLACED = "ARTIFACT_REPLACED"
    ARTIFACT_READ = "ARTIFACT_READ"
    ARTIFACT_HASH_MISMATCH = "ARTIFACT_HASH_MISMATCH"
    ARTIFACT_NOT_FOUND = "ARTIFACT_NOT_FOUND"
    HISTORY_RECORD_CREATED = "HISTORY_RECORD_CREATED"
    HISTORY_RECORD_LOADED = "HISTORY_RECORD_LOADED"
    CORRECTION_APPLIED = "CORRECTION_APPLIED"

    # Invariants & Integrity
    CONSISTENCY_CHECK_STARTED = "CONSISTENCY_CHECK_STARTED"
    CONSISTENCY_CHECK_FAILED = "CONSISTENCY_CHECK_FAILED"
    INVARIANT_VIOLATION = "INVARIANT_VIOLATION"

    # API & Frontend
    API_REQUEST = "API_REQUEST"
    API_RESPONSE = "API_RESPONSE"
    UI_TENDER_SELECTED = "UI_TENDER_SELECTED"
    UI_NEW_TENDER_SUBMITTED = "UI_NEW_TENDER_SUBMITTED"
    UI_FILE_SELECTED = "UI_FILE_SELECTED"
    UI_UPLOAD_STARTED = "UI_UPLOAD_STARTED"
    UI_UPLOAD_COMPLETED = "UI_UPLOAD_COMPLETED"
    UI_ANALYSIS_STARTED = "UI_ANALYSIS_STARTED"
    UI_ANALYSIS_FINISHED = "UI_ANALYSIS_FINISHED"
    UI_REVIEW_OPENED = "UI_REVIEW_OPENED"
    UI_REVIEW_COMPLETED = "UI_REVIEW_COMPLETED"
    UI_GENERATION_CLICKED = "UI_GENERATION_CLICKED"


# ---------------- Sensitive Data Redaction ----------------

SENSITIVE_KEYS = {"api_key", "apikey", "password", "secret", "token", "auth", "authorization", "bearer"}

def redact_sensitive(data: Any) -> Any:
    """Recursively redacts API keys, secrets, and authorization tokens."""
    if isinstance(data, dict):
        res = {}
        for k, v in data.items():
            if str(k).lower() in SENSITIVE_KEYS or any(s in str(k).lower() for s in ("api_key", "password", "secret", "bearer_token")):
                res[k] = "[REDACTED]"
            else:
                res[k] = redact_sensitive(v)
        return res
    elif isinstance(data, list):
        return [redact_sensitive(x) for x in data]
    elif isinstance(data, str):
        # Mask bearer tokens and long api keys
        if data.startswith("Bearer "):
            return "Bearer [REDACTED]"
        if re.search(r'sk-[a-zA-Z0-9]{20,}', data):
            return re.sub(r'sk-[a-zA-Z0-9]{20,}', 'sk-[REDACTED]', data)
        return data
    return data


# ---------------- Canonical Structured Event ----------------

class TelemetryEvent:
    def __init__(
        self,
        event_id: str,
        timestamp: str,
        severity: str,
        event_type: str,
        session_id: Optional[str] = None,
        actor_id: Optional[str] = None,
        actor_type: Optional[str] = "system",
        module: Optional[str] = None,
        route: Optional[str] = None,
        operation: Optional[str] = None,
        tender_id: Optional[int] = None,
        project_id: Optional[str] = None,
        run_id: Optional[int] = None,
        document_id: Optional[str] = None,
        review_item_id: Optional[str] = None,
        generation_id: Optional[str] = None,
        state_before: Optional[str] = None,
        state_after: Optional[str] = None,
        status: Optional[str] = None,
        duration_ms: Optional[float] = None,
        message: Optional[str] = None,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        stack_trace: Optional[str] = None,
        parent_event_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        input_fingerprint: Optional[str] = None,
        output_fingerprint: Optional[str] = None,
        source: Optional[str] = "tenderops",
        component: Optional[str] = "backend",
        version: Optional[str] = APP_VERSION,
        payload: Optional[Dict[str, Any]] = None
    ):
        self.event_id = event_id
        self.timestamp = timestamp
        self.severity = severity
        self.event_type = event_type
        self.session_id = session_id
        self.actor_id = actor_id
        self.actor_type = actor_type
        self.module = module
        self.route = route
        self.operation = operation
        self.tender_id = tender_id
        self.project_id = project_id
        self.run_id = run_id
        self.document_id = document_id
        self.review_item_id = review_item_id
        self.generation_id = generation_id
        self.state_before = state_before
        self.state_after = state_after
        self.status = status
        self.duration_ms = duration_ms
        self.message = message
        self.error_type = error_type
        self.error_message = error_message
        self.stack_trace = stack_trace
        self.parent_event_id = parent_event_id
        self.correlation_id = correlation_id
        self.input_fingerprint = input_fingerprint
        self.output_fingerprint = output_fingerprint
        self.source = source
        self.component = component
        self.version = version
        self.payload = payload or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "severity": self.severity,
            "event_type": self.event_type,
            "session_id": self.session_id,
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "module": self.module,
            "route": self.route,
            "operation": self.operation,
            "tender_id": self.tender_id,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "document_id": self.document_id,
            "review_item_id": self.review_item_id,
            "generation_id": self.generation_id,
            "state_before": self.state_before,
            "state_after": self.state_after,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "message": self.message,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "stack_trace": self.stack_trace,
            "parent_event_id": self.parent_event_id,
            "correlation_id": self.correlation_id,
            "input_fingerprint": self.input_fingerprint,
            "output_fingerprint": self.output_fingerprint,
            "source": self.source,
            "component": self.component,
            "version": self.version,
            "payload": redact_sensitive(self.payload or {})
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> TelemetryEvent:
        payload_raw = row["payload_json"] if "payload_json" in row.keys() else None
        try:
            payload = json.loads(payload_raw) if payload_raw else {}
        except Exception:
            payload = {}
        return cls(
            event_id=row["event_id"],
            timestamp=row["timestamp"],
            severity=row["severity"],
            event_type=row["event_type"],
            session_id=row["session_id"] if "session_id" in row.keys() else None,
            actor_id=row["actor_id"] if "actor_id" in row.keys() else None,
            actor_type=row["actor_type"] if "actor_type" in row.keys() else None,
            module=row["module"] if "module" in row.keys() else None,
            route=row["route"] if "route" in row.keys() else None,
            operation=row["operation"] if "operation" in row.keys() else None,
            tender_id=row["tender_id"] if "tender_id" in row.keys() else None,
            project_id=row["project_id"] if "project_id" in row.keys() else None,
            run_id=row["run_id"] if "run_id" in row.keys() else None,
            document_id=row["document_id"] if "document_id" in row.keys() else None,
            review_item_id=row["review_item_id"] if "review_item_id" in row.keys() else None,
            generation_id=row["generation_id"] if "generation_id" in row.keys() else None,
            state_before=row["state_before"] if "state_before" in row.keys() else None,
            state_after=row["state_after"] if "state_after" in row.keys() else None,
            status=row["status"] if "status" in row.keys() else None,
            duration_ms=row["duration_ms"] if "duration_ms" in row.keys() else None,
            message=row["message"] if "message" in row.keys() else None,
            error_type=row["error_type"] if "error_type" in row.keys() else None,
            error_message=row["error_message"] if "error_message" in row.keys() else None,
            stack_trace=row["stack_trace"] if "stack_trace" in row.keys() else None,
            parent_event_id=row["parent_event_id"] if "parent_event_id" in row.keys() else None,
            correlation_id=row["correlation_id"] if "correlation_id" in row.keys() else None,
            input_fingerprint=row["input_fingerprint"] if "input_fingerprint" in row.keys() else None,
            output_fingerprint=row["output_fingerprint"] if "output_fingerprint" in row.keys() else None,
            source=row["source"] if "source" in row.keys() else None,
            component=row["component"] if "component" in row.keys() else None,
            version=row["version"] if "version" in row.keys() else None,
            payload=payload
        )


# ---------------- Context Propagation ----------------

_current_ctx = contextvars.ContextVar("tenderops_telemetry_ctx", default={})
_id_lock = threading.Lock()
_event_seq = 0

def _generate_event_id() -> str:
    global _event_seq
    with _id_lock:
        _event_seq += 1
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
        rnd = hashlib.sha256(os.urandom(8)).hexdigest()[:6]
        return f"E-{now_str}-{_event_seq:05d}-{rnd}"

def _generate_correlation_id() -> str:
    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
    rnd = hashlib.sha256(os.urandom(8)).hexdigest()[:8]
    return f"c_{now_str}_{rnd}"

def get_context() -> Dict[str, Any]:
    return dict(_current_ctx.get() or {})

def set_context_values(**kwargs):
    ctx = get_context()
    ctx.update({k: v for k, v in kwargs.items() if v is not None})
    _current_ctx.set(ctx)

@contextlib.contextmanager
def scope(**kwargs):
    old_ctx = get_context()
    new_ctx = dict(old_ctx)
    if "correlation_id" not in new_ctx and "correlation_id" not in kwargs:
        new_ctx["correlation_id"] = _generate_correlation_id()
    new_ctx.update({k: v for k, v in kwargs.items() if v is not None})
    token = _current_ctx.set(new_ctx)
    try:
        yield new_ctx
    finally:
        _current_ctx.reset(token)


# ---------------- Database Initialization & Persistence ----------------

_db_initialized = False
_db_init_lock = threading.Lock()

def init_telemetry_db(db_path: Optional[Union[str, Path]] = None):
    global _db_initialized
    with _db_init_lock:
        target = Path(db_path) if db_path else DEFAULT_DB_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(target), timeout=20)
        try:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS telemetry_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                timestamp TEXT NOT NULL,
                severity TEXT NOT NULL,
                event_type TEXT NOT NULL,
                session_id TEXT,
                actor_id TEXT,
                actor_type TEXT,
                module TEXT,
                route TEXT,
                operation TEXT,
                tender_id INTEGER,
                project_id TEXT,
                run_id INTEGER,
                document_id TEXT,
                review_item_id TEXT,
                generation_id TEXT,
                state_before TEXT,
                state_after TEXT,
                status TEXT,
                duration_ms REAL,
                message TEXT,
                error_type TEXT,
                error_message TEXT,
                stack_trace TEXT,
                parent_event_id TEXT,
                correlation_id TEXT,
                input_fingerprint TEXT,
                output_fingerprint TEXT,
                source TEXT,
                component TEXT,
                version TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_telemetry_timestamp ON telemetry_events(timestamp);
            CREATE INDEX IF NOT EXISTS idx_telemetry_event_type ON telemetry_events(event_type);
            CREATE INDEX IF NOT EXISTS idx_telemetry_severity ON telemetry_events(severity);
            CREATE INDEX IF NOT EXISTS idx_telemetry_run_id ON telemetry_events(run_id);
            CREATE INDEX IF NOT EXISTS idx_telemetry_tender_id ON telemetry_events(tender_id);
            CREATE INDEX IF NOT EXISTS idx_telemetry_correlation_id ON telemetry_events(correlation_id);
            CREATE INDEX IF NOT EXISTS idx_telemetry_session_id ON telemetry_events(session_id);
            CREATE INDEX IF NOT EXISTS idx_telemetry_error_type ON telemetry_events(error_type);

            CREATE TABLE IF NOT EXISTS telemetry_incidents (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                severity TEXT NOT NULL,
                tender_id INTEGER,
                run_id INTEGER,
                session_id TEXT,
                event_count INTEGER NOT NULL DEFAULT 0,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                likely_cause TEXT,
                impact TEXT,
                status TEXT NOT NULL DEFAULT 'OPEN',
                details_json TEXT
            );

            CREATE TABLE IF NOT EXISTS telemetry_sessions (
                session_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                last_active_at TEXT NOT NULL,
                actor_id TEXT,
                active_module TEXT,
                active_tender_id INTEGER,
                active_run_id INTEGER,
                active_project_id TEXT,
                metadata_json TEXT
            );
            """)
            conn.commit()
            _db_initialized = True
        finally:
            conn.close()


def _ensure_file_sinks():
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        APP_LOG.parent.mkdir(parents=True, exist_ok=True)
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        ERRORS_LOG.parent.mkdir(parents=True, exist_ok=True)
        DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _write_file_sink(event: TelemetryEvent):
    _ensure_file_sinks()
    line = event.to_json() + "\n"
    try:
        # App log captures INFO and above
        if event.severity in (Severity.INFO, Severity.WARNING, Severity.ERROR, Severity.CRITICAL):
            with open(str(APP_LOG), "a", encoding="utf-8") as f:
                f.write(line)
        # Audit log captures state transitions, review, decisions, artifacts, auth
        if event.event_type in (
            EventType.STATE_TRANSITION, EventType.RUN_CREATED, EventType.RUN_COMPLETED,
            EventType.RUN_FAILED, EventType.PRICE_DECISION, EventType.HUMAN_GATE_RESOLVED,
            EventType.REVIEW_COMPLETED, EventType.GENERATION_COMPLETED, EventType.GENERATION_BLOCKED,
            EventType.DOCUMENT_ASSOCIATED, EventType.DOCUMENT_ASSOCIATION_REJECTED,
            EventType.ARTIFACT_CREATED, EventType.INVARIANT_VIOLATION, EventType.CORRECTION_APPLIED
        ):
            with open(str(AUDIT_LOG), "a", encoding="utf-8") as f:
                f.write(line)
        # Errors log captures WARNING, ERROR, CRITICAL
        if event.severity in (Severity.ERROR, Severity.CRITICAL, Severity.WARNING):
            with open(str(ERRORS_LOG), "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass


def log_event(
    event_type: str,
    message: Optional[str] = None,
    severity: str = Severity.INFO,
    module: Optional[str] = None,
    operation: Optional[str] = None,
    route: Optional[str] = None,
    tender_id: Optional[int] = None,
    project_id: Optional[str] = None,
    run_id: Optional[int] = None,
    document_id: Optional[str] = None,
    review_item_id: Optional[str] = None,
    generation_id: Optional[str] = None,
    state_before: Optional[str] = None,
    state_after: Optional[str] = None,
    status: Optional[str] = None,
    duration_ms: Optional[float] = None,
    error_type: Optional[str] = None,
    error_message: Optional[str] = None,
    stack_trace: Optional[str] = None,
    correlation_id: Optional[str] = None,
    parent_event_id: Optional[str] = None,
    session_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_type: Optional[str] = None,
    input_fingerprint: Optional[str] = None,
    output_fingerprint: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    db_path: Optional[Union[str, Path]] = None,
    component: Optional[str] = "backend"
) -> TelemetryEvent:
    """Canonical logging entrypoint. Writes to SQLite + file sinks."""
    ctx = get_context()
    now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    eid = _generate_event_id()

    # Context fallbacks
    eff_corr = correlation_id or ctx.get("correlation_id") or _generate_correlation_id()
    eff_session = session_id or ctx.get("session_id")
    eff_actor_id = actor_id or ctx.get("actor_id")
    eff_actor_type = actor_type or ctx.get("actor_type") or "system"
    eff_module = module or ctx.get("module")
    eff_route = route or ctx.get("route")
    eff_tender = tender_id if tender_id is not None else ctx.get("tender_id")
    eff_project = project_id if project_id is not None else ctx.get("project_id")
    eff_run = run_id if run_id is not None else ctx.get("run_id")
    eff_parent = parent_event_id or ctx.get("parent_event_id")

    event = TelemetryEvent(
        event_id=eid,
        timestamp=now_utc,
        severity=severity,
        event_type=event_type,
        session_id=eff_session,
        actor_id=eff_actor_id,
        actor_type=eff_actor_type,
        module=eff_module,
        route=eff_route,
        operation=operation or ctx.get("operation"),
        tender_id=eff_tender,
        project_id=eff_project,
        run_id=eff_run,
        document_id=document_id or ctx.get("document_id"),
        review_item_id=review_item_id,
        generation_id=generation_id,
        state_before=state_before,
        state_after=state_after,
        status=status,
        duration_ms=duration_ms,
        message=message or f"{event_type} event recorded",
        error_type=error_type,
        error_message=error_message,
        stack_trace=stack_trace,
        parent_event_id=eff_parent,
        correlation_id=eff_corr,
        input_fingerprint=input_fingerprint,
        output_fingerprint=output_fingerprint,
        source="tenderops",
        component=component,
        version=APP_VERSION,
        payload=redact_sensitive(payload or {})
    )

    # Persist to database
    target_db = Path(db_path) if db_path else DEFAULT_DB_PATH
    try:
        if not _db_initialized:
            init_telemetry_db(target_db)
        conn = sqlite3.connect(str(target_db), timeout=10)
        try:
            conn.execute("""
                INSERT INTO telemetry_events (
                    event_id, timestamp, severity, event_type, session_id, actor_id, actor_type,
                    module, route, operation, tender_id, project_id, run_id, document_id,
                    review_item_id, generation_id, state_before, state_after, status,
                    duration_ms, message, error_type, error_message, stack_trace,
                    parent_event_id, correlation_id, input_fingerprint, output_fingerprint,
                    source, component, version, payload_json, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
            """, (
                event.event_id, event.timestamp, event.severity, event.event_type,
                event.session_id, event.actor_id, event.actor_type, event.module,
                event.route, event.operation, event.tender_id, event.project_id,
                event.run_id, event.document_id, event.review_item_id, event.generation_id,
                event.state_before, event.state_after, event.status, event.duration_ms,
                event.message, event.error_type, event.error_message, event.stack_trace,
                event.parent_event_id, event.correlation_id, event.input_fingerprint,
                event.output_fingerprint, event.source, event.component, event.version,
                json.dumps(event.payload or {}, ensure_ascii=False), event.timestamp
            ))

            # Maintain session record if session_id is active
            if event.session_id:
                conn.execute("""
                    INSERT INTO telemetry_sessions (
                        session_id, created_at, last_active_at, actor_id, active_module,
                        active_tender_id, active_run_id, active_project_id, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        last_active_at=excluded.last_active_at,
                        active_module=COALESCE(excluded.active_module, active_module),
                        active_tender_id=COALESCE(excluded.active_tender_id, active_tender_id),
                        active_run_id=COALESCE(excluded.active_run_id, active_run_id),
                        active_project_id=COALESCE(excluded.active_project_id, active_project_id)
                """, (
                    event.session_id, event.timestamp, event.timestamp, event.actor_id,
                    event.module, event.tender_id, event.run_id, event.project_id, "{}"
                ))

            # Auto-group into incident if CRITICAL or INVARIANT_VIOLATION
            if event.severity in (Severity.CRITICAL, Severity.ERROR) or event.event_type == EventType.INVARIANT_VIOLATION:
                _record_incident_auto(conn, event)

            conn.commit()
        finally:
            conn.close()
    except Exception as ex:
        # Failsafe: never crash the core app because telemetry DB write failed
        try:
            print(f"[telemetry_error] DB write failed: {ex}", file=sys.stderr)
        except Exception:
            pass

    # Persist to file sinks
    _write_file_sink(event)

    return event


def _record_incident_auto(conn: sqlite3.Connection, event: TelemetryEvent):
    """Groups related errors or invariant violations into a tracked incident."""
    try:
        cause = event.error_type or event.event_type
        # Incident identifier derived from tender + run + cause or generic
        prefix = f"INC-T{event.tender_id or 0}-R{event.run_id or 0}"
        inc_id = f"{prefix}-{hashlib.md5(cause.encode('utf-8')).hexdigest()[:6].upper()}"

        title = event.message or f"Failure: {cause}"
        row = conn.execute("SELECT id, event_count, first_seen FROM telemetry_incidents WHERE id=?", (inc_id,)).fetchone()
        if row:
            conn.execute("""
                UPDATE telemetry_incidents SET
                    event_count = event_count + 1,
                    last_seen = ?,
                    severity = ?
                WHERE id = ?
            """, (event.timestamp, event.severity, inc_id))
        else:
            conn.execute("""
                INSERT INTO telemetry_incidents (
                    id, title, severity, tender_id, run_id, session_id,
                    event_count, first_seen, last_seen, likely_cause, impact, status, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                inc_id, title, event.severity, event.tender_id, event.run_id, event.session_id,
                1, event.timestamp, event.timestamp, cause, "Potential inconsistency or workflow obstruction",
                "OPEN", json.dumps({"originating_event": event.event_id}, ensure_ascii=False)
            ))
    except Exception:
        pass


# ---------------- State Transitions & Error Helpers ----------------

def record_state_transition(
    entity_type: str,
    entity_id: Union[int, str],
    state_before: str,
    state_after: str,
    event_type: str = EventType.STATE_TRANSITION,
    detail: Optional[str] = None,
    module: Optional[str] = None,
    run_id: Optional[int] = None,
    tender_id: Optional[int] = None,
    db_path: Optional[Union[str, Path]] = None,
    payload: Optional[Dict[str, Any]] = None
) -> TelemetryEvent:
    """Explicitly logs a workflow/entity state transition."""
    msg = f"{entity_type} {entity_id}: '{state_before}' -> '{state_after}'"
    if detail:
        msg += f" ({detail})"
    
    tid = tender_id if tender_id is not None else (int(entity_id) if str(entity_id).isdigit() and entity_type.lower() == "tender" else None)
    rid = run_id if run_id is not None else (int(entity_id) if str(entity_id).isdigit() and entity_type.lower() == "run" else None)

    return log_event(
        event_type=event_type,
        message=msg,
        severity=Severity.INFO,
        module=module,
        tender_id=tid,
        run_id=rid,
        state_before=state_before,
        state_after=state_after,
        status="SUCCESS",
        payload=payload or {"entity_type": entity_type, "entity_id": str(entity_id), "detail": detail},
        db_path=db_path
    )


def record_error(
    exc: BaseException,
    module: Optional[str] = None,
    operation: Optional[str] = None,
    tender_id: Optional[int] = None,
    run_id: Optional[int] = None,
    severity: str = Severity.ERROR,
    state_before: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    db_path: Optional[Union[str, Path]] = None
) -> TelemetryEvent:
    """Logs an exception with traceback and causal details."""
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    err_cls = exc.__class__.__name__
    err_msg = str(exc)
    msg = f"Error in {module or 'app'}.{operation or 'op'}: {err_cls}: {err_msg}"

    return log_event(
        event_type=EventType.RUN_FAILED if run_id else EventType.CONSISTENCY_CHECK_FAILED,
        message=msg,
        severity=severity,
        module=module,
        operation=operation,
        tender_id=tender_id,
        run_id=run_id,
        state_before=state_before,
        status="FAILED",
        error_type=err_cls,
        error_message=err_msg,
        stack_trace=tb,
        payload=payload or {},
        db_path=db_path
    )


def record_invariant_violation(
    invariant_name: str,
    expected: Any,
    found: Any,
    module: Optional[str] = None,
    tender_id: Optional[int] = None,
    run_id: Optional[int] = None,
    severity: str = Severity.ERROR,
    details: Optional[Dict[str, Any]] = None,
    db_path: Optional[Union[str, Path]] = None
) -> TelemetryEvent:
    """Logs an invariant / integrity check violation."""
    msg = f"Invariant '{invariant_name}' violated. Expected: {expected}, Found: {found}"
    p = details or {}
    p.update({"invariant": invariant_name, "expected": str(expected), "found": str(found)})
    
    return log_event(
        event_type=EventType.INVARIANT_VIOLATION,
        message=msg,
        severity=severity,
        module=module,
        tender_id=tender_id,
        run_id=run_id,
        error_type=f"INVARIANT_{invariant_name.upper()}",
        error_message=msg,
        payload=p,
        db_path=db_path
    )


# ---------------- Diagnostic & Invariant Checking Engine ----------------

class InvariantResult:
    def __init__(self, name: str, passed: bool, expected: Any, found: Any,
                 message: str, severity: str = Severity.ERROR,
                 originating_event_id: Optional[str] = None,
                 first_observed: Optional[str] = None,
                 likely_cause: Optional[str] = None,
                 recommended_command: Optional[str] = None):
        self.name = name
        self.passed = passed
        self.expected = expected
        self.found = found
        self.message = message
        self.severity = severity
        self.originating_event_id = originating_event_id
        self.first_observed = first_observed or datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%S")
        self.likely_cause = likely_cause
        self.recommended_command = recommended_command

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "status": "PASS" if self.passed else "FAIL",
            "expected": self.expected,
            "found": self.found,
            "message": self.message,
            "severity": self.severity,
            "originating_event_id": self.originating_event_id,
            "first_observed": self.first_observed,
            "likely_cause": self.likely_cause,
            "recommended_command": self.recommended_command
        }


def check_all_invariants(
    db_path: Optional[Union[str, Path]] = None,
    target_run_id: Optional[int] = None,
    target_tender_id: Optional[int] = None
) -> List[InvariantResult]:
    """Runs the suite of diagnostic and integrity invariants."""
    results: List[InvariantResult] = []
    target_db = Path(db_path) if db_path else DEFAULT_DB_PATH

    # 1. Database PRAGMA check
    if not target_db.exists():
        results.append(InvariantResult(
            name="database_exists",
            passed=False,
            expected=f"File {target_db.name} exists",
            found="Database file missing",
            message="Database file does not exist on disk",
            severity=Severity.CRITICAL,
            likely_cause="Database file was deleted or uninitialized",
            recommended_command="tenderops db status"
        ))
        return results

    try:
        conn = sqlite3.connect(str(target_db), timeout=10)
        conn.row_factory = sqlite3.Row
        qc = conn.execute("PRAGMA quick_check").fetchone()[0]
        results.append(InvariantResult(
            name="sqlite_quick_check",
            passed=(qc == "ok"),
            expected="ok",
            found=qc,
            message="SQLite database structure integrity check",
            severity=Severity.CRITICAL if qc != "ok" else Severity.INFO,
            likely_cause="Database file corruption" if qc != "ok" else None,
            recommended_command="tenderops db integrity"
        ))

        # 2. Orphaned run_events
        orphaned_ev = conn.execute("SELECT COUNT(*) FROM run_events WHERE run_id NOT IN (SELECT id FROM runs)").fetchone()[0]
        results.append(InvariantResult(
            name="orphan_run_events",
            passed=(orphaned_ev == 0),
            expected="0 orphaned events",
            found=f"{orphaned_ev} orphaned events",
            message="All run events must reference valid runs",
            severity=Severity.ERROR if orphaned_ev > 0 else Severity.INFO,
            likely_cause="Runs table purged without cascading to run_events",
            recommended_command="tenderops doctor"
        ))

        # 3. Orphaned runs without tender
        orphaned_runs = conn.execute("SELECT COUNT(*) FROM runs WHERE tender_id IS NULL OR tender_id = 0").fetchone()[0]
        results.append(InvariantResult(
            name="orphan_runs",
            passed=(orphaned_runs == 0),
            expected="0 runs without tender_id",
            found=f"{orphaned_runs} orphan runs",
            message="All runs must have an associated tender_id",
            severity=Severity.WARNING if orphaned_runs > 0 else Severity.INFO,
            likely_cause="Run created without tender context",
            recommended_command="tenderops doctor"
        ))

        # 4. Cross-tender document ownership & pack consistency
        proc_dir = ROOT / "data" / "demo" / "processed"
        if proc_dir.exists():
            for p in proc_dir.glob("*.json"):
                if p.name.endswith(".error.json") or not p.stem.isdigit():
                    continue
                tid = int(p.stem)
                if target_tender_id is not None and tid != target_tender_id:
                    continue
                try:
                    pk = json.loads(p.read_text(encoding="utf-8-sig"))
                    pk_tid = pk.get("tenderId")
                    if pk_tid is not None and pk_tid != tid:
                        results.append(InvariantResult(
                            name="artifact_identity_mismatch",
                            passed=False,
                            expected=f"tenderId={tid}",
                            found=f"tenderId={pk_tid}",
                            message=f"Pack {p.name} contains tenderId={pk_tid} instead of {tid}",
                            severity=Severity.CRITICAL,
                            likely_cause="Run-scoped artifact isolation failure / Cross-tender leak",
                            recommended_command=f"tenderops doctor --tender {tid}"
                        ))
                    else:
                        # Check empty completed analysis
                        boq_len = len(pk.get("boq", []))
                        if boq_len == 0:
                            results.append(InvariantResult(
                                name="empty_completed_analysis",
                                passed=False,
                                expected="> 0 BOQ rows",
                                found="0 rows",
                                message=f"Pack for tender {tid} has 0 BOQ rows",
                                severity=Severity.WARNING,
                                likely_cause="KSS spreadsheet had no valid item rows or parser rejected table",
                                recommended_command=f"tenderops why tender {tid}"
                            ))
                except Exception as ex:
                    results.append(InvariantResult(
                        name="pack_json_validity",
                        passed=False,
                        expected="Valid JSON pack",
                        found=str(ex),
                        message=f"Failed parsing processed pack {p.name}",
                        severity=Severity.ERROR,
                        likely_cause="Malformed JSON or interrupted write",
                        recommended_command=f"tenderops files tender {tid}"
                    ))

        # 5. History record integrity (outcomes point to completed runs)
        try:
            bad_outcomes = conn.execute("""
                SELECT COUNT(*) FROM outcomes o
                WHERE o.tender_id NOT IN (SELECT DISTINCT tender_id FROM runs WHERE state IN ('PACKED', 'COMPLETED'))
            """).fetchone()[0]
            results.append(InvariantResult(
                name="history_integrity",
                passed=(bad_outcomes == 0),
                expected="0 outcomes without completed run",
                found=f"{bad_outcomes} unverified outcomes",
                message="Outcomes memory should refer to successfully processed tenders",
                severity=Severity.WARNING if bad_outcomes > 0 else Severity.INFO,
                likely_cause="Outcome recorded before pipeline finished",
                recommended_command="tenderops state"
            ))
        except Exception:
            pass

        # 6. Specific run checks if target_run_id is specified
        if target_run_id is not None:
            r_row = conn.execute("SELECT * FROM runs WHERE id=?", (target_run_id,)).fetchone()
            if not r_row:
                results.append(InvariantResult(
                    name="run_existence",
                    passed=False,
                    expected=f"Run #{target_run_id} exists",
                    found="Not found",
                    message=f"Run #{target_run_id} not in database",
                    severity=Severity.ERROR,
                    recommended_command="tenderops run list"
                ))
            else:
                tid = r_row["tender_id"]
                # Verify run artifact
                pack_file = proc_dir / f"{tid}.json"
                if r_row["state"] in ("PACKED", "COMPLETED") and not pack_file.exists():
                    results.append(InvariantResult(
                        name="run_artifact_missing",
                        passed=False,
                        expected=f"Processed pack {pack_file.name} exists",
                        found="Missing on disk",
                        message=f"Run #{target_run_id} is marked {r_row['state']} but artifact is missing",
                        severity=Severity.ERROR,
                        likely_cause="Artifact deleted after run completed",
                        recommended_command=f"tenderops run outputs {target_run_id}"
                    ))

        conn.close()
    except Exception as ex:
        results.append(InvariantResult(
            name="invariant_check_execution",
            passed=False,
            expected="Clean execution",
            found=str(ex),
            message=f"Error running invariant check suite: {ex}",
            severity=Severity.ERROR,
            recommended_command="tenderops doctor"
        ))

    return results


# ---------------- Forensic & Diagnosis Reasoning Engine ----------------

def query_events(
    db_path: Optional[Union[str, Path]] = None,
    run_id: Optional[int] = None,
    tender_id: Optional[int] = None,
    session_id: Optional[str] = None,
    severity: Optional[str] = None,
    event_type: Optional[str] = None,
    module: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
) -> List[TelemetryEvent]:
    """Queries telemetry events with rich forensic filtering."""
    target_db = Path(db_path) if db_path else DEFAULT_DB_PATH
    if not target_db.exists():
        return []

    try:
        if not _db_initialized:
            init_telemetry_db(target_db)
        conn = sqlite3.connect(str(target_db), timeout=10)
        conn.row_factory = sqlite3.Row
        sql = ["SELECT * FROM telemetry_events WHERE 1=1"]
        params: List[Any] = []

        if run_id is not None:
            sql.append("AND run_id = ?")
            params.append(run_id)
        if tender_id is not None:
            sql.append("AND tender_id = ?")
            params.append(tender_id)
        if session_id is not None:
            sql.append("AND session_id = ?")
            params.append(session_id)
        if severity is not None:
            sql.append("AND severity = ?")
            params.append(severity.upper())
        if event_type is not None:
            sql.append("AND event_type = ?")
            params.append(event_type.upper())
        if module is not None:
            sql.append("AND module = ?")
            params.append(module.lower())
        if since is not None:
            # support duration strings like '30m', '2h', '1d' or ISO timestamp
            ts_since = parse_time_filter(since)
            sql.append("AND timestamp >= ?")
            params.append(ts_since)
        if until is not None:
            ts_until = parse_time_filter(until)
            sql.append("AND timestamp <= ?")
            params.append(ts_until)

        sql.append("ORDER BY id ASC LIMIT ? OFFSET ?")
        params.extend([limit, offset])

        rows = conn.execute(" ".join(sql), params).fetchall()
        conn.close()
        return [TelemetryEvent.from_row(r) for r in rows]
    except Exception:
        return []


def parse_time_filter(val: str) -> str:
    """Converts '30m', '2h', '1d' into an ISO timestamp relative to now, or returns ISO string."""
    m = re.match(r'^(\d+)([smhd])$', val.strip().lower())
    if m:
        num, unit = int(m.group(1)), m.group(2)
        delta = {
            's': datetime.timedelta(seconds=num),
            'm': datetime.timedelta(minutes=num),
            'h': datetime.timedelta(hours=num),
            'd': datetime.timedelta(days=num)
        }[unit]
        past = datetime.datetime.now(datetime.timezone.utc) - delta
        return past.isoformat()
    return val


def explain_event(event_id: str, db_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """Provides human and machine causal explanation for an event in context."""
    target_db = Path(db_path) if db_path else DEFAULT_DB_PATH
    if not target_db.exists():
        return {"error": "Database not found"}

    try:
        conn = sqlite3.connect(str(target_db), timeout=10)
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT * FROM telemetry_events WHERE event_id=?", (event_id,)).fetchone()
        if not r:
            conn.close()
            return {"error": f"Event {event_id} not found"}

        ev = TelemetryEvent.from_row(r)
        # Fetch related events with same correlation_id or run_id
        related_rows = conn.execute("""
            SELECT event_id, timestamp, severity, event_type, message, status
            FROM telemetry_events
            WHERE (correlation_id = ? OR (run_id IS NOT NULL AND run_id = ?)) AND event_id != ?
            ORDER BY id ASC LIMIT 10
        """, (ev.correlation_id, ev.run_id, ev.event_id)).fetchall()
        conn.close()

        # Build causal narrative
        why_narrative = _build_event_why_narrative(ev)
        p = ev.payload or {}
        expected = None
        observed = None
        code = f"EVENT_{ev.event_type}"
        recommended = "tenderops logs errors"
        meaning = ev.message or why_narrative

        if ev.event_type == EventType.DOCUMENT_ASSOCIATION_REJECTED:
            code = "DOCUMENT_ASSOCIATION_REJECTED"
            expected = {
                "tender_id": p.get("target_tender_id", ev.tender_id),
                "run_id": p.get("target_run_id", ev.run_id)
            }
            observed = {
                "tender_id": p.get("document_tender_id"),
                "run_id": p.get("document_run_id")
            }
            meaning = "A document was prevented from entering the requested run due to ownership mismatch."
            recommended = f"tenderops why document {ev.tender_id or ''}".strip()
        elif ev.event_type == EventType.GENERATION_BLOCKED:
            code = "GENERATION_BLOCKED"
            expected = "Review completion and zero blockers before generation"
            observed = {
                "review_required": p.get("review_required"),
                "review_completed": p.get("review_completed"),
                "blockers": p.get("blockers")
            }
            meaning = "Generation request was blocked by unmet review or gate conditions."
            recommended = "tenderops why generation"
        elif ev.event_type == EventType.INVARIANT_VIOLATION:
            code = f"INVARIANT_{str(p.get('invariant', 'UNKNOWN')).upper()}"
            expected = p.get("expected")
            observed = p.get("found")
            meaning = "A system invariant was violated."
            recommended = "tenderops validate"

        return {
            "event_id": ev.event_id,
            "timestamp": ev.timestamp,
            "event_type": ev.event_type,
            "severity": ev.severity,
            "module": ev.module,
            "route": ev.route,
            "operation": ev.operation,
            "tender_id": ev.tender_id,
            "run_id": ev.run_id,
            "state_before": ev.state_before,
            "state_after": ev.state_after,
            "status": ev.status,
            "duration_ms": ev.duration_ms,
            "message": ev.message,
            "error_type": ev.error_type,
            "error_message": ev.error_message,
            "diagnostic_code": code,
            "meaning": meaning,
            "expected": expected,
            "observed": observed,
            "why_explanation": why_narrative,
            "payload": ev.payload,
            "related_events": [dict(rw) for rw in related_rows],
            "recommended_command": recommended
        }
    except Exception as ex:
        return {"error": str(ex)}


def _build_event_why_narrative(ev: TelemetryEvent) -> str:
    """Synthesizes causal description based on event type and payload."""
    t = ev.event_type
    p = ev.payload or {}

    if t == EventType.DOCUMENT_ASSOCIATION_REJECTED:
        doc_tender = p.get("document_tender_id", "unknown")
        target_tender = p.get("target_tender_id", ev.tender_id)
        reason = p.get("reason", "Ownership violation")
        return f"Document belongs to Tender {doc_tender}; attempted association with Tender {target_tender} was rejected due to: {reason}."
    elif t == EventType.GENERATION_BLOCKED:
        req = p.get("review_required", 5)
        comp = p.get("review_completed", 0)
        blockers = p.get("blockers", 0)
        return f"Document generation blocked: {comp}/{req} review hubs completed, {blockers} blocker items unresolved."
    elif t == EventType.INVARIANT_VIOLATION:
        return f"System invariant '{p.get('invariant')}' failed: expected {p.get('expected')}, but found {p.get('found')}."
    elif t == EventType.PRICE_DECISION:
        return f"BOQ line '{p.get('boq_key')}' priced via {p.get('method')} with score {p.get('score')}; chosen item: {p.get('chosen_item_id')}."
    elif t == EventType.HUMAN_GATE_CREATED:
        return f"Human review gate created for item '{p.get('boq_key')}': {p.get('reason', 'no price found')}."
    elif t == EventType.ARTIFACT_HASH_MISMATCH:
        return f"Artifact hash mismatch at {p.get('path')}: stored hash differed from payload content hash."
    elif ev.error_message:
        return f"Operation failed with {ev.error_type}: {ev.error_message}."
    return ev.message or f"Event {t} executed successfully."


def interpret_run(run_id: int, db_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """Produces comprehensive human + machine diagnosis for a given run."""
    target_db = Path(db_path) if db_path else DEFAULT_DB_PATH
    if not target_db.exists():
        return {"error": "Database not found"}

    try:
        conn = sqlite3.connect(str(target_db), timeout=10)
        conn.row_factory = sqlite3.Row
        r_run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not r_run:
            conn.close()
            return {"error": f"Run #{run_id} not found in database"}

        tid = r_run["tender_id"]
        run_dict = dict(r_run)

        # Telemetry events
        events_rows = conn.execute(
            "SELECT * FROM telemetry_events WHERE run_id=? ORDER BY id ASC", (run_id,)
        ).fetchall()
        events = [TelemetryEvent.from_row(r) for r in events_rows]

        # Legacy run_events if telemetry_events are sparse
        if not events:
            legacy_evs = conn.execute("SELECT * FROM run_events WHERE run_id=? ORDER BY id ASC", (run_id,)).fetchall()
            for le in legacy_evs:
                events.append(TelemetryEvent(
                    event_id=f"legacy-{le['id']}",
                    timestamp=le["at"] if "at" in le.keys() else datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    severity=Severity.INFO if le["state"] not in ("FAILED", "ERROR") else Severity.ERROR,
                    event_type=le["state"],
                    run_id=run_id,
                    tender_id=tid,
                    message=le["detail"]
                ))
        conn.close()

        # Build timeline
        timeline = []
        failures = []
        for ev in events:
            ts_short = ev.timestamp[11:19] if len(ev.timestamp) >= 19 else ev.timestamp
            timeline.append({
                "time": ts_short,
                "event_id": ev.event_id,
                "type": ev.event_type,
                "severity": ev.severity,
                "detail": ev.message
            })
            if ev.severity in (Severity.ERROR, Severity.CRITICAL) or "FAIL" in ev.event_type:
                failures.append(ev)

        # Check invariants for this run
        invariants = check_all_invariants(target_db, target_run_id=run_id, target_tender_id=tid)
        failed_invs = [inv for inv in invariants if not inv.passed]

        # Synthesize status & root cause
        final_state = run_dict.get("state", "UNKNOWN")
        is_failed = final_state in ("FAILED", "ERROR") or len(failures) > 0 or len(failed_invs) > 0

        root_cause = "None detected; run executed normally."
        impact = "Run output is valid and ready for review."
        next_cmd = f"tenderops why run {run_id}"

        if is_failed:
            if failed_invs:
                root_cause = failed_invs[0].likely_cause or failed_invs[0].message
                impact = "Analysis result cannot be trusted."
                next_cmd = failed_invs[0].recommended_command or f"tenderops doctor --run {run_id}"
            elif failures:
                f_last = failures[-1]
                root_cause = f_last.error_message or f_last.message
                impact = "Execution was aborted before all outputs were written."
                next_cmd = f"tenderops logs explain {f_last.event_id}"
            else:
                root_cause = f"Run concluded in failed state: {final_state}"
                impact = "Run incomplete."
                next_cmd = f"tenderops doctor --run {run_id}"

        return {
            "run_id": run_id,
            "tender_id": tid,
            "status": "FAILED" if is_failed else "SUCCESS",
            "state": final_state,
            "started_at": run_dict.get("started_at"),
            "finished_at": run_dict.get("finished_at"),
            "cost_env_id": run_dict.get("cost_env_id"),
            "timeline": timeline,
            "failures": [
                {
                    "event_id": f.event_id,
                    "type": f.event_type,
                    "error_type": f.error_type,
                    "message": f.message,
                    "error_message": f.error_message,
                    "stack_trace": f.stack_trace
                } for f in failures
            ],
            "invariants": [inv.to_dict() for inv in invariants],
            "root_cause": root_cause,
            "impact": impact,
            "recommended_next_command": next_cmd
        }
    except Exception as ex:
        return {"error": str(ex)}


def group_errors(
    db_path: Optional[Union[str, Path]] = None,
    since: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Groups related error events by error pattern/type."""
    events = query_events(db_path=db_path, since=since, limit=1000)
    error_events = [e for e in events if e.severity in (Severity.ERROR, Severity.CRITICAL) or e.error_type or "FAIL" in e.event_type]

    groups: Dict[str, Dict[str, Any]] = {}
    for ev in error_events:
        group_key = ev.error_type or ev.event_type
        if group_key not in groups:
            groups[group_key] = {
                "error_type": group_key,
                "count": 0,
                "first_seen": ev.timestamp,
                "last_seen": ev.timestamp,
                "affected_tenders": set(),
                "affected_runs": set(),
                "affected_sessions": set(),
                "sample_messages": [],
                "events": []
            }
        g = groups[group_key]
        g["count"] += 1
        g["last_seen"] = ev.timestamp
        if ev.tender_id is not None:
            g["affected_tenders"].add(ev.tender_id)
        if ev.run_id is not None:
            g["affected_runs"].add(ev.run_id)
        if ev.session_id:
            g["affected_sessions"].add(ev.session_id)
        if len(g["sample_messages"]) < 3 and ev.message:
            g["sample_messages"].append(ev.message)
        g["events"].append(ev.event_id)

    out = []
    for idx, (k, g) in enumerate(sorted(groups.items(), key=lambda x: -x[1]["count"]), 1):
        out.append({
            "group_id": idx,
            "error_type": k,
            "count": g["count"],
            "first_seen": g["first_seen"],
            "last_seen": g["last_seen"],
            "affected_tenders": sorted(list(g["affected_tenders"])),
            "affected_runs": sorted(list(g["affected_runs"])),
            "affected_sessions": sorted(list(g["affected_sessions"])),
            "sample_messages": g["sample_messages"],
            "event_ids": g["events"]
        })
    return out


def diagnose_system(
    db_path: Optional[Union[str, Path]] = None,
    run_id: Optional[int] = None,
    tender_id: Optional[int] = None,
    session_id: Optional[str] = None
) -> Dict[str, Any]:
    """Generates the master executive diagnosis report with CRITICAL, WARNING, HEALTH, NEXT."""
    target_db = Path(db_path) if db_path else DEFAULT_DB_PATH
    critical_findings: List[Dict[str, Any]] = []
    warning_findings: List[Dict[str, Any]] = []
    findings: List[Dict[str, Any]] = []
    health: Dict[str, str] = {
        "Database": "PASS",
        "Workflow": "PASS",
        "CostDB": "PASS",
        "Pipeline": "PASS",
        "Stack": "PASS",
        "State model": "PASS"
    }
    root_causes: List[str] = []
    affected_entities: List[Dict[str, Any]] = []
    evidence_events: List[str] = []
    recommended_commands: List[str] = []

    def _code(name: str) -> str:
        return "".join(ch if ch.isalnum() else "_" for ch in name.upper())

    def _push_finding(
        bucket: List[Dict[str, Any]],
        severity: str,
        confidence: str,
        code: str,
        summary: str,
        expected: Any = None,
        observed: Any = None,
        evidence: Optional[List[str]] = None,
        likely_cause: Optional[str] = None,
        recommended_command: Optional[str] = None
    ) -> None:
        rec = {
            "severity": severity,
            "confidence": confidence,
            "code": code,
            "summary": summary,
            "expected": expected,
            "observed": observed,
            "evidence_events": evidence or [],
            "likely_cause": likely_cause,
            "recommended_command": recommended_command
        }
        bucket.append(rec)
        findings.append(rec)
        if recommended_command and recommended_command not in recommended_commands:
            recommended_commands.append(recommended_command)
        if likely_cause and likely_cause not in root_causes:
            root_causes.append(likely_cause)
        for ev in rec["evidence_events"]:
            if ev:
                evidence_events.append(ev)

    # 1. Run Invariants
    invariants = check_all_invariants(target_db, target_run_id=run_id, target_tender_id=tender_id)
    for inv in invariants:
        if not inv.passed:
            inv_code = f"INVARIANT_{_code(inv.name)}"
            if inv.severity in (Severity.CRITICAL, Severity.ERROR):
                _push_finding(
                    critical_findings,
                    severity="CRITICAL",
                    confidence="CONFIRMED",
                    code=inv_code,
                    summary=f"{inv.name}: {inv.message}",
                    expected=inv.expected,
                    observed=inv.found,
                    evidence=[inv.originating_event_id] if inv.originating_event_id else [],
                    likely_cause=inv.likely_cause,
                    recommended_command=inv.recommended_command,
                )
                if "sqlite" in inv.name or "database" in inv.name:
                    health["Database"] = "FAIL"
                elif "artifact" in inv.name:
                    health["Pipeline"] = "FAIL"
                    health["State model"] = "FAIL"
                health["Workflow"] = "FAIL"
            else:
                _push_finding(
                    warning_findings,
                    severity="WARNING",
                    confidence="CONFIRMED",
                    code=inv_code,
                    summary=f"{inv.name}: {inv.message}",
                    expected=inv.expected,
                    observed=inv.found,
                    evidence=[inv.originating_event_id] if inv.originating_event_id else [],
                    likely_cause=inv.likely_cause,
                    recommended_command=inv.recommended_command,
                )

    # 2. Check recent errors
    error_groups = group_errors(target_db, since="2h")
    for eg in error_groups:
        if eg["count"] > 0:
            msg = f"{eg['error_type']} occurred {eg['count']} times (affects tenders {eg['affected_tenders']}, runs {eg['affected_runs']})"
            _push_finding(
                warning_findings,
                severity="WARNING",
                confidence="LIKELY",
                code=f"ERROR_PATTERN_{_code(eg['error_type'])}",
                summary=msg,
                expected="No repeated telemetry error pattern",
                observed=f"{eg['count']} events in pattern group {eg['group_id']}",
                evidence=eg["event_ids"][:3],
                likely_cause=f"Repeated {eg['error_type']} telemetry errors",
                recommended_command=f"tenderops logs errors --group {eg['group_id']}"
            )
            affected_entities.append({"type": "tenders", "ids": eg["affected_tenders"]})
            affected_entities.append({"type": "runs", "ids": eg["affected_runs"]})

    # 3. Overall status
    if critical_findings:
        status = "failed"
        severity = "critical"
        summary = f"Detected {len(critical_findings)} critical diagnostic issues."
    elif warning_findings:
        status = "degraded"
        severity = "warning"
        summary = f"Detected {len(warning_findings)} warnings in telemetry and state."
    else:
        status = "healthy"
        severity = "info"
        summary = "All subsystems and state invariants are passing cleanly."
        recommended_commands.append("tenderops brief")
        findings.append({
            "severity": "INFO",
            "confidence": "INFORMATIONAL",
            "code": "SYSTEM_HEALTHY",
            "summary": "No active critical or warning diagnostic findings.",
            "expected": "No invariant violations",
            "observed": "No active violations",
            "evidence_events": [],
            "likely_cause": None,
            "recommended_command": "tenderops brief"
        })

    if not recommended_commands:
        recommended_commands.append("tenderops doctor")

    return {
        "status": status,
        "severity": severity,
        "summary": summary,
        "critical": critical_findings,
        "warning": warning_findings,
        "warnings": warning_findings,
        "findings": findings,
        "health": health,
        "root_causes": root_causes,
        "affected_entities": affected_entities,
        "evidence_events": list(set(evidence_events)),
        "invariants": [inv.to_dict() for inv in invariants],
        "recommended_commands": recommended_commands
    }


def generate_diagnostic_report(
    incident_id: Optional[str] = None,
    run_id: Optional[int] = None,
    out_path: Optional[Union[str, Path]] = None,
    db_path: Optional[Union[str, Path]] = None
) -> str:
    """Writes an AI/human diagnostic Markdown report to docs/diagnostics/."""
    _ensure_file_sinks()
    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if run_id is not None:
        rep_id = f"RUN-{run_id}"
        diag = interpret_run(run_id, db_path=db_path)
    elif incident_id is not None:
        rep_id = incident_id
        diag = diagnose_system(db_path=db_path)
    else:
        rep_id = f"DIAG-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        diag = diagnose_system(db_path=db_path)

    file_name = f"{rep_id}.md"
    target_file = Path(out_path) if out_path else DIAGNOSTICS_DIR / file_name

    md_lines = [
        f"# TenderOps Diagnostic Forensic Report: {rep_id}",
        f"**Generated:** {now_str}",
        f"**Status:** {diag.get('status', diag.get('state', 'UNKNOWN'))}",
        "",
        "## 1. Executive Diagnosis",
        f"- **Summary:** {diag.get('summary', diag.get('root_cause', 'N/A'))}",
        f"- **Impact:** {diag.get('impact', 'N/A')}",
        f"- **Likely Root Cause:** {diag.get('root_cause', diag.get('root_causes', ['N/A']))}",
        "",
        "## 2. Affected Entities & State",
        f"- **Tender ID:** {diag.get('tender_id', 'N/A')}",
        f"- **Run ID:** {diag.get('run_id', 'N/A')}",
        f"- **Cost Environment ID:** {diag.get('cost_env_id', 'N/A')}",
        "",
        "## 3. Timeline of Observed Events",
    ]

    for t in diag.get("timeline", []):
        md_lines.append(f"- `{t.get('time')}` **[{t.get('type')}]** ({t.get('severity')}) — {t.get('detail')}")

    md_lines.extend([
        "",
        "## 4. Invariant Failures & Integrity Violations"
    ])
    invs = diag.get("invariants", [])
    if invs:
        for inv in invs:
            if not inv.get("passed"):
                md_lines.append(f"- ❌ **{inv.get('name')}**: Expected `{inv.get('expected')}`, Found `{inv.get('found')}`. Cause: {inv.get('likely_cause')}")
            else:
                md_lines.append(f"- ✅ **{inv.get('name')}**: PASS")
    else:
        md_lines.append("- No invariant violations detected.")

    md_lines.extend([
        "",
        "## 5. Recommended Remediation & Next Commands",
    ])
    for cmd in diag.get("recommended_commands", [diag.get("recommended_next_command", "tenderops doctor")]):
        md_lines.append(f"- `{cmd}`")

    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("\n".join(md_lines), encoding="utf-8")
    return str(target_file)
