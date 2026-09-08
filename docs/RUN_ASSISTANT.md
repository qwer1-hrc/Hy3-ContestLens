# 评测助手：报告问答与失败诊断

运行详情页和报告详情页底部的“评测助手”可解读结果、比较版本、查询评审分歧、分析运行失败。先选择示例问题或输入问题，再点击“发送问题”。回答下方的 E1、E2 等证据可展开查看实际工具结果。会话只保留在当前页面，刷新或点击“清空对话”后清除。

## 实现与范围

助手使用独立的 Chat Completions Function Calling 循环，不复用主模型的 JSON-only 解析器。使用 `tools`、`tool_choice=auto`、`role=tool` 回传，并在同一轮内部保留供应商的 `reasoning_content/reasoning_details`；这些推理字段不返回页面、不写入日志。没有依赖远程 MCP 托管功能。

每个请求只能查询 URL 指定的 run。模型不能通过参数切换到其他 run，不能执行编译、判题、修复、重跑、取消或删除。核心 workflow、Judge、revision、checkpoint 和正式结果不会因为提问而修改。当前环境检查会执行只读 Docker 健康检查。

| 工具 | 返回内容 |
|---|---|
| `get_run_overview` | 当前运行状态、停止原因、初次与最佳版本摘要 |
| `get_evaluations` | 初次和各轮版本评测与质量门禁 |
| `get_test_results` | 指定版本逐测试点指标，不含输入、标准答案、程序输出 |
| `get_reviews` | 指定版本的诊断、Critic 摘要和逐步骤评审 |
| `compare_revisions` | 两版本的判题变化、修复计划与裁决 |
| `get_run_events` | 时间顺序的事件摘要 |
| `get_failure_diagnostics` | 失败代码、checkpoint 阶段、模型调用元数据 |
| `get_environment_status` | 当前模型配置是否完整、数据库、Docker、数据集状态 |

工具参数由 Pydantic 校验并拒绝额外字段。版本支持 `initial`、`best` 或 `r000` 等 ID；列表使用 `offset`/`limit`，默认 10 条，上限 30 条。工具结果限制长度并明确标记截断，助手可继续分页查询。问答使用运行数据快照，环境检查只代表检查时刻，不能证明过去不存在故障。

运行尚无最终报告时，可读取已有 checkpoint 的完成阶段；缺失证据会明确表示不可用。回答区分确定性观测、Critic 观点和推断。测试 AC 不等于形式化正确性证明，COMPLETED 也不一定表示所有问题已解决。

## 配置

默认继承 `[hy3]` 的 API Key、base URL、model，使用独立预算，不继承 Solver 的高输出预算。可在 `configs/app.toml` 添加：

```toml
[assistant]
max_tokens = 4096
timeout_seconds = 120
max_tool_rounds = 4
max_concurrency = 2
```

连接信息可在 `configs/secrets.local.toml` 的 `[assistant]` 中单独设置 `api_key`、`base_url`、`model`，也可使用 `HY3_ASSISTANT_API_KEY`、`HY3_ASSISTANT_BASE_URL`、`HY3_ASSISTANT_MODEL`。其他预算字段也支持同名前缀的环境变量，例如 `HY3_ASSISTANT_MAX_TOOL_ROUNDS`。优先级：配置文件 > 环境变量 > 默认值；连接字段未配置时继承 Hy3。

独立连接可在求解模型不可用时帮助解释已有失败记录。请使用供应商允许用于自定义应用的 API 连接。加载页面不会发起助手请求，点击发送才会使用模型额度。配置修改后重启 Web/API 服务生效。

每题最多 4 轮工具选择，随后用 `tool_choice=none` 请求最终回答；总工具调用上限 12 次，每次模型回复最多 6 个工具。模型超时、接口错误、截断、非法响应不会冒充完整回答，也不自动重试产生额外费用。未配置模型时，返回本地摘要和失败诊断以及明确的不可用提示。

## API

`POST /api/v1/runs/{run_id}/assistant`

```json
{
  "question": "对比初次与最佳版本，哪些测试点改善了？",
  "history": [
    {"role": "user", "content": "这次运行解决了吗？"},
    {"role": "assistant", "content": "已经通过当前测试集。"}
  ]
}
```

历史最多 8 条，仅允许 user/assistant。历史只用于理解追问，不能伪造工具证据。响应含 `question_id`、`answer`、`status`、`evidence` 和成功请求的累计 `usage`。status 为 `answered`、`unavailable` 或 `limited`。并发占满返回 429，总时限返回 504。模型供应商错误通过 `unavailable` 返回已取得证据，不返回供应商原始错误正文。

证据包含工具名、校验后的参数、调用 ID 和查询结果。应用日志记录 question ID、run ID、状态与工具名；问答正文和供应商推理不持久化。结果采用字段投影和凭据/路径脱敏，不直接向模型提供原始请求、原始模型响应、密钥配置、源码、正式输入或标准答案。

聊天框支持常用 Markdown 排版：加粗、斜体、标题、嵌套列表、引用、行内代码、围栏代码块和简单表格。已知的 `[E1]` 等编号显示为可点击证据标记；点击后展开对应证据。代码块中的标记保持原样。渲染器通过 DOM 元素和文本节点构建内容，不解析模型提供的 HTML、不加载图片，也不会将模型链接变成可执行的导航。用户消息、证据 JSON 和回传模型的对话历史保持原始文字。数学公式仍按原文显示，未引入 LaTeX 渲染。

## 验证

`tests/integration/test_assistant.py` 使用模拟供应商验证真实 HTTP 路由和工具循环，包括多轮回传、推理字段仅内部回填、越权/写工具拒绝、分页、报告与失败页入口、脱敏、超时/并发、接口异常和运行状态不变。模拟通过不能替代供应商端点的上线联调。
