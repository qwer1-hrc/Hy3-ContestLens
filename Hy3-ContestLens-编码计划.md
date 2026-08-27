# Hy3-ContestLens 整体编码计划

> 文档状态：编码前方案（尚未开始生成应用）  
> 编写日期：2026-08-23  
> 计划项目目录：`G:\hy3\Hy3-ContestLens`  
> 当前数据来源：仅 `Real_Obj/NOI-NOIP_data/2018`  
> 项目属性：个人/犀牛鸟活动作品，非腾讯官方发布

## 1. 文档目的与需求优先级

本文档用于冻结 Hy3 算法竞赛 AI 应用的整体编码方案，后续开发、测试、评测报告和演示均以本计划为基线。

需求优先级如下：

1. 用户在对话中提出的明确要求；
2. 《犀牛鸟开源-实战任务-混元大语言模型项目》PDF 中实战任务二的交付要求；
3. NOIP2018 题面中的输入输出、编译、比较、时间和内存要求；
4. 本文档补充的工程实现细节。

本轮只生成本编码计划，不创建 `Hy3-ContestLens` 项目目录，不生成应用代码，也不修改现有 `hy3_doc_mcp` 文档分析项目。

## 2. 本次冻结的关键决策

### 2.1 数据集范围

- 当前唯一题目数据集为 NOIP2018 提高组 day1、day2；
- 不额外引入其他年份 NOIP、洛谷题目或自建算法题；
- 当前共 6 道题、120 个测试点；
- 原始标准答案为 `.ans`，导入后的私有判题数据统一映射为 `.out`；
- 原始数据保持只读，导入过程记录 SHA-256，不在原目录中改名或覆盖文件；
- 后续如要增加数据集，必须先更新本计划和数据集 manifest，不能静默混入现有评测结果。

### 2.2 难度体系

题目难度严格使用用户图片所示的洛谷七档难度：

1. `入门`
2. `普及-`
3. `普及/提高-`
4. `普及+/提高`
5. `提高+/省选-`
6. `省选/NOI-`
7. `NOI/NOI+/CTSC`

系统不得根据题目名称、通过率、测试点规模或模型表现擅自推断洛谷难度。用户后续提供每道题的对应难度后，再写入正式 manifest。

当前占位表：

| 题目 ID | 中文名称 | 测试点数 | 洛谷难度 |
|---|---|---:|---|
| `road` | 铺设道路 | 10 | 待用户提供 |
| `money` | 货币系统 | 20 | 待用户提供 |
| `track` | 赛道修建 | 20 | 待用户提供 |
| `travel` | 旅行 | 25 | 待用户提供 |
| `game` | 填数游戏 | 20 | 待用户提供 |
| `defense` | 保卫王国 | 25 | 待用户提供 |

测试点的输入规模、特殊图结构和原题子任务可作为 `subtask_profile` 描述字段，但不得当作洛谷难度，也不得出现在“按难度分析”的横轴中。

### 2.3 `check_answer` 内存规则

`check_answer` 必须根据当前题目的配置确定内存上限，不能把 512MB 写死为所有题目的固定判定值。

内存上限解析规则：

```text
若 problem manifest 中存在合法的 resource_limits.memory_mb：
    使用题目配置值
否则：
    使用 judge.defaults.memory_mb = 512
```

约束：

- `memory_mb` 必须是正整数；
- `0`、负数、字符串或非法单位不能触发默认值，而应报告 `INVALID_PROBLEM_MANIFEST`；
- `null` 或字段缺失才允许回退到默认 512MB；
- 判题报告必须写出最终使用值和来源：`problem_manifest` 或 `default`；
- MLE 判定基于选手程序的峰值内存，而不是简单把监督进程的额外开销算给选手程序；
- 容器可保留少量监督器安全余量，但这部分不能计入选手可用内存，也不能改变 MLE 阈值。

当前 NOIP2018 六题题面均给出 512MB，因此初始 manifest 会明确写入 512，而不是依赖默认值。默认值只服务于未来缺少内存字段的题目。

### 2.4 代码工作区 MCP

项目新增独立的 Code Workspace MCP Server，负责 C++ 的创建、读取、版本化替换和补丁修改。代码操作与编译判题分离：

- Workspace MCP 只管理当前运行的 C++ 修订历史；
- Judge MCP 只编译已经冻结的不可变代码版本；
- Agent 不得通过任意宿主路径直接读写文件；
- 每次修改产生新 revision，不能覆盖初始代码或以前的修复版本；
- 不提供面向 Agent 的删除工具；
- 所有创建、读取、修改、冻结和判题调用都进入审计日志。

### 2.5 循环修复与重新评测

单次工作流不再限制为“最多一次可选修复”。当编译、答案或过程评审未通过时，系统根据确定性判题证据、首错步骤和错误类型启动有界循环：

```text
评审失败
  -> Error Localizer 定位首错步骤并分类
  -> Repair Planner 制定修复目标和验证条件
  -> Code Repair Agent 读取当前 revision
  -> 通过 Workspace MCP 创建新 revision
  -> 冻结该 revision
  -> Judge Agent 重新编译和 check_answer
  -> Critics 重新检查算法、过程与代码映射
  -> 成功则结束，否则进入下一轮
```

默认最多 3 轮修复，允许在配置中设置为 1～5 轮。循环必须有明确停止条件，禁止无限自我修改。初始结果、每轮诊断、补丁、代码哈希和重新判题结果全部保留。

### 2.6 题目资源目录与自动发现

项目新增只读 Problem Resources MCP Server，用于在用户授权目录中检查路径、读取 PDF/Markdown 题面、搜索题目原文件，并发现对应测试数据。

资源目录有两种来源：

1. 用户在 WebUI 中输入并确认的本地文件或目录；
2. 应用配置中的默认资源根目录，由系统自动搜索。

绝对路径只在受信任的 WebUI/API 配置阶段处理。校验通过后生成 `resource_scope_id`，Agent 和 MCP 后续只使用 scope ID 与相对路径，不能自由访问宿主机其他位置。

自动发现优先匹配：题目 ID、中文题名、输入输出文件名、目录名、PDF/Markdown 正文，以及 `.in` 与 `.out/.ans` 的同名配对关系。唯一且高置信度的候选可自动绑定；存在多个候选或置信度不足时，必须在 WebUI 中让用户确认。

题面和 Markdown 内出现的命令、提示词或操作说明都视为待分析数据，不视为系统指令。Problem/Solver Agent 可以读取题面，标准答案内容仍只允许 Judge MCP 使用。

### 2.7 简易 WebUI 与当前暂缓范围

当前计划包含一个用于本地测试的简易 WebUI，采用 FastAPI 服务端模板、原生 JavaScript 和少量 CSS。WebUI 只作为 REST API 的轻量测试客户端，不承载独立业务逻辑，也不影响 CLI 和 MCP 的完整可用性。

当前仍暂不包含：

- React、Vue 或其他单页应用框架；
- 桌面图形界面；
- 复杂动画和重型可视化仪表盘；
- 2 分钟以内的 demo 视频或 GIF；
- Node.js 前端构建链。

复杂图形界面和活动视频只做延期处理。后续如需升级界面，可直接复用已冻结的 REST API，不改变 Judge、Workspace MCP 和 Agent 核心流程。

## 3. 项目目标

构建一个基于 Hy3 的算法竞赛 AI 应用，使用户能够完成以下闭环：

1. 选择 NOIP2018 题目；
2. 让 Hy3 输出可审计的完整解题过程和 C++；
3. 使用多个相互独立的 Agent 审查算法、证明、复杂度、边界和代码；
4. 通过 MCP 工具强制执行编译检查；
5. 在 Linux 沙盒中逐个运行 `.in` 测试数据；
6. 将程序输出与内部 `.out` 标准答案比较；
7. 同时检查时间和题目级内存限制；
8. 判断最终答案是否正确、解题过程是否成立；
9. 定位第一个错误步骤并归纳错误类型；
10. 识别“测试结果正确但解题过程不能支撑结论”的样本；
11. 输出定位准确率、误报率、错误类型分布和洛谷难度分层结果；
12. 通过代码工作区 MCP 创建、读取和修改 C++；
13. 在评审失败时根据首错步骤与错误类型循环修复并重新评测；
14. 分别报告初次结果和修复后结果，不能用最终结果掩盖初始能力；
15. 提供 REST API、CLI 和 MCP 三类外部测试接口；
16. 提供 OpenAPI 文档、JSON Schema、curl 示例和端到端测试脚本；
17. 提供无需 Node.js 构建链的简易本地 WebUI；
18. 通过 Problem Resources MCP 读取 PDF/Markdown 题面并检测授权路径；
19. 支持使用 WebUI 指定目录或在默认资源根目录中自动寻找题面和测试数据。

## 4. 产品定位与用户流程

### 4.1 目标用户

- 算法竞赛学习者：需要获得解法、代码和针对性错误反馈；
- 教练或助教：需要批量检查代码和解题说明；
- 大模型评测人员：需要研究最终正确性与过程正确性的差异；
- 活动评审者：需要快速复现一次完整解题和过程评估。

### 4.2 单次评测流程

```text
选择题目
  -> 使用 WebUI 指定路径，或选择默认资源根自动发现
  -> Resources MCP 校验路径并定位 PDF/Markdown 与测试数据
  -> 冻结题面、测试集和资源限制 binding
  -> Problem Analyst 生成结构化题目规格
  -> Solver 生成解题步骤、证明、复杂度和 C++
  -> Algorithm Critic 与 Code Critic 并行盲审
  -> Judge Agent 强制调用 compile_cpp
  -> 编译成功后强制调用 check_answer
  -> Adjudicator 融合审查意见与沙盒证据
  -> 输出最终正确性、过程正确性、首错步骤和错误类型
  -> 若未满足完成条件，进入 Repair Planner
  -> Code Repair Agent 通过 Workspace MCP 生成新 revision
  -> 重新编译、重新判题、重新过程评审
  -> 成功或达到停止条件后结束
```

初始提交和每轮修复必须分开存档。修复成功不能覆盖第一次评测的失败记录，否则会破坏模型能力分析。批量报告同时展示 `initial_metrics`、`final_metrics` 和 `repair_metrics`。

## 5. 技术架构

### 5.1 技术选型

