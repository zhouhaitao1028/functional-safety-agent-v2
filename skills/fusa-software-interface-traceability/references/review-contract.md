# Review contract

## Inputs

- Module dictionaries: `Inputs/01_PIE_400/*.xlsm`
- PIE matrix: latest `Inputs/02_PIE_Matrix/PIE_Matrix_Res_*.xlsx`
- Current mapping: `Inputs/03_UnitConfig/Unit2ComponentConfig.xlsx`
- Optional baseline: `Unit2ComponentConfig(PIE*.xlsx)`
- Optional existing overview: `Outputs/01_接口收发表/软件组件接口收发总览表*.xlsx`

## Evidence required for findings

- Workbook or package member name
- Worksheet and row when the finding comes from an overview
- Signal or stable mapping key
- Component/SWC context when available
- Deterministic category and severity

## Human gate

Approval is blocked when any of these remains unresolved:

1. A module dictionary cannot be parsed.
2. A module file has no Unit-to-Component mapping.
3. A cross-application link is marked missing.
4. A configuration row was removed without an accepted change record.
5. A duplicate mapping key cannot be explained as a valid one-to-many relationship.

An approved exception must retain reviewer, rationale and the exact report hash.
