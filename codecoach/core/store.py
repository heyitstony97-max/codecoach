"""SQLite store. WAL, busy_timeout 60 s, user_version migrations, one connection per thread,
small BEGIN IMMEDIATE transactions. Stdlib + codecoach.core.models only. Datetimes are stored as
fixed-width UTC ISO strings so they sort correctly."""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import (Attempt, Course, CourseMap, Doc, Drop, Item, Job, Objective, Passage,
                     ReviewState, new_id, utcnow)

BUSY_TIMEOUT_MS = 60000
LEGACY_JOB_STALE_S = 120          # a running job with no owner and no update for this long is presumed dead
INTERRUPTED_MESSAGE = ("CodeCoach was restarted while this was running, so it stopped part-way. "
                       "Your course is safe; start it again if you need it.")


def process_token(pid: int | None = None) -> str:
    """'pid:start-time' of a process. A recycled pid has a different start time, so it is not the old owner."""
    pid = os.getpid() if pid is None else pid
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            raw = f.read().decode("utf-8", "replace")
        return f"{pid}:{raw.rsplit(')', 1)[1].split()[19]}"
    except (OSError, IndexError):
        return f"{pid}:0"


def _process_alive(token: str) -> bool:
    try:
        pid = int(token.split(":", 1)[0])
    except ValueError:
        return False
    if pid == os.getpid():
        return process_token(pid) == token
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    now = process_token(pid)
    return now == token or now.endswith(":0") and token.endswith(":0")

# Append only. Index i holds the script that takes user_version i -> i+1.
MIGRATIONS: list[str] = [
    """
    CREATE TABLE courses (id TEXT PRIMARY KEY, title TEXT NOT NULL, mode TEXT NOT NULL,
        mode_reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
    CREATE TABLE docs (id TEXT PRIMARY KEY, course_id TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL);
    CREATE INDEX docs_course ON docs(course_id);
    CREATE TABLE passages (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
        doc_id TEXT NOT NULL, location TEXT NOT NULL, text TEXT NOT NULL);
    CREATE INDEX passages_doc ON passages(doc_id, seq);
    CREATE TABLE course_maps (course_id TEXT PRIMARY KEY, json TEXT NOT NULL);
    CREATE TABLE objectives (id TEXT PRIMARY KEY, course_id TEXT NOT NULL, unit TEXT NOT NULL,
        lesson TEXT NOT NULL, kind TEXT NOT NULL, coverage TEXT NOT NULL, ord INTEGER NOT NULL,
        json TEXT NOT NULL);
    CREATE INDEX objectives_course ON objectives(course_id, ord);
    CREATE TABLE items (id TEXT PRIMARY KEY, objective_id TEXT NOT NULL, course_id TEXT,
        format TEXT NOT NULL, status TEXT NOT NULL, origin TEXT NOT NULL, checker TEXT NOT NULL,
        created_at TEXT NOT NULL, json TEXT NOT NULL);
    CREATE INDEX items_objective ON items(objective_id, status);
    CREATE INDEX items_course ON items(course_id, status);
    CREATE TABLE drops (id INTEGER PRIMARY KEY AUTOINCREMENT, course_id TEXT, objective_id TEXT NOT NULL,
        format TEXT NOT NULL, gate_code TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
        draft_hash TEXT NOT NULL DEFAULT '', at TEXT NOT NULL);
    CREATE INDEX drops_course ON drops(course_id, gate_code);
    CREATE TABLE attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL,
        response TEXT, correct INTEGER, score REAL NOT NULL, graded_by TEXT NOT NULL,
        hints_used INTEGER NOT NULL DEFAULT 0, at TEXT NOT NULL);
    CREATE INDEX attempts_item ON attempts(item_id, id);
    CREATE TABLE review_state (item_id TEXT PRIMARY KEY, due TEXT NOT NULL, stability REAL NOT NULL,
        difficulty REAL NOT NULL, reps INTEGER NOT NULL, lapses INTEGER NOT NULL, last_review TEXT);
    CREATE INDEX review_due ON review_state(due);
    CREATE TABLE reports (id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL,
        course_id TEXT, reason TEXT NOT NULL DEFAULT '', at TEXT NOT NULL);
    CREATE TABLE jobs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, pct REAL NOT NULL,
        stage TEXT NOT NULL DEFAULT '', error TEXT, course_id TEXT, created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL);
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """,
    "ALTER TABLE courses ADD COLUMN code_language TEXT",
    # subject: additive; backfill is deterministic (no LLM in a migration): a code language means Programming
    "ALTER TABLE courses ADD COLUMN subject TEXT; "
    "UPDATE courses SET subject='Programming' WHERE code_language IS NOT NULL",
    # sources: the document text as CodeCoach read it, and each passage's character span inside it.
    # Both nullable: stores made before this keep working (the API rebuilds the text from the passages).
    "ALTER TABLE docs ADD COLUMN text TEXT; "
    "ALTER TABLE passages ADD COLUMN start INTEGER; "
    "ALTER TABLE passages ADD COLUMN end INTEGER",
    # jobs: which process runs it, so a restart can tell a live job from one whose process died
    "ALTER TABLE jobs ADD COLUMN owner TEXT",
    # PDF passages where pdftotext left a gap for an equation stored as a picture
    "ALTER TABLE passages ADD COLUMN formula_missing INTEGER NOT NULL DEFAULT 0",
]


