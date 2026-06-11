# API Scanner — CANN API 兼容性分析工具

基于混合引擎（libclang + Tree-sitter + Python AST）静态语法树解析，对 [CANN](https://gitcode.com/cann) 旗下全部组件进行全量 API 声明提取，生成基线清单，用于 PR 流水线中自动检测 API 变更（新增、修改、删除）。

## 工作原理

```
AR1                     AR2                        AR3                          AR4
用户输入               文件清单                   混合引擎 AST 解析              输出
───────────┐    ┌──────────────────┐    ┌─────────────────────────┐    ┌──────────────────┐
组件绝对路径 │───▶│ file-list.csv     │───▶│ .h/.hpp → libclang       │───▶│ func-export.json │
            │    │                  │    │ .cpp/.c  → Tree-sitter   │    │ data-export.json │
            │    │ inc-pkg-conf.csv  │    │ .py      → Python ast    │    │                  │
            │    │ 过滤:experimental/│    │                         │    │ baseline 清单     │
            │    │ tests/build/stub/ │    │ 提取:                   │    └──────────────────┘
            │    │ examples/output/  │    │ · FunctionDecl          │
            │    └──────────────────┘    │ · CXXMethodDecl          │
            │                           │ · macro-function         │
            │                           │ · macro/struct/class/    │
            │                           │   union/enum             │
            │                           └─────────────────────────┘
```

## 前置条件

- Python ≥ 3.9
- libclang 动态库（macOS/Xcode 自带；Linux 需 `apt install libclang-dev`）
- Tree-sitter Python 绑定

```bash
pip install clang tree-sitter tree-sitter-c tree-sitter-cpp
```

纯静态分析，无需编译数据库。

## 快速开始

```bash
# 1. 安装依赖
pip install clang tree-sitter tree-sitter-c tree-sitter-cpp

# 2. 运行扫描（默认模式：全量 API + inc/pkg 类型声明）
python main.py /path/to/cann/component-a /path/to/cann/component-b

# 3. 全量类型声明模式
python main.py --full-data-type /path/to/cann/component-a

# 4. 仅生成文件清单（跳过解析）
python main.py --skip-ar3 /path/to/cann/component-a

# 5. 查看结果
cat output/func-export.json   # API 声明
cat output/data-export.json   # 类型声明（macro/struct/class/union/enum）
```

## 配置文件

`design/inc-pkg-conf.csv` — 组件对外 API 管控目录映射表（`|` 分隔）。

| 字段 | 说明 |
|---|---|
| `component` | 组件名称 |
| `type` | 目录类型：`inc`（对外头文件）或 `pkg`（包级头文件） |
| `path` | 相对于组件根目录的路径模式，`;` 分隔多个模式，支持通配符 |

## 输出格式

### func-export.json — API 声明

```jsonc
{
  "include/driver/ts_api.h": [{
    "func_name": "tsDevSendMsgAsync",
    "kind": "FunctionDecl",          // FunctionDecl | CXXMethodDecl | macro-function
    "label": "inc",
    "location": "/abs/path/file.h:17:0",
    "extra_info": {
      "deprecated": "",
      "is_definition": false,
      "parameters": [
        ["eventType", "const uint32_t", "", ""],  // [名称, 类型, 预留, 默认值]
        ["waitType", "const uint32_t", "", "3"]
      ],
      "return": "void",
      "type": "void tsDevSendMsgAsync(...)",       // 完整声明（无函数体）
      "visibility": "extern"                       // extern | internal | macro | public | protected | private
    }
  }]
}
```

### data-export.json — 类型声明

```jsonc
{
  "include/driver/ts_api.h": [{
    "data_name": "TS_INNER_SUCCESS",
    "kind": "macro",                  // macro | struct | class | union | enum
    "path": "ts_api.h",
    "label": "inc",
    "location": "/abs/path/file.h:17:0",
    "source": "#define TS_INNER_SUCCESS 1"
  }]
}
```

## 可见性标签

| label | 含义 |
|---|---|
| `inc` | inc-pkg-conf.csv 中 `type=inc` 目录下的文件 |
| `pkg` | inc-pkg-conf.csv 中 `type=pkg` 目录下的文件 |
| `internal` | 组件内未被 inc-pkg-conf.csv 覆盖的文件 |

## 目录结构

```
design/
  requirements-func.md   # 功能需求（AR1–AR4）
  inc-pkg-conf.csv       # 组件管控目录映射
  impl_plan.md           # 优化方案与根因知识库
scripts/
  ar1.py                 # 组件路径输入
  ar2.py                 # 文件清单生成
  ar3.py                 # AST 解析（混合引擎）
  ar4.py                 # 结果格式化输出
main.py                  # CLI 入口
output/                  # 扫描结果
tests/
  fixtures/              # 测试样本
  test_ar3_accuracy.py   # 27 测试用例（19 种声明样式）
```

## 性能

| 指标 | 数值 |
|---|---|
| 扫描文件 | 23,187 |
| 耗时 | ~4 min |
| 速度 | ~96 files/s |
| API 声明 | 212,000+ |
| type 字段准确率 | >99.9% |
| 参数提取成功率 | 99.9%+ |

## 设计约束

- **不做模版实例化**：仅提取主模版声明，不展开特化
- **不做宏展开**：仅记录声明点，不追踪宏生成的间接 API
- **纯静态分析**：无需编译，不依赖构建系统
- **混合引擎**：libclang（语义精度）+ Tree-sitter（语法回退） + Python ast
- **自动 -D 宏检测**：扫描 CANN 特有 `__aicore__` 等属性宏，自动预定义

## 测试

```bash
python -m unittest tests/test_ar3_accuracy.py -v
# 27 tests, 0 failures
```
