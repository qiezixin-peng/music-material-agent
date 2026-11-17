# 架构与数据可信度

```text
XLSX -> durable job/rows -> five leased platform partitions
                           | musicdl candidate discovery
                           | soda-agent-helper signed fallback
                           | Scrapling page evidence
                           | Gandalf optional anchor
                           v
                     immutable snapshots
                           v
                 ring/all-to-all full review
                           v
                  verified files + final XLSX
```

SQLite 使用 WAL 模式，保存任务状态、租约、观测、不可变快照哈希、复核 verdict 和事件索引。大体积 HTML、网络响应、调试输出及中间下载位于每个任务独立的临时痕迹目录。

高层批处理把这些状态机暴露给 Agent：采集批处理按平台领取租约并逐行提交，任何时刻崩溃都只需继续未记录行；复核批处理重新检索来源平台的每一行，并将 verdict/patch 追加到冻结快照之外；物料批处理使用独立 `materials` 表保存文件状态；最终收口只有在要求的覆盖率全部满足时才生成带哈希的正式结果。

可信度按来源分层：平台原生响应/页面是事实来源；`musicdl 2.14.2` 是五平台主干候选与下载后端；`soda-agent-helper` 是汽水的无界面签名搜索和下载补强，不提供 Web UI 或监听端口；Scrapling 是页面结构变化时的自适应证据层；Gandalf 只提供候选关系。跨平台匹配由标题、艺人、专辑、时长和版本词共同评分，不能仅靠名称相等。

当输入含平台 ID 时，协调器跳过模糊关键词并优先执行平台官方精确回查；汽水 SID 和 QQ MID 已有真实官方回查链路。Gandalf 只在原生候选低于置信度门槛时产生待验证 ID；这些 ID 必须经官方回查后才能进入候选，Gandalf 名称仅作为 `anchor_evidence` 留痕。汽水关键词接口当前每个查询变体只扫描第一页，因此始终保持 `exhaustive=false` 并写入分页限制警告。

下载采用两阶段提交：后端引用解析出的短期地址不进入任务日志，内容先写临时 staging；文件签名、SHA-256、FFprobe 音频流和时长通过后才原子移动到正式输出。封面、头像和歌词使用独立的类型与大小验证。

元数据补充不保存 `musicdl.raw_data`。适配器只读取经过审计的平台字段路径，将日期归一化为 `YYYY-MM-DD`，并在 `metadata_evidence` 中记录具体来源路径。当前已实测网易云 `publishTime`、QQ `time_public` 与歌手 MID、酷狗 `PublishDate` 与歌手头像模板、酷我 `web_artistpic_short`。QQ 搜索采用 `musicdl` 构造并签名的官方加密端点；若下载解析失败，搜索元数据仍以 `metadata_only` 候选保留。内联歌词通过独立 MCP 工具写入 UTF-8 文件，留痕仅含完整性元数据。

状态机把“事实不存在”和“技术上没拿到”分开。`AUTH_REQUIRED`、`RATE_LIMITED`、`SEARCH_UNAVAILABLE`、`SEARCH_PARTIAL`、`FOUND_CANDIDATES` 均不能降级为 `NOT_FOUND_EXHAUSTIVE`。

音频自动化遵守无 UI 约束：汽水通过 headless helper 下载；其他平台在专用无界面下载链完成前不会回退启动旧 Web 服务，而是写入 `HEADLESS_BACKEND_REQUIRED`。这不会影响搜索元数据、封面、头像和歌词的采集与审计。
