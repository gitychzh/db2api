#!/usr/bin/env python3
"""db2api 边缘转发：把 ECS:3115 的 OpenAI 兼容请求转发到家庭出口的 db2api 网关。

为什么需要这一层
----------------
豆包的风控按出口 IP 的可信度打分。阿里云机房 IP 会直接要求滑轨验证；
境外代理（AWS 东京）能过但会周期性抖动。家庭宽带 IP 长期稳定放行。
所以浏览器（真正的登录态与指纹）只跑在家里那台 Windows 上，
ECS 只做无状态的反向代理——对外提供固定域名/端口，不加任何缓存。

用法
----
    UPSTREAM=http://100.109.109.107:9090 PORT=3115 python3 edge_proxy.py

- 原样透传 method / path / query / body / Authorization 头。
- SSE 流式响应逐块 flush，不做缓冲。
- 上游不可达时返回 502 并给出明确 JSON 错误，便于客户端重试。
"""
from __future__ import annotations

import os
import sys

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

UPSTREAM = os.environ.get("UPSTREAM", "http://100.109.109.107:9090").rstrip("/")
PORT = int(os.environ.get("PORT", "3115"))
HOST = os.environ.get("HOST", "0.0.0.0")
TIMEOUT = float(os.environ.get("UPSTREAM_TIMEOUT", "300"))

# 逐跳头不应转发
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}

app = FastAPI(title="db2api edge proxy", docs_url=None, redoc_url=None)


def _upstream_error(exc: Exception) -> JSONResponse:
    msg = f"upstream unreachable ({UPSTREAM}): {type(exc).__name__}: {exc}"
    return JSONResponse(
        status_code=502,
        content={"error": {"message": msg, "type": "upstream_error", "code": 502}},
    )


@app.get("/edge/health")
async def edge_health():
    """边缘层自身与上游的健康汇总，便于监控一眼看穿是哪一跳出问题。"""
    out = {"edge": "ok", "upstream": UPSTREAM}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{UPSTREAM}/health")
            out["upstream_status"] = r.status_code
            try:
                out["upstream_health"] = r.json()
            except Exception:
                out["upstream_health"] = r.text[:200]
    except Exception as exc:
        out["upstream"] = f"error: {type(exc).__name__}: {exc}"
        out["upstream_status"] = 502
    return out


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def forward(path: str, request: Request):
    url = f"{UPSTREAM}/{path}"
    if request.url.query:
        url += f"?{request.url.query}"

    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
    body = await request.body()

    client = httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT, connect=15))
    try:
        req = client.build_request(request.method, url, headers=headers, content=body or None)
        resp = await client.send(req, stream=True)
    except Exception as exc:
        await client.aclose()
        return _upstream_error(exc)

    resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in HOP_BY_HOP}
    content_type = resp.headers.get("content-type", "")

    # SSE 必须流式转发，否则会破坏增量输出与客户端的 chunk 超时
    if "text/event-stream" in content_type:

        async def stream():
            try:
                async for chunk in resp.aiter_raw():
                    yield chunk
            finally:
                await resp.aclose()
                await client.aclose()

        return StreamingResponse(
            stream(), status_code=resp.status_code, headers=resp_headers,
            media_type=content_type,
        )

    try:
        payload = await resp.aread()
    finally:
        await resp.aclose()
        await client.aclose()
    return Response(content=payload, status_code=resp.status_code,
                    headers=resp_headers, media_type=content_type or None)


if __name__ == "__main__":
    import uvicorn

    print(f"edge proxy: 0.0.0.0:{PORT} -> {UPSTREAM}", file=sys.stderr)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