- 后端：Python 3.11+、FastAPI、Pydantic；
- 简易 WebUI：Jinja2 模板、原生 JavaScript、原生 CSS；
- 题面读取：pypdf/pdfplumber（PDF）、UTF-8 分块读取（Markdown）；
- Hy3 接口：OpenAI 兼容 Chat Completions；
- 多 Agent：显式有向图状态机，不隐藏调度过程；
- MCP：官方 Python SDK；
- 本地 MCP 传输：`stdio`；
- 部署 MCP 传输：Streamable HTTP；
- 数据和运行记录：SQLite + JSON/JSONL/CSV；
- C++ 沙盒：Linux Docker；
- 外部测试接口：REST API、CLI、MCP；
- 异步任务进度：基于游标的事件查询和可选 NDJSON 事件流；
- 测试：pytest、API 合约测试、CLI 集成测试、Docker 集成测试；
- 报告：CSV、JSON 和可打印 HTML。

### 5.2 逻辑结构

```text
External Test Clients
    |-- Browser / Simple WebUI
    |-- curl / HTTP client
    |-- CLI
    |-- MCP Inspector / MCP client
    |
FastAPI REST API / Event Query
    |
Multi-Agent Orchestrator
    |-- Problem Analyst
    |-- Solver
    |-- Algorithm Critic ----|
    |-- Code Critic ---------| 并行、上下文隔离
    |-- Judge Agent ---------|
    |-- Repair Planner
    |-- Code Repair Agent
    |
MCP Clients
    |-- Problem Resources MCP
    |     |-- inspect_path_scope
    |     |-- validate_scoped_path
    |     |-- list_scoped_directory
    |     |-- find_problem_assets
    |     |-- read_problem_document
    |     `-- inspect_test_dataset
    |
    |-- Code Workspace MCP
    |     |-- create_cpp_submission
    |     |-- read_cpp_submission
    |     |-- replace_cpp_submission
    |     |-- apply_cpp_patch
    |     |-- list_cpp_revisions
    |     `-- freeze_cpp_revision
    |
    `-- Judge MCP
          |-- compile_cpp
          |-- check_answer
          |-- run_single_test
          `-- fuzz_small_case
    |
Linux Docker Sandbox
    |
Adjudicator / Error Localizer
    |
Loop Controller --失败且可修复--> Repair Planner
    |
Run Store / Reports / External Responses
```

## 6. 多 Agent 设计

### 6.1 Problem Analyst

职责：

- 通过 Problem Resources MCP 读取已绑定的 PDF/Markdown 题面；
- 当未提供 binding 时触发授权目录内的自动发现；
- 在输出中保留题面 document ID、PDF 页码或 Markdown 行号；
- 提取题意、输入、输出、约束和资源限制；
- 识别图、树、动态规划、组合计数等问题结构；
- 列出边界条件和容易误读的要求；
- 不生成最终代码。

输出：`problem_spec.json`。

### 6.2 Solver

职责：

- 生成面向用户的可审计解题说明；
- 输出算法步骤、步骤依赖、不变量和正确性证明；
- 给出时间、空间复杂度；
- 列出边界情况；
- 生成 C++；
- 在生成代码中加入 `// [STEP Sx]` 映射注释。

应用评估的是 Solver 明确输出的解题说明，不声称能够读取模型内部不可见的私有思维链。

### 6.3 Algorithm Critic

职责：

- 独立重建参考解法；
- 逐步判断推导是否成立；
- 检查不变量、证明、复杂度和约束使用；
- 标注最早出现问题的步骤；
- 不读取 Code Critic 的意见。

### 6.4 Code Critic

职责：

- 检查算法步骤与代码实现是否一致；
- 检查整数溢出、数组边界、递归深度、初始化、状态转移；
- 检查文件 I/O 与标准输入输出兼容性；
- 检查代码声明的复杂度是否真实；
- 将问题定位到具体函数或代码区间；
- 不读取 Algorithm Critic 的意见。

### 6.5 Judge Agent

职责：

- 必须调用 `compile_cpp`；
- 编译成功后必须调用 `check_answer`；
- 使用 run 已冻结的题面和测试数据 binding，不能在修复轮次中切换数据源；
- 不能用自然语言判断替代工具结果；
- 不允许读取标准答案内容；
- 保存完整 MCP 调用记录。

### 6.6 Adjudicator / Error Localizer

职责：

- 融合两名 Critic 与确定性沙盒证据；
- 判断最终答案是否正确；
- 判断过程是否成立；
- 给出首错步骤、代码位置、错误类型、证据和置信度；
- 证据不足时返回 `UNRESOLVED`，不得臆造错误位置。

### 6.7 Repair Planner

职责：

- 读取当前 revision、编译结果、逐测试点摘要和过程诊断；
- 根据首错步骤和错误类型决定修复目标；
- 区分局部代码补丁、算法级重写、解题过程修订或二者同时修改；
- 写出本轮预期改善和必须重新验证的风险点；
- 不直接修改 C++。

Repair Planner 的输出至少包括：

```text
target_revision
root_cause
first_error_step_id
error_type
repair_scope
required_changes[]
regression_risks[]
success_criteria[]
```

### 6.8 Code Repair Agent

职责：

- 使用 `read_cpp_submission` 读取目标 revision；
- 优先使用 `apply_cpp_patch` 完成可解释的局部修复；
- 只有算法根本错误时才调用 `replace_cpp_submission` 完整重写；
- 每次修改必须引用 `base_sha256`，避免在过期版本上修改；
- 修改完成后重新读取新 revision，确认代码哈希和预期变更；
- 不得直接调用宿主文件系统，也不得读取标准答案。

### 6.9 Loop Controller

Loop Controller 是确定性状态机，不由 LLM 自由决定是否无限继续。默认配置：

```yaml
repair:
  enabled: true
  max_rounds: 3
  hard_max_rounds: 5
  stop_after_no_improvement_rounds: 2
  preserve_all_revisions: true
  rerun_all_tests: true
  rerun_process_review: true
```

完成条件：

- 编译成功；
- 所有正式测试点 AC；
- 没有高置信度 `CONTRADICTED` 过程步骤；
- 解法复杂度满足题目限制；
- 代码与解题过程一致。

停止但未完成的条件：

- 达到 `max_rounds`；
- 连续两轮代码哈希、失败测试集合或核心诊断没有实质改善；
- 定位结果为 `UNRESOLVED` 且无法生成可靠修复计划；
- Docker、Hy3 或 MCP 不可用；
- 触发安全策略；
- 修复导致更严重回归且回滚后仍无法继续；
- 用户取消任务或预算耗尽。

循环结束状态使用 `COMPLETED`、`MAX_ROUNDS`、`STALLED`、`UNRESOLVED`、`INFRASTRUCTURE_ERROR`、`SECURITY_STOP` 或 `CANCELLED`。

## 7. 可审计解题过程格式

每次 Solver 输出必须符合固定 Schema：

```text
problem_summary
assumptions[]
steps[]
  - step_id
  - goal
  - statement
  - dependencies[]
  - invariant
  - justification
proof_obligations[]
complexity
  - time
  - space
boundary_cases[]
cpp_source
code_step_map[]
```

过程评估器逐项返回：

- `SUPPORTED`：有充分依据；
- `UNSUPPORTED`：结论可能正确但缺少支撑；
- `CONTRADICTED`：与题意、代码、反例或测试结果矛盾；
- `NOT_ASSESSABLE`：当前证据无法判断。

首错步骤取拓扑顺序中最早的 `CONTRADICTED`，或由确定性证据明确击穿的步骤。`UNSUPPORTED` 单独计入过程完整性，不自动等同于算法错误。

## 8. MCP Servers 设计

### 8.1 Problem Resources MCP Server

Problem Resources MCP 是只读资源发现服务。它负责路径检测、题面读取和测试集结构发现，不负责修改用户文件，也不向 Solver、Critic 或 Repair Agent返回标准答案内容。

#### 8.1.1 路径授权模型

WebUI/API 接收用户给出的绝对路径，后端先在受信任边界内执行：

1. 规范化 Windows/Linux 路径；
2. 要求目标真实存在且可读；
3. 判断文件、目录、符号链接、junction 和 reparse point；
4. 检查是否位于管理员配置的允许根目录，或是否由本地用户显式授予；
5. 记录规范化绝对路径、文件标识、授权来源和 SHA-256/mtime 快照；
6. 生成不可猜测的 `resource_scope_id`；
7. 只把 scope ID、显示名称和公开元数据交给 Agent。

远程部署模式禁止普通 API 调用者任意授权服务器本地绝对路径，只能选择管理员预先配置的资源根。允许本地 WebUI 显式授权的功能必须绑定 `127.0.0.1`，并在配置中单独开启。

#### 8.1.2 `inspect_path_scope`

输入 `resource_scope_id`，返回：

```text
scope_id
display_name
scope_kind: file | directory
exists
readable
allowed
granted_by: configured_root | local_user
allowed_extensions
created_at
snapshot_state
```

响应不向 Agent暴露不必要的宿主绝对路径；完整路径只进入受保护审计日志。

#### 8.1.3 `validate_scoped_path`

在 scope 内验证相对路径：

```json
{
  "resource_scope_id": "...",
  "relative_path": "day1/road/road1.in",
  "expected_kind": "file",
  "expected_extensions": [".in"]
}
```

返回存在性、类型、大小、扩展名、可读性、相对规范路径和文件哈希。工具拒绝绝对路径、盘符切换、UNC 跳转、`..`、符号链接逃逸、junction 逃逸和大小写规范化后越界。

#### 8.1.4 `list_scoped_directory`

列出授权目录中的相对条目，支持受限递归：

- 默认不递归；
- 最大深度由配置限制；
- 最大条目数由配置限制；
- 结果只包含相对路径、类型、大小和允许的扩展名；
- 默认忽略隐藏文件、缓存、版本控制目录和无关二进制；
- 不能跟随指向 scope 外部的链接。

#### 8.1.5 `find_problem_assets`

根据题目元数据自动寻找原题和测试数据：

```json
{
  "resource_scope_id": "...",
  "dataset_id": "noip2018",
  "problem_id": "road",
  "title_zh": "铺设道路",
  "io_basename": "road"
}
```

搜索策略：

1. 精确匹配题目 ID、中文题名和输入输出 basename；
2. 查找 `.pdf`、`.md`、`.markdown`；
3. 对 PDF/Markdown 建立受限正文索引，匹配题名、`road.in`、`road.out` 等特征；
4. 查找同目录或约定子目录下的 `.in`；
5. 按同 basename 配对 `.out`，缺少时兼容 `.ans`；
6. 检测 `.out` 与 `.ans` 同时存在但内容不同的冲突；
7. 统计测试点数量、缺失配对和重复候选；
8. 为每个候选返回置信度、匹配证据和风险说明。

