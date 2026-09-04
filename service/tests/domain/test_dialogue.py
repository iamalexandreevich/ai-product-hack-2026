import statistics
import time

from agentgate.api.schemas import HISTORY_MAX_BYTES
from agentgate.domain.dialogue import Dialogue, OMITTED_MARKER
from agentgate.profiles.schema import History
from tests.factories import dialogue, turn


def test_empty_dialogue_is_empty_and_has_a_stable_digest():
    assert Dialogue().is_empty and Dialogue.of([]).is_empty
    assert Dialogue().digest() == Dialogue.of([]).digest()
    assert len(Dialogue().digest()) == 64


def test_digest_is_the_same_for_the_same_turns():
    assert dialogue(turn(), turn(role="assistant", author="agent")).digest() == dialogue(
        turn(), turn(role="assistant", author="agent")
    ).digest()


def test_digest_differs_when_any_field_of_any_turn_differs():
    base = dialogue(turn(content="a"), turn(role="toolresult", author="system", content="b", tool="bash", call_id="c1"))
    variants = [
        dialogue(turn(content="A"), base.turns[1]),
        dialogue(base.turns[0], turn(role="toolcall", author="system", content="b", tool="bash", call_id="c1")),
        dialogue(base.turns[0], turn(role="toolresult", author="agent", content="b", tool="bash", call_id="c1")),
        dialogue(base.turns[0], turn(role="toolresult", author="system", content="b", tool="sh", call_id="c1")),
        dialogue(base.turns[0], turn(role="toolresult", author="system", content="b", tool="bash", call_id="c2")),
        dialogue(base.turns[1], base.turns[0]),
    ]
    assert len({base.digest(), *(v.digest() for v in variants)}) == len(variants) + 1


def test_last_human_request_skips_agent_authored_human_turns():
    d = dialogue(
        turn(content="real request"),
        turn(role="human", author="agent", content="subagent instructions"),
        turn(role="assistant", author="agent", content="ok"),
    )
    assert d.last_human_request() == "real request"


def test_last_human_request_is_none_without_a_human_authored_turn():
    assert dialogue(turn(role="human", author="agent", content="x")).last_human_request() is None
    assert Dialogue().last_human_request() is None


def test_digest_of_a_full_size_history_stays_cheap():
    # Runs on every request before stage 1, in addition to its budget; this
    # guards against an accidentally quadratic digest, not a specific budget.
    big = dialogue(*[turn(role="toolresult", author="system", content="x" * 640) for _ in range(200)])
    assert len(big.digest()) == 64
    samples = []
    for _ in range(50):
        t0 = time.perf_counter()
        big.digest()
        samples.append((time.perf_counter() - t0) * 1000)
    assert statistics.median(samples) <= 5.0
    assert 200 * 640 <= HISTORY_MAX_BYTES  # sanity: this really is a wire-legal history


def test_digest_survives_a_lone_surrogate_in_content():
    assert len(dialogue(turn(content="\ud800")).digest()) == 64


def budget(**over) -> History:
    data = {"budget_chars": 100, "per_turn_chars": {"human": 20, "assistant": 20, "toolcall": 20, "toolresult": 20}}
    for key, value in over.items():
        if key == "budget_chars":
            data[key] = value
        else:
            data["per_turn_chars"][key] = value
    return History.model_validate(data)


def test_fit_leaves_a_dialogue_within_budget_untouched():
    d = dialogue(turn(content="short"), turn(role="assistant", author="agent", content="also short"))
    assert d.fit(budget()) == d


def test_fit_caps_a_human_turn_keeping_the_tail():
    d = dialogue(turn(content="0123456789" * 3))
    assert d.fit(budget(human=10)).turns[0].content == "0123456789"
    assert d.fit(budget(human=12)).turns[0].content == "890123456789"


def test_fit_caps_a_tool_result_keeping_head_and_tail_with_a_marker():
    content = "HEAD" + "x" * 100 + "TAIL"
    fitted = dialogue(turn(role="toolresult", author="system", content=content)).fit(budget(toolresult=40))
    got = fitted.turns[0].content
    assert got.startswith("HEAD") and got.endswith("TAIL")
    assert OMITTED_MARKER.split("{n}")[0] in got and "chars omitted" in got
    assert len(got) <= 40


def test_fit_marker_reports_how_many_characters_were_cut():
    content = "z" * 200
    got = dialogue(turn(role="assistant", author="agent", content=content)).fit(budget(assistant=50)).turns[0].content
    marker = got[got.index("…"):got.rindex("…") + 1]
    kept = len(got) - len(marker)
    assert got.count("z") == kept
    assert marker == OMITTED_MARKER.format(n=200 - kept)
    assert len(got) <= 50


def test_fit_cap_smaller_than_the_marker_keeps_only_the_marker():
    got = dialogue(turn(role="toolcall", author="agent", content="z" * 100)).fit(budget(toolcall=5)).turns[0].content
    assert got == OMITTED_MARKER.format(n=100)


def test_fit_drops_the_oldest_turns_first_and_counts_them():
    d = dialogue(*[turn(content=f"turn {i:02d} " + "." * 10) for i in range(10)])  # 18 chars each
    fitted = d.fit(budget(budget_chars=50, human=20))
    assert [t.content[:7] for t in fitted.turns] == ["turn 08", "turn 09"]
    assert fitted.omitted == 8


def test_fit_never_drops_the_newest_turn_even_when_it_alone_exceeds_the_budget():
    d = dialogue(turn(content="old"), turn(role="toolresult", author="system", content="n" * 500))
    fitted = d.fit(budget(budget_chars=10, toolresult=40))
    assert len(fitted.turns) == 1 and fitted.omitted == 1
    assert fitted.turns[0].content.endswith("n") and len(fitted.turns[0].content) <= 40


def test_fit_result_content_never_exceeds_the_budget_when_more_than_one_turn_remains():
    d = dialogue(*[turn(role="toolresult", author="system", content="r" * 300) for _ in range(20)])
    fitted = d.fit(budget(budget_chars=100, toolresult=30))
    assert sum(len(t.content) for t in fitted.turns) <= 100
    assert len(fitted.turns) == 3 and fitted.omitted == 17


def test_fit_of_empty_is_empty():
    assert Dialogue().fit(budget()) == Dialogue()


def test_fit_keeps_role_author_tool_and_call_id():
    original = turn(role="toolresult", author="system", content="x" * 100, tool="bash", call_id="c1")
    fitted = dialogue(original).fit(budget(toolresult=30)).turns[0]
    assert (fitted.role, fitted.author, fitted.tool, fitted.call_id) == (
        original.role, original.author, original.tool, original.call_id,
    )


def test_fit_accumulates_previously_omitted_turns():
    already = Dialogue(turns=(turn(content="a" * 30), turn(content="b" * 30)), omitted=3)
    assert already.fit(budget(budget_chars=30, human=30)).omitted == 4
