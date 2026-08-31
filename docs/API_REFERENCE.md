# REST API 参考

基础地址为 `http://127.0.0.1:8000`，版本化业务接口使用 `/api/v1`。完整机器可读 Schema 由 `/openapi.json` 提供。

核心链路：

1. `POST /api/v1/resource-scopes:validate` 只校验绝对路径；
2. `POST /api/v1/resource-scopes` 在本地模式显式授权；
3. `POST /api/v1/resource-scopes/{scope_id}:discover` 返回候选、证据、置信度和配对统计；
4. `POST /api/v1/problems/{problem_id}/resource-binding` 冻结候选；
5. `POST /api/v1/runs` 创建 run，`POST /runs/{id}/start` 异步启动；
6. `GET /runs/{id}/events?after_seq=N` 断点轮询，或读取 NDJSON stream；
7. `GET /runs/{id}/result` 获取初始、逐轮、最佳与最终结果。

Workspace API 仅使用 `run_id + submission_id + revision_id`，不接受源码绝对路径。Judge compile 仅接受冻结 `source_artifact_id`，check-answer 仅接受 `compile_artifact_id + dataset_id + problem_id`。错误响应固定含 `error_code`、`message`、`details`。

中文报告与解题工作流相互独立：

- `GET /api/v1/runs/{run_id}/report` 返回 HTML 报告，复用已有中文正文；`?priority=true` 用于从当前运行进入并返回该运行详情。GET 本身不调用模型，浏览器加载报告专用脚本后才提交启动请求。
- `POST /api/v1/report-translations` 接收 `{}`，按运行目录名称升序排队；可选 `priority_run_id` 将当前报告提升至待处理队首，`retry_run_id` 显式重试此前失败的翻译。已中文化报告不重复处理，空报告与进行中的运行不会排队。
- `GET /api/v1/report-translations` 只读取状态，返回 `active_run_id`、`busy` 和每个 run 的 `status`、`processed`、`position`、分段进度与更新时间；不会启动或恢复翻译。

缓存位于各运行的 `report_translation/zh-CN.json`，带原文指纹和完成标记。失败与中断只影响中文展示，不会修改原始评测结果或解题事件。

## 可选图片理解

创建运行新增 `image_understanding: "ask" | "use" | "skip"`，默认 `skip`。WebUI 使用 `ask`；等待时可向 `POST /api/v1/runs/{run_id}/image-understanding` 提交 `{"request_id":"...","choice":"use"}` 或 `skip`。`GET /api/v1/runs/{run_id}` 返回持久化的图片理解状态和选择标识。详见 [图片理解 API 与回退规则](IMAGE_UNDERSTANDING.md)。