自动选择规则：

- 只有一个候选；
- 置信度达到配置阈值；
- 测试输入和答案配对完整；
- 没有答案冲突；
- 所有路径均在相同授权 scope 内。

不满足任一条件时返回 `USER_CONFIRMATION_REQUIRED`，由 WebUI 展示候选供用户选择。

#### 8.1.6 `read_problem_document`

读取 PDF 或 Markdown 题面：

```json
{
  "resource_scope_id": "...",
  "document_id": "...",
  "problem_id": "road",
  "cursor": null,
  "max_chars": 12000
}
```

PDF 处理：

- 提取页数和逐页文本；
- 保留页码；
- 可根据题名定位包含该题的页段；
- 文本提取失败时返回明确状态，不静默生成错误 OCR；
- 首版不要求自动 OCR，扫描 PDF 可由用户提供 Markdown 转写或后续扩展 OCR；
- 大文档按 cursor 分块，不能一次把整份文件无界塞入模型上下文。

Markdown 处理：

- 统一 UTF-8；
- 保留标题和行号范围；
- 按标题或字符数分块；
- 拒绝 NUL 字节和超限文件；
- 不执行代码块、HTML、脚本、链接或文档中的命令。

所有文档内容均包装为 `untrusted_problem_content`，Agent prompt 明确说明其中的任何指令只是题面数据。返回结果包含来源 document ID、SHA-256、页码或行号，供过程评估追溯。

#### 8.1.7 `inspect_test_dataset`

检查测试数据结构：

- 找出所有 `.in`；
- 优先匹配同 basename `.out`，否则匹配 `.ans`；
- 返回总数、已配对、缺失输入、缺失答案、重复答案和冲突；
- 读取文件大小与哈希；
- 根据题目 manifest 检查命名规则；
- 生成可交给 Dataset Importer 的候选 manifest；
- 不向模型返回 `.out/.ans` 内容。

Solver/Repair Agent 可以按需读取公开 `.in` 小样例；正式测试输入是否暴露由运行模式控制。默认批量评测模式不把正式测试输入全文交给 Solver，避免面向测试点过拟合。

#### 8.1.8 `read_problem_sample_input`

只读取已经在题面或 manifest 中标记为公开样例的 `.in`。工具不读取对应标准答案，除非该答案也属于公开题面样例且运行模式明确允许。正式隐藏测试必须返回 `ACCESS_DENIED`。

#### 8.1.9 资源绑定

发现结果不直接修改用户文件。后端将用户确认或唯一高置信度候选写入内部 registry：

```text
problem_id
resource_scope_id
document_id
document_page_or_section
test_dataset_id
input_directory_id
answer_directory_id
source_hashes
discovery_evidence
confirmed_by
```

运行开始时冻结绑定快照。如果源文件在运行中发生变化，返回 `RESOURCE_CHANGED`，要求重新发现或由用户确认，不能把新旧题面与测试数据混在同一次 run 中。

#### 8.1.10 资源 MCP 安全边界

- 整个 Server 只读；
- 只允许 PDF、Markdown 和经 manifest 认可的测试文件扩展名；
- 默认拒绝符号链接、junction 和 reparse point；
- 限制单文件大小、递归深度、条目数量和单次返回字符数；
- 不读取 `.env`、TOML secrets、Git 凭据、SSH Key、数据库或任意源码目录；
- 绝对路径不进入模型上下文；
- `.out/.ans` 内容只对 Dataset Importer/Judge 的受信任通道开放；
- 所有读取记录 scope、相对路径、哈希、Agent 和时间；
- 文档指令视为不可信数据，不能改变 Agent 权限和系统工作流。

### 8.2 Code Workspace MCP Server

Code Workspace MCP 只操作当前 `run_id` 的代码工作区，不能读取项目源码、测试输入、标准答案、配置文件或其他运行目录。客户端使用 `run_id + submission_id + revision_id`，不传递任意绝对路径。

#### 8.2.1 `create_cpp_submission`

创建初始 C++：

```json
{
  "run_id": "...",
  "problem_id": "road",
  "source_code": "...",
  "created_by": "solver"
}
```

返回：

```json
{
  "submission_id": "...",
  "revision_id": "r000",
  "sha256": "...",
  "size_bytes": 0
}
```

同一 `submission_id` 只能有一个 `r000`。重复创建必须返回冲突，不能覆盖已有初始版本。

#### 8.2.2 `read_cpp_submission`

按 `submission_id + revision_id` 读取指定版本。读取结果包含源码、哈希、父 revision、创建 Agent 和时间。默认不允许读取“最新版本”这种可变指针，Agent 必须显式指定 revision，避免评审期间内容变化。

#### 8.2.3 `replace_cpp_submission`

创建完整替换版本，适用于算法被判定为根本错误、局部补丁无法修复的情况。必须提供：

- `base_revision_id`；
- `base_sha256`；
- 新源码；
- `repair_round`；
- `repair_plan_id`；
- 替换原因。

工具只创建新 revision，不覆盖 base revision。

#### 8.2.4 `apply_cpp_patch`

使用 unified diff 创建局部修改版本。必须满足：

- patch 只能作用于当前 submission 的单个 `.cpp`；
- 必须提供 `base_sha256`；
- patch 应用前后均校验 UTF-8、大小和扩展名；
- patch 不能创建额外文件；
- 上下文不匹配时原子失败，不产生半成品 revision；
- 成功后返回新 revision、源码哈希和规范化 diff。

#### 8.2.5 `list_cpp_revisions`

返回版本链：

```text
r000 初始生成
  -> r001 第 1 轮局部补丁
  -> r002 第 2 轮完整替换
  -> r003 第 3 轮局部补丁
```

每个节点包含父 revision、Agent、错误类型、修复轮次、代码哈希、编译 artifact 和判题摘要。

#### 8.2.6 `get_cpp_metadata`

返回问题 ID、版本、大小、哈希、创建者和冻结状态，不返回源码。用于 UI、审计和 Judge Agent 的前置校验。

#### 8.2.7 `freeze_cpp_revision`

将指定 revision 冻结为不可变 `source_artifact_id`。冻结前再次核对源码哈希；冻结后该 artifact 只能被 Judge MCP 读取。`compile_cpp` 必须接收冻结 artifact，不能直接编译一个仍可能变化的工作区路径。

#### 8.2.8 工作区安全边界

- 只允许 `.cpp`；
- 单文件大小默认不超过 1MB；
- 统一 UTF-8，拒绝 NUL 字节；
- 拒绝绝对路径、`..`、符号链接、junction 和路径穿越；
- 写入采用临时文件、校验、原子提交三阶段；
- revision 只增不改；
- 不提供删除工具；
- 运行结束后的清理由受信任的生命周期管理器执行，不暴露给 Agent；
- 审计日志记录调用 Agent、输入 revision、输出 revision、前后哈希和 diff 摘要；
- Workspace MCP 无权读取 `data/private`、`.out`、`.ans`、API Key 或应用源码。

### 8.3 Judge 工具一：`compile_cpp`

建议输入：

```json
{
  "problem_id": "road",
  "source_artifact_id": "...",
  "source_sha256": "...",
  "compile_profile": "noip2018_cpp"
}
```

执行内容：

1. 解析由 Workspace MCP 冻结的不可变源码 artifact，并复核哈希；
2. 创建无网络编译沙盒；
3. 使用题目配置的编译命令；
4. 初始 NOIP2018 配置使用与题面等价的 `g++ source.cpp -lm -o main`；
5. 另做一次只用于诊断的 warning/static 编译；
6. 记录编译器版本、容器镜像 digest、命令、耗时和二进制哈希；
7. 返回不可伪造的 `compile_artifact_id`。

建议输出：

```json
{
  "verdict": "OK",
  "compile_artifact_id": "...",
  "compiler": "...",
  "duration_ms": 0,
  "diagnostics": [],
  "binary_sha256": "..."
}
```

非零退出、超时、没有产生可执行文件均判为 CE。

Judge MCP 不接受任意源码路径或可变 revision。相同 `source_artifact_id + compile_profile + compiler_image_digest` 可以安全复用编译缓存，缓存命中仍需返回完整环境和哈希信息。

### 8.4 Judge 工具二：`check_answer`

建议输入：

```json
{
  "compile_artifact_id": "...",
  "dataset_id": "noip2018",
  "problem_id": "road",
  "test_ids": null
}
```

`check_answer` 不接受用户或模型传入的标准答案路径。工具根据 `dataset_id + problem_id` 在内部解析测试 manifest，避免路径注入或标准答案泄露。

逐测试点流程：

1. 加载题目级时间、内存、输出和比较规则；
2. 根据第 2.3 节解析 `effective_memory_limit_mb`；
3. 建立全新的运行工作区；
4. 将测试 `.in` 同时接入 stdin 和 `<problem>.in`；
5. 以非 root 用户启动程序；
6. 禁止网络，限制 PID、CPU、内存和输出大小；
7. 优先读取 `<problem>.out`，未生成时读取 stdout；
8. 若文件输出和 stdout 均非空且内容不同，判为 `IO_CONFLICT`；
9. 与内部 `.out` 全文比较；
10. 记录 CPU、墙钟、峰值内存、退出码和首个差异位置；
11. 所有测试点均执行，不因首次失败提前退出；
12. 输出逐点结果和总分。

每个测试点只有同时满足以下条件才通过：

- 编译成功；
- 正常退出；
- 输出全文相同；
- CPU 和墙钟满足题目时限；
- 峰值内存不超过 `effective_memory_limit_mb`；
- 未超出输出限制；
- 未触发沙盒安全规则。

逐测试点输出至少包含：

```json
{
  "test_id": "road1",
  "verdict": "AC",
  "cpu_ms": 0,
  "wall_ms": 0,
  "peak_rss_mb": 0,
  "memory_limit_mb": 512,
  "memory_limit_source": "problem_manifest",
  "expected_sha256": "...",
  "actual_sha256": "...",
  "first_diff": null
}
```

标准 verdict：

- `AC`
- `CE`
- `WA`
- `TLE`
- `MLE`
- `RE`
- `OLE`
- `IO_CONFLICT`
- `SANDBOX_VIOLATION`
- `INVALID_PROBLEM_MANIFEST`
- `SANDBOX_UNAVAILABLE`

### 8.5 输出比较规则

NOIP2018 使用题面规定的全文比较：

- 统一 CRLF/LF；
- 过滤行末空格；
- 过滤文末回车；
- 不进行宽松 token 比较；
- 数字、字符、行序、额外行、缺失行存在差异均判 WA。

