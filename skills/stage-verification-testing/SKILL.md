---
name: stage-verification-testing
description: 测试环节（组件/集成/系统）的工程师经验 skill：声明本环节用到的指南模板、易错点与跨环节跳转参考。由测试工程师维护。
stage: verification-testing
---

# 测试 Skill（样式模板）

> 本 skill 由具体干活的工程师维护，harness 在执行测试环节任务前按本声明检索材料并注入子 Agent。

## 用到的指南模板

- `gd-verification-testing` 测试用例设计指南（主指南，三层覆盖矩阵按第 1 节执行）
- `tp-test-case` 测试用例模板（产出物格式）
- `tp-fsr-list` / `tp-tsr-list` 需求清单（输入物，用例 linked_req_ids 与之对齐）

## 易错点

- 【待补充：由该环节工程师填写本环节评审中最常打回的问题】
- 【示例】故障注入用例只写了注入点，没写预期安全状态和恢复行为

## 跳转参考

- 【示例】当系统测试需要危害场景复现 → 参考 `tp-hara-worksheet` 的场景假设列还原工况
- 【待补充：其他跳转条件】
