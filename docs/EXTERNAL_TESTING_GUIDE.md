# 外部接口验收指南

执行 `scripts/smoke_api.ps1` 检查健康、能力和六题元数据。完整流程使用 WebUI 或 REST 完成资源发现、binding、run、事件游标、判题、修复与报告，再用 CLI 查询同一 `run_id`，核对 revision、源码哈希、compile artifact 和 check ID 一致。

环境未配置时，`/healthz` 仍返回 200；`/readyz` 返回 503 并按 SQLite、Hy3、MCP、Docker、私有数据逐组件说明。基础设施失败不是模型失败，不计入准确率。

