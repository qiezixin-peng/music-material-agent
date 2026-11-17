# 音乐物料 Agent

这是一个供 Agent 调用的本机持久化项目，不绑定任何聊天窗口。项目负责五平台音乐搜索、物料采集、任务留痕、断点续跑和全量交叉复核。

当前开发里程碑 `0.1.0` 已实现：

- 中央 SQLite 任务数据库（WAL 模式，可供多个 Agent 进程并发连接）。
- XLSX 检查和行级任务导入。
- 汽水、网易、QQ、酷狗、酷我五个平台分区。
- Agent 租约、心跳、分区冻结和不可变快照哈希。
- 环形全量复核及全互审计划。
- 逐行复核结论、修正补丁和 100% 覆盖率门槛。
- 每个任务独立的临时痕迹目录和追加式 `events.jsonl`。
- MCP 工具和本机健康检查入口。
- `collect_music_url` 可直接接收五平台歌手主页、歌曲详情页或专辑详情页；识别平台与对象 ID，优先走官方接口/客户端协议和原生解析，并保存页面正文、结构化 API 快照、SHA-256、最终 URL 和失败原因。它不是只接受表格的接口。
- 查询变体生成、跨字段候选评分和技术失败/未找到状态隔离。
- 候选验收与排序分离：平台 ID 必须符合原生格式，标题/歌手/版本须达到最低证据门槛；低分第一名只进入拒绝证据，不能写成找到。
- 搜索正常但只有低可信候选时返回 `NO_CONFIDENT_MATCH`；接口异常或非穷尽空结果才返回 `SEARCH_PARTIAL`，两者不再混为“失败”。
- `musicdl 2.14.2` 五平台适配器；保留平台原始字段且不记录临时音频 URL。
- 对 `musicdl` 内部吞掉的后端异常进行调用级截获，空结果不会掩盖接口故障。
- Scrapling 网页证据层：保存 HTML、哈希、OG/JSON-LD 元数据和自适应选择器数据库。
- 即时搜索逐次写入独立临时目录的 `search-events.jsonl`。
- Gandalf 可选锚点已接入 Windows 凭据管理器，所有候选固定标注 `anchor_only` 和非权威。
- 汽水使用无界面的 `soda-agent-helper`：直接读取已登录桌面客户端 Cookie，并在进程内调用客户端 `bdms.node` 完成签名；不启动浏览器、Web UI 或常驻服务。
- 已知汽水 SID 时优先走官方 ID 精确回查；关键词搜索当前每个变体读取上游第一页，这一边界会写入每次 attempt，不能据此宣称穷尽未找到。
- 汽水候选确定后会继续读取官方专辑详情页，保存 `release_date`、`release_company_raw` 和来源类型；客户端显示的 `©`/`℗` 权利主体不会被无依据改写为唱片厂牌。
- Gandalf 仅在原生结果低于置信度门槛时可选触发，最多提供待验证的平台 ID；每个 ID 必须回到平台官方接口解析成功，其记录名称永不覆盖平台原始字段。
- 音频先进入临时 staging，经文件签名、SHA-256、FFprobe 音频流和时长验真后才进入正式输出。
- 封面、头像和歌词下载带类型、大小和 HTML 错误页检查。
- `musicdl` 原始响应采用字段白名单提取；候选确定后，五个平台都会按各自 `album_id` 补查平台专辑详情。网易云、QQ、酷狗、酷我保存 `release_date`、`release_company_raw`、`release_company_kind` 与字段来源，汽水保存客户端专辑页脚对应字段。整份响应、签名参数、密钥形字段和短期 URL 不进入任务记录。
- 酷狗发行主体使用当前 KMR 专辑接口的 `publish_company`，发行日期使用同一响应的 `publish_date`；旧版专辑接口只作为日期降级来源。
- QQ 使用 `musicdl` 的官方加密搜索端点；即使后续音频解析失败，也会保留官方搜索元数据候选并明确标注 `metadata_only`，不会再把“音频暂不可取”误判为“歌曲不存在”。
- 原生接口直接返回的歌词文本可由 `save_verified_lyric_text` 验证后原子落盘；事件只记录路径、大小与 SHA-256，不复制整篇歌词。
- 最终 XLSX 同时导出源行、五平台原始字段、证据和逐行复核结论。
- 每个平台观察正式保存并导出 `song_url`、`album_url`、`artist_ids`、`artist_homepage_url(s)`；多人合唱不会只留下第一个歌手。酷狗歌曲只使用响应给出的 `EMixSongID`；专辑和歌手优先保存可被官方接口重新解析的数字路由，不拿文件 Hash 或不透明 Web ID 猜地址。
- `collect_platform_batch` 将表格映射、原生搜索、逐行留痕、断点续跑和分区冻结串成一个 Agent 批处理；常见中英文列名可自动识别，也支持任务级 `column_map`。
- 持久 Worker 在冻结分区前会对技术失败自动退避重试；重试后仍失败才以显式技术状态封存，绝不改写成未找到。
- QQ 官方加密搜索做请求节流，并对临时 `code=2001` 短退避重试；Gandalf 会话按采集进程复用，404 仅表示无锚点。
- `review_platform_batch` 对分配中的每一行独立重搜，不抽样；只有同平台 ID 复核一致才自动判定通过。
- `collect_materials_batch` 将封面、头像、歌词及其缺失/失败状态保存到独立物料清单，不修改已冻结搜索快照。
- `finalize_job` 严格检查采集、互审和物料覆盖率，随后输出最终 XLSX、SHA-256 与 `final-manifest.json`。

