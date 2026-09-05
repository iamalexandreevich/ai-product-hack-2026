from agentgate.inspect.segments import Segment, Segments


def test_segments_cover_a_range_only_inside_one_or_adjacent_segments():
    s = Segments(items=(Segment(start=0, end=2, lines=("a", "b", "c")), Segment(start=3, end=4, lines=("d", "e"))))
    assert s.covers(1, 2)
    assert s.covers(2, 3)
    assert not s.covers(4, 5)


def test_empty_segments_cover_nothing():
    assert not Segments().covers(0, 0)


def test_segments_count_chars_as_the_prompt_will_see_them():
    s = Segments(items=(Segment(start=0, end=1, lines=("ab", "c")),))
    assert s.chars == 5  # "ab\n" + "c\n"
