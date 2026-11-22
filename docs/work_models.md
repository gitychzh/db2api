# 工作模型市场：注册表与 id 透传

豆包「工作」页签的模型市场里，每个模型在服务端用 `model_item_key` 标识（形如
`person-deepseek-v4-1-flash-261017`）。网关不认识新模型时有两种接法。

## 1. 直接透传（零配置）

`model` 字段原样当作 `model_item_key` 用：

```bash
curl http://127.0.0.1:9090/v1/chat/completions \
  -H "Authorization: Bearer $DOUBAO_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"person-deepseek-v4-1-flash-261017",
       "messages":[{"role":"user","content":"1+1=?"}]}'
```

- `person-xxx`：原样使用
- `work:xxx`：剥掉 `work:` 前缀后使用
- 其它任意 id：同样原样使用（`DOUBAO_WORK_PASSTHROUGH=0` 可关闭透传，恢复严格模式）

透传的请求继承 `WORK_MODEL_DEFAULTS`：`variant=mid3`、`reasoning_effort=5`、
`mode_id=3`、`need_deep_think=3`、`chunk_delta=false`——也就是工具调用与思考模式都可用的组合。

## 2. 注册表（给模型起好记的名字）

把 `work_models.example.json` 复制成 `doubao2api/work_models.json`，按 `别名 -> model_item_key`
填即可。重启网关后，别名会出现在 `GET /v1/models`，opencode 等客户端就能直接选。

```json
{
  "glm-5.3-flash": {"model_item_key": "person-glm-5-3-flash-261017"},
  "qwen4-max":     {"model_item_key": "person-qwen4-max-261017"},
  "kimi-k3":       "person-kimi-k3-261017"
}
```

只写字符串等于只指定 `model_item_key`，其余参数用默认值；写对象则可以逐项覆盖
`reasoning_effort` / `mode_id` / `memory_profile` / `total_window_size` /
`variant` / `need_deep_think` / `chunk_delta`。

另有环境变量 `DOUBAO_EXTRA_WORK_IDS="a,b,c"` 可把额外的 id 追加进 `/v1/models`，不改文件。

## 3. 验证某个 id 是否被服务端真的选中

`POST /debug/raw_chat` 会把服务端回显的 `model_item_key` 抓出来（字段 `model_echo`）：

```bash
curl -X POST http://127.0.0.1:9090/debug/raw_chat \
  -H "Authorization: Bearer $DOUBAO_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"person-glm-5-3-flash-261017","text":"说一句你好"}'
```

- `model_echo` 里出现你传的 key：选中成功
- `model_echo` 为空或出现豆包默认模型：key 不对，服务端回落了默认模型
