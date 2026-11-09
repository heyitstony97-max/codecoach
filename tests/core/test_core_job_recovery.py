"""B5 item 8: a job whose process died must not stay 'running' forever (it blocked deleting the course)."""

import subprocess
import sys
import textwrap
from datetime import timedelta
from pathlib import Path

import pytest

from codecoach.core.models import utcnow
from codecoach.core.store import Store, process_token

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "importer"))
sys.path.insert(0, str(ROOT / "tests" / "surface"))


def _store(tmp_path):
    return Store(tmp_path / "a.db")


def _set_owner(store, job_id, owner):
    with store._tx() as c:
        c.execute("UPDATE jobs SET owner=? WHERE id=?", (owner, job_id))


def _age(store, job_id, seconds):
    from codecoach.core.store import _ts
    with store._tx() as c:
        c.execute("UPDATE jobs SET updated_at=? WHERE id=?", (_ts(utcnow() - timedelta(seconds=seconds)), job_id))


def test_job_of_a_dead_process_becomes_an_error_with_a_plain_message(tmp_path):
    s = _store(tmp_path)
    j = s.create_job("bank", course_id="c1")
    s.update_job(j.id, status="running", pct=45.2, stage="items 49/73")
    _set_owner(s, j.id, "999999:1")                        # no such process
    assert s.reap_orphan_jobs() == [j.id]
    got = s.get_job(j.id)
    assert got.status == "error" and "restart" in got.error.lower() and got.pct == pytest.approx(45.2)


def test_job_owned_by_a_live_process_is_left_alone(tmp_path):
    s = _store(tmp_path)
    j = s.create_job("import")
    s.update_job(j.id, status="running")
    assert s.get_job(j.id).status == "running"             # create_job stamped this process as the owner
    assert s.reap_orphan_jobs() == []
    assert s.get_job(j.id).status == "running"


def test_pid_reuse_is_not_mistaken_for_the_owner(tmp_path):
    s = _store(tmp_path)
    j = s.create_job("import")
    s.update_job(j.id, status="running")
    pid = process_token().split(":")[0]
    _set_owner(s, j.id, f"{pid}:1")                        # same pid, different start time
    assert s.reap_orphan_jobs() == [j.id]


def test_finished_jobs_are_never_touched(tmp_path):
    s = _store(tmp_path)
    j = s.create_job("bank")
    s.update_job(j.id, status="done", pct=100, stage="done")
    _set_owner(s, j.id, "999999:1")
    assert s.reap_orphan_jobs() == []
    assert s.get_job(j.id).status == "done"


def test_legacy_job_without_owner_is_reaped_only_when_stale(tmp_path):
    s = _store(tmp_path)
    old, fresh = s.create_job("bank"), s.create_job("bank")
    for j in (old, fresh):
        s.update_job(j.id, status="running")
        _set_owner(s, j.id, None)
    _age(s, old.id, 600)
    assert s.reap_orphan_jobs() == [old.id]
    assert s.get_job(fresh.id).status == "running"


def test_the_stuck_bank_job_no_longer_blocks_delete(tmp_path):
    from importer_helpers import HISTORY, scripted
    from codecoach.service import CodeCoachService
    s = _store(tmp_path)
    svc = CodeCoachService(s, scripted(), background=False, auto_bank=False)
    cid = svc.job(svc.start_import(title="Kings", text=HISTORY)).course_id
    j = s.create_job("bank", course_id=cid)
    s.update_job(j.id, status="running", pct=45.0, stage="items 49/73")
    _set_owner(s, j.id, "999999:1")                        # the process that was building it was killed
    out = svc.delete_course(cid, confirm=True)             # used to raise 409 course_stopping / course_busy forever
    assert out["deleted"] is True


def test_a_new_service_marks_interrupted_jobs_at_startup(tmp_path):
    from importer_helpers import scripted
    from codecoach.service import CodeCoachService
    s = _store(tmp_path)
    j = s.create_job("import")
    s.update_job(j.id, status="running", pct=30, stage="mapping")
    _set_owner(s, j.id, "999999:1")
    CodeCoachService(s, scripted(), background=False, auto_bank=False)
    assert s.get_job(j.id).status == "error"


def test_a_really_killed_process_is_detected(tmp_path):
    """Kill -9 a child that holds a running job, then open the store again."""
    script = tmp_path / "hold.py"
    script.write_text(textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(ROOT)!r})
        from codecoach.core.store import Store
        s = Store({str(tmp_path / 'k.db')!r})
        j = s.create_job("bank", course_id="c1")
        s.update_job(j.id, status="running", pct=10, stage="items 5/73")
        print(j.id, flush=True)
        time.sleep(600)
    """))
    p = subprocess.Popen([sys.executable, "-I", str(script)], stdout=subprocess.PIPE, text=True)
    try:
        job_id = p.stdout.readline().strip()
        s = Store(tmp_path / "k.db")
        assert s.reap_orphan_jobs() == []                  # the child is alive: hands off
        assert s.get_job(job_id).status == "running"
    finally:
        p.kill()
        p.wait()
    assert s.reap_orphan_jobs() == [job_id]
    assert s.get_job(job_id).status == "error"