工具同时保留规范化前后的哈希，并输出有限长度的首差异摘要，避免把完整标准答案返回给 Agent。

### 8.6 Judge 辅助工具

- `run_single_test`：只运行指定测试点，用于复现错误；
- `fuzz_small_case`：生成小规模数据并使用参考求解器检查；
- `inspect_problem_manifest`：只返回题目公开元数据，不返回标准答案；
- `sandbox_healthcheck`：验证 Docker、编译器和资源限制是否可用。

## 9. C++ 沙盒设计

未知 C++ 只能在 Linux Docker 中运行，绝不直接在 Windows 宿主机执行。

运行约束：

- `--network none`；
- 只读根文件系统；
- 独立 tmpfs 工作区；
- 非 root UID；
- `--cap-drop ALL`；
- `no-new-privileges`；
- Docker 默认 seccomp；
- 禁止挂载 Docker socket；
- 只读挂载当前测试输入；
- 不传入 Hy3 API Key 或宿主敏感环境变量；
- 限制 PID、CPU、内存、文件数量、栈和输出大小；
- 超时后终止整个 PID namespace；
- 每个测试点使用全新工作区，禁止读取上一个测试点输出。

为了避免监督器内存影响 MLE：

- 使用最小化静态运行监督器记录子进程 `wait4/rusage`；
- `peak_rss_mb` 只统计被测程序；
- 容器硬限制可包含固定监督器安全余量；
- MLE 比较仍严格使用题目配置值，安全余量不计入选手额度；
- 报告同时记录题目限额、程序峰值和容器硬限制，保证可审计。

Docker daemon 不可用时必须返回 `SANDBOX_UNAVAILABLE`，不得自动退化成宿主机运行。

## 10. API Key 与模型配置

不在 Python 源码中硬编码 Key。预留以下本地文件：

```text
configs/secrets.local.toml.example   # 提交到仓库，空占位
configs/secrets.local.toml           # 本地使用，加入 .gitignore
```

示例：

```toml
[hy3]
api_key = ""
base_url = "http://127.0.0.1:8000/v1"
model = "hy3"
```

读取优先级严格为：

1. 读取 `configs/secrets.local.toml` 中的非空值；
2. 文件不存在或相应值为空时，读取环境变量；
3. 两者都不存在时给出明确配置错误。

环境变量：

```text
HY3_API_KEY
HY3_BASE_URL
HY3_MODEL
HY3_REASONING_EFFORT
HY3_TEMPERATURE
HY3_TOP_P
HY3_MAX_TOKENS
```

上线时删除 `secrets.local.toml`，代码会自动改用环境变量。日志、异常、运行记录、外部接口和 WebUI 均不得显示完整 Key。

## 11. NOIP2018 数据组织

### 11.1 当前数据清单

| 题目 | `.in` | `.ans` | 题面时限 | 题面内存 |
|---|---:|---:|---:|---:|
| road | 10 | 10 | 1s | 512MB |
| money | 20 | 20 | 1s | 512MB |
| track | 20 | 20 | 1s | 512MB |
| travel | 25 | 25 | 1s | 512MB |
| game | 20 | 20 | 1s | 512MB |
| defense | 25 | 25 | 2s | 512MB |

### 11.2 导入后目录

```text
data/private/noip2018/
  day1/
    road/tests/*.in
    road/expected/*.out
    money/tests/*.in
    money/expected/*.out
    track/tests/*.in
    track/expected/*.out
  day2/
    travel/tests/*.in
    travel/expected/*.out
    game/tests/*.in
    game/expected/*.out
    defense/tests/*.in
    defense/expected/*.out
```

`data/private` 加入 `.gitignore`。公开仓库保留导入脚本、manifest、来源说明、哈希清单和可公开的小型演示数据。未经授权不直接重新分发完整 NOIP 原始测试数据。

### 11.3 Problem manifest 示例

```yaml
schema_version: 1
dataset_id: noip2018
problem_id: road
title_zh: 铺设道路
luogu_difficulty: null
resource_limits:
  time_ms: 1000
  memory_mb: 512
io:
  basename: road
  input_mode: stdin_and_file
  output_mode: stdout_or_file
judge:
  comparator: noip_fulltext
  input_glob: tests/*.in
  expected_glob: expected/*.out
  score_per_test: 10
```

### 11.4 难度 manifest 示例

```yaml
allowed_levels:
  - 入门
  - 普及-
  - 普及/提高-
  - 普及+/提高
  - 提高+/省选-
  - 省选/NOI-
  - NOI/NOI+/CTSC

problems:
  road: null
  money: null
  track: null
  travel: null
  game: null
  defense: null
```

用户给出映射前，UI 显示“待标注”，批量评测允许运行，但难度图表显示“难度数据未完成”，不得擅自填值。最终活动报告生成前，六题难度必须全部通过枚举校验。

### 11.5 资源根配置与自动发现

路径不得硬编码在 Python 源码中。示例配置：

```toml
[resources]
allow_local_webui_grants = true
auto_discover = true
max_depth = 8
max_entries = 20000
max_document_mb = 50
max_return_chars = 12000
auto_bind_confidence = 0.90

[[resources.roots]]
name = "noip2018-local"
path = "<ABSOLUTE_PATH_TO_NOIP2018>"
read_only = true
```

当前本地运行时可将根目录配置为 `G:\hy3\Real_Obj\NOI-NOIP_data\2018`，但提交仓库的配置样例必须保留占位符。若 WebUI 用户另行指定 PDF、Markdown 或目录，则创建新的临时/持久 `resource_scope_id`。

自动发现为六题分别建立绑定。day1/day2 PDF 中包含多道题，因此绑定必须记录 PDF 页段或 Markdown 标题范围，不能把整份 PDF 不加区分地当作单题题面。

### 11.6 资源发现优先级

题目资源选择顺序：

1. 当前 run 在 WebUI/API 中显式指定的已验证文件或目录；
2. 该题已由用户确认的持久 binding；
3. 默认资源根中的唯一高置信度自动发现结果；
4. 多候选时暂停 run 并返回 `USER_CONFIRMATION_REQUIRED`；
5. 未找到时返回 `PROBLEM_ASSET_NOT_FOUND`，不得用模型记忆虚构题面或测试数据。

任何运行都必须在开始时冻结题面文档哈希、测试集哈希清单和 manifest 版本，以保证后续 Agent 循环使用同一份资源。

## 12. 过程错误分类体系

错误分类固定为：

- `STATEMENT_MISREAD`：题意误读；
- `CONSTRAINT_OMISSION`：遗漏约束或条件；
- `WRONG_ALGORITHM`：算法思想错误；
- `PROOF_GAP`：证明、不变量或推导不成立；
- `CIRCULAR_REASONING`：循环论证；
- `HALLUCINATED_CLAIM`：虚构性质、定理或结论；
- `COMPLEXITY_TLE`：时间复杂度不满足题目；
- `COMPLEXITY_MLE`：空间复杂度不满足题目；
- `BOUNDARY_ERROR`：边界条件错误；
- `INTEGER_OVERFLOW`：整数范围错误；
- `STATE_TRANSITION_ERROR`：状态定义或转移错误；
- `IMPLEMENTATION_MISMATCH`：思路与代码不一致；
- `IO_OR_FORMAT_ERROR`：输入输出或格式错误；
- `COMPILE_ERROR`：编译错误；
- `RUNTIME_ERROR`：运行错误或未定义行为；
- `RESULT_CORRECT_PROCESS_INVALID`：结果正确但过程不成立；
- `UNRESOLVED`：证据不足，无法可靠定位。

每个错误记录包含：

```text
error_type
first_error_step_id
code_location
evidence[]
confidence
final_result_correct
process_correct
repair_suggestion
```

### 12.1 诊断到修复的路由规则

Loop Controller 根据 verdict 和过程错误类型选择下一轮 Agent 路径：

| 失败证据 | 主要 Agent 路径 | 默认修改策略 | 重新验证范围 |
|---|---|---|---|
| CE | Code Critic -> Repair Planner -> Code Repair Agent | 根据编译诊断局部补丁 | 重新编译；成功后全量判题与过程评审 |
| WA | Error Localizer -> Algorithm/Code Critic -> Repair Planner | 修复首错步骤对应算法或实现 | 全部正式测试点 + 相关小规模反例 |
| TLE | Algorithm Critic -> Repair Planner | 算法级优化，必要时完整替换 | 全量测试、复杂度复核、性能回归 |
| MLE | Algorithm/Code Critic -> Repair Planner | 数据结构或状态压缩优化 | 全量测试、题目级内存复核 |
| RE | Code Critic -> Repair Planner | 边界、递归、溢出或未定义行为补丁 | 失败点、sanitizer 小样例、全量测试 |
| OLE/IO_CONFLICT | Code Critic -> Repair Planner | 输出或文件 I/O 局部补丁 | 编译后全量测试 |
| 所有测试 AC，但过程有 `CONTRADICTED` | Algorithm Critic -> Repair Planner | 优先修订解题过程；代码与过程不一致时同时修复 C++ | 全量过程评审；代码改变时重新全量判题 |
| `RESULT_CORRECT_PROCESS_INVALID` | Algorithm Critic -> Repair Planner | 修复证明、步骤依赖或代码映射 | 过程评审；代码改变时重新判题 |
| `UNRESOLVED` | Loop Controller | 不盲目修改 | 终止或请求人工介入 |

正式测试标准答案不得作为 Repair Agent 的输入。Repair Agent 只能获得：

- verdict 和失败测试 ID；
- CPU、内存、退出码和有限日志；
- 不包含完整标准答案的首差异摘要；
- Critic 的步骤级诊断；
- 小规模自生成反例的输入与参考输出；
- 当前代码和历史 diff。

### 12.2 单轮修复状态

每轮保存不可变状态：

```text
repair_round_id
parent_revision_id
parent_sha256
diagnosis_snapshot
repair_plan
patch_or_replacement
new_revision_id
new_sha256
compile_result
answer_check_result
algorithm_review
code_review
improvement_summary
loop_decision
```

`improvement_summary` 使用确定性排序评估进展：

1. 安全违规和基础设施错误优先终止；
2. CE 修复为可编译视为改善；
3. AC 测试点数增加视为改善；
4. 相同 AC 数下，严重 verdict 从 RE/TLE/MLE 降为 WA 视为改善；
5. 最终测试结果相同时，过程中的高置信度矛盾减少视为改善；
6. 仅自然语言声称“已改进”不算改善。

