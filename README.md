# Hy3-ContestLens

Hy3-ContestLens 是一个面向算法竞赛学习、教学和大模型评测的过程级评估应用。它使用 Hy3 生成结构化解题过程与 C++17 程序，再结合相互隔离的 Algorithm Critic、Code Critic、Linux Docker 判题器和确定性裁决器，回答四个问题：

1. 最终程序是否正确；
2. 解题过程是否成立；
3. 错误最早从哪一步开始；
4. 错误属于哪一种类型，以及能否通过有界修复循环改正。

> 本项目是个人 / 犀牛鸟活动作品，并非腾讯或腾讯混元官方发布。项目通过 Hy3 调用模型能力，不训练或微调模型。

当前数据范围固定为 NOIP2018 提高组 day1/day2 的 6 道题、120 个测试点。六题的洛谷七档难度尚待用户提供；在收到人工映射前，界面与报告只显示“待标注”，不会根据题名、通过率或模型表现自行推断。

## 1. 功能概览

- **Problem Resources MCP**：创建只读资源 scope，安全读取 PDF/Markdown，自动发现题面与测试目录，检查 `.in`、`.out`、`.ans` 配对；
- **Code Workspace MCP**：创建 C++ submission，保存只增不改的 revision，通过 `base_sha256` 阻止过期修改，支持完整替换和 unified diff patch；
- **Judge MCP**：只编译冻结后的源码 artifact，在 Linux Docker 中逐点执行，检查 CE、WA、TLE、MLE、RE、OLE 和 I/O 冲突；
- **多 Agent 工作流**：Problem Analyst、Solver、Algorithm Critic、Code Critic、Judge、Adjudicator、Repair Agent；
- **有界修复**：默认最多 3 轮、硬上限 5 轮，保留初次、逐轮、最佳和最终结果；
- **四种入口**：REST API、`hy3-contest` CLI、三个 MCP Server、无需 Node.js 构建的 WebUI；
- **评测产物**：SQLite、JSON、JSONL、CSV、可打印 HTML 报告、OpenAPI 和 JSON Schema；
- **有效性验证**：72 条过程案例模板、首错定位准确率和误报率计算工具、双人盲标与仲裁接口。

## 2. 系统架构

```text
WebUI / CLI / REST / MCP Inspector
                |
            FastAPI API
                |
       Multi-Agent Workflow
     /          |           \
Resources MCP  Workspace MCP  Judge MCP
     |             |             |
PDF/Markdown   C++ revisions   Linux Docker
NOIP2018 data  frozen source   compile + run
                |
       Diagnosis / Repair Loop
                |
        JSON / CSV / HTML reports
```

WebUI、CLI 和 REST 最终调用同一组服务。Workspace MCP 不读取正式答案；Judge MCP 不接受用户传入的源码路径或答案路径；模型只会接触题面、公开元数据和经过裁剪的判题证据。

## 3. 环境要求

项目统一使用 Conda 管理 Python 运行环境。

必需软件：

- Miniconda 或 Anaconda，建议 Conda 24 或更新版本；
- Docker Desktop，并切换到 Linux containers；
- Docker Compose v2；
- Windows PowerShell 7、Windows PowerShell 5.1，或 Linux/macOS shell；
- 可访问的 Hy3 OpenAI-compatible Chat Completions endpoint。

推荐资源：

- 磁盘空间至少 2 GB，用于 Conda 环境、私有测试数据和 Docker 镜像；
- 内存至少 8 GB；
- 本地服务端口 `8000` 未被占用。

检查基础工具：

```powershell
conda --version
docker version
docker compose version
```

如果 PowerShell 找不到 `conda activate`，先执行：

```powershell
conda init powershell
```

关闭并重新打开 PowerShell 后再继续。

## 4. 创建 Conda 环境

进入项目目录：

```powershell
Set-Location G:\hy3\Real_Obj\Hy3-ContestLens
```