def _ts(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _dt_opt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._conns: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        self._memory_conn: sqlite3.Connection | None = None
        self._migrate()

    # ------------------------------------------------------------ connection plumbing
    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is not None:
            return c
        if self.path == ":memory:":
            raise ValueError("use a file path: Store does not support :memory: across threads")
        c = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None,
                            check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        self._local.conn = c
        with self._lock:
            self._conns.append(c)
        return c

    def close(self) -> None:
        with self._lock:
            for c in self._conns:
                try:
                    c.close()
                except sqlite3.Error:
                    pass
            self._conns.clear()
        self._local = threading.local()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        c = self._conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
        except BaseException:
            c.execute("ROLLBACK")
            raise
        else:
            c.execute("COMMIT")

    def _migrate(self) -> None:
        c = self._conn()
        if c.execute("PRAGMA user_version").fetchone()[0] >= len(MIGRATIONS):
            return
        c.execute("BEGIN IMMEDIATE")
        try:
            v = c.execute("PRAGMA user_version").fetchone()[0]   # re-read under the write lock
            for i in range(v, len(MIGRATIONS)):
                for stmt in _split_sql(MIGRATIONS[i]):
                    c.execute(stmt)
                c.execute(f"PRAGMA user_version={i + 1}")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        c.execute("COMMIT")

    def schema_version(self) -> int:
        return self._conn().execute("PRAGMA user_version").fetchone()[0]

    # ------------------------------------------------------------ courses / docs / passages
    def put_course(self, course: Course) -> Course:
        with self._tx() as c:
            c.execute("INSERT INTO courses(id,title,mode,mode_reason,created_at,code_language,subject) "
                      "VALUES(?,?,?,?,?,?,?) "
                      "ON CONFLICT(id) DO UPDATE SET title=excluded.title, mode=excluded.mode, "
                      "mode_reason=excluded.mode_reason, code_language=excluded.code_language, subject=excluded.subject",
                      (course.id, course.title, course.mode, course.mode_reason, _ts(course.created_at),
                       course.code_language, course.subject))
        return course

    def get_course(self, course_id: str) -> Course | None:
        r = self._conn().execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()
        return _course(r) if r else None

    def list_courses(self) -> list[Course]:
        rows = self._conn().execute("SELECT * FROM courses ORDER BY created_at, id").fetchall()
        return [_course(r) for r in rows]

    def set_course_mode(self, course_id: str, mode: str, reason: str = "") -> None:
        with self._tx() as c:
            c.execute("UPDATE courses SET mode=?, mode_reason=? WHERE id=?", (mode, reason, course_id))

    def set_course_subject(self, course_id: str, subject: str | None) -> str | None:
        """Trim, cap at 40 chars, store None when empty. Returns what was stored."""
        value = " ".join(subject.split())[:40].strip() if isinstance(subject, str) else ""
        value = value or None
        with self._tx() as c:
            c.execute("UPDATE courses SET subject=? WHERE id=?", (value, course_id))
        return value

    def put_doc(self, doc: Doc, text: str | None = None) -> Doc:
        """Upsert. `text` (the document as read) is stored when given and left alone when None."""
        with self._tx() as c:
            c.execute("INSERT INTO docs(id,course_id,name,kind,text) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE "
                      "SET course_id=excluded.course_id, name=excluded.name, kind=excluded.kind, "
                      "text=COALESCE(excluded.text, docs.text)",
                      (doc.id, doc.course_id, doc.name, doc.kind, text))
        return doc

    def get_doc(self, doc_id: str) -> Doc | None:
        r = self._conn().execute("SELECT id,course_id,name,kind FROM docs WHERE id=?", (doc_id,)).fetchone()
        return Doc(**dict(r)) if r else None

    def list_docs(self, course_id: str) -> list[Doc]:
        rows = self._conn().execute("SELECT id,course_id,name,kind FROM docs WHERE course_id=? ORDER BY id",
                                    (course_id,)).fetchall()
        return [Doc(**dict(r)) for r in rows]

    def get_doc_text(self, doc_id: str) -> str | None:
        r = self._conn().execute("SELECT text FROM docs WHERE id=?", (doc_id,)).fetchone()
        return r["text"] if r else None

    def doc_text_length(self, doc_id: str) -> int | None:
        r = self._conn().execute("SELECT LENGTH(text) n FROM docs WHERE id=?", (doc_id,)).fetchone()
        return r["n"] if r else None

    def set_doc_text(self, doc_id: str, text: str, spans: dict[str, tuple[int, int]]) -> None:
        """Save the document text and every passage span in ONE transaction (the backfill)."""
        with self._tx() as c:
            c.execute("UPDATE docs SET text=? WHERE id=?", (text, doc_id))
            c.executemany("UPDATE passages SET start=?, end=? WHERE id=? AND doc_id=?",
                          [(a, b, pid, doc_id) for pid, (a, b) in spans.items()])

    def put_passages(self, passages: list[Passage]) -> int:
        """Upsert by id; document order is the first-insert order."""
        with self._tx() as c:
            c.executemany(
                "INSERT INTO passages(id,doc_id,location,text,start,end,formula_missing) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE "
                "SET location=excluded.location, text=excluded.text, formula_missing=excluded.formula_missing, "
                "start=COALESCE(excluded.start, passages.start), end=COALESCE(excluded.end, passages.end)",
                [(p.id, p.doc_id, p.location, p.text, p.start, p.end, int(p.formula_missing)) for p in passages])
        return len(passages)

    def get_passage(self, passage_id: str) -> Passage | None:
        r = self._conn().execute("SELECT id,doc_id,location,text,start,end,formula_missing FROM passages WHERE id=?",
                                 (passage_id,)).fetchone()
        return Passage(**dict(r)) if r else None

    def list_passages(self, *, doc_id: str | None = None, course_id: str | None = None) -> list[Passage]:
        sql, args = "SELECT p.id,p.doc_id,p.location,p.text,p.start,p.end,p.formula_missing FROM passages p", []
        where = []
        if course_id is not None:
            sql += " JOIN docs d ON d.id=p.doc_id"
            where.append("d.course_id=?")
            args.append(course_id)
        if doc_id is not None:
            where.append("p.doc_id=?")
            args.append(doc_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        rows = self._conn().execute(sql + " ORDER BY p.seq", args).fetchall()
        return [Passage(**dict(r)) for r in rows]

    def adjacent_passage_ids(self, passage_id: str) -> tuple[str | None, str | None]:
        c = self._conn()
        r = c.execute("SELECT seq, doc_id FROM passages WHERE id=?", (passage_id,)).fetchone()
        if not r:
            return None, None
        prev = c.execute("SELECT id FROM passages WHERE doc_id=? AND seq<? ORDER BY seq DESC LIMIT 1",
                         (r["doc_id"], r["seq"])).fetchone()
        nxt = c.execute("SELECT id FROM passages WHERE doc_id=? AND seq>? ORDER BY seq LIMIT 1",
                        (r["doc_id"], r["seq"])).fetchone()
        return (prev["id"] if prev else None), (nxt["id"] if nxt else None)

    # ------------------------------------------------------------ course map / objectives
    def save_map(self, cmap: CourseMap) -> None:
        """Replace the course's map and objective rows (items are kept)."""
        with self._tx() as c:
            c.execute("INSERT INTO course_maps(course_id,json) VALUES(?,?) ON CONFLICT(course_id) "
                      "DO UPDATE SET json=excluded.json", (cmap.course_id, cmap.model_dump_json()))
            c.execute("DELETE FROM objectives WHERE course_id=?", (cmap.course_id,))
            c.executemany(
                "INSERT INTO objectives(id,course_id,unit,lesson,kind,coverage,ord,json) VALUES(?,?,?,?,?,?,?,?)",
                [(o.id, cmap.course_id, o.unit, o.lesson, o.kind, o.coverage, i, o.model_dump_json())
                 for i, o in enumerate(cmap.objectives())])

    def get_map(self, course_id: str) -> CourseMap | None:
        r = self._conn().execute("SELECT json FROM course_maps WHERE course_id=?", (course_id,)).fetchone()
        if not r:
            return None
        cmap = CourseMap.model_validate_json(r["json"])
        # objective coverage may have been updated since save_map; the objectives table wins
        cov = {x["id"]: x["coverage"] for x in self._conn().execute(
            "SELECT id,coverage FROM objectives WHERE course_id=?", (course_id,))}
        for o in cmap.objectives():
            if o.id in cov and o.coverage != cov[o.id]:
                o.coverage = cov[o.id]  # type: ignore[assignment]
        return cmap

    def get_objective(self, objective_id: str) -> Objective | None:
        r = self._conn().execute("SELECT json, coverage FROM objectives WHERE id=?", (objective_id,)).fetchone()
        return _objective(r) if r else None

    def list_objectives(self, course_id: str) -> list[Objective]:
        rows = self._conn().execute("SELECT json, coverage FROM objectives WHERE course_id=? ORDER BY ord",
                                    (course_id,)).fetchall()
        return [_objective(r) for r in rows]

    def set_coverage(self, objective_id: str, coverage: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE objectives SET coverage=? WHERE id=?", (coverage, objective_id))

    def course_of_objective(self, objective_id: str) -> str | None:
        r = self._conn().execute("SELECT course_id FROM objectives WHERE id=?", (objective_id,)).fetchone()
        return r["course_id"] if r else None

    # ------------------------------------------------------------ items
    def add_item(self, item: Item) -> bool:
        """Insert; returns False (and changes nothing) when the id already exists."""
        course_id = self.course_of_objective(item.objective_id)
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO items(id,objective_id,course_id,format,status,origin,checker,created_at,json) "
                "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                (item.id, item.objective_id, course_id, item.format, item.status, item.origin,
                 item.checker, _ts(item.created_at), item.model_dump_json()))
            return cur.rowcount == 1

    def get_item(self, item_id: str) -> Item | None:
        r = self._conn().execute("SELECT json,status FROM items WHERE id=?", (item_id,)).fetchone()
        return _item(r) if r else None

    def list_items(self, *, objective_id: str | None = None, course_id: str | None = None,
                   status: str | None = None, format: str | None = None) -> list[Item]:
        where, args = [], []
        for col, val in (("objective_id", objective_id), ("course_id", course_id),
                         ("status", status), ("format", format)):
            if val is not None:
                where.append(f"{col}=?")
                args.append(val)
        sql = "SELECT json,status FROM items" + (" WHERE " + " AND ".join(where) if where else "")
        rows = self._conn().execute(sql + " ORDER BY created_at, id", args).fetchall()
        return [_item(r) for r in rows]

    def count_items(self, *, course_id: str | None = None, objective_id: str | None = None,
                    status: str | None = None) -> int:
        where, args = [], []
        for col, val in (("objective_id", objective_id), ("course_id", course_id), ("status", status)):
            if val is not None:
                where.append(f"{col}=?")
                args.append(val)
        sql = "SELECT COUNT(*) FROM items" + (" WHERE " + " AND ".join(where) if where else "")
        return self._conn().execute(sql, args).fetchone()[0]

    def set_item_status(self, item_id: str, status: str) -> bool:
        with self._tx() as c:
            return c.execute("UPDATE items SET status=? WHERE id=?", (status, item_id)).rowcount == 1

    def mark_dropped(self, item_id: str) -> bool:
        return self.set_item_status(item_id, "dropped")

    def mark_reported(self, item_id: str, reason: str = "") -> bool:
        """Status -> reported (leaves the bank) and logs the report."""
        with self._tx() as c:
            r = c.execute("SELECT course_id FROM items WHERE id=?", (item_id,)).fetchone()
            if not r:
                return False
            c.execute("UPDATE items SET status='reported' WHERE id=?", (item_id,))
            c.execute("INSERT INTO reports(item_id,course_id,reason,at) VALUES(?,?,?,?)",
                      (item_id, r["course_id"], reason, _ts(utcnow())))
            return True

    def count_reports(self, course_id: str | None = None) -> int:
        if course_id is None:
            return self._conn().execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        return self._conn().execute("SELECT COUNT(*) FROM reports WHERE course_id=?", (course_id,)).fetchone()[0]

    # ------------------------------------------------------------ drops
    def log_drop(self, drop: Drop) -> Drop:
        course_id = drop.course_id or self.course_of_objective(drop.objective_id)
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO drops(course_id,objective_id,format,gate_code,detail,draft_hash,at) VALUES(?,?,?,?,?,?,?)",
                (course_id, drop.objective_id, drop.format, drop.gate_code, drop.detail, drop.draft_hash, _ts(drop.at)))
            return drop.model_copy(update={"id": cur.lastrowid, "course_id": course_id})

    def count_drops_by_gate(self, course_id: str | None = None) -> dict[str, int]:
        if course_id is None:
            rows = self._conn().execute("SELECT gate_code, COUNT(*) n FROM drops GROUP BY gate_code")
        else:
            rows = self._conn().execute(
                "SELECT gate_code, COUNT(*) n FROM drops WHERE course_id=? GROUP BY gate_code", (course_id,))
        return {r["gate_code"]: r["n"] for r in rows}

    def list_drops(self, course_id: str | None = None, *, limit: int = 100) -> list[Drop]:
        sql, args = "SELECT * FROM drops", []
        if course_id is not None:
            sql += " WHERE course_id=?"
            args.append(course_id)
        rows = self._conn().execute(sql + " ORDER BY id DESC LIMIT ?", [*args, limit]).fetchall()
        return [Drop(id=r["id"], course_id=r["course_id"], objective_id=r["objective_id"], format=r["format"],
                     gate_code=r["gate_code"], detail=r["detail"], draft_hash=r["draft_hash"],
                     at=_dt(r["at"])) for r in rows]

    # ------------------------------------------------------------ attempts
    def add_attempt(self, a: Attempt) -> Attempt:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO attempts(item_id,response,correct,score,graded_by,hints_used,at) VALUES(?,?,?,?,?,?,?)",
                (a.item_id, json.dumps(a.response), None if a.correct is None else int(a.correct),
                 a.score, a.graded_by, a.hints_used, _ts(a.at)))
            return a.model_copy(update={"id": cur.lastrowid})

    def list_attempts(self, *, item_id: str | None = None, objective_id: str | None = None,
                      course_id: str | None = None, since: datetime | None = None,
                      newest_first: bool = False, limit: int | None = None) -> list[Attempt]:
        sql = ("SELECT a.* FROM attempts a JOIN items i ON i.id=a.item_id")
        where, args = [], []
        for col, val in (("a.item_id", item_id), ("i.objective_id", objective_id), ("i.course_id", course_id)):
            if val is not None:
                where.append(f"{col}=?")
                args.append(val)
        if since is not None:
            where.append("a.at>=?")
            args.append(_ts(since))
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY a.id " + ("DESC" if newest_first else "ASC")
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        return [_attempt(r) for r in self._conn().execute(sql, args).fetchall()]

    def count_attempts(self, *, course_id: str | None = None, item_id: str | None = None) -> int:
        sql, args = "SELECT COUNT(*) FROM attempts a JOIN items i ON i.id=a.item_id", []
        where = []
        if course_id is not None:
            where.append("i.course_id=?")
            args.append(course_id)
        if item_id is not None:
            where.append("a.item_id=?")
            args.append(item_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        return self._conn().execute(sql, args).fetchone()[0]

    # ------------------------------------------------------------ review state
    def put_review_state(self, rs: ReviewState) -> ReviewState:
        with self._tx() as c:
            c.execute(
                "INSERT INTO review_state(item_id,due,stability,difficulty,reps,lapses,last_review) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET due=excluded.due, "
                "stability=excluded.stability, difficulty=excluded.difficulty, reps=excluded.reps, "
                "lapses=excluded.lapses, last_review=excluded.last_review",
                (rs.item_id, _ts(rs.due), rs.stability, rs.difficulty, rs.reps, rs.lapses, _ts(rs.last_review)))
        return rs

    def get_review_state(self, item_id: str) -> ReviewState | None:
        r = self._conn().execute("SELECT * FROM review_state WHERE item_id=?", (item_id,)).fetchone()
        return _review(r) if r else None

    def list_due(self, *, course_id: str | None = None, now: datetime | None = None,
                 limit: int | None = None) -> list[ReviewState]:
        """Review states due at `now` for ACTIVE items, most overdue first."""
        sql = ("SELECT r.* FROM review_state r JOIN items i ON i.id=r.item_id "
               "WHERE i.status='active' AND r.due<=?")
        args: list[Any] = [_ts(now or utcnow())]
        if course_id is not None:
            sql += " AND i.course_id=?"
            args.append(course_id)
        sql += " ORDER BY r.due, r.item_id"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        return [_review(r) for r in self._conn().execute(sql, args).fetchall()]

    def list_review_states(self, *, course_id: str | None = None) -> list[ReviewState]:
        sql, args = "SELECT r.* FROM review_state r JOIN items i ON i.id=r.item_id", []
        if course_id is not None:
            sql += " WHERE i.course_id=?"
            args.append(course_id)
        return [_review(r) for r in self._conn().execute(sql + " ORDER BY r.item_id", args).fetchall()]

    # ------------------------------------------------------------ jobs
    def create_job(self, kind: str, *, course_id: str | None = None, job_id: str | None = None) -> Job:
        job = Job(id=job_id or new_id("job_"), kind=kind, course_id=course_id)
        with self._tx() as c:
            c.execute("INSERT INTO jobs(id,kind,status,pct,stage,error,course_id,created_at,updated_at,owner) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (job.id, job.kind, job.status, job.pct, job.stage, job.error, job.course_id,
                       _ts(job.created_at), _ts(job.updated_at), process_token()))
        return job

    def reap_orphan_jobs(self) -> list[str]:
        """Mark queued/running jobs whose owning process is gone as errors (a kill or crash mid-job).
        Jobs owned by a live process, including this one, are left alone. Returns the ids changed."""
        reaped: list[str] = []
        now = utcnow()
        rows = self._conn().execute("SELECT id, owner, updated_at FROM jobs WHERE status IN ('queued','running')").fetchall()
        for r in rows:
            if r["owner"]:
                dead = not _process_alive(r["owner"])
            else:
                dead = (now - _dt(r["updated_at"])).total_seconds() > LEGACY_JOB_STALE_S
            if not dead:
                continue
            with self._tx() as c:
                cur = c.execute("UPDATE jobs SET status='error', error=?, updated_at=? "
                                "WHERE id=? AND status IN ('queued','running')",
                                (INTERRUPTED_MESSAGE, _ts(now), r["id"]))
            if cur.rowcount:
                reaped.append(r["id"])
        return reaped

    def update_job(self, job_id: str, *, status: str | None = None, pct: float | None = None,
                   stage: str | None = None, error: str | None = None,
                   course_id: str | None = None) -> Job | None:
        sets: list[str] = ["updated_at=?"]
        args: list[Any] = [_ts(utcnow())]
        for col, val in (("status", status), ("pct", pct), ("stage", stage), ("error", error),
                         ("course_id", course_id)):
            if val is not None:
                sets.append(f"{col}=?")
                args.append(val)
        with self._tx() as c:
            c.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", [*args, job_id])
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> Job | None:
        r = self._conn().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not r:
            return None
        return Job(id=r["id"], kind=r["kind"], status=r["status"], pct=r["pct"], stage=r["stage"],
                   error=r["error"], course_id=r["course_id"], created_at=_dt(r["created_at"]),
                   updated_at=_dt(r["updated_at"]))

    def list_jobs(self, *, status: str | None = None) -> list[Job]:
        sql, args = "SELECT id FROM jobs", []
        if status:
            sql += " WHERE status=?"
            args.append(status)
        ids = [r["id"] for r in self._conn().execute(sql + " ORDER BY created_at, id", args)]
        return [j for j in (self.get_job(i) for i in ids) if j]

    # ------------------------------------------------------------ delete a course
    def course_footprint(self, course_id: str) -> dict[str, int]:
        """What delete_course would remove, as counts (no changes)."""
        c = self._conn()
        n = lambda sql, *a: c.execute(sql, a).fetchone()[0]  # noqa: E731
        in_items = "SELECT id FROM items WHERE course_id=? OR objective_id IN (SELECT id FROM objectives WHERE course_id=?)"
        return {
            "docs": n("SELECT COUNT(*) FROM docs WHERE course_id=?", course_id),
            "passages": n("SELECT COUNT(*) FROM passages WHERE doc_id IN (SELECT id FROM docs WHERE course_id=?)", course_id),
            "objectives": n("SELECT COUNT(*) FROM objectives WHERE course_id=?", course_id),
            "items": n(f"SELECT COUNT(*) FROM ({in_items})", course_id, course_id),
            "attempts": n(f"SELECT COUNT(*) FROM attempts WHERE item_id IN ({in_items})", course_id, course_id),
            "review_states": n(f"SELECT COUNT(*) FROM review_state WHERE item_id IN ({in_items})", course_id, course_id),
            "drops": n("SELECT COUNT(*) FROM drops WHERE course_id=?", course_id),
            "jobs": n("SELECT COUNT(*) FROM jobs WHERE course_id=?", course_id),
        }

    def delete_course(self, course_id: str) -> dict[str, int] | None:
        """Remove a course and everything that hangs off it, in ONE transaction: docs, passages, map and
        objectives, items, attempts, review states, reports, drops, jobs and this course's meta keys.
        Other courses are untouched. Returns the counts removed, or None when there is no such course."""
        if self.get_course(course_id) is None:
            return None
        counts = self.course_footprint(course_id)
        with self._tx() as c:
            item_ids = [r[0] for r in c.execute(
                "SELECT id FROM items WHERE course_id=? OR objective_id IN (SELECT id FROM objectives WHERE course_id=?)",
                (course_id, course_id))]
            for i in range(0, len(item_ids), 500):
                chunk = item_ids[i:i + 500]
                q = ",".join("?" * len(chunk))
                c.execute(f"DELETE FROM attempts WHERE item_id IN ({q})", chunk)
                c.execute(f"DELETE FROM review_state WHERE item_id IN ({q})", chunk)
                c.execute(f"DELETE FROM reports WHERE item_id IN ({q})", chunk)
                c.execute(f"DELETE FROM meta WHERE key IN ({q})", [f"codecoach:hints:{x}" for x in chunk])
            last = c.execute("SELECT value FROM meta WHERE key='codecoach:last_item'").fetchone()
            if last and json.loads(last[0]) in set(item_ids):
                c.execute("DELETE FROM meta WHERE key='codecoach:last_item'")
            act = c.execute("SELECT value FROM meta WHERE key='codecoach:active_course'").fetchone()
            if act and json.loads(act[0]) == course_id:
                c.execute("DELETE FROM meta WHERE key='codecoach:active_course'")
            c.execute("DELETE FROM items WHERE course_id=? OR objective_id IN "
                      "(SELECT id FROM objectives WHERE course_id=?)", (course_id, course_id))
            c.execute("DELETE FROM passages WHERE doc_id IN (SELECT id FROM docs WHERE course_id=?)", (course_id,))
            c.execute("DELETE FROM docs WHERE course_id=?", (course_id,))
            c.execute("DELETE FROM objectives WHERE course_id=?", (course_id,))
            c.execute("DELETE FROM course_maps WHERE course_id=?", (course_id,))
            c.execute("DELETE FROM reports WHERE course_id=?", (course_id,))
            c.execute("DELETE FROM drops WHERE course_id=?", (course_id,))
            c.execute("DELETE FROM jobs WHERE course_id=?", (course_id,))
            # per-course meta: "<namespace>:<course_id>" (import report, placement, lessons seen, prior)
            for (k,) in c.execute("SELECT key FROM meta WHERE key LIKE ?", ("%:" + course_id,)).fetchall():
                if k.rsplit(":", 1)[-1] == course_id and not k.startswith("codecoach:hints:"):
                    c.execute("DELETE FROM meta WHERE key=?", (k,))
            c.execute("DELETE FROM courses WHERE id=?", (course_id,))
        return counts

    # ------------------------------------------------------------ meta (small key/value for lanes)
    def set_meta(self, key: str, value: Any) -> None:
        with self._tx() as c:
            c.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, json.dumps(value)))

    def delete_meta_keys(self, key: str) -> int:
        """Delete meta `key` and every key under it (`key:...`). Returns how many rows went."""
        esc = key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self._tx() as c:
            return c.execute("DELETE FROM meta WHERE key=? OR key LIKE ? ESCAPE '\\'", (key, esc + ":%")).rowcount

    def get_meta(self, key: str, default: Any = None) -> Any:
        r = self._conn().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default


