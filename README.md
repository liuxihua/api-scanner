# API Scanner — CANN API 兼容性分析工具

基于 Tree-sitter 静态语法树解析，对 [CANN](https://gitcode.com/cann) 旗下全部组件进行全量 API / 类型声明提取，生成基线清单，用于 PR 流水线中自动检测 API 变更（新增、修改、删除）。

## 工作原理

```
AR1                   AR2                      AR3                         AR4
用户输入             文件清单                 AST 解析                    输出
─────────┐    ┌──────────────┐    ┌──────────────────────┐    ┌─────────────────┐
组件绝对  │───▶│ file-list.csv │───▶│ Tree-sitter 解析      │───▶│ func-export.json│
路径      │    │              │    │ (C/C++/Python AST)    │    │ data-export.json│
          │    │ 过滤规则:     │    │                      │    │                 │
          │    │ *.h/.hpp/.cpp│    │ 提取:                 │    │ baseline 清单    │
          │    │ *.cxx/.cc/.py│    │ · FunctionDecl        │    │                 │
          │    │              │    │ · CXXMethodDecl       │    └─────────────────┘
          │    │ 排除:         │    │ · macro-function      │
          │    │ experimental/ │    │ · macro               │
          │    │ tests/build/  │    │ · struct/union/enum   │
          │    │ output/stub/  │    └──────────────────────┘
          │    └──────────────┘
```

## 前置条件

- Python ≥ 3.9
- `pip install tree-sitter`（C/C++ 语法树解析）
- Python 标准库 `ast` 模块（Python 语法树解析，内置）

无需安装 Clang/LLVM，无需编译数据库（`compile_commands.json`）。

## 快速开始

```bash
# 1. 安装依赖
pip install tree-sitter

# 2. 准备配置（如需新增/调整组件）
#    编辑 design/inc-pkg-conf.csv

# 3. 运行扫描（AR1→AR4 一站式）
python scripts/scan.py /path/to/cann/component-a /path/to/cann/component-b

# 4. 查看结果
cat output/func-export.json   # API 声明
cat output/data-export.json   # 类型声明（macro/struct/union/enum）
```

## 配置文件

`design/inc-pkg-conf.csv` — 组件对外 API 管控目录映射表。

| 字段 | 说明 |
|---|---|
| `component` | 组件名称 |
| `type` | 目录类型：`inc`（对外头文件）或 `pkg`（包级头文件） |
| `path` | 相对于组件根目录的路径模式，`;` 分隔多个模式，支持通配符 |

同一组件可配置多条记录（如 `runtime` 同时有 `inc` 和 `pkg` 路径）。

## 输出格式

### func-export.json — API 声明

```jsonc
{
  "include/driver/ts_api.h": [
    {
      "func_name": "tsDevSendMsgAsync",
      "kind": "FunctionDecl",            // FunctionDecl | CXXMethodDecl | macro-function
      "label": "inc",
      "location": "/home/user/cann/runtime/include/driver/ts_api.h:17:0",
      "extra_info": {
        "deprecated": "",
        "is_definition": false,
        "parameters": [
          ["eventType", "const uint32_t", "", ""],   // [名称, 类型, 预留_模版/宏穿透, 默认值]
          ["waitType", "const uint32_t", "", "3"]
        ],
        "return": "void",
        "type": "void tsDevSendMsgAsync(const uint32_t eventType, const uint32_t waitType = 3)",
        "visibility": "extern"             // extern | internal | macro | public | protected | private
      }
    }
  ]
}
```

### data-export.json — 类型声明

```jsonc
{
  "include/driver/ts_api.h": [
    {
      "data_name": "TS_INNER_SUCCESS",
      "kind": "macro",                     // macro | struct | union | enum
      "path": "ts_api.h",
      "label": "inc",
      "location": "/home/user/cann/runtime/include/driver/ts_api.h:17:0",
      "source": "#define TS_INNER_SUCCESS 1"
    }
  ]
}
```

## 声明类型与可见性

### 声明类型

| 输出文件 | kind | 来源（Tree-sitter 节点） |
|---|---|---|
| `func-export.json` | `FunctionDecl` | 命名空间/全局作用域下 `function_definition`；`template_declaration` 内的 `function_definition` |
| `func-export.json` | `CXXMethodDecl` | `class_specifier`/`struct_specifier` 体内的 `function_definition`；文件作用域声明符含 `::` 的修正 |
| `func-export.json` | `macro-function` | `preproc_function_def` 节点 |
| `data-export.json` | `macro` | `preproc_def` 节点 |
| `data-export.json` | `struct` | `struct_specifier` 节点 |
| `data-export.json` | `union` | `union_specifier` 节点 |
| `data-export.json` | `enum` | `enum_specifier` 节点 |

### 可见性标签

| label | 含义 |
|---|---|
| `inc` | `inc-pkg-conf.csv` 中 `type=inc` 目录下的文件 |
| `pkg` | `inc-pkg-conf.csv` 中 `type=pkg` 目录下的文件 |
| `internal` | 组件内未被 `inc-pkg-conf.csv` 覆盖的其他文件 |

## 目录结构

```
design/
  requirements-func.md   # 完整功能需求（AR1–AR4）
  inc-pkg-conf.csv       # 组件管控目录映射
scripts/                 # 实现代码
output/                  # 扫描结果
  func-export.json
  data-export.json
```

## 设计约束

- **不做模版实例化**：仅提取主模版声明，不展开隐式/显式特化
- **不做宏展开**：仅记录 `#define` 声明，不追踪宏生成的间接 API
- **纯静态分析**：无需编译，不依赖构建系统，解析结果可复现
- **Tree-sitter 选型**：轻量（~5MB），零编译上下文，极速解析，天然适配 CI/CD

## 开发状态

项目当前处于 **设计阶段**，`scripts/` 和 `output/` 目录待实现。