如果新 revision 回归，系统保留该 revision 和证据，但下一轮从历史上当前最优 revision 分支，而不是被迫继续修改更差版本。最佳版本选择规则和分支关系必须写入审计记录。

### 12.3 初始能力与修复能力分离

最终报告至少区分：

- `initial_submission_result`：Solver 首次生成结果；
- `repair_round_results[]`：每轮修改和评测；
- `best_submission_result`：循环中确定性指标最优版本；
- `final_submission_result`：循环停止时选定交付版本；
- `repair_success`：失败初始版本是否最终达到完成条件；
- `repair_round_count`：实际修复轮数；
- `regression_count`：出现回归的轮数。

论文或活动报告中的“模型初始准确率”只能使用首次提交；“Agent 系统最终准确率”使用最终版本，二者不能混合统计。

## 13. 有效性验证数据

验证案例全部围绕当前 6 道 NOIP2018 题目构建，不引入其他题目数据集。

目标规模为 72 个过程案例，每题 12 个：

- 2 个完全正确案例；
- 2 个“结果正确但过程不成立”案例；
- 6 个单一首错变异案例；
- 2 个多错误或对抗性案例。

变异方式包括：

- 颠倒不等号；
- 删除必要边界；
- 把 `long long` 改成 `int`；
- 使用错误的复杂度结论；
- 删除树、环或特殊结构处理；
- 修改 DP 状态或转移；
- 错误文件名或输出格式；
- 只硬编码样例或部分测试点；
- 给出正确代码但配套错误证明；
- 给出看似合理但无法支持代码的解法描述。

每个案例由两名标注者独立标注，冲突时进行人工仲裁。划分为：

- 48 个开发/调试案例；
- 24 个锁定验证案例。

锁定验证集不能用于调整提示词、阈值或错误分类规则。

## 14. 指标定义

### 14.1 最终答案指标

- 测试点通过率；
- 题目 AC 率；
- 按原题分值计算的得分；
- CE、WA、TLE、MLE、RE、OLE 分布；
- CPU 时间和峰值内存分布。

### 14.2 过程指标

- 过程正确率；
- 首错步骤精确定位率；
- 首错步骤相邻一步容忍定位率；
- 错误类别 macro-F1；
- `RESULT_CORRECT_PROCESS_INVALID` 召回率；
- 正确过程样本误报率；
- 被标记样本中真实过程问题与误报的比例；
- 同一输出重复三次评估的一致率；
- 人工标注者之间的 Cohen's kappa。

### 14.3 修复循环指标

- 初次提交测试点通过率与题目 AC 率；
- 最终提交测试点通过率与题目 AC 率；
- 修复成功率；
- 平均和最大修复轮数；
- 每类错误的修复成功率；
- 首错步骤被正确修复的比例；
- 修复后新增回归测试点数量；
- `STALLED`、`MAX_ROUNDS`、`UNRESOLVED` 等停止原因分布；
- 局部 patch 与完整 replace 的成功率；
- 每次成功修复的 Hy3 调用次数、token 和墙钟成本。

### 14.4 洛谷难度分层指标

用户提供六题难度后，按七档枚举聚合：

- 最终测试点通过率；
- 题目 AC 率；
- 过程正确率；
- 首错定位率；
- 错误类型分布；
- 平均 CPU 和峰值内存。

某一难度档没有题目时显示“无样本”，不能补零后参与总体平均。由于当前只有六题，报告必须同时显示题目数和测试点数，避免把大量测试点误当作大量独立题目。

## 15. 简易 WebUI 与外部测试接口规划

项目提供一个简易本地 WebUI，同时确保所有主要能力都能通过 REST API、命令行和 MCP 独立测试。四种入口共享相同的业务服务和 Pydantic Schema，WebUI 不得绕过 API 直接修改代码或触发判题。

### 15.1 简易 WebUI

实现方式：

- FastAPI + Jinja2 服务端页面；
- 原生 JavaScript 调用 `/api/v1`；
- 原生 CSS，静态资源随 Python 包一起发布；
- 不使用 React、Vue、npm、pnpm 或独立前端构建步骤；
- 默认只在 `127.0.0.1` 提供；
- 页面关闭不取消后台 run。

最小页面：

```text
GET /                         系统状态与 6 道题入口
GET /ui/resources             路径授权、题面/测试数据发现与候选确认
GET /ui/runs/new              创建 run、选择题目和修复轮数
GET /ui/runs/{run_id}         Agent 状态、编译、逐点判题和循环修复
GET /ui/submissions/{id}      C++ revision 列表、源码和 diff
GET /ui/reports               历史运行与报告下载
GET /ui/annotations/{task_id} 简单人工标注表单
```

`/ui/runs/{run_id}` 是核心测试页，至少展示：

- 当前运行状态与 Agent 节点；
- 题目难度、时间和内存限制；
- 当前 C++ revision 和 SHA-256；
- 编译结果与日志摘要；
- 每个测试点的 verdict、CPU、墙钟和峰值内存；
- 首错步骤、错误类型、证据和修复计划；
- 各轮 revision、patch/replace、回归和停止原因；
- 初始、最佳和最终结果；
- 启动、取消、继续修复、停止修复和下载报告按钮。

`/ui/resources` 至少支持：

- 输入 PDF、Markdown 或目录路径；
- 调用后端路径检测并显示存在性、类型、可读性和授权结果；
- 选择“使用当前路径”或“在默认资源根自动寻找”；
- 显示题面候选、PDF 页段/Markdown 标题、测试点配对数量和置信度；
- 对多候选进行人工确认；
- 显示缺失 `.in`、缺失 `.out/.ans` 和答案冲突；
- 保存题目资源 binding；
- 不在页面上显示标准答案内容。

页面通过事件游标每秒轮询增量状态；首版不实现复杂图表，汇总信息使用表格、状态标签和 `<pre>` diff。WebUI 不显示完整标准答案、API Key、宿主路径或 Docker 内部信息。

### 15.2 REST API 基线

- 基础路径：`/api/v1`；
- 默认只监听 `127.0.0.1`；
- 请求与响应统一使用 UTF-8 JSON；
- 大型 C++ 可使用 `multipart/form-data` 上传，但进入服务层后仍转换为统一 submission Schema；
- 长任务返回 `202 Accepted + run_id`，不保持单个 HTTP 请求直到评测完成；
- 每个错误返回稳定的 `error_code`、可读消息、关联 ID 和可选详情；
- OpenAPI JSON 固定暴露在 `/openapi.json`；
- Swagger/ReDoc 仅作为 FastAPI 自动接口文档，可在生产配置中关闭，不属于项目图形界面；
- 所有有副作用的请求支持 `Idempotency-Key`，防止测试重试时重复创建 run 或 revision；
- 远程部署时支持外部 API token，本地模式可以显式关闭认证。

### 15.3 健康检查与元数据接口

```text
GET /healthz
GET /readyz
GET /api/v1/system/capabilities
GET /api/v1/datasets
GET /api/v1/datasets/noip2018/problems
GET /api/v1/problems/{problem_id}
GET /api/v1/problems/{problem_id}/tests/summary
```

要求：

- `/healthz` 只判断服务进程存活；
- `/readyz` 检查 SQLite、Hy3 配置、三个 MCP Server 和 Docker sandbox；
- readiness 失败时返回分组件状态，不暴露 API Key；
- problem 响应包含洛谷难度、时间、内存及内存来源；
- 测试摘要只返回测试点数量和元数据，不返回 `.in`、`.out` 或 `.ans` 内容。

### 15.4 题目资源与路径发现接口

```text
POST /api/v1/resource-scopes:validate
POST /api/v1/resource-scopes
GET  /api/v1/resource-scopes
GET  /api/v1/resource-scopes/{scope_id}
POST /api/v1/resource-scopes/{scope_id}:discover
GET  /api/v1/resource-scopes/{scope_id}/candidates
POST /api/v1/problems/{problem_id}/resource-binding
GET  /api/v1/problems/{problem_id}/resource-binding
POST /api/v1/problems/{problem_id}/resource-binding:rediscover
```

`resource-scopes:validate` 在本地模式接收用户提供的路径，返回规范化结果但不自动授权。`POST /resource-scopes` 需要显式用户确认或管理员配置，并返回 `resource_scope_id`。远程模式只能选择预配置根目录。

`discover` 接收题目 ID、中文题名和搜索模式：

```json
{
  "problem_id": "road",
  "mode": "auto",
  "preferred_document": null,
  "search_tests": true
}
```

结果返回题面候选、测试集候选、匹配证据、置信度和是否需要用户确认。API 不返回 `.out/.ans` 内容。绑定后，run 创建接口只接收 `resource_binding_id`，不直接接收宿主绝对路径。

### 15.5 Run 与多 Agent 接口

```text
POST /api/v1/runs
GET  /api/v1/runs/{run_id}
POST /api/v1/runs/{run_id}/start
POST /api/v1/runs/{run_id}/cancel
GET  /api/v1/runs/{run_id}/events?after_seq={n}
GET  /api/v1/runs/{run_id}/events/stream
GET  /api/v1/runs/{run_id}/result
GET  /api/v1/runs/{run_id}/report
```

`POST /runs` 接收题目 ID、`resource_binding_id`、模型配置覆盖、最大修复轮数和是否启用循环修复。未提供 binding 时，后端按第 11.6 节自动发现；候选不唯一时进入 `WAITING_FOR_RESOURCE_CONFIRMATION`。服务返回 `run_id` 和初始状态。状态枚举：

```text
CREATED
DISCOVERING_RESOURCES
WAITING_FOR_RESOURCE_CONFIRMATION
ANALYZING
SOLVING
REVIEWING
COMPILING
JUDGING
LOCALIZING
REPAIRING
REJUDGING
COMPLETED
FAILED
CANCELLED
```

事件查询使用单调递增 `seq`。普通自动化脚本可轮询 `events?after_seq=`；需要流式日志时使用 NDJSON 事件流。客户端断开不会取消服务器端 run，取消必须显式调用 cancel。

### 15.6 C++ submission 与 revision 接口

```text
POST /api/v1/runs/{run_id}/submissions
GET  /api/v1/runs/{run_id}/submissions/{submission_id}
GET  /api/v1/runs/{run_id}/submissions/{submission_id}/revisions
GET  /api/v1/runs/{run_id}/submissions/{submission_id}/revisions/{revision_id}
POST /api/v1/runs/{run_id}/submissions/{submission_id}/revisions:replace
POST /api/v1/runs/{run_id}/submissions/{submission_id}/revisions:patch
POST /api/v1/runs/{run_id}/submissions/{submission_id}/revisions/{revision_id}:freeze
```

