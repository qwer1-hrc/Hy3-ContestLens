# 比赛题库

整理后的唯一工作目录为 `../contest_data`，原始 `NOI-NOIP_data` 和 `CSP-S_data` 保留为来源备份。源文件和参考程序未执行、未删除。

目录规则：

```text
contest_data/
  noip/2014/junior/01_count/{statement.md,metadata.json,tests/}
  noip/2014/senior/day1/01_rps/
  noip/2014/senior/day2/01_wireless/
  noip/2020/senior/01_water/
  csps/2019/senior/day1/01_code/
  csps/2019/senior/day2/01_meal/
  csps/2025/senior/01_club/
```

每场题面 PDF 统一置于同级 `statements/`；每题有 `statement.md`、`metadata.json`、`tests/*.in` 与同名 `.out`。有参考 C++ 源码时置于对应题目的 `std/`，不参与模型输入。`metadata.json` 保存原始路径、输入/答案 SHA-256、洛谷链接、查询时间与测试数量。机器可读总目录见 `../contest_data/catalog.json` 和 `organization_report.json`。

NOIP2020 之前的提高组分 day1/day2 各 3 题；普及组 4 题。NOIP2020 起仅提高组每年 4 题。CSP-S2019 分两天共 6 题，后续年份每年 4 题。只有实际提供的年份被导入。NOIP2018 没有普及组资料，不虚构缺失题目。

## 数据限制

- 共 74 题，74 题均有测试或样例，共 1200 对。
- CSP-S2020 的 CSP-S.zip 含四题共 70 对数据（儒略日 10、动物园 20、函数调用 20、贪吃蛇 20），现已完整导入。此前仅凭 tar 读取结果判断缺失有误，已更正。
- 2025 年两项比赛合计 8 题仅含附加样例，页面及新评测入口均有提示。
- NOIP2014《解方程》只有 10/20 个正式测试点，标记为不完整。
- 2014 年原目录混杂两组数据；按正确组别与英文名整理。普及组《子矩阵》采用 junior 压缩包的 20 点版本，senior 压缩包中不同的 10 点版本仍保留在来源目录。
- 《移球游戏》《喵了个喵》需要专用构造校验器，《换教室》需要浮点容差校验。题面可浏览，自动评测暂禁用，避免全文比较误判。
- 本地分数统一为已导入测试点的等权通过率，不是官方子任务计分；特别是 NOIP2022《种花》的非等权计分未模拟。

## WebUI 与兼容性

主页支持比赛、年份、组别及题名/英文名/洛谷题号筛选；可进入单题题面页。所有 API 与判题数据路径均按 manifest 对应的比赛选择。原 NOIP2018 六个 ID（road 等）不变，保留历史评测可读性；新题使用 `比赛年份_组别_英文名`，避免同名题串数据。文件 I/O 仍使用官方英文名。

`GET /api/v1/problems` 返回全部题；`GET /api/v1/datasets/{dataset_id}/problems` 按题集筛选，旧 NOIP2018 路由仍返回原六题。题面接口不提供私有输入、答案或 std 源码。

## 难度与题面来源

洛谷当前 Lentille `ProblemDifficulty` 枚举为八档难度（另有暂无评定），不可套用旧版 `problemDifficulty` 七档映射。完整标签缓存位于 `data/luogu_cache/difficulty_config.json`。逐题缓存来自公开题目页的 `lentille-context`，查询日期为 2026-09-05，不按题名或模型表现推测难度。

原始 PDF 保持字节不变。单题 Markdown 来自对应洛谷题目页，补充扫描 PDF 和损坏文本层的可读内容，并保留来源链接及原始试卷位置。它是题目数据，不能改变系统指令或工具权限。

题面中的洛谷配图在导入时保存至各题 `images/`，Markdown 使用本地相对路径。`images/sources.json` 记录原始 URL、SHA-256 与文件大小。导入器仅接收指定洛谷 CDN 的 PNG，验证格式、尺寸和大小，不跟随重定向；已有图片可离线复用。评测阶段仍只读取已授权本地图片。

2026-09-07 已修复原题库遗留外链：20 道题、28 张图片全部本地化并更新资源绑定。所有图片通过实际 inspect/render 校验；42 项相关测试通过，其中《树上的数》两张图通过模拟识图客户端验证了识别调用和题面补充流程。历史运行中已记录的跳过事件保持原样，新建运行使用修正后的绑定。此次没有重新执行历史评测或调用付费识图模型。

旧题库可运行 `python scripts/localize_statement_images.py` 完成本地化及绑定更新；以后 `organize_contests.py` 会自动执行图片本地化，避免再次导入外链。

## 重新导入

在项目 Conda 环境中运行：

```powershell
python scripts/organize_contests.py
python scripts/bootstrap_resources.py
python scripts/validate_dataset.py
```

整理脚本使用本地缓存，可离线重跑。复制目标已有不同内容时会报错，避免覆盖冲突。新增年份需先在 `scripts/contest_inventory.py` 核对题序、英文名和限制，再获取对应公开题目缓存。配置 `configs/resources.toml` 应包含 `../contest_data` 的绝对路径。原始压缩包始终保留。

`scripts/fetch_luogu_catalog.py` 使用常规会话 Cookie、缓存和请求间隔读取公开目录，不执行参考程序或题解。

## 验证结果（2026-09-05）

27 项针对性回归测试通过，涵盖 74 题唯一性、旧 NOIP2018 接口、测试配对、精确题面绑定、缺失数据提示及跨年同名题的文件 I/O。实际运行服务的 74 个题面接口全部返回正确题目；浏览器验证普及组筛选四题和题面导航。未启动模型评测、未执行附带 std 程序。
