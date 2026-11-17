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
- 查询变体生成、跨字段候选评分和技术失败/未找到状态隔离。
- `musicdl 2.14.2` 五平台适配器；保留平台原始字段且不记录临时音频 URL。
- 对 `musicdl` 内部吞掉的后端异常进行调用级截获，空结果不会掩盖接口故障。
- Scrapling 网页证据层：保存 HTML、哈希、OG/JSON-LD 元数据和自适应选择器数据库。
- 即时搜索逐次写入独立临时目录的 `search-events.jsonl`。
- Gandalf 可选锚点已接入 Windows 凭据管理器，所有候选固定标注 `anchor_only` 和非权威。
- 本机 `go-music-dl` 汽水后端可按需隐藏启动，并检测汽水官方客户端本地签名器。
- 音频先进入临时 staging，经文件签名、SHA-256、FFprobe 音频流和时长验真后才进入正式输出。
- 封面、头像和歌词下载带类型、大小和 HTML 错误页检查。
- 最终 XLSX 同时导出源行、五平台原始字段、证据和逐行复核结论。

后续里程碑将继续完善汽水登录态、五平台头像来源策略、后台批量工作队列和自动回填导出。

## 开发运行

```powershell
uv sync --extra dev --extra musicdl --extra adapters
uv run music-material doctor
uv run pytest
uv run music-material-mcp
uv run music-material mcp-http --host 127.0.0.1 --port 8766
```

## 默认目录

- 项目数据：`D:\三喜\Agent项目\音乐物料Agent\data`
- 任务临时痕迹：`D:\三喜\临时文件\音乐物料任务-<job_id>`
- 正式结果：`D:\三喜\歌曲物料下载\Agent任务\<job_id>`

账号、密码和 Cookie 不写入任务表、日志或 MCP 参数；后续凭据层使用 Windows 凭据管理器。

`mcp-http` 提供 `http://127.0.0.1:8766/mcp`，可由多个 Agent 窗口共享；stdio 模式保留给只支持本地进程型 MCP 的客户端。

Agent 的硬性操作规则见 [AGENTS.md](AGENTS.md)，架构与可信度边界见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 已知外部条件

- `musicdl` 使用 PolyForm Noncommercial 许可证；若此项目用于商业活动，部署前必须单独确认授权或替换该后端。
- 当前机器已安装 FFmpeg 9.0.1，文件级验真已启用。
- Gandalf 不进入常规真值链；即使以后接入，也只在候选不确定时按需锚定。