REST 层必须调用 Code Workspace MCP，不能绕过 MCP 直接写文件。返回内容包含 revision、父版本、SHA-256、创建者和审计 ID。patch/replace 必须接收 `base_sha256`。

### 15.7 编译与答案检查接口

```text
POST /api/v1/judge/compile
GET  /api/v1/judge/compilations/{compile_artifact_id}
POST /api/v1/judge/check-answer
GET  /api/v1/judge/checks/{check_id}
GET  /api/v1/judge/checks/{check_id}/tests
```

约束：

- compile 只接收冻结的 `source_artifact_id`；
- check-answer 只接收 `compile_artifact_id + dataset_id + problem_id`；
- API 不接受标准答案路径；
- 逐测试点响应包含 verdict、CPU、墙钟、峰值内存、内存限额和来源；
- 大结果默认分页；
- 默认不返回完整编译二进制和完整标准输出；
- REST verdict 必须与直接 MCP 调用结果一致。

### 15.8 过程诊断与循环修复接口

```text
GET  /api/v1/runs/{run_id}/diagnosis
GET  /api/v1/runs/{run_id}/repair-rounds
GET  /api/v1/runs/{run_id}/repair-rounds/{round_id}
POST /api/v1/runs/{run_id}/repair
POST /api/v1/runs/{run_id}/repair:continue
POST /api/v1/runs/{run_id}/repair:stop
GET  /api/v1/runs/{run_id}/revisions/{revision_id}/evaluation
```

`POST /repair` 可以由 Loop Controller 自动调用，也可由外部测试者手动触发。响应必须提供 Repair Plan、目标 revision 和异步任务 ID。接口不能跳过重新编译、全量 `check_answer` 和过程复核。

### 15.9 批量评测与报告接口

```text
POST /api/v1/benchmarks
GET  /api/v1/benchmarks/{benchmark_id}
GET  /api/v1/benchmarks/{benchmark_id}/results
GET  /api/v1/benchmarks/{benchmark_id}/artifacts/{artifact_name}
```

支持按 6 道题、指定题目或指定 revision 批量运行。输出 JSON、CSV 和 HTML 报告，包含初始指标、修复后指标、定位准确率、误报率、错误分布和难度分层。

### 15.10 人工标注接口

```text
POST /api/v1/annotations/tasks
GET  /api/v1/annotations/tasks/{task_id}
POST /api/v1/annotations/tasks/{task_id}/labels
POST /api/v1/annotations/tasks/{task_id}/adjudication
GET  /api/v1/annotations/export
```

接口支持盲标、首错步骤、错误类型和备注。系统预测在标注提交前不返回给标注者；仲裁接口必须保留两份原始标注。

### 15.11 CLI 测试接口

提供统一命令 `hy3-contest`：

```text
hy3-contest health
hy3-contest list-problems
hy3-contest show-problem road
hy3-contest resources validate --path <path>
hy3-contest resources grant --path <path>
hy3-contest resources discover --scope <scope_id> --problem road
hy3-contest resources bind --scope <scope_id> --problem road --candidate <id>
hy3-contest solve --problem road --max-repair-rounds 3
hy3-contest submit --problem road --source solution.cpp
hy3-contest compile --submission <id> --revision r000
hy3-contest check --compile-artifact <id> --problem road
hy3-contest repair --run <run_id>
hy3-contest watch --run <run_id>
hy3-contest report --run <run_id> --format json
hy3-contest benchmark --dataset noip2018
```

CLI 默认调用本地 REST API；增加 `--direct-mcp` 后可以绕过 REST 直接测试 MCP 合约，但仍不能绕过 Workspace/Judge 的安全边界。所有命令支持 `--json`，便于脚本解析。

### 15.12 MCP 外部测试接口

三个 MCP Server 均提供：

- 本地 `stdio` 启动配置；
- Streamable HTTP 部署配置；
- MCP Inspector 测试说明；
- `tools/list` 快照测试；
- 每个 tool 的合法、非法和边界请求示例；
- 结构化输出 Schema；
- 超时、取消和错误码说明。

### 15.13 WebUI 与外部接口一致性要求

- WebUI、REST、CLI、MCP 调用相同服务核心；
- 相同源码、题目和配置必须产生相同 verdict；
- 每个响应包含 `request_id` 或 `run_id`，便于定位日志；
- API Schema 变更需要更新 `schema_version` 和契约测试；
- 不兼容变更只能进入新的 `/api/v2`；
- 响应不得泄露标准答案、API Key、宿主路径和 Docker 内部敏感信息；
- 外部接口应能覆盖项目所有核心能力，自动化测试不依赖人工点击 WebUI；
- WebUI 的操作结果必须能通过 REST、CLI 或 MCP 查询和复现。

## 16. 计划目录结构

