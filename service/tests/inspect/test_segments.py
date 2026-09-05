from agentgate.inspect.detectors import Action, Finding
from agentgate.inspect.segments import Segment, Segments, build
from agentgate.profiles.schema import ModelBudget


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


def _lines(n: int) -> list[str]:
    return [f"line {i}" for i in range(n)]


def _finding(line: int, line_end: int | None = None) -> Finding:
    return Finding(line=line, rule_id="inspect.injection", action=Action.mask, line_end=line_end)


def test_a_window_surrounds_each_finding():
    s = build(_lines(100), [_finding(50)], ModelBudget(window_lines=3))
    assert [(seg.start, seg.end) for seg in s.items] == [(47, 53)]
    assert s.items[0].lines == tuple(f"line {i}" for i in range(47, 54))


def test_windows_are_clamped_to_the_output():
    s = build(_lines(5), [_finding(0), _finding(4)], ModelBudget(window_lines=12))
    assert [(seg.start, seg.end) for seg in s.items] == [(0, 4)]


def test_overlapping_and_adjacent_windows_merge():
    s = build(_lines(100), [_finding(10), _finding(14), _finding(30)], ModelBudget(window_lines=2))
    assert [(seg.start, seg.end) for seg in s.items] == [(8, 16), (28, 32)]


def test_a_range_finding_is_windowed_around_its_whole_range():
    s = build(_lines(100), [_finding(10, 20)], ModelBudget(window_lines=1))
    assert [(seg.start, seg.end) for seg in s.items] == [(9, 21)]


def test_no_findings_means_the_head_of_the_output():
    s = build(_lines(10), [], ModelBudget(max_chars=1000))
    assert [(seg.start, seg.end) for seg in s.items] == [(0, 9)]


def test_segments_are_cut_by_segment_max_lines():
    s = build(_lines(10), [], ModelBudget(segment_max_lines=4))
    assert [(seg.start, seg.end) for seg in s.items] == [(0, 3), (4, 7), (8, 9)]


def test_segments_beyond_max_segments_are_omitted_and_counted():
    s = build(_lines(100), [_finding(10), _finding(50), _finding(90)], ModelBudget(window_lines=1, max_segments=2))
    assert [(seg.start, seg.end) for seg in s.items] == [(9, 11), (49, 51)]
    assert (s.omitted_segments, s.omitted_lines) == (1, 3)


def test_max_chars_truncates_the_last_segment_and_counts_the_rest():
    lines = ["abcdefghij"] * 10  # 11 chars each with the newline
    s = build(lines, [], ModelBudget(max_chars=25))
    assert [(seg.start, seg.end) for seg in s.items] == [(0, 1)]
    assert s.omitted_lines == 8
    assert s.omitted_segments == 0


def test_max_chars_that_fits_no_line_omits_the_whole_segment():
    s = build(["abcdefghij"] * 3, [], ModelBudget(max_chars=5))
    assert s.items == ()
    assert (s.omitted_segments, s.omitted_lines) == (1, 3)


def test_segment_coordinates_are_those_of_output_split():
    output = "a\nb\nc\n"
    lines = output.split("\n")
    s = build(lines, [_finding(1)], ModelBudget(window_lines=0))
    assert s.items[0].lines == (lines[1],)
    assert s.covers(1, 1)
