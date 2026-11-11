---
name: stage-tsr-authoring
description: TSR 编写环节的工程师经验 skill：声明本环节用到的指南模板、易错点与跨环节跳转参考。由系统安全需求工程师维护。
stage: tsr-authoring
---

# TSR 编写 Skill（样式模板）

> 本 skill 由具体干活的工程师维护，harness 在执行 TSR 环节任务前按本声明检索材料并注入子 Agent。

## 用到的指南模板

- `gd-tsr-authoring` TSR 编写指南（主指南）
- `tp-tsr-list` TSR 需求清单模板（产出物格式，parent_sg_ids 必填）
- `gd-safety-goal` 安全目标编写指南（输入物规范，核对 SG 状态必须为已批准）

## 易错点

- 【待补充：由该环节工程师填写本环节评审中最常打回的问题】
- 【示例】一条 TSR 隐式覆盖两条安全目标，parent_sg_ids 只写了一条

## 跳转参考

- 【示例】当 TSR 需要 ASIL 分解 → 参考 `gd-tsr-authoring` 第 2.3 节，并补独立性论证后再提交
- 【待补充：其他跳转条件】