### 4.1 一键创建或更新

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_conda.ps1
conda activate hy3-contestlens
```

脚本会：

1. 检查 Conda 是否可用；
2. 环境不存在时读取 `environment.yml` 创建环境；
3. 环境已存在时执行带 `--prune` 的更新；
4. 安装当前项目及开发依赖；
5. 执行 `pip check` 和包导入检查。

如果 Conda channel 暂时不可访问，但本机包缓存中已有 Python 3.12、pip、setuptools 和 wheel，可以使用：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_conda.ps1 -Offline
conda activate hy3-contestlens
```

`-Offline` 只让 Conda 使用本机包缓存创建最小环境；项目的 Python 依赖仍由环境内部的 pip 安装，因此 pip 索引或 pip 缓存至少要有一个可用。任一步失败时脚本会返回非零状态，不会错误地报告成功。

如果最小环境所需的 Conda 包也不在缓存中，可以克隆一个本机已有的 Python 3.12 Conda 环境后安装本项目：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_conda.ps1 -CloneFrom fast_api
conda activate hy3-contestlens
```

`-CloneFrom` 不访问 Conda channel，适合受限网络下的本地验证，但目标环境会继承源环境中的额外包。源环境应使用 Python 3.12，且其 Conda 包仍在本地缓存中。网络恢复后建议删除目标环境，再按 `environment.yml` 创建最小环境。

### 4.2 手动创建

```powershell
conda env create -f environment.yml
conda activate hy3-contestlens
```

以后 `environment.yml` 发生变化时执行：

```powershell
conda env update -n hy3-contestlens -f environment.yml --prune
```

确认当前 shell 正在使用正确环境：

```powershell
conda info --envs
python --version
python -c "import sys, hy3_contestlens; print(sys.executable); print(hy3_contestlens.__version__)"
```

输出的 Python 路径应位于 Conda 的 `envs\hy3-contestlens` 目录中。

不希望激活环境时，可以为任意命令添加前缀：

```powershell
conda run -n hy3-contestlens python scripts\validate_dataset.py
conda run -n hy3-contestlens hy3-contest --json list-problems
```

## 5. 配置应用

### 5.1 Hy3 模型连接

复制本地密钥模板：

```powershell
Copy-Item configs\secrets.local.toml.example configs\secrets.local.toml
```

编辑 `configs/secrets.local.toml`：

```toml
[hy3]
api_key = "YOUR_HY3_API_KEY"
base_url = "http://127.0.0.1:8001/v1"
model = "hy3"
reasoning_effort = "medium"
temperature = 0.2
top_p = 0.95
max_tokens = 8192
response_format = "json_schema"
max_attempts = 3
retry_backoff_seconds = 1.0
```

`base_url` 必须指向真实的 Hy3 OpenAI-compatible 服务。上例假设模型服务运行在 `8001` 端口；本项目自己的 Web/API 服务默认使用 `8000`，两者不能指向同一个服务进程。

密钥读取优先级：

1. `configs/secrets.local.toml` 中的非空值；
2. 对应环境变量；
3. 两处都没有时，readiness 返回明确的 `HY3_NOT_CONFIGURED`。

可用环境变量：

```text
HY3_API_KEY
HY3_BASE_URL
HY3_MODEL
HY3_REASONING_EFFORT
HY3_TEMPERATURE
HY3_TOP_P
HY3_MAX_TOKENS
HY3_RESPONSE_FORMAT
HY3_MAX_ATTEMPTS
HY3_RETRY_BACKOFF_SECONDS
```

`configs/secrets.local.toml` 已被 `.gitignore` 排除。不要把 API Key 写进 README、源码、命令历史、截图或公开仓库。

#### 结构化响应、重试与诊断

- 默认使用 `response_format.type=json_schema`，将实际 Pydantic Schema 传给模型接口，并在本地再次校验。协议格式见[腾讯 TokenHub 混元调用指南](https://cloud.tencent.com/document/product/1823/132252)。Schema 约束不替代算法、源码和评测结果检查。
- `max_attempts=3` 表示每个模型调用最多请求 3 次（首次 + 2 次重试），允许范围 1–5；这是模型请求重试，不是代码修复轮数。重试可能增加耗时和 API 费用，不会自动增加 `max_tokens`。
- 缺字段、类型错误、非法 JSON、异常响应结构和 `finish_reason=length` 会带着校验反馈请求完整重生成，不通过填空值伪造成功。超时、连接异常、HTTP 408/429/5xx 会有限重试；其他 HTTP 4xx、拒答和内容过滤不重试。指数退避及数值型 `Retry-After` 的等待时间上限均为 30 秒。
- 收到取消标记后不会发起下一次模型请求；已经在途的请求仍可能完成并计费。
- 若旧服务明确不支持 `json_schema`，可显式设置 `response_format="json_object"`，保留本地校验和重试。客户端不会在 400 后静默降低约束；优先检查服务支持范围和返回的错误详情。
- 每次工作流请求都会写入 `runs/<run_id>/model_calls/<call_id>-<attempt>.json`，并发评审和不同运行相互隔离。失败报告中的 `details.diagnostics` 是相对于本次运行目录的日志路径。
- 日志保存模型参数、Schema、提示词哈希/长度、请求 ID、HTTP 状态、`finish_reason`、token 用量、响应和校验错误；不保存请求头及提示词正文。响应中的已配置 API Key、常见凭据字段会脱敏，`reasoning_content` 等思考正文会省略；用量中的 reasoning token 计数保留。单次响应记录上限为 200 万字符，超出后明确记录 `body_truncated`、原长度与响应哈希。
- 日志仅存本地、不通过 Web/API 公开，但仍可能包含题目内容和生成源码，分享前应人工检查。日志写入异常不会掩盖模型结果，会在服务日志告警，并在失败详情的 `diagnostic_log_errors` 中列出。

修改代码或配置后需重启 Web/API 服务。历史失败记录不会被改写，重新发起运行后才会生成上述诊断日志。

### 5.2 资源根目录

首次使用时复制配置模板：

```powershell
if (-not (Test-Path configs\resources.toml)) {
    Copy-Item configs\resources.example.toml configs\resources.toml
}
```

编辑 `configs/resources.toml`：

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
path = "G:\\hy3\\Real_Obj\\NOI-NOIP_data\\2018"
read_only = true
```

