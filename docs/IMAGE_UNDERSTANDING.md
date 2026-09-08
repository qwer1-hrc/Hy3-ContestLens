# 可选题面图片理解

主模型仍只接收文字。工作流在题面分析前检查绑定范围内的 PDF/Markdown；检测到实际嵌入图片、PDF inline image 或文字较少的扫描页时，可让独立的多模态模型描述图形，再将文字补充交给 Hy3。检测器会忽略 PDF 的纯矢量绘制操作，因为同一类操作也用于页眉横线、项目符号和表格边框；纯矢量且没有嵌入图片的示意图不会自动触发提示。

## 配置

在项目环境中安装可选的渲染依赖：

```powershell
conda activate hy3-contestlens
python -m pip install -e ".[dev,vision]"
```

在现有 `configs/secrets.local.toml` 中添加独立配置（不要覆盖原有 `[hy3]` 等配置）：

```toml
[image_understanding]
api_key = "你的图片模型密钥"
base_url = "https://api.moonshot.cn/v1"
model = "kimi-k3"
stream = true
```

`base_url` 支持 `/v1` 根地址或完整 `/chat/completions` 地址。模型名可以改成服务商提供的其他视觉模型。接口使用 `messages[].content` 数组中的 `image_url`，图片以 PNG base64 发送，符合 [Kimi 官方视觉接口文档](https://platform.kimi.com/docs/guide/use-kimi-vision-model)。不向图片模型发送 Hy3 的推理级别、温度、JSON Schema 等参数，避免模型间参数要求不兼容。

连接与预算均可使用 `HY3_IMAGE_` 前缀的环境变量，例如 `HY3_IMAGE_API_KEY`、`HY3_IMAGE_BASE_URL`、`HY3_IMAGE_MODEL`、`HY3_IMAGE_MAX_TOKENS`。优先级为非空本地 secrets 配置、非空 app 配置、环境变量、默认值。配置变更后重启服务。

默认预算位于 `configs/app.toml` 的 `[image_understanding]`：

| 参数 | 默认值 | 用途 |
| --- | --- | --- |
| `max_tokens` | 8192 | 每张图片的输出 token 上限 |
| `stream` | true | 逐段接收长响应，降低中间代理等待完整结果时断连的风险 |
| `max_attempts` | 3 | 对超时、连接中断、不完整流、HTTP 408/5xx、节点过载和账户速率限制的总尝试次数 |
| `retry_backoff_seconds` | 10 | 无 Retry-After 时的指数退避起始秒数 |
| `timeout_seconds` | 300 | 每次图片调用的总时限，包含连接、推理和正文生成 |
| `idle_timeout_seconds` | 90 | 单次连接/读写等待时限；持续返回流式数据时不以此截断总生成时间 |
| `decision_timeout_seconds` | 300 | WebUI 等待选择的最长秒数；到期自动跳过 |
| `max_images` | 12 | 每次运行处理的最多页面/图片数，超出部分记为警告 |
| `max_image_mb` | 8 | 单张本地图片及渲染后 PNG 大小上限 |
| `max_image_side` | 2400 | 渲染/缩放后的最长边像素数 |
| `max_output_chars` | 16000 | 单张图片文字描述长度上限 |

此配置不继承主模型或报告翻译模型的密钥、地址和参数。图片调用使用 Kimi 当前推荐的 `max_completion_tokens`；默认以 SSE 流式接收，完成后才接受并注入描述。没有图片密钥、不完整配置或无效的图片预算会禁用此功能，不改变主模型及 `/readyz` 的就绪条件。项目的 Conda 安装脚本会安装 `vision` 依赖；旧环境若缺少依赖，运行会直接显示缺少的组件并继续原流程，不会出现已经确认使用后才逐张失败的假象。

## WebUI 操作

1. 从“新评测”创建运行。页面使用 `ask` 策略。
2. 若已配置图片模型并检测到可处理图片，“工作流进度”中会出现“等待图片理解选择”，并自动展开。
3. 点击“使用更精确的图片理解”，将检测到的页面/图片和最多 12000 字符的题面上下文发给独立模型，可能产生额外费用。点击“不使用，继续运行”则立即恢复原流程。
4. 超过等待时间未选择，自动跳过。页面刷新后仍可操作当前选择。重复点击不重复执行，旧运行阶段的选择不能覆盖新阶段。
5. “图片描述已生成”步骤可展开查看转写内容。成功描述会补入题面；部分或全部图片失败时保留警告，不导致整个工作流失败。等待或识别期间均可取消运行，取消后不再启动 Hy3 分析。

## 支持范围与安全边界

- PDF 只检查已绑定的页码范围；严格检测实际图片对象和文字较少的扫描页，不把页眉横线、项目符号、表格边框等纯矢量排版当作图片。对选中的页面仍会整页渲染，保留嵌入图片、矢量连线、箭头、布局和可见文字。渲染使用 [PDFium 页面接口](https://pypdfium2.readthedocs.io/en/stable/python_api.html)，并加锁防止并行运行时线程冲突。
- Markdown 支持行内图片、引用式图片和 HTML `img`，仅处理绑定行范围内的图片引用。支持已授权目录中的 PNG/JPEG/WebP/GIF/BMP；动图仅处理首帧。禁止远程 URL、绝对路径、父目录跳转、符号链接以及超出授权范围的文件，不自动联网下载图片。SVG 等不支持的引用会记录警告。需要使用远程图片时，先将图片保存到授权目录并改为相对路径。
- 只授权单个 Markdown 文件时，不会自动扩大到它的同目录图片；如需处理这些图片，应授权包含题面和图片的目录。
- 图片模型只转写题面事实、节点/边/方向/权值/几何关系，标记无法辨认的细节，不求解题目。模型仍可能误识别，转写并不保证完全正确。
- 原题面与图片描述始终作为 `untrusted_problem_content`，不能成为系统指令、改变工具权限或要求读取测试答案。原题面 SHA-256 不变，图片补充有单独的哈希；发送给 Hy3 的内容不包含图片 base64。

每次运行保留 `problem_document.json`（实际传给 Hy3 分析的题面）、`image_understanding.json`（选择、来源、转写与警告）和 `problem_spec.json`（保留视觉来源证据供求解/审查/修复使用）。图片文件字节与密钥不写入事件或这些记录。

### 后台显示 token 消耗，但页面提示连接或响应失败

计费和客户端验收发生在不同位置。模型服务收到图片后即可计算输入 token，并可能已经生成输出 token；客户端仍须从网络链路收到完整响应、确认正常结束并通过内容检查，才会将描述注入题面。服务端已计费但响应随后被代理/网关关闭、超时、截断、返回错误状态或缺少结束标记时，页面显示失败是正确行为，不能把残缺描述当作题面事实。

2026-09-03 的运行 `run_uLeTlTTM3DRKpGE` 在约 65.7 秒内顺序处理两页，每页约 32.8 秒后失败，后台可看到 token；旧实现既使用非流式请求，又只保存通用的 `IMAGE_MODEL_FAILED`，因此无法从该历史记录还原 HTTP 状态和具体异常。请求环境使用本机 7897 代理，时间模式与此前约 31 秒的长响应断连相近，但没有代理或上游日志就不能断言具体是哪一端关闭连接。

图片模型现默认使用 Kimi 官方支持的 SSE 流式响应和 `max_completion_tokens`。每张图完成后保存 HTTP 状态、请求 ID、耗时、结束原因、用量及流式事件统计；失败时保存安全的异常类型和断流统计，不保存图片字节、密钥或推理正文。一次成功描述只有在完整结束后才注入；失败图片仍按可选功能约定回退到原题面。

2026-09-04 的运行 `run_qxQfPoATbSbGvKVI` 两页分别收到 HTTP 429。用户授权的单页复现返回 `engine_overloaded_error`，约 1.45 秒获得响应，未提供 Retry-After；余额由用户确认正常。这表示 Kimi 计算节点临时过载，不是图片检测、渲染、余额或项目内图片并发问题。客户端将所有图片调用串行化，对 `engine_overloaded_error` 和 `rate_limit_reached_error` 有界重试并遵守合法 Retry-After；多次限流失败后跳过剩余图片，避免连续触发同一限流。余额不足类 429 不重试。

2026-09-08 的 `run_sSpjunh6rWVHSBmV` 第二张图收到 HTTP 200 和 3200 个事件，但只有推理内容、正文为零；90.047 秒时被本地总时限终止。旧重试策略没有包含超时，因此尝试一次便回退。现将默认总时限增加到 300 秒，并独立保留 90 秒连接/读写等待时限；超时、传输中断、不完整流及 HTTP 408/5xx 也会有界重试，最多 3 次。不会接受未正常结束的部分正文。显式配置的 `timeout_seconds` 仍表示总时限，secrets 中的旧配置会优先于 app 配置。诊断区分 `total_timeout`、`transport_timeout`、`transport_error`，记录最终尝试耗时和含重试等待的 `total_duration_ms`。旧运行保留原始警告，不因代码更新而重写历史证据。

## API

创建运行时可以传入 `"image_understanding": "ask" | "use" | "skip"`：

- `ask`：检测到可处理图片且模型已配置时等待选择。
- `use`：直接尝试理解，无需 WebUI 操作。
- `skip`：不调用图片模型。REST/CLI/批量运行默认为此值，已有客户端不会因等待操作而停住。

`GET /api/v1/runs/{run_id}` 的 `image_understanding` 字段包含当前状态和选择标识；刷新/轮询只读取状态，不调用模型。等待时提交：

```http
POST /api/v1/runs/{run_id}/image-understanding
Content-Type: application/json

{"request_id":"从运行状态获取的 image_choice 标识","choice":"use"}
```

`choice` 也可为 `skip`。无效、过期、取消后或与已确认选择冲突的请求返回 409；同一选择重复提交为幂等操作。配置是否可用可从 `GET /api/v1/system/capabilities` 的 `image_understanding` 字段读取，接口不暴露密钥。
