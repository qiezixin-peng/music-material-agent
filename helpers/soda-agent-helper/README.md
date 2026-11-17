# Soda Agent Helper

这是仅供 Agent/MCP 调用的无界面进程，不提供网页、桌面窗口或监听端口。

- 输入：标准输入中的单个 JSON 请求；Cookie 只在该进程内存中使用。
- 输出：标准输出中的单个 JSON 响应；搜索响应不包含音频临时 URL。
- 搜索：依次使用汽水 PC 签名搜索、PC Web 搜索和 Android 回退。
- 下载：在进程内解析、下载及必要时解密到 Agent 指定的 staging 文件。
- 上游：固定 `music-lib` 提交 `044c84bed714058863f04ca5e0128d18f02ebba5`。

构建命令：`./scripts/build_soda_helper.ps1`。构建产物被 Git 忽略，来源与哈希记录在 `bin/soda-agent-helper.build.json`。