提交仓库时只保留带占位符的 `resources.example.toml`。本地 `resources.toml` 已被忽略，因为它包含机器相关的绝对路径。

### 5.3 应用端口和数据库

需要覆盖默认值时，将 `configs/app.example.toml` 复制为 `configs/app.toml` 后编辑：

```toml
[app]
host = "127.0.0.1"
port = 8000
local_mode = true
database_path = "runs/contestlens.sqlite3"
```

默认只监听 `127.0.0.1`。如果要暴露到局域网或公网，必须先增加认证、关闭任意本地路径授权并配置反向代理；不要直接把本地模式暴露到外部网络。

## 6. 导入 NOIP2018 数据

原始数据位于项目外部：

```text
G:\hy3\Real_Obj\NOI-NOIP_data\2018
```

导入脚本只读取原目录，将 `.in` 和 `.ans` 复制到被 Git 忽略的私有判题区，并把 `.ans` 映射为 `.out`。原始文件不会被重命名或覆盖。

```powershell
conda activate hy3-contestlens
python scripts\import_noip2018.py
python scripts\validate_dataset.py
python scripts\bootstrap_resources.py
```

预期校验结果：

| 题目 | 输入 | 答案 | 时限 | 内存 |
|---|---:|---:|---:|---:|
| road | 10 | 10 | 1000 ms | 512 MB |
| money | 20 | 20 | 1000 ms | 512 MB |
| track | 20 | 20 | 1000 ms | 512 MB |
| travel | 25 | 25 | 1000 ms | 512 MB |
| game | 20 | 20 | 1000 ms | 512 MB |
| defense | 25 | 25 | 2000 ms | 512 MB |

