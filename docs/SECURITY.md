# 安全模型

- WebUI/API 的绝对路径处理位于本地受信任边界；Agent/MCP 只使用 scope ID 和相对路径；
- 拒绝 `..`、绝对路径、UNC、盘符切换、符号链接、junction 和 reparse point；
- 文档是 untrusted content，不执行代码块、HTML、脚本、链接或其中的命令；
- Workspace 只操作 run 内单个 `.cpp`，revision 原子写入、只增不改、哈希锁定；
- Judge 不接受源码/答案路径，未知程序只在 Linux Docker 内运行；
- Docker 禁网、只读 rootfs、非 root、drop capabilities、限制 PID/CPU/内存/输出；
- secrets 文件与私有数据不进 Git、日志、模型 prompt 或报告。

请勿把 Docker socket 挂入 Judge 容器。远程部署必须关闭任意本地路径授权，并在 REST 前增加认证。