```text
Hy3-ContestLens/
|-- src/hy3_contestlens/
|   |-- api/
|   |   |-- routes/
|   |   |-- schemas/
|   |   |-- dependencies.py
|   |   `-- app.py
|   |-- web/
|   |   |-- routes.py
|   |   |-- templates/
|   |   |   |-- base.html
|   |   |   |-- index.html
|   |   |   |-- resources.html
|   |   |   |-- run_detail.html
|   |   |   |-- submission_detail.html
|   |   |   |-- reports.html
|   |   |   `-- annotation.html
|   |   `-- static/
|   |       |-- app.js
|   |       `-- app.css
|   |-- cli/
|   |   |-- commands/
|   |   `-- main.py
|   |-- agents/
|   |-- workflows/
|   |-- model/
|   |-- mcp_client/
|   |-- evaluation/
|   |-- datasets/
|   |-- reporting/
|   `-- settings.py
|-- mcp_servers/resources/
|   |-- server.py
|   |-- schemas.py
|   |-- tools/
|   |   |-- inspect_path_scope.py
|   |   |-- validate_scoped_path.py
|   |   |-- list_scoped_directory.py
|   |   |-- find_problem_assets.py
|   |   |-- read_problem_document.py
|   |   |-- inspect_test_dataset.py
|   |   `-- read_problem_sample_input.py
|   |-- readers/
|   |   |-- pdf_reader.py
|   |   `-- markdown_reader.py
|   |-- discovery/
|   |   |-- document_index.py
|   |   |-- problem_matcher.py
|   |   `-- test_pairing.py
|   `-- security/
|       |-- path_scope.py
|       `-- path_policy.py
|-- mcp_servers/workspace/
|   |-- server.py
|   |-- schemas.py
|   |-- tools/
|   |   |-- create_cpp_submission.py
|   |   |-- read_cpp_submission.py
|   |   |-- replace_cpp_submission.py
|   |   |-- apply_cpp_patch.py
|   |   |-- list_cpp_revisions.py
|   |   |-- get_cpp_metadata.py
|   |   `-- freeze_cpp_revision.py
|   `-- storage/
|       |-- revision_store.py
|       |-- artifact_store.py
|       `-- audit_log.py
|-- mcp_servers/judge/
|   |-- server.py
|   |-- schemas.py
|   |-- tools/
|   |   |-- compile_cpp.py
|   |   |-- check_answer.py
|   |   |-- run_single_test.py
|   |   `-- fuzz_small_case.py
|   `-- sandbox/
|       |-- Dockerfile.compile
|       |-- Dockerfile.run
|       |-- runner/
|       `-- seccomp/
|-- data/
|   |-- manifests/noip2018/
|   |-- samples/
|   |-- process_cases/
|   `-- private/
|-- configs/
|   |-- app.example.toml
|   |-- resources.example.toml
|   |-- secrets.local.toml.example
|   `-- mcp/
|-- scripts/
|   |-- import_noip2018.py
|   |-- validate_dataset.py
|   |-- run_benchmark.py
|   `-- export_report.py
|-- tests/
|   |-- unit/
|   |-- integration/
|   |-- sandbox_abuse/
|   `-- e2e/
|-- docs/
|   |-- API_REFERENCE.md
|   |-- CLI_REFERENCE.md
|   |-- MCP_TESTING.md
|   |-- EXTERNAL_TESTING_GUIDE.md
|   |-- WEBUI_GUIDE.md
|   |-- EVALUATION_METHOD.md
|   |-- ERROR_TAXONOMY.md
|   |-- DATASET_CARD.md
|   |-- SECURITY.md
|   |-- VALIDATION_REPORT.md
|   `-- TROUBLESHOOTING.md
|-- reports/
|-- runs/
|   `-- <run_id>/
|       |-- workspace/
|       |-- revisions/
|       |-- artifacts/
|       `-- repair_rounds/
|-- docker-compose.yml
|-- pyproject.toml
|-- README.md
|-- LICENSE
`-- .gitignore
```

## 17. 编码阶段与门禁

### 阶段 0：项目隔离与需求冻结

工作：

- 创建独立 `Hy3-ContestLens` 目录；
- 保留当前根目录已有项目；
- 落实 `.gitignore`、许可证和个人作品声明；
- 固定配置、运行记录和 manifest Schema。

门禁：不得覆盖或重命名现有项目文件。

### 阶段 1：题目资源发现与 NOIP2018 数据导入

工作：

- 实现只读 Problem Resources MCP Server；
- 实现路径 scope 授权、规范化、越界检测和审计；
- 实现 PDF/Markdown 题面读取与分块；
- 实现题目文件和 `.in/.out/.ans` 自动发现；
- 实现唯一高置信度自动绑定与多候选人工确认；
- 导入 6 道题、120 组输入输出；
- 将 `.ans` 私有映射为 `.out`；
- 生成题目、测试点和哈希 manifest；
- 写入每题时间和内存；
- 为六题保留洛谷难度空值。

门禁：用户指定路径和默认资源根两种模式均能定位 NOIP2018 题面与数据；路径逃逸被拒绝；120 个输入与 120 个答案全部一一配对，哈希复核通过。

### 阶段 2：代码工作区与沙盒判题 MCP

工作：

- 实现独立 Code Workspace MCP Server；
- 实现 `create_cpp_submission`、`read_cpp_submission`；
- 实现 `replace_cpp_submission`、`apply_cpp_patch`；
- 实现 revision 链、哈希校验、原子写入和审计日志；
- 实现 `freeze_cpp_revision`，向 Judge MCP 提供不可变源码 artifact；
- 实现 Judge MCP 的 `compile_cpp`；
- 实现 Judge MCP 的 `check_answer`；
- 实现题目级内存解析和默认 512MB 回退；
- 实现 stdin 与文件 I/O 兼容；
- 实现全文比较；
- 实现逐测试点资源记录。

门禁：Agent 无法通过任意路径读写 C++；历史 revision 不能覆盖；正确、CE、WA、TLE、MLE、RE、OLE、I/O 冲突样例均能准确识别。

### 阶段 3：沙盒安全加固

工作：

- 网络隔离；
- 权限、capability 和 seccomp；
- PID、CPU、内存和输出限制；
- 测试文件只读；
- 进程树回收；
- 运行工作区隔离。

门禁：无限循环、fork bomb、超量输出、网络访问、宿主路径读取和环境变量窃取测试全部被阻止。

### 阶段 4：Hy3 与多 Agent 工作流

工作：

- 实现 Hy3 Model Adapter；
- 实现配置文件优先、环境变量回退；
- 实现 Problem Analyst、Solver、两个 Critic、Judge、Adjudicator、Repair Planner 和 Code Repair Agent；
- 实现并行盲审和裁决；
- 实现事件记录和重放；
- 实现默认最多 3 轮、硬上限 5 轮的修复循环；
- 实现进展判断、最佳 revision 选择、回归保留和停止状态；
- 每轮通过 Workspace MCP 修改代码，并重新调用 Judge MCP；
- 每轮重新运行过程评审。

门禁：任何 Judge Agent 流程都无法绕过编译和答案检查；循环不能无限执行；修复不能覆盖初始结果。

### 阶段 5：过程评估器

工作：

- 实现步骤 Schema；
- 实现错误分类；
- 实现代码步骤映射；
- 实现首错定位；
- 实现错误类型到修复策略的路由；
- 实现初始能力与修复后能力分离统计；
- 实现 72 个 NOIP2018 过程案例；
- 实现人工标注与一致性统计。

门禁：锁定集能够独立运行并输出定位准确率与误报率。

### 阶段 6：简易 WebUI、外部测试接口与报告

工作：

- 实现版本化 `/api/v1` REST API；
- 实现 Jinja2 + 原生 JavaScript/CSS 的简易本地 WebUI；
- 实现资源路径授权、自动发现、题目选择、run 状态、逐点判题、revision/diff、循环修复和报告页面；
- 实现健康检查、题目、run、submission、判题、修复、批量评测和人工标注接口；
- 实现基于游标的事件查询和 NDJSON 事件流；
- 实现 `hy3-contest` CLI；
- 提供 MCP stdio、Streamable HTTP 和 Inspector 测试配置；
- 生成 OpenAPI、JSON Schema、curl 和 CLI 示例；
- CSV、JSON、HTML 报告；
- 难度缺失状态和无样本状态处理。

门禁：WebUI 能完成一次从题目选择到循环修复和报告查看的流程；同时不依赖 WebUI，仅使用 curl、CLI 或 MCP Inspector 也能完成相同流程。

### 阶段 7：最终验证与接口交付

工作：

- 运行全部自动测试；
- 使用真实 Hy3 Endpoint 运行完整评测；
- 完成人工抽检；
- 填写六题洛谷难度；
- 输出最终分析报告；
- 冻结 OpenAPI、CLI 和 MCP 合约；
- 提供一键端到端接口测试脚本。

门禁：除当前明确暂缓的视频/GIF 和复杂前端外，用户补充要求和本计划验收清单全部通过。

## 18. 测试计划

### 18.1 单元测试

- API Key 文件优先级；
- 环境变量回退；
- Windows/Linux 路径规范化；
- resource scope 内外路径判断；
- PDF 页段与 Markdown 标题分块；
- 题目 ID、中文题名和 I/O basename 匹配；
- `.in` 与 `.out/.ans` 配对及冲突检测；
- 难度七档枚举校验；
- 难度空值处理；
- 题目内存配置优先；
- 内存缺失时默认 512MB；
- 非法内存值报错而非回退；
- `.ans -> .out` 映射；
- 全文比较规范化；
- verdict 优先级；
- 错误分类 Schema。

### 18.2 MCP 合约测试

- `tools/list` Schema；
- `inspect_path_scope` 只返回受限公开信息；
- `validate_scoped_path` 拒绝绝对路径、`..`、UNC/盘符跳转和越界链接；
- `list_scoped_directory` 执行深度、条目数和扩展名限制；
- `find_problem_assets` 能发现单 PDF 多题和分目录测试集；
- 多候选返回 `USER_CONFIRMATION_REQUIRED`；
- `read_problem_document` 正确返回 PDF 页码或 Markdown 行号；
- 文档中的指令不改变工具权限；
- `inspect_test_dataset` 不返回 `.out/.ans` 内容；
- 正式隐藏 `.in` 不能通过 `read_problem_sample_input` 读取；
- 源文件变化后返回 `RESOURCE_CHANGED`；
- `create_cpp_submission` 只创建 `r000`；
- `read_cpp_submission` 必须显式指定 revision；
- `replace_cpp_submission` 和 `apply_cpp_patch` 校验 `base_sha256`；
- patch 冲突时不生成半成品 revision；
- `freeze_cpp_revision` 产生不可变 artifact；
- Workspace MCP 拒绝绝对路径、路径穿越和非 `.cpp` 文件；
- `compile_cpp` 输入校验；
- `compile_cpp` 拒绝未冻结 revision 和任意源码路径；
- `check_answer` 拒绝任意路径；
- 结构化输出字段完整；
- stdio 不被日志污染；
- 工具超时和取消；
- 重复调用幂等性。

### 18.3 REST API 合约测试

- `/openapi.json` 与已提交快照一致；
- 所有路由使用 `/api/v1`；
- Pydantic 请求和响应 Schema 校验；
- 长任务返回 202 和稳定 `run_id`；
- `Idempotency-Key` 重试不重复创建资源；
- run 状态转换合法；
- 事件 `seq` 单调递增且可断点查询；
- cancel 幂等且能终止后续修复轮次；
- 分页、过滤和错误响应一致；
- readiness 能分别报告 Hy3、MCP、Docker 和数据库状态；
- 路径 validate 与 grant 分离，校验不自动授权；
- 远程模式拒绝任意宿主绝对路径授权；
- discover 返回候选、置信度、匹配证据和配对统计；
- resource binding 后 run 使用冻结的资源哈希；
- API 不泄露标准答案、API Key、宿主路径和内部异常堆栈；
- REST 与直接 MCP 调用产生相同 verdict。

### 18.4 CLI 集成测试

- `--help` 和每个子命令可运行；
- `resources validate/discover/bind` 完整链路；
- `--json` 输出可被机器解析且 stdout 不混入日志；
- 非零错误返回稳定退出码；
- `watch` 能从事件游标恢复；
- `submit -> compile -> check` 完整链路；
- `solve -> repair -> report` 完整链路；
- `--direct-mcp` 与 REST 模式结果一致；
- Windows PowerShell 与 Linux shell 路径处理；
- CLI 不读取或打印标准答案和完整 API Key。

### 18.5 简易 WebUI 测试

- 七个核心页面均可渲染；
- resources 页面能校验路径、自动发现和确认候选；
- resources 页面不显示答案内容和未经脱敏的绝对路径；
- 静态 JS/CSS 不依赖外部 CDN；
- 创建 run 表单正确调用 `/api/v1/runs`；
- run 页面能按事件游标增量刷新；
- 取消、继续修复和停止修复按钮调用正确 API；
- revision 页面正确转义 C++，防止脚本注入；
- diff、编译日志和测试结果正确转义；
- WebUI 不显示标准答案、API Key、宿主路径和内部异常堆栈；
- 未启动 Docker 或未配置 Hy3 时显示可理解的 readiness 错误；
- 页面关闭不会取消后台 run；
- WebUI 产生的 run 可由 CLI 和 REST 查询；
- FastAPI TestClient 完成主要页面 smoke，无需 Node.js 测试链。

### 18.6 判题集成测试

- 正确程序；
- 编译失败；
- 错误答案；
- 超时；
- 超内存；
- 运行崩溃；
- 无限输出；
- `freopen` 文件 I/O；
- stdout 输出；
- 文件输出与 stdout 冲突；
- 题目内存不是 512MB 的模拟 manifest；
- 缺少内存字段的默认 512MB 模拟 manifest。

### 18.7 沙盒攻击测试

- fork bomb；
- 创建大量线程；
- 访问网络；
- 读取宿主敏感文件；
- 写只读目录；
- 符号链接逃逸；
- junction/reparse point 逃逸；
- UNC、盘符切换和大小写规范化路径逃逸；
- 超深目录、超多条目和超大 PDF/Markdown；
- 恶意 Markdown/HTML/脚本和 PDF 内提示注入；
- 调用 shell；
- 大文件输出；
- 睡眠与无限循环；
- 尝试读取 Hy3 环境变量。

### 18.8 Agent 工作流测试

- Hy3 超时；
- 非法 JSON；
- 缺失步骤；
- 重复工具调用；
- Critic 意见冲突；
- 工具结论与 LLM 结论冲突；
- 证据不足返回 `UNRESOLVED`；
- 修复结果不覆盖初次结果；
- CE、WA、TLE、MLE、RE 分别路由到正确修复策略；
- 每轮修改后强制重新编译和重新判题；
- 代码变化后强制重新运行全部测试点；
- 连续无改善时返回 `STALLED`；
- 达到轮数上限时返回 `MAX_ROUNDS`；
- 修复回归时保留坏 revision 并从当前最佳 revision 继续；
- 所有测试 AC 但过程错误时继续修订过程或代码映射；
- `UNRESOLVED` 不触发盲目代码重写。

### 18.9 端到端测试

- road 正确代码完整通过；
- WebUI 指定 day1 PDF/目录后正确找到 road 题面和 10 组数据；
- 未指定路径时从默认 NOIP2018 根自动发现 road 资源；
- 多个同名题面时 run 暂停并等待用户确认；
- road 边界错误代码被定位；
- road 边界错误经过 Workspace MCP 局部补丁后重新评测并 AC；
- 编译错误经过至少一轮修复后进入正式判题；
- 无法在最大轮数内修复的代码正确停止并保留全部历史；
- 文件 I/O 标准程序被正确执行；
- 一道复杂题完成多 Agent + MCP 全流程；
- REST、CLI 和 MCP 均能触发并查询同一次完整评测；
- 简易 WebUI 能触发评测，并由 REST/CLI 查询相同 run；
- 批量报告正确聚合 6 题；
- 未提供难度时不生成虚假的难度结论。

## 19. 评测产物

每个运行目录：

```text
runs/<run_id>/
  request.json
  problem_spec.json
  solver_output.json
  workspace/
    submission.json
    revisions/
      r000.cpp
      r001.cpp
      ...
    revision_graph.json
    workspace_audit.jsonl
  source_artifacts/
  algorithm_critic.json
  code_critic.json
  initial_evaluation/
    compile_result.json
    answer_check_result.json
    process_evaluation.json
  repair_rounds/
    round_001/
      diagnosis.json
      repair_plan.json
      patch.diff
      compile_result.json
      answer_check_result.json
      process_evaluation.json
      loop_decision.json
    ...
  best_revision.json
  final_evaluation.json
  final_report.html