总数必须为 120。导入 manifest 会保存源文件和导入文件的 SHA-256，用于发现数据变化。

如果数据位于其他目录：

```powershell
python scripts\import_noip2018.py --source "D:\datasets\NOIP2018"
```

`bootstrap_resources.py` 只会自动绑定唯一、高置信度、无冲突且配对完整的候选；其他情况必须在 WebUI 中人工确认。

## 7. 构建 Docker 判题沙盒

启动 Docker Desktop 并确认 Linux daemon 可用：

```powershell
docker version
docker compose config --quiet
docker compose build
```

构建完成后应存在：

```text
hy3-contestlens-compile:local
hy3-contestlens-run:local
```

查看镜像：

```powershell
docker image inspect hy3-contestlens-compile:local
docker image inspect hy3-contestlens-run:local
```

未知 C++ **只能**在 Linux Docker 中执行。Docker daemon 或镜像不可用时，Judge 返回 `SANDBOX_UNAVAILABLE`，不会调用 Windows/宿主机上的 `g++` 作为替代。

运行容器默认采用：

- `--network none`；
- 只读根文件系统；
- 非 root UID；
- `--cap-drop ALL`；
- `no-new-privileges`；
- PID、CPU、内存、栈、文件和输出限制；
- 每个测试点全新工作目录。

提交程序使用 `-static-libstdc++ -static-libgcc` 编译，避免编译镜像与精简运行镜像之间的 C++ 运行库版本不一致。所有初始提交和修复 revision 还必须包含与题目 `io.basename` 对应的活动文件重定向，例如 `road` 题必须在任何输入输出前调用：

```cpp
freopen("road.in", "r", stdin);
freopen("road.out", "w", stdout);
```

Solver 和 Repair Agent 会收到这一强制要求，Workspace 也会进行确定性校验；缺少、注释掉或使用错误题目文件名的源码将以 `REQUIRED_FILE_IO_MISSING` 拒绝，CLI/API 手工提交同样适用。

## 8. 启动 REST API 和 WebUI

激活环境后运行：

```powershell
conda activate hy3-contestlens
hy3-contest-api
```

等价的显式命令：

```powershell
python -m uvicorn hy3_contestlens.api.app:app --host 127.0.0.1 --port 8000
```

不激活环境时：

```powershell
conda run --no-capture-output -n hy3-contestlens hy3-contest-api
```

常用地址：

| 地址 | 用途 |
|---|---|
| <http://127.0.0.1:8000/> | WebUI 首页 |
| <http://127.0.0.1:8000/ui/resources> | 路径授权、资源发现和 binding |
| <http://127.0.0.1:8000/ui/runs/new> | 创建评测 run |
| <http://127.0.0.1:8000/healthz> | 进程存活检查 |
| <http://127.0.0.1:8000/readyz> | Hy3、Docker、数据和数据库 readiness |
| <http://127.0.0.1:8000/docs> | Swagger UI |
| <http://127.0.0.1:8000/openapi.json> | OpenAPI JSON |

推荐操作流程：

1. 打开 `/readyz`，确认数据库和私有数据已就绪；
2. 打开“资源”页，选择已授权 scope；
3. 对题目执行自动发现，检查 PDF 页段和测试配对数；
4. 唯一候选可直接 binding，多候选必须人工确认；
5. 打开“新评测”，选择题目和最大修复轮数；
6. 在 run 页面观察 Agent 状态、编译、逐点 verdict、首错步骤和 revision；
7. 下载或打开最终 HTML 报告。

浏览器关闭不会取消后台任务。取消必须显式调用 API 或点击 run 页面的“取消”。

## 9. CLI 用法

