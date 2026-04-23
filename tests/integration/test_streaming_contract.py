from chatdba.dingtalk.channel import StreamUpdateBuffer


def test_streaming_buffer_collects_workflow_and_model_chunks():
    buffer = StreamUpdateBuffer(interval_ms=1000)
    for chunk in ["Parsing SQL\n", "Collecting EXPLAIN\n", "Recommendation: add index"]:
        buffer.add(chunk)

    assert buffer.flush(force=True) == "Parsing SQL\nCollecting EXPLAIN\nRecommendation: add index"
