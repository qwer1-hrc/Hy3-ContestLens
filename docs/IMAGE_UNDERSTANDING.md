# 可选题面图片理解

主模型仍只接收文字。工作流在题面分析前检查绑定范围内的 PDF/Markdown；检测到位图、矢量图形或文字较少的 PDF 页面时，可让独立的多模态模型描述图形，再将文字补充交给 Hy3。该检测是保守的启发式检查，表格边框、装饰线或空白页也可能触发提示，不代表一定缺少题意。

## 配置

在项目环境中安装可选的渲染依赖：

```powershell
conda activate hy3-contestlens
python -m pip install -e ".[vision]"
```

在现有 `configs/secrets.local.toml` 中添加独立配置（不要覆盖原有 `[hy3]` 等配置）：

```toml
[image_understanding]
api_key = "你的图片模型密钥"
base_url = "https://api.moonshot.cn/v1"
model = "kimi-k3"
```

`base_url` 支持 `/v1` 根地址或完整 `/chat/completions` 地址。模型名可以改成服务商提供的其他视觉模型。接口使用 `messages[].content` 数组中的 `image_url`，图片以 PNG base64 发送，符合 [Kimi 官方视觉接口文档](https://platform.kimi.com/docs/guide/use-kimi-vision-model)。不向图片模型发送 Hy3 的推理级别、温度、JSON Schema 等参数，避免模型间参数要求不兼容。

连接与预算均可使用 `HY3_IMAGE_` 前缀的环境变量，例如 `HY3_IMAGE_API_KEY`、`HY3_IMAGE_BASE_URL`、`HY3_IMAGE_MODEL`、`HY3_IMAGE_MAX_TOKENS`。优先级为非空本地 secrets 配置、非空 app 配置、环境变量、默认值。配置变更后重启服务。

默认预算位于 `configs/app.toml` 的 `[image_understanding]`：

| 参数 | 默认值 | 用途 |
| --- | --- | --- |
| `max_tokens` | 8192 | 每张图片的输出 token 上限 |
| `timeout_seconds` | 90 | 每张图片的调用总超时；失败不自动重试 |
| `decision_timeout_seconds` | 300 | WebUI 等待选择的最长秒数；到期自动跳过 |
| `max_images` | 12 | 每次运行处理的最多页面/图片数，超出部分记为警告 |
| `max_image_mb` | 8 | 单张本地图片及渲染后 PNG 大小上限 |
| `max_image_side` | 2400 | 渲染/缩放后的最长边像素数 |
| `max_output_chars` | 16000 | 单张图片文字描述长度上限 |

此配置不继承主模型或报告翻译模型的密钥、地址和参数。没有图片密钥、不完整配置或无效的图片预算会禁用此功能，不改变主模型及 `/readyz` 的就绪条件。未安装可选依赖也不影响启动或原流程；选择识别后若依赖缺失，会记录警告并继续。

## WebUI 操作

1. 从“新评测”创建运行。页面使用 `ask` 策略。
2. 若已配置图片模型并检测到可处理图片，“工作流进度”中会出现“等待图片理解选择”，并自动展开。
3. 点击“使用更精确的图片理解”，将检测到的页面/图片和最多 12000 字符的题面上下文发给独立模型，可能产生额外费用。点击“不使用，继续运行”则立即恢复原流程。
4. 超过等待时间未选择，自动跳过。页面刷新后仍可操作当前选择。重复点击不重复执行，旧运行阶段的选择不能覆盖新阶段。
5. “图片描述已生成”步骤可展开查看转写内容。成功描述会补入题面；部分或全部图片失败时保留警告，不导致整个工作流失败。等待或识别期间均可取消运行，取消后不再启动 Hy3 分析。

## 支持范围与安全边界

- PDF 只检查已绑定的页码范围；对选中的页面整页渲染，保留嵌入图片、矢量连线、箭头、布局和可见文字，不仅提取 OCR。渲染使用 [PDFium 页面接口](https://pypdfium2.readthedocs.io/en/stable/python_api.html)，并加锁防止并行运行时线程冲突。
- Markdown 支持行内图片、引用式图片和 HTML `img`，仅处理绑定行范围内的图片引用。支持已授权目录中的 PNG/JPEG/WebP/GIF/BMP；动图仅处理首帧。禁止远程 URL、绝对路径、父目录跳转、符号链接以及超出授权范围的文件，不自动联网下载图片。SVG 等不支持的引用会记录警告。需要使用远程图片时，先将图片保存到授权目录并改为相对路径。
- 只授权单个 Markdown 文件时，不会自动扩大到它的同目录图片；如需处理这些图片，应授权包含题面和图片的目录。
- 图片模型只转写题面事实、节点/边/方向/权值/几何关系，标记无法辨认的细节，不求解题目。模型仍可能误识别，转写并不保证完全正确。
- 原题面与图片描述始终作为 `untrusted_problem_content`，不能成为系统指令、改变工具权限或要求读取测试答案。原题面 SHA-256 不变，图片补充有单独的哈希；发送给 Hy3 的内容不包含图片 base64。

每次运行保留 `problem_document.json`（实际传给 Hy3 分析的题面）、`image_understanding.json`（选择、来源、转写与警告）和 `problem_spec.json`（保留视觉来源证据供求解/审查/修复使用）。图片文件字节与密钥不写入事件或这些记录。

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
