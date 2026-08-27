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

