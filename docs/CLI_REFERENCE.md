# CLI 参考

`hy3-contest --help` 显示完整参数。所有命令默认读取 `configs/app.toml` 中的 `host` 和 `port` 组成服务地址（未配置时为 `http://127.0.0.1:8000`），可用 `--base-url` 覆盖；`--json` 保证 stdout 是可解析 JSON，错误写入 stderr 并返回退出码 2。

资源子命令包括 `validate`、`grant`、`discover`、`bind`；核心命令包括 `solve`、`submit`、`compile`、`check`、`watch`、`report`。CLI 不读取标准答案，也不输出 Hy3 API Key。