CLI 的全局 `--json` 参数写在子命令之前。

### 9.1 健康和题目

```powershell
hy3-contest --json health
hy3-contest --json list-problems
hy3-contest --json show-problem road
```

### 9.2 资源发现与绑定

列出已有 scope：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/resource-scopes |
    ConvertTo-Json -Depth 8
```

校验、授权、发现和绑定：

```powershell
hy3-contest --json resources validate --path "G:\hy3\Real_Obj\NOI-NOIP_data\2018"
hy3-contest --json resources grant --path "G:\hy3\Real_Obj\NOI-NOIP_data\2018"
hy3-contest --json resources discover --scope <scope_id> --problem road
hy3-contest --json resources bind --scope <scope_id> --problem road --candidate <candidate_id>
```

### 9.3 创建和观察评测

```powershell
hy3-contest --json solve --problem road --max-repair-rounds 3
hy3-contest watch --run <run_id>
hy3-contest --json report --run <run_id> --format json
```

### 9.4 手工提交、冻结、编译和判题

先通过 REST/WebUI 创建 run，再执行：

```powershell
hy3-contest --json submit --run <run_id> --problem road --source .\solution.cpp
hy3-contest --json compile --run <run_id> --submission <submission_id> --revision r000
hy3-contest --json check --compile-artifact <compile_artifact_id> --problem road
```

`compile` 会先冻结指定 revision；Judge 不接受工作区内仍可变化的源码。

## 10. MCP Server 用法

项目提供三个独立 stdio MCP Server：

```powershell
conda activate hy3-contestlens
python -m mcp_servers.resources.server
python -m mcp_servers.workspace.server
python -m mcp_servers.judge.server
```

也可以直接通过 Conda 调用：

```powershell
conda run --no-capture-output -n hy3-contestlens python -m mcp_servers.resources.server
conda run --no-capture-output -n hy3-contestlens python -m mcp_servers.workspace.server
conda run --no-capture-output -n hy3-contestlens python -m mcp_servers.judge.server
```

`configs/mcp/` 中的配置已经使用第二种方式，因此 MCP 客户端不依赖当前终端是否执行过 `conda activate`。使用前将配置里的 `<PROJECT_ROOT>` 替换为本项目绝对路径。

三个服务的核心工具：

| Server | 工具 |
|---|---|
| Resources | `inspect_path_scope`、`validate_scoped_path`、`list_scoped_directory`、`find_problem_assets`、`read_problem_document`、`inspect_test_dataset` |
| Workspace | `create_cpp_submission`、`read_cpp_submission`、`replace_cpp_submission`、`apply_cpp_patch`、`list_cpp_revisions`、`freeze_cpp_revision` |
| Judge | `compile_cpp`、`check_answer`、`run_single_test`、`inspect_problem_manifest`、`sandbox_healthcheck` |

详细的 MCP Inspector 顺序和非法请求测试见 `docs/MCP_TESTING.md`。

## 11. REST API 示例

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/healthz
Invoke-RestMethod http://127.0.0.1:8000/readyz
```

创建 run：

```powershell
$body = @{
    problem_id = "road"
    repair = @{
        enabled = $true
        max_rounds = 3
    }
} | ConvertTo-Json -Depth 4

$run = Invoke-RestMethod `
    -Method Post `
    -Uri http://127.0.0.1:8000/api/v1/runs `
    -Headers @{ "Idempotency-Key" = "road-local-001" } `
    -ContentType "application/json" `
    -Body $body

Invoke-RestMethod `
    -Method Post `
    -Uri "http://127.0.0.1:8000/api/v1/runs/$($run.run_id)/start"
```

增量读取事件：

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/runs/$($run.run_id)/events?after_seq=0"
```

完整请求样例位于 `examples/http/road.http`，固定接口快照位于 `docs/openapi.json`，结构化输出 Schema 位于 `docs/schemas/`。

## 12. 批量评测和报告

