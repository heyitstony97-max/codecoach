"""Store.delete_meta_keys: removes a key and the keys under it, nothing that merely shares a prefix."""

from proj_helpers import new_store


def test_delete_meta_keys_is_exact_plus_children():
    s = new_store()
    for k in ("project:p_1", "project:p_1:step:0", "project:p_1:step:1", "project:p_10", "project:p_10:step:0",
              "project:p_1x", "other"):
        s.set_meta(k, {"v": k})
    assert s.delete_meta_keys("project:p_1") == 3
    assert [s.get_meta(k) is not None for k in ("project:p_10", "project:p_10:step:0", "project:p_1x", "other")] \
        == [True] * 4
    assert s.get_meta("project:p_1") is None and s.get_meta("project:p_1:step:1") is None
    assert s.delete_meta_keys("project:p_1") == 0


def test_delete_meta_keys_treats_like_wildcards_literally():
    s = new_store()
    s.set_meta("a_b", 1)
    s.set_meta("a_b:c", 2)
    s.set_meta("axb:c", 3)
    s.delete_meta_keys("a_b")
    assert s.get_meta("axb:c") == 3 and s.get_meta("a_b") is None and s.get_meta("a_b:c") is None
