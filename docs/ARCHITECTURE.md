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

单对象入口与表格任务共用同一可信度边界。`collect_music_url` 先把 URL 解析为 `(platform, entity_type, platform_id)`；歌曲优先走原生 ID 详情，专辑优先走原生专辑详情，原始 HTTP 页面再由 Scrapling 保存响应、最终 URL、哈希与结构化元数据。歌手页至少保留平台 ID 与页面快照；汽水歌手页使用已安装桌面客户端实际注册的 `luna://luna.com/artist/<id>` 路由，因为该平台没有等价的公开 Web 歌手页。

当输入含平台 ID 时，协调器跳过模糊关键词并优先执行平台官方精确回查；汽水 SID、网易云歌曲 ID、QQ MID、酷狗 Hash 和酷我歌曲 ID 均已有真实官方回查链路。Gandalf 只在原生候选低于置信度门槛时产生待验证 ID；这些 ID 必须经官方回查后才能进入候选，Gandalf 名称仅作为 `anchor_evidence` 留痕。汽水关键词接口当前每个查询变体只扫描第一页，因此始终保持 `exhaustive=false` 并写入分页限制警告。

下载采用两阶段提交：后端引用解析出的短期地址不进入任务日志，内容先写临时 staging；文件签名、SHA-256、FFprobe 音频流和时长通过后才原子移动到正式输出。封面、头像和歌词使用独立的类型与大小验证。

元数据补充不保存 `musicdl.raw_data`。适配器只读取经过审计的平台字段路径，将日期归一化为 `YYYY-MM-DD`，并在 `metadata_evidence` 中记录具体来源路径。搜索层保留网易云 `publishTime`、QQ `time_public`、酷狗 `PublishDate` 等初始证据；匹配完成后再用候选自己的 `album_id` 读取该平台专辑详情。网易云、QQ、酷我读取专辑 `publish_time` 与 `company`；酷狗读取签名 KMR 专辑响应的 `publish_date` 与 `publish_company`，旧专辑接口作为日期降级来源。QQ 搜索采用 `musicdl` 构造并签名的官方加密端点；若下载解析失败，搜索元数据仍以 `metadata_only` 候选保留。内联歌词通过独立 MCP 工具写入 UTF-8 文件，留痕仅含完整性元数据。

稳定对象链接是 observation 的正式字段，不藏在原始响应里。网易云使用数字 ID 路由，QQ 使用当前 `ryqq_v2` MID 路由，酷我使用数字详情路由；酷狗网页歌曲和专辑分别必须有响应中的 `EMixSongID`、`EAlbumID` 才生成链接，文件 Hash 仅用于歌曲 API 身份，不能拼成网页地址。汽水 helper 从官方 track 响应保留歌曲、专辑和全部歌手 ID，并输出 Web 分享页或客户端深链。最终 XLSX 同步导出这些字段。

汽水搜索卡片不包含完整发行信息。匹配候选确定后，适配器以歌曲返回的 `album_id` 读取官方专辑详情，提取 `release_date` 与客户端页脚中的 `©`/`℗` 权利主体，分别保存为 `release_date`、`release_company_raw`、`release_company_kind`，并写入 `metadata_evidence`。权利主体和唱片厂牌在语义上不强行等同。

候选排序只负责召回顺序，不等于验收。进入 observation 前还必须通过平台 ID 形状、标题相似度、歌手相似度和版本兼容性门槛；未通过的候选保留在 `rejected_candidate_summaries`，不会占用结果字段。Gandalf 的 ID 先做平台格式校验，再做官方详情回查；其会话在单个采集进程内复用，避免逐查询登录和过度调用。

`NO_CONFIDENT_MATCH` 表示平台原生搜索成功，但返回项均未通过证据门槛；它不是“平台已穷尽不存在”。`SEARCH_PARTIAL` 专用于接口错误、限流或非穷尽空响应，并在持久 Worker 冻结前自动退避重试。

状态机把“事实不存在”和“技术上没拿到”分开。`AUTH_REQUIRED`、`RATE_LIMITED`、`SEARCH_UNAVAILABLE`、`SEARCH_PARTIAL`、`FOUND_CANDIDATES` 均不能降级为 `NOT_FOUND_EXHAUSTIVE`。

音频自动化遵守无 UI 约束：汽水通过 headless helper 下载；网易云、QQ、酷狗、酷我通过 `musicdl` 的 Python 后端在单一进程内重新取得精确 ID 的短期音频引用，直接写入 staging。项目不调用 `musicdl` 旧 Web 服务，也不保存其包含短期 URL 的下载结果 pickle；文件必须通过签名、FFprobe、时长和 SHA-256 验证后才进入正式目录。