六题批量运行：

```powershell
python scripts\run_benchmark.py --wait
```

只运行指定题目：

```powershell
python scripts\run_benchmark.py --problems road money --max-repair-rounds 3 --wait
```

导出单个 run 的 HTML 报告：

```powershell
python scripts\export_report.py --run <run_id>
```

每个 run 会保存：

```text
runs/<run_id>/
  problem_spec.json
  solver_output.json
  algorithm_critic.json
  code_critic.json
  workspace/
  artifacts/
  initial_evaluation/
  repair_rounds/
  best_revision.json
  final_evaluation.json
```

报告严格区分：

- `initial_submission_result`：Solver 初次能力；
- `repair_round_results[]`：每轮诊断、代码和重新评测；
- `best_submission_result`：确定性指标最优版本；
- `final_submission_result`：最终交付版本。

基础设施或配置失败不会混入模型准确率。

## 13. 过程评估有效性验证

生成的 `data/process_cases/noip2018_process_cases.jsonl` 包含 72 条覆盖模板。模板明确标记为 `TEMPLATE_NOT_EXECUTED`，不能冒充真实实验结果。

真实验证步骤：

1. 配置 Hy3 和 Docker；
2. 将模板物化为具体 Solver 输出和 C++；
3. 完成确定性判题与过程评估；
4. 由两名标注者进行盲标；
5. 对分歧样本仲裁；
6. 导出 prediction 与 annotation JSONL；
7. 计算定位准确率和误报构成。

```powershell
python scripts\validate_localization.py `
    --predictions reports\predictions.jsonl `
    --annotations reports\annotations.jsonl
```

六题难度均标注前，批量结果会返回 `DIFFICULTY_DATA_INCOMPLETE`，不会生成虚假的难度拐点结论。

## 14. 测试与验收

运行完整测试：

```powershell
conda activate hy3-contestlens
pytest
```

常用独立检查：

```powershell
python scripts\validate_dataset.py
python scripts\export_schemas.py
python scripts\export_openapi.py
python -m pip check
docker compose config --quiet
```

