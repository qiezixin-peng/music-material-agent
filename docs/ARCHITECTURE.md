# 架构与数据可信度

```text
XLSX -> durable job/rows -> five leased platform partitions
                           | musicdl candidate discovery
                           | Scrapling page evidence
                           | Gandalf optional anchor (planned)
                           v
                     immutable snapshots
                           v
                 ring/all-to-all full review
                           v
                  verified files + final XLSX
```

SQLite 使用 WAL 模式，保存任务状态、租约、观测、不可变快照哈希、复核 verdict 和事件索引。大体积 HTML、网络响应、调试输出及中间下载位于每个任务独立的临时痕迹目录。

可信度按来源分层：平台原生响应/页面是事实来源；musicdl 是可替换的候选与下载后端；Scrapling 是页面结构变化时的自适应证据层；Gandalf 只提供候选关系。跨平台匹配由标题、艺人、专辑、时长和版本词共同评分，不能仅靠名称相等。

状态机把“事实不存在”和“技术上没拿到”分开。`AUTH_REQUIRED`、`RATE_LIMITED`、`SEARCH_UNAVAILABLE`、`SEARCH_PARTIAL`、`FOUND_CANDIDATES` 均不能降级为 `NOT_FOUND_EXHAUSTIVE`。
