# Lifecycle（Discovery → Growth）
- 每阶段必须交付的工件与门（Gate）：
Discovery: PROBLEM.md, COMPETITORS.md, RISKS.md  | Gate: 双方同意问题陈述
Shaping:  PRD.md, Roadmap.md, Milestones.md      | Gate: PRD 锁定（双签门）
Arch/UX:  ARCH.md, API.md, SCHEMA.*, UX mock     | Gate: 架构落锤（双签门）
Impl:     tests/*, src/*, CI config              | Gate: 覆盖率≥X，关键用例绿灯
Quality:  SAST, Bench, Observability             | Gate: 性能/安全阈值通过
Release:  CHANGELOG, release scripts             | Gate: 版本标记（双签门）
Growth:   Events.md, Dashboards.md, Runbook.md   | Gate: 告警与仪表到位
