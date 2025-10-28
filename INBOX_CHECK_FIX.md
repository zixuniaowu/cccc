# Inbox 残留消息检查修复说明

## 问题描述

**之前的行为：**
- 每次点击 Launch 按钮都会检查 inbox 是否有消息文件
- 只要 inbox 里有文件，就会弹出"残留消息"对话框
- 在正常运行过程中，inbox 有消息是正常的工作流程，不应该被视为"残留"

**问题根源：**
- `_check_residual_inbox()` 在每次 Launch 时都会执行
- 没有区分"初始设置后的第一次检查"和"后续启动的检查"
- 导致用户每次重启 TUI 都会看到残留消息提示

## 解决方案

### 核心思路
使用持久化标记文件 `state/inbox_checked.flag` 来记录"是否已完成初始 inbox 检查"：
- **第一次 Launch**：标记文件不存在，执行 inbox 检查，创建标记文件
- **后续 Launch**：标记文件已存在，直接跳过检查，继续启动

### 实现细节

#### 1. 检查标记文件（app.py:2659-2664）
```python
# Check if inbox has already been checked (flag file exists)
inbox_checked_flag = self.home / "state" / "inbox_checked.flag"
if inbox_checked_flag.exists():
    # Already checked before, skip and continue launch
    self._continue_launch()
    return
```

#### 2. 创建标记文件（app.py:2687-2690）
```python
# Set flag to indicate inbox check completed
# Do this BEFORE showing dialog to prevent repeated checks
inbox_checked_flag.parent.mkdir(parents=True, exist_ok=True)
inbox_checked_flag.write_text(f"Inbox checked at {time.time()}\n", encoding='utf-8')
```

**关键设计决策：**
- 在显示对话框**之前**就创建标记文件
- 防止用户在对话框显示期间再次点击 Launch 导致重复检查
- 标记文件内容包含时间戳，便于调试和审计

### 工作流程

#### 场景 1：全新安装/首次设置
1. 用户配置 actors/IM 等设置
2. 点击 Launch 按钮
3. `inbox_checked.flag` 不存在 → 执行 inbox 检查
4. 创建 `inbox_checked.flag` 标记文件
5. 如果有残留消息，显示对话框让用户选择
6. 继续启动 orchestrator

#### 场景 2：后续启动
1. TUI 启动，加载已有配置
2. 用户点击 Launch 按钮
3. `inbox_checked.flag` 已存在 → **直接跳过检查**
4. 继续启动 orchestrator
5. inbox 里的消息被视为正常工作消息，不会触发残留消息对话框

#### 场景 3：需要重新检查（手动重置）
如果用户需要重新触发 inbox 检查（例如清理了历史数据），可以手动删除标记文件：
```bash
rm .cccc/state/inbox_checked.flag
```

下次 Launch 时会重新执行检查。

## 文件位置

- **标记文件**：`.cccc/state/inbox_checked.flag`
- **修改代码**：`.cccc/tui_ptk/app.py:2651-2698`（`_check_residual_inbox` 方法）

## 向后兼容性

- 对于已有的 CCCC 安装，标记文件不存在，第一次启动时会执行一次 inbox 检查
- 后续启动会自动跳过，无需用户干预
- 不影响任何现有功能和配置

## 测试建议

### 测试 1：首次设置
1. 删除 `.cccc/state/inbox_checked.flag`（如果存在）
2. 在 `.cccc/mailbox/peerA/inbox/` 放置一些测试文件
3. 启动 TUI，配置设置，点击 Launch
4. **预期**：显示残留消息对话框
5. **验证**：`inbox_checked.flag` 已创建

### 测试 2：后续启动
1. 确保 `.cccc/state/inbox_checked.flag` 存在
2. 在 inbox 放置新的测试文件
3. 退出并重新启动 TUI，点击 Launch
4. **预期**：不显示残留消息对话框，直接启动
5. **验证**：inbox 中的消息不会触发对话框

### 测试 3：正常工作流程
1. 正常使用 CCCC，让 peers 交换消息
2. inbox 中会有正常的工作消息
3. 退出并重新启动 TUI
4. **预期**：不会将正常消息误认为"残留消息"

## 相关代码

### 其他 mailbox 检查机制（不受影响）
- `_check_mailbox_alerts()`（line 2619）：运行时每 10 秒检查新消息增加
  - 这是正常的运行时监控，不是"残留消息检查"
  - 显示的是 "Mailbox Alert" 对话框（不同于 "Inbox Cleanup"）
  - 不受本次修复影响，继续正常工作

## 总结

这个修复确保了 inbox 残留消息检查只在**真正的初始设置完成后**执行一次，后续启动时不会误将正常工作消息视为残留消息。通过简单的标记文件机制，实现了清晰的语义区分和良好的用户体验。
