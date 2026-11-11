# 软件接口追溯 Agent

`software_interface_engineer` 是“模型理解、确定性执行”的混合型 Engineer Agent。DeepSeek 只把用户自然语言约束为 `generate_overview`、`compare_overviews`、`analyze_package` 或澄清问题，不读取文件、不生成路径，也不参与 Excel 计算。随后确定性执行器负责从模块软件接口表生成正式接口总览表、比较新旧总览表，并读取 Unit2ComponentConfig、PIE Matrix 和既有总览完成追溯审查。默认 `fast` 引擎是根据恢复的 VBA 源码实现的纯 Python 等价流程；`legacy` 引擎在隔离副本上执行固定原宏，用作兼容性基准和回退，不调用旧 GUI EXE。

## 能力边界

- 识别标准目录/ZIP 中的模块接口表、最新 PIE Matrix、当前/基线 Unit 配置、Mapping XLSM 和接口总览模板。
- 用纯 Python 复刻 `UpdateXLSMFileNameColumn20251212` 与 `Process_Single_File_Inner202604201900` 的确定性业务规则，生成正式接口总览表。
- 可显式切换 `legacy` 引擎运行原宏，进行历史结果复验。
- 按原业务键比较新旧总览，输出新增、删除、内容变化和疑似重命名。
- 从“信号和参数定义”提取方向、DataType、端口、元素和映射键。
- 审计既有总览中的跨应用层 SWC 链路和信号流。
- 对当前/基线内部、外部配置执行重复行感知的确定性对比。
- 保存报告和明细 JSONL，报告通过 SHA-256 与人工审批绑定。
- 对生成结果独立复算序号、必填字段和 AA/AB 跨应用层流；校验不通过即阻断交付。

源 ZIP/目录永远只读。生成工具先创建独立工作区，再执行生成和校验；失败会输出诊断 JSON。生成报告、输入清单、日志、配置快照、总览表、对比表和行级证据均带 SHA-256。默认生成不依赖 Excel/WPS；只有 `legacy` 引擎使用 Excel COM。新旧版本对比和证据提取为纯 Python。

## 直接运行

```powershell
python run.py interface-generate ..\外部接口生成与对比工具.zip `
  --output data\interface-generation

# 仅用于历史兼容复验；需要本机 Excel/WPS COM
python run.py interface-generate ..\外部接口生成与对比工具.zip `
  --output data\interface-generation-legacy `
  --engine legacy

python run.py interface-overview-compare .\new.xlsx .\old.xlsx `
  --output data\interface-comparison

python run.py interface-analyze ..\外部接口生成与对比工具.zip `
  --output data\interface-analysis
```

`interface-generate` 默认使用 `--engine fast` 且只执行生成；添加 `--compare` 才会在包内存在历史总览时继续执行完整对比。生成和对比保持为两个独立业务能力，避免只需要总览时额外消耗约一分钟。`--no-compare` 仅为兼容旧脚本保留。`--engine auto` 当前等价于 `fast`。只有 `--engine legacy` 要求可用的 Microsoft Excel 或 WPS COM 环境。`interface-overview-compare` 不要求 Excel/WPS。

通过 Manager 自动规划和统一编排运行：

```powershell
python run.py init interface-demo --title "软件接口追溯"
python run.py manager-plan interface-demo `
  --request "审查软件组件接口收发关系、跨应用层链路完整性和配置变更" `
  --input IN-INTERFACE-PACKAGE software_interface_package "..\外部接口生成与对比工具.zip"