# ---------------------------------------------------------------- row helpers


def _split_sql(script: str) -> list[str]:
    return [s.strip() for s in script.split(";") if s.strip()]


def _course(r: sqlite3.Row) -> Course:
    return Course(id=r["id"], title=r["title"], mode=r["mode"], mode_reason=r["mode_reason"],
                  created_at=_dt(r["created_at"]), code_language=r["code_language"], subject=r["subject"])


def _objective(r: sqlite3.Row) -> Objective:
    o = Objective.model_validate_json(r["json"])
    if o.coverage != r["coverage"]:
        o = o.model_copy(update={"coverage": r["coverage"]})
    return o


def _item(r: sqlite3.Row) -> Item:
    it = Item.model_validate_json(r["json"])
    if it.status != r["status"]:
        it = it.model_copy(update={"status": r["status"]})
    return it


def _attempt(r: sqlite3.Row) -> Attempt:
    return Attempt(id=r["id"], item_id=r["item_id"], response=json.loads(r["response"]) if r["response"] else None,
                   correct=None if r["correct"] is None else bool(r["correct"]), score=r["score"],
                   graded_by=r["graded_by"], hints_used=r["hints_used"], at=_dt(r["at"]))


def _review(r: sqlite3.Row) -> ReviewState:
    return ReviewState(item_id=r["item_id"], due=_dt(r["due"]), stability=r["stability"],
                       difficulty=r["difficulty"], reps=r["reps"], lapses=r["lapses"],
                       last_review=_dt_opt(r["last_review"]))
