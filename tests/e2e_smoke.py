"""db2api 端到端冒烟测试（登录后运行）。

用法:
    .venv\\Scripts\\python.exe tests\\e2e_smoke.py

覆盖: /health 登录态、/v1/models、非流式对话、流式对话、伪 tool calling。
默认模型 doubao-think（可用 --model 覆盖）。不测 deepseek（本网关无此模型）。
"""
import argparse
import json
import os
import sys
import time

import httpx

BASE = os.environ.get("DB2API_BASE", "http://127.0.0.1:9090")


def load_key() -> str:
    key = os.environ.get("DOUBAO_API_KEY", "")
    if key:
        return key
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        for line in open(env_path, encoding="utf-8"):
            if line.startswith("DOUBAO_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="doubao-think")
    args = ap.parse_args()
    key = load_key()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    ok = True

    with httpx.Client(base_url=BASE, headers=headers, timeout=180) as c:
        # 1. health / 登录态
        h = c.get("/health").json()
        print(f"[1] health: logged_in={h.get('logged_in')} status={h.get('status')}")
        if not h.get("logged_in"):
            print("    未登录，先扫码（POST /v1/session/qr-login 或 /admin 页面）")
            return 1

        # 2. models
        models = [m["id"] for m in c.get("/v1/models").json().get("data", [])]
        print(f"[2] models: {models}")

        # 3. 非流式
        t0 = time.time()
        r = c.post("/v1/chat/completions", json={
            "model": args.model,
            "messages": [{"role": "user", "content": "1+1等于几？只回答一个数字。"}],
            "stream": False,
        })
        r.raise_for_status()
        j = r.json()
        msg = j["choices"][0]["message"]
        text = (msg.get("content") or "").strip()
        print(f"[3] sync {args.model}: {text!r} ({time.time()-t0:.1f}s)")
        ok &= "2" in text

        # 4. 流式
        t0 = time.time()
        chunks, first = 0, None
        with c.stream("POST", "/v1/chat/completions", json={
            "model": "doubao",
            "messages": [{"role": "user", "content": "从1数到5，用空格分隔。"}],
            "stream": True,
        }) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                d = json.loads(line[6:])
                delta = d.get("choices", [{}])[0].get("delta", {})
                if delta.get("content"):
                    chunks += 1
                    if first is None:
                        first = time.time() - t0
        print(f"[4] stream doubao: {chunks} chunks, TTFB={first:.2f}s, total={time.time()-t0:.1f}s")
        ok &= chunks > 0

        # 5. 伪 tool calling
        t0 = time.time()
        r = c.post("/v1/chat/completions", json={
            "model": args.model,
            "messages": [{"role": "user", "content": "北京今天天气怎么样？"}],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "查询指定城市的当前天气",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string", "description": "城市名"}},
                        "required": ["city"],
                    },
                },
            }],
            "stream": False,
        })
        r.raise_for_status()
        j = r.json()
        choice = j["choices"][0]
        tc = choice["message"].get("tool_calls")
        print(f"[5] tools {args.model}: finish={choice.get('finish_reason')} "
              f"tool_calls={json.dumps(tc, ensure_ascii=False) if tc else None} ({time.time()-t0:.1f}s)")
        ok &= bool(tc) and tc[0]["function"]["name"] == "get_weather"

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())