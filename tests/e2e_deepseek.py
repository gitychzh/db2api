"""DeepSeek V4.1 Flash（豆包工作模型）端到端验证脚本。

验证三件事：
1) 非流式 + tools  → OpenAI 标准 tool_calls
2) 流式 + tools    → 增量 tool_calls
3) 流式无 tools    → reasoning_content（思考模式）与 content 分离

运行：.venv\\Scripts\\python.exe tests\\e2e_deepseek.py
"""
import json
import os
import sys

import httpx

BASE = os.environ.get("DB2API_BASE", "http://127.0.0.1:9090")
MODEL = os.environ.get("DB2API_MODEL", "deepseek-v4.1-flash")


def load_key() -> str:
    """密钥只从环境变量或同目录 .env 读，绝不写死在仓库里。"""
    key = os.environ.get("DOUBAO_API_KEY", "")
    if key:
        return key
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        for line in open(env_path, encoding="utf-8"):
            if line.startswith("DOUBAO_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


HEADERS = {"Authorization": f"Bearer {load_key()}"}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command and return stdout",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "the command to run"},
                    "description": {"type": "string"},
                },
                "required": ["command"],
            },
        },
    }
]


def show(title):
    print(f"\n===== {title} =====")


def test_nonstream_tools():
    show("1. 非流式 + tools")
    body = {
        "model": MODEL,
        "stream": False,
        "messages": [{"role": "user", "content": "列一下当前目录里的文件名"}],
        "tools": TOOLS,
    }
    with httpx.Client(timeout=200) as c:
        r = c.post(f"{BASE}/v1/chat/completions", headers=HEADERS, json=body)
    print("HTTP", r.status_code)
    if r.status_code != 200:
        print(r.text[:500])
        return False
    data = r.json()
    msg = data["choices"][0]["message"]
    fr = data["choices"][0]["finish_reason"]
    print("finish_reason:", fr)
    print("content:", (msg.get("content") or "")[:200])
    print("tool_calls:", json.dumps(msg.get("tool_calls"), ensure_ascii=False)[:400])
    ok = fr == "tool_calls" and msg.get("tool_calls")
    print("RESULT:", "PASS" if ok else "FAIL")
    return bool(ok)


def test_stream_tools():
    show("2. 流式 + tools")
    body = {
        "model": MODEL,
        "stream": True,
        "messages": [{"role": "user", "content": "用 bash 看看当前目录有哪些文件"}],
        "tools": TOOLS,
    }
    calls, content, finish = {}, [], None
    with httpx.Client(timeout=200) as c:
        with c.stream("POST", f"{BASE}/v1/chat/completions", headers=HEADERS, json=body) as r:
            print("HTTP", r.status_code)
            if r.status_code != 200:
                print(r.read().decode("utf-8", "ignore")[:500])
                return False
            for line in r.iter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                ch = (chunk.get("choices") or [{}])[0]
                d = ch.get("delta") or {}
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
                if d.get("content"):
                    content.append(d["content"])
                for tc in d.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", 0), {"name": "", "args": ""})
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["args"] += fn["arguments"]
    print("finish_reason:", finish)
    print("content:", "".join(content)[:200])
    print("tool_calls:", json.dumps(calls, ensure_ascii=False)[:400])
    ok = finish == "tool_calls" and calls and any(v["name"] for v in calls.values())
    print("RESULT:", "PASS" if ok else "FAIL")
    return bool(ok)


def test_stream_thinking():
    show("3. 流式无 tools（思考模式）")
    body = {
        "model": MODEL,
        "stream": True,
        "messages": [{"role": "user", "content": "用一句话解释 TCP 三次握手"}],
        "reasoning_effort": "high",
    }
    reasoning, content, finish = [], [], None
    with httpx.Client(timeout=200) as c:
        with c.stream("POST", f"{BASE}/v1/chat/completions", headers=HEADERS, json=body) as r:
            print("HTTP", r.status_code)
            if r.status_code != 200:
                print(r.read().decode("utf-8", "ignore")[:500])
                return False
            for line in r.iter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                ch = (chunk.get("choices") or [{}])[0]
                d = ch.get("delta") or {}
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
                if d.get("reasoning_content"):
                    reasoning.append(d["reasoning_content"])
                if d.get("content"):
                    content.append(d["content"])
    rtxt = "".join(reasoning)
    ctxt = "".join(content)
    print("reasoning chars:", len(rtxt), "| content chars:", len(ctxt))
    print("reasoning head:", rtxt[:200].replace("\n", " "))
    print("content:", ctxt[:300].replace("\n", " "))
    ok = len(rtxt) > 0 and len(ctxt) > 0
    print("RESULT:", "PASS" if ok else ("NO-REASONING(仅正文)" if ctxt else "FAIL"))
    return bool(ok)


def test_stream_tools_with_thinking():
    show("4. 流式 + tools + 思考（两者必须同时成立）")
    body = {
        "model": MODEL,
        "stream": True,
        "messages": [{"role": "user", "content": "用 bash 看看当前目录里有几个文件，先想清楚再调用"}],
        "tools": TOOLS,
    }
    calls, content, reasoning, finish = {}, [], [], None
    with httpx.Client(timeout=200) as c:
        with c.stream("POST", f"{BASE}/v1/chat/completions", headers=HEADERS, json=body) as r:
            print("HTTP", r.status_code)
            if r.status_code != 200:
                print(r.read().decode("utf-8", "ignore")[:500])
                return False
            for line in r.iter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                ch = (chunk.get("choices") or [{}])[0]
                d = ch.get("delta") or {}
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
                if d.get("reasoning_content"):
                    reasoning.append(d["reasoning_content"])
                if d.get("content"):
                    content.append(d["content"])
                for tc in d.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", 0), {"name": "", "args": ""})
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["args"] += fn["arguments"]
    rtxt = "".join(reasoning)
    print("finish_reason:", finish)
    print("reasoning chars:", len(rtxt), "| head:", rtxt[:160].replace("\n", " "))
    print("content:", "".join(content)[:160])
    print("tool_calls:", json.dumps(calls, ensure_ascii=False)[:300])
    ok = finish == "tool_calls" and calls and len(rtxt) > 0
    print("RESULT:", "PASS" if ok else "FAIL")
    return bool(ok)


if __name__ == "__main__":
    results = {
        "nonstream_tools": test_nonstream_tools(),
        "stream_tools": test_stream_tools(),
        "stream_thinking": test_stream_thinking(),
        "stream_tools_thinking": test_stream_tools_with_thinking(),
    }
    show("SUMMARY")
    for k, v in results.items():
        print(f"{k}: {'PASS' if v else 'FAIL'}")
    sys.exit(0 if all(results.values()) else 1)
