from agentgate.domain.usage import Usage, cost_amount


def test_from_raw_response_parses_prompt_and_completion_tokens():
    usage = Usage.from_raw_response({"usage": {"prompt_tokens": 812, "completion_tokens": 41}})
    assert usage == Usage(input_tokens=812, output_tokens=41, reasoning_tokens=0)


def test_from_raw_response_parses_reasoning_tokens_when_present():
    raw = {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "completion_tokens_details": {"reasoning_tokens": 3}}}
    usage = Usage.from_raw_response(raw)
    assert usage.reasoning_tokens == 3


def test_from_raw_response_is_none_when_usage_missing():
    assert Usage.from_raw_response({"choices": []}) is None


def test_from_raw_response_is_none_when_usage_is_not_a_dict():
    assert Usage.from_raw_response({"usage": "nope"}) is None


def test_from_raw_response_is_none_when_prompt_tokens_is_not_an_int():
    raw = {"usage": {"prompt_tokens": "812", "completion_tokens": 41}}
    assert Usage.from_raw_response(raw) is None


def test_from_raw_response_is_none_when_completion_tokens_is_not_an_int():
    raw = {"usage": {"prompt_tokens": 812, "completion_tokens": None}}
    assert Usage.from_raw_response(raw) is None


def test_from_raw_response_is_none_when_prompt_tokens_is_a_bool():
    raw = {"usage": {"prompt_tokens": True, "completion_tokens": 41}}
    assert Usage.from_raw_response(raw) is None


def test_from_raw_response_defaults_reasoning_tokens_when_not_an_int():
    raw = {
        "usage": {
            "prompt_tokens": 10, "completion_tokens": 5,
            "completion_tokens_details": {"reasoning_tokens": "three"},
        },
    }
    usage = Usage.from_raw_response(raw)
    assert usage == Usage(input_tokens=10, output_tokens=5, reasoning_tokens=0)


def test_from_raw_response_defaults_reasoning_tokens_when_a_bool():
    raw = {
        "usage": {
            "prompt_tokens": 10, "completion_tokens": 5,
            "completion_tokens_details": {"reasoning_tokens": True},
        },
    }
    usage = Usage.from_raw_response(raw)
    assert usage.reasoning_tokens == 0


def test_cost_amount_is_none_without_prices():
    usage = Usage(input_tokens=1000, output_tokens=1000)
    assert cost_amount(usage, None, None) is None
    assert cost_amount(usage, 0.15, None) is None


def test_cost_amount_computes_per_million_price():
    usage = Usage(input_tokens=812, output_tokens=41)
    amount = cost_amount(usage, 0.15, 0.60)
    assert amount == (812 * 0.15 + 41 * 0.60) / 1_000_000
