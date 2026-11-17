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

可信度按来源分层：平台原生响应/页面是事实来源；`musicdl 2.14.2` 是五平台主干候选与下载后端；`soda-agent-helper` 是汽水的无界面签名搜索和下载补强，不提供 Web UI 或监听端口；Scrapling 是页面结构变化时的自适应证据层；Gandalf 只提供候选关系。跨平台匹配由标题、艺人、专辑、时长和版本词共同评分，不能仅靠名称相等。

下载采用两阶段提交：后端引用解析出的短期地址不进入任务日志，内容先写临时 staging；文件签名、SHA-256、FFprobe 音频流和时长通过后才原子移动到正式输出。封面、头像和歌词使用独立的类型与大小验证。

状态机把“事实不存在”和“技术上没拿到”分开。`AUTH_REQUIRED`、`RATE_LIMITED`、`SEARCH_UNAVAILABLE`、`SEARCH_PARTIAL`、`FOUND_CANDIDATES` 均不能降级为 `NOT_FOUND_EXHAUSTIVE`。
