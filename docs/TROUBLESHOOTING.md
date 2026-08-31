# 排障

- `/readyz` 显示 `HY3_NOT_CONFIGURED`：复制 secrets 示例并填写非空 Key、endpoint 和 model，或设置 `HY3_API_KEY/HY3_BASE_URL/HY3_MODEL`。
- `SANDBOX_UNAVAILABLE`：启动 Docker Desktop 的 Linux containers，再执行 `docker compose build`。不要改用宿主 `g++`。
- `PRIVATE_DATASET_NOT_IMPORTED`：执行 `python scripts/import_noip2018.py`，再运行校验脚本。
- `USER_CONFIRMATION_REQUIRED`：在资源页检查候选的 PDF 页段、配对数和风险后手动 binding。
- `RESOURCE_CHANGED`：题面或测试文件在 binding 后变化，重新发现并确认。
- `STALE_REVISION`：patch 的 `base_sha256` 过期，显式读取目标 revision 后重试。

## `HY3_INVALID_RESPONSE`：先区分失败类型

该兼容错误码不等于上游 HTTP 502。检查 `failure.json` 的 `details.failure_kind`、`http_status`、`finish_reason` 和 `attempts`：

| failure_kind | 含义 | 处理方向 |
| --- | --- | --- |
| `schema_validation` | JSON 可解析，但缺必填字段或字段类型错误 | 查看 `validation_errors`，确认原生 Schema 请求与响应；客户端会有限纠错重试 |
| `json_decode` | HTTP 响应正文或模型内容不是完整合法 JSON | 检查脱敏 `response.body`、结束原因和服务端错误 |
| `response_shape` | 缺 choices/message/content，或响应结构异常 | 检查端点协议，不能将这类响应当作有效解法 |
| `output_truncated` | 上游返回 `finish_reason=length` | 查看用量与请求中的 `max_tokens`；客户端请求简洁但完整的重生成，不自动扩大预算 |
| `http_error` | 上游确实返回非成功 HTTP 状态 | 408/429/5xx 有限重试；401/403 检查凭据，400/422 检查服务的参数和 Schema 支持 |
| `transport_error` | 请求超时或连接等传输异常 | 查看 `exception_type`，检查网络和服务；不会只保存空异常字符串 |
| `refusal` | 上游拒答或内容过滤 | 不自动重试；检查任务内容与服务要求 |

默认采用原生 `json_schema`，求解阶段每次调用最多 3 次尝试。该次数独立于 `[repair]` 代码修复轮数。兼容旧端点时可手动配置 `response_format="json_object"`，但本地校验不会关闭。报告翻译使用独立配置与片段级重试，见下文。

按 `details.diagnostics` 找到本次运行目录下的 `model_calls/*.json`，依次核对：

1. `request`：实际模型、参数、Schema、提示词哈希；不含请求密钥和提示词全文。
2. `response`：HTTP 状态、请求 ID、结束原因、token 用量及脱敏响应。`body_truncated=true` 表示诊断文件截断展示，并非模型本身一定被 token 截断。
3. `failure`、`will_retry`：每次失败的具体类型及是否安排重试。`failure.json` 汇总最终失败和全部可用日志路径。

不要将报错展示中的 `...` 当作模型截断证据，也不要为缺失的 `cpp_source` 或 `steps` 填空值绕过校验。

历史运行没有保存的原始响应无法补回。更新后重启服务，再创建新运行验证；日志包含生成内容，分享前请再次检查并脱敏。

## 格式化失败

查看 `runs/<run_id>/report_translation/zh-CN.json` 的 `error_code`、`failed_segments` 和 `translation_profile`，再查看同目录 `model_calls/*.json`。报告翻译只读取原始结果，不会改变解题状态。

- `RemoteProtocolError` 且在固定约 30 秒失败：模型服务或中间网关可能提前断开连接，不能仅靠扩大本地超时解决。翻译已改用独立 `low` 推理、4096 token 上限和 2 段/2000 字符的小批次；若仍失败，需要检查上游连接或网关限制。
- `REPORT_TRANSLATION_INCOMPLETE`：模型漏掉、重复返回编号或没有输出中文。短编号映射后有效片段会先保存，自动补试只包含未完成片段。原始哈希无需由模型抄写。
- 旧版本若反复卡在 `deterministic_judge_verdict=WA` 等机器标记：这些完整系统标记现已改为本地中文映射，不再请求模型。重启后点击“重试格式化”即可跳过该卡点并复用已成功的译文，无需清空缓存。
- 报告专用 `max_attempts` 表示每批未完成片段的总尝试上限。底层 HTTP 客户端的 `max_attempts=1` 是有意设计，避免已经成功的片段因嵌套重试再次发送。
- 格式化默认以 `max_concurrency=2` 并发执行批次，429 会触发所有格式化请求共享的退避窗口。若上游仍频繁限流，可在 `[report_translation]` 中改为 `max_concurrency=1`；不必清空缓存。批次失败后会先保存其他已发出的有效结果，未启动的批次留待手动续传。
- 重启服务后在报告页点击“重试格式化”。不要删除已经完成或部分完成的缓存；本次更新保持旧缓存兼容。