REST smoke：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\smoke_api.ps1
```

端到端接口脚本：

```powershell
python scripts\e2e_test.py --problem road
python scripts\e2e_test.py --problem road --wait
```

Docker daemon 未启动时，普通单元测试仍可运行；任何未知 C++ 执行必须返回 `SANDBOX_UNAVAILABLE`，不得绕过沙盒。

当前验证详情见 `docs/VALIDATION_REPORT.md`。

## 15. 安全边界

### 15.1 题面与路径

- WebUI/API 只在受信任边界接收绝对路径；
- Agent/MCP 只使用不可猜测的 scope ID 和相对路径；
- 拒绝绝对路径穿越、`..`、UNC、盘符切换、symlink、junction 和 reparse point；
- PDF/Markdown 中的命令和提示词始终标记为 `untrusted_problem_content`；
- 不执行文档中的代码块、HTML、脚本或链接。

### 15.2 标准答案

- Solver 和 Repair Agent 不读取正式 `.out/.ans`；
- Resources MCP 只返回答案文件类型、大小和哈希，不返回内容；
- Judge 根据 `dataset_id + problem_id` 从私有区加载答案；
- API 不接受外部标准答案路径。

### 15.3 C++ 执行

- Workspace 每次修改创建新 revision，不覆盖历史；
- patch 必须携带正确的 `base_sha256`；
- Judge 只编译冻结 artifact；
- 未知代码只在 Linux Docker 内运行；
- 容器不接收 Hy3 Key 或宿主敏感环境变量。

更多信息见 `docs/SECURITY.md`。

## 16. 常见问题

### `HY3_NOT_CONFIGURED`

检查 `configs/secrets.local.toml` 是否存在，Key、base URL 和 model 是否为非空值。也可以检查环境变量：

```powershell
Get-ChildItem Env:HY3_*
```

### `SANDBOX_UNAVAILABLE`

```powershell
docker version
docker compose build
docker image inspect hy3-contestlens-run:local
```

确保 Docker Desktop 已启动且使用 Linux containers。

### `PRIVATE_DATASET_NOT_IMPORTED`

```powershell
python scripts\import_noip2018.py
python scripts\validate_dataset.py
```

### `USER_CONFIRMATION_REQUIRED`

发现了多个题面/测试候选，或者置信度不足。打开 `/ui/resources`，检查页段、配对数、冲突和证据后人工选择。

### `RESOURCE_CHANGED`

题面或测试文件在 binding 后发生变化。重新执行 discover 和 binding，不要在同一 run 中混用新旧资源。

### `STALE_REVISION`

patch 使用了过期的 `base_sha256`。先显式读取目标 revision，再基于该版本重新生成 patch。

### 端口 8000 已占用

复制 `configs/app.example.toml` 为 `configs/app.toml`，修改端口后启动。访问地址和 CLI `--base-url` 也要同步修改。

### Conda 环境需要重建

优先更新：

```powershell
conda env update -n hy3-contestlens -f environment.yml --prune
```

需要完全重建时：

```powershell
conda deactivate
conda env remove -n hy3-contestlens
conda env create -f environment.yml
```

## 17. 项目目录

```text
Hy3-ContestLens/
  environment.yml             Conda 环境定义
  pyproject.toml               Python 包与入口点
  src/hy3_contestlens/         服务核心、Agent、REST、CLI、WebUI
  mcp_servers/                 Resources、Workspace、Judge MCP
  configs/                     应用、资源、密钥和 MCP 配置示例
  data/manifests/              NOIP2018 公开 manifest
  data/private/                本地私有判题数据，不进入 Git
  data/process_cases/          72 条过程验证模板
  scripts/                     环境、导入、验证、报告和 smoke 脚本
  tests/                       单元与集成测试
  docs/                        API、CLI、MCP、评估、安全和验证文档
  examples/                    HTTP、CLI 和 MCP 示例
  runs/                        SQLite、revision 和每次运行产物
  reports/                     批量统计与导出报告
```

## 18. 当前仍需补充的外部内容

1. 可用的 Hy3 endpoint、模型名和 API Key；
2. 六题对应的洛谷七档难度；
3. 启动 Docker daemon 后完成 Linux 镜像构建和真实沙盒集成测试；
4. 真实 Hy3 输出、人工盲标和定位准确率/误报率实验；
5. 最终活动提交所需的 2 分钟内 demo 视频或 GIF。

这些状态会在 readiness、验证报告或批量报告中明确显示，不会用推断值或伪造实验数据补齐。

## 19. 延伸文档

- `docs/API_REFERENCE.md`：REST API 约定；
- `docs/CLI_REFERENCE.md`：CLI 行为和退出码；
- `docs/MCP_TESTING.md`：MCP Inspector 测试；
- `docs/EXTERNAL_TESTING_GUIDE.md`：外部接口验收；
- `docs/WEBUI_GUIDE.md`：WebUI 页面流程；
- `docs/EVALUATION_METHOD.md`：过程评估与指标；
- `docs/ERROR_TAXONOMY.md`：错误类型；
- `docs/DATASET_CARD.md`：数据来源和限制；
- `docs/SECURITY.md`：安全模型；
- `docs/VALIDATION_REPORT.md`：当前验证结果；
- `docs/TROUBLESHOOTING.md`：排障说明。

## 20. 可选图片理解

题面含图片、扫描页或矢量图形时，可独立配置 `kimi-k3` 等视觉模型，将结构理解后的文字补充给 Hy3。WebUI 在工作流步骤中提供“使用更精确的图片理解 / 不使用，继续运行”选择；不配置、跳过或识别失败均继续原流程。详见 [图片理解配置与使用](docs/IMAGE_UNDERSTANDING.md)。
