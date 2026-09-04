from agentgate.engine.timings import Latency, Timings


def test_unmeasured_stages_are_none():
    latency = Timings().finish()
    assert latency.stage1_ms is None and latency.stage2_ms is None


def test_total_is_always_measured():
    assert Timings().finish().total_ms >= 0


def test_measured_stage_is_reported():
    timings = Timings()
    with timings.stage(1):
        pass
    latency = timings.finish()
    assert latency.stage1_ms is not None and latency.stage2_ms is None


def test_stage_is_recorded_even_when_the_body_raises():
    timings = Timings()
    try:
        with timings.stage(2):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert timings.finish().stage2_ms is not None


def test_to_schema_maps_onto_the_wire_model():
    schema = Latency(total_ms=7, stage1_ms=1, stage2_ms=5).to_schema()
    assert (schema.stage1, schema.stage2, schema.total) == (1, 5, 7)
