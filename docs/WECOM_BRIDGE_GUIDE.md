# 企业微信桥接使用指南

## 概述

企业微信桥接（WeCom Bridge）是 CCCC Pair 的**仅出站**聊天适配器，通过企业微信机器人 Webhook 将智能体的消息推送到企业微信群聊。

### 特点

✅ **Markdown 渲染**：支持 markdown_v2 格式，提供丰富的消息展示
✅ **自动速率限制**：智能控制发送频率，避免触发官方限制
✅ **零配置启动**：配置 Webhook URL 即可开始使用
✅ **实时推送**：智能体消息实时推送到企业微信群
❌ **仅出站**：Webhook 机制不支持接收用户消息（无入站）

---

## 快速开始

### 1. 获取 Webhook URL

1. 在企业微信群中，点击右上角 `...` → `群设置` → `群机器人`
2. 点击 `添加群机器人` → `新创建一个机器人`
3. 设置机器人名称和头像
4. 复制生成的 Webhook URL（格式如下）：
   ```
   https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=693a91f6-7xxx-4bc4-97a0-0ec2sifa5aaa
   ```

⚠️ **重要提醒**：请妥善保管 Webhook URL，不要泄露到 GitHub、博客等公开场所！

### 2. 配置 CCCC

编辑 `.cccc/settings/wecom.yaml`：

```yaml
# 企业微信 Webhook URL（必填）
webhook_url: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY"

# 或通过环境变量配置
# webhook_url_env: WECOM_WEBHOOK_URL

# 自动启动（随 cccc run 启动）
autostart: false

# 消息类型（推荐 markdown_v2）
message_type: markdown_v2  # markdown_v2 | markdown | text

# 速率限制（官方限制：20条/分钟）
rate_limit:
  max_messages: 18        # 保守设置为 18 条/分钟
  window_seconds: 60

# 详细日志（调试模式）
verbose: false
```

### 3. 启动桥接

```bash
# 方式一：手动启动
cccc bridge wecom start

# 方式二：设置 autostart: true 后随 cccc run 自动启动
# 编辑 wecom.yaml 设置 autostart: true
cccc run
```

### 4. 验证运行

```bash
# 查看桥接状态
cccc bridge wecom status

# 查看实时日志
cccc bridge wecom logs -f

# 查看最近 50 行日志
cccc bridge wecom logs -n 50
```

---

## 使用场景

### 场景 1：团队协作监控

将智能体的工作进度实时推送到企业微信群，团队成员可以随时了解：
- 智能体正在处理的任务
- 完成的工作和证据（commit hash、测试结果）
- 遇到的问题和风险提示

### 场景 2：异步工作通知

当您离开电脑时，智能体会继续工作并将结果推送到企业微信：
- 在手机上查看智能体的工作进度
- 及时了解任务完成情况
- 无需回到电脑前即可获取更新

### 场景 3：多项目管理

为不同项目配置不同的企业微信机器人：
- 每个项目有独立的群聊和机器人
- 消息隔离，互不干扰
- 方便团队成员专注于各自项目

---

## 消息格式示例

### Markdown_v2 格式（推荐）

```markdown
## 📨 PeerA

完成了用户认证模块的实现。

### 📋 Evidence
- 测试通过：100% coverage
- Commit: abc123def

*2025-12-01 14:30:25*
```

### Markdown 格式（传统）

```markdown
**PeerA**
完成了用户认证模块的实现。

数据含义
2025-12-01 14:30:25
```

### Text 格式（纯文本）

```
[PeerA] 完成了用户认证模块的实现。 (2025-12-01 14:30:25)
```

---

## 高级配置

### 使用环境变量

如果不想在配置文件中明文存储 Webhook URL，可以使用环境变量：

```bash
# 设置环境变量
export WECOM_WEBHOOK_URL="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY"

# wecom.yaml 留空
webhook_url: ""
webhook_url_env: WECOM_WEBHOOK_URL
```

### 调整速率限制

根据实际需求调整发送频率：

```yaml
rate_limit:
  # 更保守（避免突发流量）
  max_messages: 15
  window_seconds: 60

  # 更激进（接近官方限制）
  max_messages: 19
  window_seconds: 60
```

### 启用详细日志

调试时可以启用详细日志：

```yaml
verbose: true
```

查看日志：
```bash
tail -f .cccc/state/bridge-wecom.log
```

---

## 常见问题

### Q1: 消息发送失败，显示 "errcode: 93000"

**原因**：Webhook URL 中的 key 无效或已过期。

**解决方案**：
1. 检查 Webhook URL 是否完整且正确
2. 重新在企业微信群中生成新的机器人和 Webhook URL
3. 更新 `wecom.yaml` 中的 `webhook_url`

### Q2: 消息发送失败，显示 "errcode: 45009"

**原因**：触发速率限制（超过 20 条/分钟）。

