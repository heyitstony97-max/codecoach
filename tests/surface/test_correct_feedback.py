from codecoach.service import correct_feedback


def test_correct_is_said_once():
    assert correct_feedback("Correct") == "Correct."
    assert correct_feedback("Correct (checked numerically)") == "Correct (checked numerically)"
    assert correct_feedback("") == "Correct."
    assert correct_feedback(None) == "Correct."


def test_other_notes_follow_correct():
    assert correct_feedback("The passage says X.") == "Correct. The passage says X."
