"""tool_calling 纯函数回归测试。

不碰网络与浏览器，直接测提示词层的工具协议解析。
运行：.venv\\Scripts\\python.exe -m pytest tests/test_tool_calling.py -q
（或 python tests/test_tool_calling.py）
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from doubao2api.tool_calling import (  # noqa: E402
    _normalize_arg_keys,
    _normalize_content,
    _scrub_data_uris,
    _strip_lead_noise,
    has_complete_tool_calls,
    is_tool_call_start,
    parse_tool_calls_xml,
)


def _names(text):
    parsed = parse_tool_calls_xml(text) or []
    return [tc["function"]["name"] for tc in parsed]


def test_standard_block():
    text = '<tool_call>\n{"name":"bash","arguments":{"command":"ls"}}\n</tool_call>'
    assert is_tool_call_start(text)
    assert has_complete_tool_calls(text)
    assert _names(text) == ["bash"]


def test_fragment_opening_tag_second_call():
    """流式里第二个工具调用的开标签常被切成 "tool_call>"（丢掉 "<"）。

    回归：这种块必须被识别成工具调用，不能当正文漏给客户端。
    """
    text = (
        'tool_call>\n{"name": "read", "arguments": {"path": "/etc/hostname"}}\n</tool_call>'
    )
    assert is_tool_call_start(text)
    assert has_complete_tool_calls(text)
    assert _names(text) == ["read"]


def test_fragment_and_standard_in_one_buffer():
    text = (
        '<tool_call>{"name":"bash","arguments":{"command":"uname -m"}}</tool_call>\n'
        'tool_call>{"name":"read","arguments":{"path":"/etc/hostname"}}</tool_call>'
    )
    assert _names(text) == ["bash", "read"]


def test_lead_noise_before_json():
    text = '>\n{"name": "bash", "arguments": {"command": "ls"}}'
    assert _strip_lead_noise(text).startswith("{")
    assert _names(text) == ["bash"]


def test_plain_text_is_not_a_tool_call():
    text = "这是一段普通回答，没有工具调用。"
    assert not is_tool_call_start(text)
    assert not has_complete_tool_calls(text)
    assert parse_tool_calls_xml(text) is None


def test_partial_prefix_buffers():
    """只收到半个标签时既不能 flush 也不能解析，必须继续缓冲。"""
    for text in ("tool_call", "<tool_cal", "_call>"):
        assert is_tool_call_start(text)
        assert not has_complete_tool_calls(text)


def test_arg_keys_cleaned():
    assert json.loads(_normalize_arg_keys({"command!": "ls"})) == {"command": "ls"}
    assert json.loads(_normalize_arg_keys('{"path?":"a.txt"}')) == {"path": "a.txt"}


def test_normalize_content_drops_blobs():
    parts = [
        {"type": "text", "text": "hello"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 5000}},
        {"type": "file", "file": {"file_id": "x"}},
    ]
    out = _normalize_content(parts)
    assert "hello" in out
    assert "A" * 100 not in out
    assert "omitted" in out


def test_scrub_data_uris():
    blob = "data:image/png;base64," + "B" * 400
    out = _scrub_data_uris(f"before {blob} after")
    assert "B" * 100 not in out
    assert "before" in out and "after" in out


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failed += 1
                print(f"FAIL {name}: {exc}")
    sys.exit(1 if failed else 0)
