# MCP Inspector 示例

将 `configs/mcp/*.json` 的 `<PROJECT_ROOT>` 替换为项目绝对路径，分别连接 Resources、Workspace 和 Judge Server。先调用 Resources 的 `find_problem_assets`，再通过 REST/WebUI 确认 binding；Workspace 创建并冻结 revision 后，Judge 才接受编译。

