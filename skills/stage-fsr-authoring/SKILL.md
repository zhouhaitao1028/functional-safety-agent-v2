---
name: stage-fsr-authoring
description: FSR 导出环节的工程师经验 skill：声明本环节用到的指南模板、易错点与跨环节跳转参考。由软件安全需求工程师维护。
stage: fsr-authoring
---

# FSR 导出 Skill

> 本 skill 由软件安全需求一线工程师维护，harness 在执行 FSR 环节任务前按本声明检索材料并注入子 Agent。

## 用到的指南模板

- `gd-fsr-authoring` FSR 导出指南（主指南，TBD 规则与时间约束分解按第 2 节执行）
- `tp-fsr-list` FSR 清单模板（产出物格式，关联信号 ID 必须与接口总览表一致）
- `tp-tsr-list` TSR 需求清单模板（输入物，核对 TSR 状态必须为已批准）

## 易错点

- TBD 被臆造值替换是合规红线：模型/新手最容易在「分配组件」「时间约束」两列编数值，凡未确认一律写 TBD 并在备注给阻塞原因和责任人。
- 接口相关 FSR 忘写关联信号 ID：接口总览表后续变更时影响分析会漏掉这条 FSR，属于追溯链断裂。
- 时间约束照抄 FTTI：FSR 必须把 FTTI 分解为软件采样周期 + 响应 deadline（如 FTTI 200ms → 采样 10ms、响应 50ms、余量 140ms），并写清余量。
- 一条 FSR 覆盖多条 TSR 但 parent_tsr_ids 漏映射：变更影响分析会少通知下游。
- 分配组件写了架构里不存在的组件名：必须先核对架构分配表，未定则写 TBD。

## 跳转参考

- 当 FSR 涉及总线信号收发 → 参考 `gd-fsr-authoring` 第 2.2 节，并核对软件接口总览表中该信号的 AA/AB 分配，信号属性变更时同步更新本 FSR。
- 当 TSR 要求的机制在软件上不可实现（周期/内存不满足）→ 回跳 `gd-tsr-authoring`，走需求变更流程，禁止在 FSR 层面私自放宽约束。
- 当分配对象涉及 ASIL 分解 → 参考 `gd-software-architecture` 第 2.2 节，确认免于干涉设计已覆盖。