python run.py plan-approve interface-demo --reviewer "接口负责人" --comment "输入范围已确认"
python run.py workflow-run interface-demo
python run.py item-approve interface-demo W01 --reviewer "软件架构工程师" --comment "缺失链路已评审"
python run.py workflow-run interface-demo
```

最后一次 `workflow-run` 返回 `completed`。Manager 不能批准自己的计划，Workflow 也不能越过任何交付物人工门。

统一 Workflow 通过工作项的结构化 `parameters.operation` 路由该子 Agent：

- `generate_overview`：输入恰好一个 `software_interface_package`，默认 `engine=fast`。
- `compare_overviews`：输入恰好两个 `interface_overview`，也可消费上游生成任务的 `software_interface_task_result`。
- `analyze_package`：输入恰好一个 `software_interface_package`，执行审查型分析。

无需模型即可用示例工作计划验证真实子 Agent 链路：

```powershell
python run.py init interface-generation --title "软件接口总览生成"
python run.py plan-submit interface-generation examples\workplan_software_interface_generation.json
python run.py plan-approve interface-generation --reviewer "接口负责人" --comment "批准生成"
python run.py workflow-run interface-generation --lease-seconds 180
```

执行结果保存为 `software_interface_task_result` JSON，其中记录 operation、引擎、输入指纹、正式 Excel、校验报告和全部 SHA-256；工作项随后停在人工确认门。

## MCP Server

本地子 Agent 或支持 MCP 的客户端可以启动：

```powershell
$env:FS_INTERFACE_MCP_ALLOWED_ROOTS = "C:\workspace"
python run.py interface-mcp
```

stdio Server 提供五个工具：

- `fusa_interface_generate_overview`
- `fusa_interface_compare_overviews`
- `fusa_interface_analyze_package`
- `fusa_interface_compare_configs`
- `fusa_interface_inspect_overview`

客户端配置示例：

```json
{
  "mcpServers": {
    "fusa-interface": {
      "command": "python",
      "args": ["C:\\workspace\\functional-safety-agent-v2\\run.py", "interface-mcp"],
      "env": {
        "FS_INTERFACE_MCP_ALLOWED_ROOTS": "C:\\workspace"
      }
    }
  }
}
```

MCP 中的路径参数必须落在 `FS_INTERFACE_MCP_ALLOWED_ROOTS` 下。Server 不暴露任意命令执行能力，也不会启动旧 GUI EXE。`fusa_interface_generate_overview` 的 `engine` 默认为 `fast`；只有传入 `legacy` 才能运行固定的两个已登记宏，且只操作隔离副本。

## 架构关系

```text
Manager
  -> software_interface_engineer
       -> InterfaceDeliveryService
            -> fast：纯 Python 规则引擎（默认）
            -> legacy：原宏兼容引擎（隔离副本 + COM Busy 有界重试）
            -> 独立 AA/AB 校验
            -> 原业务键版本对比
       -> InterfaceAnalysisService（补充审查）
       -> MCP Server（供外部子 Agent 调用）
       -> 正式 Excel + JSON 报告 + JSONL 行证据 + SHA-256
  -> 人工确认
```

统一 Workflow、CLI 和 MCP 现在共用同一套确定性实现。Manager 在工作计划中填写 `parameters.operation`，`software_interface_engineer` 随后直接调用生成、总览对比或审查服务；跨进程、跨框架或外部子 Agent 仍可使用 MCP，不需要把旧 EXE 交给模型操作。

## 准确性、兼容性与性能边界

- 固定源：每次报告记录 `_build_src.zip` 与快速引擎源码的 SHA-256；当前原源码版本为 `A0B6DDDED6A05A2C476FBD34A72EA86FE4ED11F0BE9F7155C10512568118759C`。
- 保留逻辑：配置选择、模块表聚合、PIE Matrix 内外映射、方向翻转、AA/AB 参与规则、适配器排除规则、比较业务键和重命名候选算法均来自恢复的原源码。
- 兼容回归：在当前 59 份模块表、3,276 条 Matrix 记录的样本上，原 VBA 的 12,510 条最终记录全部在快速结果中逐字段匹配，`legacy_only=0`；快速引擎总计 12,546 条。
- 已识别修正：额外 36 条为源表中真实存在并能形成确定性关系、但旧 VBA 执行结果没有保留的组件 GV 记录，其中 `FM-ACM` 24 条、`MCF-IN` 4 条、`RCF-IN` 8 条。首次采用快速结果时必须审核这些新增记录，不能无提示替换已批准基线。
- 性能回归：同一样本中快速核心生成约 29.7 秒，原 VBA 核心宏约 159 秒（同阶段约 160 秒），约提升 5.3 倍；包含 ZIP 解包、全量 SHA-256、独立校验和证据落盘的独立生成端到端约 59.8 秒。
- 旧引擎优化：COM 层保留 Ready 等待、Busy 有界重试、无交互告警和可靠退出；它用于回归和紧急回退，不再作为默认生产路径。
- 对比遇到重复业务键时必须报告并进入人工门，不能把“最后一行覆盖”默认为正确结论。
- 对比报告同时提供 `content_multiset_parity`：排除无业务含义的序号列后按整行内容做多重集合校验，用来识别重复业务键导致的配对噪声；它是补充证据，不替换原业务键比较结果。
- `链路流信号流缺失` 是业务发现，不等同于程序失败；必须由软件架构/接口负责人确认。
