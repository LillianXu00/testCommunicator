from a2a_server.backends.openclaw import extract_output_text


def test_extract_output_text_from_responses_items() -> None:
    payload = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "planner result"}]}]}
    assert extract_output_text(payload) == "planner result"


def test_extract_output_text_prefers_top_level_value() -> None:
    assert extract_output_text({"output_text": "direct"}) == "direct"
