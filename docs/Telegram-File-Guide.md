# Telegram 文件交互开箱指南（M2）

群聊原则
- 显式路由是必须：/a /b /both（推荐，隐私模式安全），或 a: / b: / both:（需关闭隐私模式）。
- 无显式路由的消息/文件一律不处理（限频提示一次用法）。

入站（Telegram → 本地）
- 发送：选择“附件”，在 caption 开头写路由与一句话说明（示例：`/both 这是 UI 截图`）。
- 落盘：`.cccc/work/upload/inbound/<chat_id>/<YYYYMMDD>/<MID>__<安全文件名>`；同目录 `.meta.json` 记录 `{mime,bytes,sha256,caption,ts}`。
- inbox：每个目标 Peer 生成 `.cccc/mailbox/<peer>/inbox/000123.<MID>.txt`，内容为 `<FROM_USER>` 段，包含 `[MID]`、文件相对路径、hash/大小/MIME、caption 摘要。

出站（本地 → Telegram）
- 发件箱：将文件放到 `.cccc/work/upload/outbound/<peer>/{files|photos}/<MID>__name`，可选同名 `.caption.txt`；Bot 自动发送“单条消息（文件+caption）”。
- 去重/节流：默认 30s 合并与限频，避免刷屏；日志与 ledger 记录 bridge-file-outbound。

查看与管理
- `/files [in|out] [N]` 列最近 N 个入/出站文件；`/file N` 查看上一列表第 N 项详情（路径/大小/MTime/sha256/mime/caption）。
- 晋升与校验（后续）：`/promote <id> dst=docs/evidence/...` 生成 patch；`/check <id> kind=<name>` 用 Evidence Runner 校验（写回 verdict/log 引用）。

安全默认
- 尺寸/类型限制：默认 16MB，允许 text/*, image/png|jpeg, application/pdf|zip；文件名净化，caption 脱敏；EXIF 去隐。
- work/** 非权威：文件不会直接改变仓库状态；晋升需走 patch 队列（≤150 行）与预检。

提示
- 群聊使用命令路由 `/a /b /both` 可在隐私模式下稳定；如需 a:/b:/both:，请在 BotFather `/setprivacy → Disable`。
- 私聊可设默认路由，但仍建议显式路由，便于审计与查阅。

