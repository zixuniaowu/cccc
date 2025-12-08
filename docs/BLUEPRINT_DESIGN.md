# Blueprint Task System - 历史设计参考

> **⚠️ 已弃用**：本文档描述原始 Blueprint 设计（使用 Progress Markers）。
> 
> **当前实现**：使用 ccontext MCP，详见：
> - [ccontext MCP 设计综述](plan/ccontext_mcp_roadmap.md)
> - [CCCC 集成综述](plan/cccc_ccontext_integration.md)
>
> 本文档仅作为历史参考保留。

## 设计演进摘要

### 原始设计 (v1-v7)

**核心机制**：Progress Markers
```
Agent 在消息中写: progress: T001.S1 done
Orchestrator 解析消息，更新 task.yaml
```

**目录结构**：
```
docs/por/
├── POR.md
├── scope.yaml
├── T001-oauth/task.yaml
├── T002-logging/task.yaml
└── ...
```

**问题**：
1. 需要解析消息内容（不可靠）
2. 两套机制：规划时 Agent 写文件，执行时 Agent 写 marker
3. 概念复杂（goal detection, threshold check, etc.）

### 最终设计 (ccontext MCP)

**核心机制**：MCP 工具或直接编辑
```
有 MCP: Agent 调用 update_task tool
无 MCP: Agent 直接编辑 context/tasks/T001.yaml
```

**目录结构**：
```
context/
├── context.yaml    # 里程碑 + 笔记 + 引用
├── tasks/
│   └── T001.yaml
└── archive/
```

**优势**：
1. 单一机制（无论有无 MCP）
2. 职责清晰（Agent 负责更新，CCCC 只读取显示）
3. 概念简化（milestone 取代 goal/why/agents）

## 保留的设计原则

以下原则在新设计中保留：

### 1. 命令 vs 自然语言
```
脚本能做的 → 命令实现（确定性、即时、零成本）
需要智能的 → 自然语言（Agent处理）
```

### 2. 分母已知
```
用户关心："项目进度如何？"
需要回答："3/5 任务完成"
因此：先规划后执行，总任务数已知
```

### 3. 最小 Agent 负担
```
原设计：一行 marker
新设计：一个工具调用或一次文件编辑
```

## 被移除的概念

| 概念 | 移除原因 |
|------|----------|
| Progress Markers | MCP 工具更可靠 |
| Goal Detection | 过于复杂，Agent 自行判断 |
| Threshold Check | 同上 |
| scope.yaml | 任务计数从目录读取即可 |
| Quick Task Promotion | 简化为直接创建任务 |

## 关键决策记录

### D1: 为什么从 Progress Markers 转向 MCP

Progress Markers 需要 Orchestrator 解析消息内容：
- 正则匹配可能失败
- Agent 可能忘记写 marker
- 调试困难

MCP 工具调用是显式的 API：
- 成功/失败状态明确
- Agent 不会"意外"调用工具
- 结构化参数，无歧义

### D2: 为什么保留无 MCP 模式

不是所有 Agent 都支持 MCP：
- Codex CLI 等简单工具无 MCP Client
- 用户可能未配置 MCP
- YAML 文件编辑是通用 fallback

### D3: 为什么从 docs/por/ 移到 context/

- `docs/` 通常是文档目录，不应该有运行时状态
- `context/` 明确表示"执行上下文"
- 与 ccontext MCP 命名一致
