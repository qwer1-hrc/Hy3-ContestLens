# MCP 测试说明

Resources、Workspace、Judge Server 分别从 `mcp_servers.<name>.server` 启动，stdout 仅用于 MCP stdio 帧，应用日志必须写 stderr。建议在 MCP Inspector 中依次：

1. Resources：`inspect_path_scope`、`find_problem_assets`、`read_problem_document`、`inspect_test_dataset`；确认结果无答案内容与宿主绝对路径；
2. Workspace：创建 `r000`，读取显式 revision，使用正确与错误 `base_sha256` patch，冻结 revision；
3. Judge：用冻结 artifact 调 `compile_cpp`，成功后调用 `check_answer`；确认 Docker 不可用时返回 `SANDBOX_UNAVAILABLE`。

必须额外验证绝对路径、`..`、UNC、盘符跳转、非 `.cpp`、过期哈希、未冻结源码和任意答案路径均被拒绝。

