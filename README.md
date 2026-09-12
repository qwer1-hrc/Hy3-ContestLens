# Hy3-ContestLens

> 个人 / 活动作品（犀牛鸟开源实战任务——混元大语言模型项目），非腾讯官方发布。
> 模型能力通过 [Hy3](https://github.com/Tencent-Hunyuan/Hy3) 调用，不涉及模型训练或微调。

基于腾讯混元 Hy3 的**算法竞赛解题过程评估与错误定位系统**。面向 NOIP / CSP 等可验证场景（题目有标准答案、可自动判题），不只判断最终答案对错，而是对解题过程做完整评估：

- **过程正确性判定** —— 检查推理链条是否成立，识别跳步、循环论证、定理误用、条件遗漏、幻觉断言等问题
- **错误步骤定位** —— 解答出错时，定位错误最早出现的步骤（首错步骤）
- **错误类型归类** —— 17 类固定错误分类体系，见 [docs/ERROR_TAXONOMY.md](docs/ERROR_TAXONOMY.md)
- **"答案对但过程不成立"识别** —— 检出数值巧合、误用定理却蒙对结果等 `RESULT_CORRECT_PROCESS_INVALID` 样本

## 关键产出位置

| 产出 | 路径 |
| --- | --- |
| Demo 演示视频 | [`demo.mp4`](demo.mp4)（仓库根目录，展示一次完整的解题与过程评估流程） |
| 最终评测分析报告（PDF） | [`output/pdf/Hy3-ContestLens_评测分析报告.pdf`](output/pdf/Hy3-ContestLens_评测分析报告.pdf) |
| 分析结果与评测证据包 | [`outputs/evaluation_20260911/`](outputs/evaluation_20260911/)（详见下方说明） |

**核心结论速览**：63 道可自动评测题均形成闭环记录；86 条已完成运行中，答案准确率 74.4%、过程正确率 70.9%、严格成功率 59.3%；模型在普及档及以下保持稳定，省选/NOI− 档开始出现明显断崖。失败样本中"答案正确但过程无效"与"实现不一致"合计占 68.6%，说明主要矛盾在过程可支撑性而非最终答案。完整分析见报告。

## 工作原理

```
题面 → Solver 生成带步骤 ID / 依赖 / 不变量 / 复杂度 / C++ 代码的结构化解答
     → Algorithm Critic 与 Code Critic 并行盲审（互不可见对方意见）
     → Docker 沙盒中确定性编译与判题（CE/WA/TLE/MLE/RE/OLE）
     → 融合评审与判题证据：判定过程正确性、定位首错步骤、归类错误类型
     → 有界修复循环（最多 3–5 轮，携带失败尝试记忆，连续不改善时概率性重检）
```

难题（省选/NOI− 及以上）额外启用公开样例前置验证与小规模穷举差分比较。私有测试输入与标准答案全程不发给模型。详细设计见 [docs/EVALUATION_METHOD.md](docs/EVALUATION_METHOD.md) 与 [docs/WORKFLOW_CAPABILITIES.md](docs/WORKFLOW_CAPABILITIES.md)。

## 评测证据包说明（outputs/evaluation_20260911/）

| 文件 | 内容 |
| --- | --- |
| `Hy3-ContestLens_评测结果与人工抽检.xlsx` | 汇总、难度分层、运行明细、版本验证与可编辑人审台账 |
| `Hy3-ContestLens_评测分析报告.md` | 分析报告 Markdown 版（PDF 同内容） |
| `complete_run_results.csv` | 全部已完成工作流的运行级明细 |
| `problem_coverage.csv` | 按题目去重的历史覆盖情况 |
| `summary_metrics.json` | 机器可读汇总指标 |
| `validation_sample.csv/jsonl` | 10 条"先有问题、后严格成功"的配对验证样本 |
| `manual_audit_records.csv` | 人工抽检台账（含 AI 辅助预审，人审字段留待双人盲标） |

## 环境要求

- Python ≥ 3.11（推荐 conda 3.12，见 `environment.yml`）
- Docker Desktop（Linux containers，用于沙盒编译与判题；首次使用需构建镜像，见快速开始第 3 步）
- 有效的 Hy3 API Key

## 快速开始

```bash
# 1. 创建环境并安装
conda env create -f environment.yml
conda activate hy3-contestlens

# 2. 配置（密钥不入库，见 .gitignore）
cp configs/app.example.toml configs/app.toml
cp configs/secrets.local.toml.example configs/secrets.local.toml
# 在 secrets.local.toml 中填入 Hy3 API Key

# 3. 构建沙盒镜像（编译镜像 + 运行镜像，需先启动 Docker Desktop 的 Linux containers）
docker compose build

# 4. 启动服务（默认 http://127.0.0.1:8000，含 WebUI）
hy3-contest-api

# 5. CLI 解题与评估（solve / submit / compile / check / report 等子命令）
hy3-contest --help
```

`docker compose build` 会按 `docker-compose.yml` 构建 `hy3-contestlens-compile:local` 与 `hy3-contestlens-run:local` 两个镜像（Dockerfile 位于 `mcp_servers/judge/sandbox/`），所有代码编译与判题均在容器内执行，不依赖宿主编译器。若运行时报 `SANDBOX_UNAVAILABLE`，说明镜像未构建或 Docker 未启动，重新执行该命令即可。

更多调用方式（CLI / HTTP / MCP）见 `examples/`；API 与 CLI 完整参数见 `docs/API_REFERENCE.md`、`docs/CLI_REFERENCE.md`。

## 目录速览

- `src/hy3_contestlens/` — 应用源码：`workflow.py`（主流程）、`evaluation.py`（过程评估）、`judge.py`（确定性判题）、`model_diagnostics.py`（错误定位与分类）、`api/`（HTTP 服务）、`web/`（WebUI）
- `scripts/` — 题集导入（`import_noip2018.py`、`fetch_luogu_catalog.py`）、批量评测（`run_collection_campaign.py`）、评测包与报告生成（`build_evaluation_package.py`、`build_evaluation_report.py`）
- `configs/` — 应用、资源与密钥配置样例
- `docs/` — 评估方法、错误分类体系、API/CLI 参考、WebUI 指南等详细文档
- `tests/` — 287 项 Python 测试及前端测试
- `runs/` — 运行记录与 SQLite 数据库（本地生成，不入库）
- `outputs/`、`output/pdf/` — 评测证据包与正式 PDF 报告

## 许可

Apache-2.0，见 [LICENSE](LICENSE)。