`musicdl 2.14.2` 始终是五平台主干后端；无界面汽水 helper 是针对客户端登录态、PC 签名搜索和音频下载的补强层，Scrapling 负责网页证据。三者的候选统一进入同一评分、留痕和复核流程。任何非穷尽空结果都不会被报告为平台不存在。

单链接入口供 Agent 直接调用：

- `collect_music_url(url)`：歌手主页、歌曲页、专辑页的统一入口。
- 歌曲页会先按平台 ID 调用原生详情解析；专辑页统一返回名称、封面、简介、曲目数、发行日期/主体和关联歌手；歌手页统一返回名称、头像、简介及平台实际提供的歌曲数、专辑数、MV 数、关注数等字段。缺失字段保持缺失，不跨平台猜填。
- 汽水公开 Web 有歌曲/专辑分享页；歌手主页是桌面客户端原生路由 `luna://luna.com/artist/<id>`，详情直接复用客户端 `GetArtistDetail` 的 `/luna/pc/artists/:id` 官方协议。

## 开发运行

```powershell
uv sync --extra dev --extra musicdl --extra adapters
./scripts/build_soda_helper.ps1
uv run music-material doctor
uv run pytest
uv run music-material-mcp
uv run music-material mcp-http --host 127.0.0.1 --port 8766
./scripts/start_shared_mcp.ps1
./scripts/status_shared_mcp.ps1
./scripts/stop_shared_mcp.ps1
```

## 默认目录

- 项目数据：`D:\三喜\Agent项目\音乐物料Agent\data`
- 任务临时痕迹：`D:\三喜\临时文件\音乐物料任务-<job_id>`
- 正式结果：`D:\三喜\歌曲物料下载\Agent任务\<job_id>`

账号、密码和 Cookie 不写入任务表、日志、MCP 参数或 Git。汽水 Cookie 仅通过 helper 的标准输入在内存中传递；签名身份写入被 Git 忽略的本机运行数据目录。正常工作流不需要扫码登录。

`mcp-http` 提供 `http://127.0.0.1:8766/mcp`，可由多个 Agent 窗口共享；stdio 模式保留给只支持本地进程型 MCP 的客户端。

`start_shared_mcp.ps1` 在隐藏进程中启动同一个 localhost MCP；状态记录在被 Git 忽略的 `data/runtime/shared-mcp.json`，标准输出和错误日志位于 `D:\三喜\临时文件\音乐物料MCP服务-共享`。停止脚本会核对 PID 的实际可执行文件，避免误停其他进程。

物料工具分为两类：`download_verified_asset` 处理封面、头像或远程歌词 URL；`save_verified_lyric_text` 处理 `musicdl` 候选中已经内联返回的歌词。两者都先写 staging，通过验证后才移动到正式任务输出。

推荐 Agent 工作流：`create_job` → 五个 Agent 分别循环调用 `collect_platform_batch` → `create_review_plan` → 各复核 Agent 循环调用 `review_platform_batch` → 各平台调用 `collect_materials_batch` → `finalize_job`。每个批处理都能从数据库断点续跑；默认一批 10 行，长表不需要绑定单个聊天窗口。

Agent 的硬性操作规则见 [AGENTS.md](AGENTS.md)，架构与可信度边界见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 已知外部条件

- `musicdl` 使用 PolyForm Noncommercial 许可证；若此项目用于商业活动，部署前必须单独确认授权或替换该后端。
- 当前机器已安装 FFmpeg 9.0.1，文件级验真已启用。
- Gandalf 不进入常规真值链；即使以后接入，也只在候选不确定时按需锚定。
- 五平台音频均走无界面链路：汽水使用客户端 helper，其余平台由 `musicdl` 在同一进程内解析短期地址并直接下载；短期地址不写入数据库、日志或下载清单，也不会启动旧 Web/UI 服务。