**解决方案**：
1. 降低 `rate_limit.max_messages` 配置（如设置为 15）
2. 增加 `rate_limit.window_seconds`（如设置为 90）
3. 桥接会自动等待并重试

### Q3: 收不到消息

**检查步骤**：
1. 确认桥接已启动：`cccc bridge wecom status`
2. 查看日志：`cccc bridge wecom logs -f`
3. 检查 Webhook URL 是否配置正确
4. 验证企业微信机器人是否在目标群中

### Q4: Markdown 渲染不正常

**原因**：不同 `message_type` 支持的 Markdown 语法不同。

**解决方案**：
- 使用 `markdown_v2`（推荐）：支持完整的 Markdown 语法
- 使用 `markdown`：支持部分 Markdown 和字体颜色
- 使用 `text`：纯文本，无格式

### Q5: 如何停止桥接？

```bash
cccc bridge wecom stop
```

或者直接杀掉进程：
```bash
pkill -f bridge_wecom.py
```

---

## 管理命令

| 命令 | 说明 |
|------|------|
| `cccc bridge wecom start` | 启动企业微信桥接 |
| `cccc bridge wecom stop` | 停止企业微信桥接 |
| `cccc bridge wecom restart` | 重启企业微信桥接 |
| `cccc bridge wecom status` | 查看桥接运行状态 |
| `cccc bridge wecom logs` | 查看最近 120 行日志 |
| `cccc bridge wecom logs -n 50` | 查看最近 50 行日志 |
| `cccc bridge wecom logs -f` | 实时跟踪日志（Ctrl+C 退出） |
| `cccc bridge all start` | 启动所有桥接（包括 wecom） |
| `cccc bridge all stop` | 停止所有桥接 |
| `cccc doctor` | 检查 wecom 配置和环境 |

---

## 注意事项

### 性能影响

- 企业微信桥接占用资源极少（仅出站，无轮询）
- 每条消息平均延迟 < 100ms
- 支持并发发送（受速率限制保护）

### 限制

1. **仅出站**：无法接收用户从企业微信发送的消息
2. **速率限制**：20 条/分钟（官方限制）
3. **消息长度**：Markdown 最长 4096 字节
4. **不支持文件**：Webhook 不支持文件上传

---

## 进阶使用

### 多群推送

如果需要同时推送到多个企业微信群，可以创建多个桥接实例：

1. 复制 `bridge_wecom.py` 为 `bridge_wecom2.py`
2. 创建 `wecom2.yaml` 配置文件
3. 修改 `cccc.py` 添加 wecom2 桥接支持

或者使用脚本批量推送：

```python
import requests
import json

webhooks = [
    "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=KEY1",
    "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=KEY2",
]

message = {
    "msgtype": "markdown_v2",
    "markdown_v2": {
        "content": "## 测试消息\n这是一条测试消息"
    }
}

for webhook in webhooks:
    requests.post(webhook, json=message)
```

### 自定义消息格式

修改 `bridge_wecom.py` 中的格式化函数：

```python
def _format_message_markdown_v2(self, event: Dict[str, Any]) -> str:
    """自定义消息格式"""
    lines = []

    # 自定义标题
    lines.append(f"## 🚀 [{event.get('from_peer', 'System')}] 工作报告")
    lines.append("")

    # 添加项目信息
    lines.append(f"**项目**：{os.getenv('PROJECT_NAME', 'CCCC')}")
    lines.append("")

    # 内容
    text = event.get('text', '').strip()
    if text:
        lines.append(text)
        lines.append("")

    # 时间戳
    ts = event.get('ts', '')
    if ts:
        lines.append(f"---")
        lines.append(f"*{ts}*")

    return '\n'.join(lines)
```

---

## 故障排查

### 查看完整日志

```bash
# 桥接日志
tail -f .cccc/state/bridge-wecom.log

# 编排器日志
tail -f .cccc/state/orchestrator.log

# 出站箱日志
tail -f .cccc/state/ledger.jsonl | grep bridge
```

### 手动测试 Webhook

使用 curl 测试 Webhook 是否可用：

```bash
curl 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY' \
  -H 'Content-Type: application/json' \
  -d '{
    "msgtype": "markdown_v2",
    "markdown_v2": {
      "content": "## 测试\n这是一条测试消息"
    }
  }'
```

预期响应：
```json
{
  "errcode": 0,
  "errmsg": "ok"
}
```

### 重置桥接状态

如果桥接出现异常，可以重置状态：

```bash
# 停止桥接
cccc bridge wecom stop

# 删除状态文件
rm .cccc/state/bridge-wecom.pid
rm .cccc/state/bridge-wecom.lock
rm .cccc/state/outbox-cursor-wecom.json

# 重新启动
cccc bridge wecom start
```

---

## 更新日志

### v0.3.22 (2025-12-01)
- ✨ 首次发布企业微信桥接
- ✅ 支持 markdown_v2、markdown、text 三种消息格式
- ✅ 自动速率限制（18 条/分钟）
- ✅ 集成到 `cccc bridge` 命令

---