```

批量评测产物：

```text
reports/
  evaluation_results.csv
  final_answer_summary.csv
  process_accuracy.csv
  repair_effectiveness.csv
  repair_rounds.csv
  repair_stop_reasons.csv
  localization_validation.csv
  false_positive_audit.csv
  error_distribution.csv
  difficulty_summary.csv
  human_annotations.csv
  validation_report.html
```

## 20. 外部接口测试方案

当前以可重复执行的接口测试作为主要验收方式，并额外提供简易 WebUI 进行人工测试；视频/GIF 仍暂缓。项目提供：

```text
scripts/smoke_api.ps1
scripts/smoke_api.sh
scripts/e2e_test.py
examples/http/
examples/cli/
examples/mcp/
examples/webui/
```

### 20.1 REST smoke 流程

1. 调用 `/healthz` 和 `/readyz`；
2. 校验并授权 NOIP2018 资源目录；
3. 自动发现 `road` 原题 PDF 页段和 10 组测试数据；
4. 查询 NOIP2018 六题元数据；
5. 使用 resource binding 创建一个 `road` run；
6. 启动多 Agent；
7. 使用事件游标等待编译和判题；
8. 若初始评审失败，观察自动修复轮次；
9. 获取初始、最佳和最终 revision；
10. 下载 JSON/CSV/HTML 报告；
11. 校验资源、运行、代码和判题哈希关系。

示意请求：

```bash
curl http://127.0.0.1:8000/readyz
curl -X POST http://127.0.0.1:8000/api/v1/resource-scopes:validate \
  -H "Content-Type: application/json" \
  -d '{"path":"<NOIP2018_PATH>"}'
curl -X POST http://127.0.0.1:8000/api/v1/resource-scopes \
  -H "Content-Type: application/json" \
  -d '{"path":"<NOIP2018_PATH>","confirmed":true}'
curl -X POST http://127.0.0.1:8000/api/v1/resource-scopes/<scope_id>:discover \
  -H "Content-Type: application/json" \
  -d '{"problem_id":"road","mode":"auto","search_tests":true}'
curl http://127.0.0.1:8000/api/v1/datasets/noip2018/problems
curl -X POST http://127.0.0.1:8000/api/v1/runs \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: local-road-001" \
  -d '{"problem_id":"road","resource_binding_id":"<binding_id>","repair":{"enabled":true,"max_rounds":3}}'
curl http://127.0.0.1:8000/api/v1/runs/<run_id>/result
```

### 20.2 CLI smoke 流程

```bash
hy3-contest health --json
hy3-contest resources validate --path <NOIP2018_PATH> --json
hy3-contest resources grant --path <NOIP2018_PATH> --json
hy3-contest resources discover --scope <scope_id> --problem road --json
hy3-contest list-problems --json
hy3-contest solve --problem road --max-repair-rounds 3 --json
hy3-contest watch --run <run_id>
hy3-contest report --run <run_id> --format json
```

CLI 测试脚本必须检查退出码、JSON 可解析性、最终状态、revision 链和逐测试点资源字段。

### 20.3 MCP smoke 流程

使用 MCP Inspector 分别连接 Resources MCP、Workspace MCP 和 Judge MCP：

1. 检查已授权 resource scope；
2. 自动发现 `road` 题面和测试数据；
3. 读取 road 对应 PDF 页段；
4. 创建 C++ submission；
5. 读取并 patch 一个 revision；
6. 冻结 revision；
7. 编译冻结 artifact；
8. 对 `road` 调用 `check_answer`；
9. 核对 REST 查询到的结果与 MCP 结果一致。

### 20.4 WebUI smoke 流程

1. 浏览器打开 `http://127.0.0.1:8000/`；
2. 在 resources 页面输入 NOIP2018 目录；
3. 校验路径并自动发现 `road` 原题和测试数据；
4. 确认候选 binding；
5. 确认 readiness 与六题元数据正确显示；
6. 选择 `road`，创建启用 3 轮修复的 run；
7. 在 run 页面观察 Agent 状态、编译、逐点判题和首错定位；
8. 查看 Code Repair Agent 生成的新 revision 和 diff；
9. 等待重新判题或明确停止状态；
10. 下载最终报告；
11. 使用 CLI 查询同一 `run_id`，确认结果一致。

### 20.5 外部接口与 WebUI 交付物

- 固定版本的 `openapi.json`；
- REST API 参考文档；
- CLI 命令参考；
- MCP 工具参考与 Inspector 配置；
- 资源路径授权、自动发现和题面读取说明；
- curl、PowerShell、Python 示例；
- 简易 WebUI 使用说明与页面 smoke 清单；
- 一键 smoke 与端到端测试脚本；
- 错误码表和排障文档。

## 21. 最终验收清单

- [ ] 项目独立于现有文档分析项目；
- [ ] README 标注个人/活动作品和非腾讯官方；
- [ ] 当前题目数据仅包含 NOIP2018 六题；
- [ ] 120 组输入输出全部配对；
- [ ] `.ans` 已在私有判题区映射为 `.out`；
- [ ] 六题难度均来自用户提供的洛谷七档映射；
- [ ] 未提供难度时系统不自行推断；
- [ ] `compile_cpp` 为真实 MCP 工具；
- [ ] `check_answer` 为真实 MCP 工具；
- [ ] 存在独立只读 Problem Resources MCP Server；
- [ ] WebUI 可以接收用户指定的 PDF、Markdown 或目录路径；
- [ ] 路径校验与路径授权分离，并生成受限 `resource_scope_id`；
- [ ] Agent 只能使用 scope ID 和相对路径，不能自由访问宿主绝对路径；
- [ ] 能读取 PDF 指定页段和 Markdown 指定标题/行号范围；
- [ ] 文档内容被标记为不可信题面数据，不能改变系统指令和工具权限；
- [ ] 能从默认资源根自动寻找六题对应原题和测试数据；
- [ ] `.in` 与 `.out/.ans` 能按 basename 配对并发现缺失、重复和冲突；
- [ ] 多候选或低置信度时要求用户在 WebUI 确认；
- [ ] 自动发现失败时返回明确错误，不用模型记忆虚构题面；
- [ ] Solver/Repair Agent 不能读取正式 `.out/.ans` 内容；
- [ ] run 开始时冻结题面和测试数据哈希，资源变化可被检测；
- [ ] 存在独立 Code Workspace MCP Server；
- [ ] 能通过 MCP 创建、读取、完整替换和局部 patch C++；
- [ ] Agent 不能使用任意绝对路径读写源码；
- [ ] 每次代码修改生成新 revision，不覆盖历史版本；
- [ ] `base_sha256` 能阻止在过期版本上修改；
- [ ] Judge MCP 只编译冻结的不可变源码 artifact；
- [ ] Workspace MCP 不能读取测试数据、标准答案和 API Key；
- [ ] `check_answer` 实际运行 C++ 并逐个读取 `.in`；
- [ ] 输出与 `.out` 按全文比较规则校验；
- [ ] 题目 manifest 中的内存值优先生效；
- [ ] 只有缺少内存字段时才使用默认 512MB；
- [ ] 判题报告记录内存值及其来源；
- [ ] 时间、内存、退出码和输出限制全部参与 AC 判定；
- [ ] 未知 C++ 只在 Linux Docker 沙盒运行；
- [ ] 沙盒不可用时拒绝运行，不降级到宿主机；
- [ ] 应用真实使用多 Agent；
- [ ] Algorithm Critic 与 Code Critic 在裁决前上下文隔离；
- [ ] Judge Agent 不能绕过 MCP；
- [ ] 评审失败后能依据首错步骤和错误类型生成 Repair Plan；
- [ ] Code Repair Agent 只能通过 Workspace MCP 修改代码；
- [ ] 每轮代码修改后强制重新编译、重新运行全部测试点并重新过程评审；
- [ ] 修复循环默认最多 3 轮、硬上限 5 轮；
- [ ] 连续无改善、无法定位、基础设施失败和安全违规均能正确停止；
- [ ] 回归 revision 被保留，系统可以从历史最佳 revision 继续；
- [ ] 初始指标、每轮指标和最终指标分开统计；
- [ ] 提供基于 Jinja2、原生 JavaScript 和 CSS 的简易本地 WebUI；
- [ ] WebUI 不依赖 React、Vue 或 Node.js 构建链；
- [ ] WebUI 能选择题目、创建 run、查看 Agent 状态、逐点判题、错误定位和修复轮次；
- [ ] WebUI 所有写操作均通过 `/api/v1`，不绕过 Workspace/Judge MCP；
- [ ] REST、CLI 和 MCP 在没有 WebUI 时仍能覆盖全部核心能力；
- [ ] WebUI、REST、CLI 和 MCP 对同一 run 返回一致结果；
- [ ] 能定位首个错误步骤；
- [ ] 有固定错误分类体系；
- [ ] 能识别结果正确但过程无效的案例；
- [ ] 完成定位准确率验证；
- [ ] 完成误报率和人工抽检；
- [ ] 输出最终答案准确率、过程正确率、错误分布和难度分层；
- [ ] API Key 先从本地文件读取，再回退环境变量；
- [ ] API Key 不进入 Git、日志和评测产物；
- [ ] 完成 README、环境样例、运行说明和分析报告；
- [ ] 完成 OpenAPI、CLI、MCP 和简易 WebUI 测试说明。

## 22. 开发前仍待用户提供的唯一业务数据

后续请提供以下六题与洛谷七档难度的对应关系：

```text
road     -> 待提供
money    -> 待提供
track    -> 待提供
travel   -> 待提供
game     -> 待提供
defense  -> 待提供
```

允许值只能是：

```text
入门
普及-
普及/提高-
普及+/提高
提高+/省选-
省选/NOI-
NOI/NOI+/CTSC
```

在难度映射提供前，可以开发数据导入、MCP、沙盒和 Agent 工作流，但最终难度分层实验与活动分析报告保持未完成状态。
