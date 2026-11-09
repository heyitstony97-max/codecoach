"""CodeCoachService.delete_course and /codecoach delete <course-id> [confirm]."""

import pytest
from surface_helpers import make_service

from codecoach import tools
from codecoach.service import ServiceError


def test_command_without_confirm_shows_what_would_go_and_deletes_nothing():
    svc, store, cid = make_service()
    n_items = store.count_items(course_id=cid)
    out = tools.make_command(lambda: svc)(f"delete {cid}")
    assert "would delete" in out and "Nothing was deleted" in out and f"/codecoach delete {cid} confirm" in out
    assert store.get_course(cid) is not None and store.count_items(course_id=cid) == n_items


def test_command_with_confirm_deletes():
    svc, store, cid = make_service()
    out = tools.make_command(lambda: svc)(f"delete {cid} confirm")
    assert out.startswith("Deleted") and store.get_course(cid) is None
    assert store.list_objectives(cid) == [] and store.count_items(course_id=cid) == 0


def test_only_the_word_confirm_counts():
    svc, store, cid = make_service()
    for tail in ("yes", "confirmed", "please"):
        tools.make_command(lambda: svc)(f"delete {cid} {tail}")
    assert store.get_course(cid) is not None


def test_command_usage_and_unknown_course():
    svc, store, cid = make_service()
    cmd = tools.make_command(lambda: svc)
    assert "Usage" in cmd("delete")
    assert "No course" in cmd("delete nope confirm")
    assert store.get_course(cid) is not None
    assert "/codecoach delete" in tools.HELP


def test_service_refuses_while_an_import_is_running():
    svc, store, cid = make_service()
    job = store.create_job("import", course_id=cid)
    store.update_job(job.id, status="running")
    with pytest.raises(ServiceError) as e:
        svc.delete_course(cid, confirm=True)
    assert e.value.status == 409 and store.get_course(cid) is not None
