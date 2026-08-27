from hy3_contestlens.judge import compare_noip_fulltext, normalize_noip_fulltext


def test_fulltext_normalizes_newlines_and_trailing_spaces():
    equal, diff, expected_hash, actual_hash = compare_noip_fulltext(b"1  \r\n2\r\n", b"1\n2\n")
    assert equal is True
    assert diff is None
    assert expected_hash == actual_hash


def test_fulltext_is_not_a_token_comparator():
    equal, diff, _, _ = compare_noip_fulltext(b"1 2\n", b"1\n2\n")
    assert equal is False
    assert diff["line"] == 1


def test_extra_line_is_wrong_answer():
    equal, diff, _, _ = compare_noip_fulltext(b"ok\n", b"ok\nextra\n")
    assert equal is False
    assert diff["actual_length"] > diff["expected_length"]

