# 排障

- `/readyz` 显示 `HY3_NOT_CONFIGURED`：复制 secrets 示例并填写非空 Key、endpoint 和 model，或设置 `HY3_API_KEY/HY3_BASE_URL/HY3_MODEL`。
- `SANDBOX_UNAVAILABLE`：启动 Docker Desktop 的 Linux containers，再执行 `docker compose build`。不要改用宿主 `g++`。
- `PRIVATE_DATASET_NOT_IMPORTED`：执行 `python scripts/import_noip2018.py`，再运行校验脚本。
- `USER_CONFIRMATION_REQUIRED`：在资源页检查候选的 PDF 页段、配对数和风险后手动 binding。
- `RESOURCE_CHANGED`：题面或测试文件在 binding 后变化，重新发现并确认。
- `STALE_REVISION`：patch 的 `base_sha256` 过期，显式读取目标 revision 后重试。

