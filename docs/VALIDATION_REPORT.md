# 验证报告

最近执行日期：2026-08-25。

已完成：

- `pytest`：30 项通过；唯一提示是 FastAPI/Starlette TestClient 对当前 httpx 适配层的弃用预告，不影响结果；
- 私有数据导入：120/120 配对，road/money/track/travel/game/defense 分别为 10/20/20/25/20/25，源文件与导入文件 SHA-256 一致；
- 资源发现：六题均为唯一 `AUTO_BINDABLE`，PDF 页段为 2、3-4、5-7、2-3、4-6、7-8；
- MCP `tools/list`：Resources 7 项、Workspace 7 项、Judge 6 项；
- REST/OpenAPI：健康、readiness、资源、run、revision、judge、benchmark、annotation 合约已生成快照；
- WebUI：浏览器验证首页、资源自动发现、创建 run、事件时间线、配置失败报告；控制台无错误；
- Python 依赖：`pip check` 无冲突；Docker Compose 配置可解析；
- Conda 运行环境：`hy3-contestlens` 已创建，解释器为 `E:\Anaconda\envs\hy3-contestlens\python.exe`；项目包、CLI、数据校验和完整测试均已在该环境中验证；
- 安全：过期 revision、路径逃逸、文件型 scope、提示注入包装、沙盒不可用拒绝降级均有自动测试。

尚未完成且不伪造：

- Hy3 endpoint/API Key 未配置，因此真实 Solver/Critic/Repair Agent 实验状态为 `NOT_RUN`；
- Docker daemon 未启动，两张 Linux 镜像尚未构建，Linux `wait4/rusage` runner 不能由 Windows 宿主编译器验证；
- 六题洛谷难度待用户提供，难度分层状态为 `DIFFICULTY_DATA_INCOMPLETE`；
- 72 条过程案例已生成覆盖模板，但需真实 Hy3 输出、Docker 判题与双人盲标后才能计算定位准确率和误报率；
- demo 视频/GIF 按编码计划延期，最终活动提交前补充。
