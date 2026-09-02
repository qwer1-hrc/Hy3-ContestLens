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
| `stream_incomplete` | 流式连接结束，但缺少结束原因或 `[DONE]` 标记 | 不接受残缺内容，有限重试；检查模型服务或中间网关 |
| `stream_error` | 服务已返回 HTTP 200，但随后发送 SSE 错误帧 | HTTP 200 不代表生成成功；查看脱敏错误帧与请求 ID |
| `refusal` | 上游拒答或内容过滤 | 不自动重试；检查任务内容与服务要求 |

默认采用原生 `json_schema`，求解阶段每次调用最多 3 次尝试。该次数独立于 `[repair]` 代码修复轮数。兼容旧端点时可手动配置 `response_format="json_object"`，但本地校验不会关闭。报告翻译使用独立配置与片段级重试，见下文。

按 `details.diagnostics` 找到本次运行目录下的 `model_calls/*.json`，依次核对：

1. `request`：实际模型、参数、Schema、提示词哈希；不含请求密钥和提示词全文。
2. `response`：HTTP 状态、请求 ID、结束原因、token 用量及脱敏响应。`body_truncated=true` 表示诊断文件截断展示，并非模型本身一定被 token 截断。
3. `failure`、`will_retry`：每次失败的具体类型及是否安排重试。`failure.json` 汇总最终失败和全部可用日志路径。

不要将报错展示中的 `...` 当作模型截断证据，也不要为缺失的 `cpp_source` 或 `steps` 填空值绕过校验。

历史运行没有保存的原始响应无法补回。更新后重启服务，再创建新运行验证；日志包含生成内容，分享前请再次检查并脱敏。

### 固定约 31 秒断连、题面分析成功但生成代码失败

2026-08-31 的五次失败记录均包含主模型 `RemoteProtocolError`。多数失败尝试耗时约 31.4 秒，早于本地 480 秒等待上限；题面分析等较短请求可以成功。这些证据符合服务端或中间连接在长时间等待响应时提前断开的情况，但仅凭客户端日志不能确定具体是哪一层关闭连接。最新四次运行的图片理解状态均为 `skipped`，没有调用 Kimi；更早的 14:26 运行在图片功能接入前也存在同一错误。

主模型现在默认使用 `stream=true` 接收 SSE，生成期间持续读取数据，最终仍组装并严格校验完整 JSON。`reasoning_effort`、`max_tokens`、模型和密钥不会因此改变。不向后续节点传递未完成输出，也不记录推理增量正文。流式请求仍可能因上游故障失败，并非保证成功；修改后需重启服务。

若自定义服务不支持 SSE，可在 `[hy3]` 中显式设置 `stream=false`，或在没有文件覆盖时使用 `HY3_STREAM=false`。对于忽略流式参数并返回普通 JSON 的服务，客户端仍能解析并校验。报告翻译保持原有小批次非流式方式，图片模型配置不受影响。流式协议参考 [腾讯 TokenHub 接口文档](https://cloud.tencent.com/document/product/1823/135872)。

新的模型日志包含实际 `request.stream`，以及流式响应的首事件时间、事件数量、是否收到结束标记。报告列表会显示可安全公开的失败阶段/原因，并提供运行详情入口；旧失败运行不会自动变成成功。

用户授权后的真实复测已完成：与最近失败的货币系统调用相比，提示词哈希及模型参数完全一致，流式请求经过原代理链路在 54 秒后完整返回并通过结构校验。详细证据、7060 token 用量及判断边界见 [2026-08-31 连接诊断记录](HY3_CONNECTION_DIAGNOSIS_20260831.md)。

## 题面已读出，但程序只回显输入或生成占位代码

先对照运行目录中的 `problem_document.json` 和 `problem_spec.json`。2026-08-31 的填数游戏运行 `run_7mzIXPTwnDCZFIun` 已读到 PDF 第 4–6 页，包含路径约束、三个公开样例和规模表；但分析模型返回空摘要和空列表，旧版类型校验仍判为成功。求解、审查和修复又只收到空规格，于是产生回显/求和占位程序，最终 0/20、停止原因 `STALLED`。`COMPLETED` 表示工作流结束，不等于程序获得 AC。

现在分析 Schema 要求摘要、输入、输出、约束和来源引用非空，也拒绝空白字符串。空分析会触发有限重试；耗尽后停在分析阶段，不再浪费求解/修复调用。工作流另有独立校验，拒绝自定义客户端返回的空分析。原题面（包括成功的图片理解补充）保存在 `problem_spec.source_document` 并完整传给求解、两路审查和所有修复轮次；模型摘要不再是它们唯一的题意来源。

新增 `problem_analysis.json` 保存分析节点的输出。非空校验不能保证分析在语义上完全正确，PDF 上标、表格和图片仍可能产生歧义，应结合原 PDF 与可选图片理解核对。历史运行不会被改写；重启服务并新建评测后才会使用修复后的链路。

## 格式化失败

查看 `runs/<run_id>/report_translation/zh-CN.json` 的 `error_code`、`failed_segments` 和 `translation_profile`，再查看同目录 `model_calls/*.json`。报告翻译只读取原始结果，不会改变解题状态。

- `RemoteProtocolError` 且在固定约 30 秒失败：模型服务或中间网关可能提前断开连接，不能仅靠扩大本地超时解决。翻译已改用独立 `low` 推理、4096 token 上限和 2 段/2000 字符的小批次；若仍失败，需要检查上游连接或网关限制。
- `REPORT_TRANSLATION_INCOMPLETE`：模型漏掉、重复返回编号或没有输出中文。短编号映射后有效片段会先保存，自动补试只包含未完成片段。原始哈希无需由模型抄写。
- 旧版本若反复卡在 `deterministic_judge_verdict=WA` 等机器标记：这些完整系统标记现已改为本地中文映射，不再请求模型。重启后点击“重试格式化”即可跳过该卡点并复用已成功的译文，无需清空缓存。
- 报告专用 `max_attempts` 表示每批未完成片段的总尝试上限。底层 HTTP 客户端的 `max_attempts=1` 是有意设计，避免已经成功的片段因嵌套重试再次发送。
- 格式化默认以 `max_concurrency=2` 并发执行批次，429 会触发所有格式化请求共享的退避窗口。若上游仍频繁限流，可在 `[report_translation]` 中改为 `max_concurrency=1`；不必清空缓存。批次失败后会先保存其他已发出的有效结果，未启动的批次留待手动续传。
- 重启服务后在报告页点击“重试格式化”。不要删除已经完成或部分完成的缓存；本次更新保持旧缓存兼容。
