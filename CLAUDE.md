# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Static API compatibility scanner for the [CANN](https://gitcode.com/cann) open-source ecosystem. Parses C/C++/Python source files across all CANN components to extract a baseline inventory of every public API declaration — used in PR pipelines to detect API changes (additions, modifications, deletions).

**Current performance:** 23,187 files scanned in ~4 min, producing 212,000+ API declarations. Accuracy: >99.9% type field, 99.9%+ parameter extraction.

## Repository structure

```
design/
  requirements-func.md   # Full functional requirements (AR1–AR4)
  inc-pkg-conf.csv       # Component → include/pkg directory mapping
scripts/
  ar1.py                 # Component path input & validation
  ar2.py                 # File inventory generation (inc-pkg-conf.csv matching)
  ar3.py                 # AST extraction — hybrid engine (libclang + Tree-sitter + ast)
  ar4.py                 # Output formatting to JSON
main.py                  # CLI entry point (AR1→AR4 pipeline)
output/                  # Generated artifacts (func-export.json, data-export.json, file-list.csv)
tests/
  fixtures/
    api_styles.h         # 17 API declaration styles test header (libclang engine)
    api_styles.cpp       # Macro + header guard test source (Tree-sitter engine)
  test_ar3_accuracy.py   # 27 test cases covering 19 declaration style categories
```

## Usage

```bash
# 默认模式（仅 API + inc/pkg 类型声明）
python main.py /path/to/cann/component-a /path/to/cann/component-b

# 全量模式（API + 所有文件的类型声明）
python main.py --full-data-type /path/to/cann/component-a

# 仅生成文件清单（跳过解析）
python main.py --skip-ar3 /path/to/cann/component-a

# 指定配置和输出目录
python main.py --config design/inc-pkg-conf.csv --output-dir results/ /path/to/comp
```

## Pipeline rules & constraints

### AR1 — 组件路径输入

- 接收一个或多个组件**绝对路径**
- 不产出中间文件，路径直接从 CLI 传入 AR2

### AR2 — 文件清单生成 (file-list.csv)

**目录过滤规则：**

| 规则 | 路径模式 | 行为 |
|---|---|---|
| R1 | `*/experimental/*` | 跳过 |
| R2 | `*/tests/*`, `*/build/*`, `*/output/*`, `*/stub/*`, `*/examples/*` | 跳过 |

**文件扫描规则：**

| 规则 | 条件 | 扫描范围 |
|---|---|---|
| R3 | **管控目录内**（inc-pkg-conf.csv 中 `inc`/`pkg` 路径） | `.h`, `.hpp`, `.cpp`, `.cxx`, `.cc`, `.py` |
| R4 | **管控目录外**（`internal` 标签） | 仅 `.h`, `.hpp`（头文件） |

**可见性标签：**

| label | 定义 |
|---|---|
| `inc` | inc-pkg-conf.csv 中 `type=inc` 目录下的文件（对外公开 API） |
| `pkg` | inc-pkg-conf.csv 中 `type=pkg` 目录下的文件（包级 API） |
| `internal` | 组件内未被 inc-pkg-conf.csv 覆盖的文件 |

**输出格式：** `component, label, filename, path`（path 为相对于组件根的路径）

### AR3 — AST 解析（混合引擎）

#### 引擎选择规则

| 规则 | 扩展名 | 引擎 | 说明 |
|---|---|---|---|
| E1 | `.h`, `.hpp` | **libclang** | 语义级 API 声明解析 |
| E2 | `.cpp`, `.cxx`, `.cc`, `.c` | **Tree-sitter** | 语法级宏 + 函数解析 |
| E3 | `.py` | **Python `ast`** | 标准库 |

**E1.1 子规则 — STL/重库预检：** `.h`/`.hpp` 文件在 libclang 解析前，先扫描前 8KB 是否包含以下头文件：

```
vector, map, set, list, deque, queue, stack, string, memory,
algorithm, functional, iterator, numeric, utility, tuple,
thread, mutex, condition_variable, future, atomic,
iostream, fstream, sstream, regex, chrono, random,
unordered_map, unordered_set, type_traits,
nlohmann, json, boost, Eigen, opencv, protobuf, grpc, Python.h, pybind11
```

命中 → **直接切换 Tree-sitter**（避免 libclang AST 爆炸至 100k+ cursor）。

**E1.2 子规则 — 超时回退：** libclang `walk_preorder()` 若超过 15ms 未完成 → `TimeoutError` → 回退 Tree-sitter。

**CANN 属性宏自动检测：** libclang 解析前扫描文件前 4KB → 提取 `__[a-z][a-z_]+__` 模式 → 过滤标准库宏 → 生成 `-D` 参数。核心属性：`__aicore__`（5,037 文件）、`__gm__`（1,177）、`__ubuf__`（758），共 30+ 种。结果按组件目录缓存。

#### 提取开关规则

| 规则 | `full_data_type` | label | API 声明 | 类型声明 |
|---|---|---|---|---|
| S1 | `False`（默认） | `inc` / `pkg` | ✅ | ✅ |
| S2 | `False`（默认） | `internal` | ✅ | ❌ |
| S3 | `True` | 任意 | ✅ | ✅ |

**提取公式：** `extract_types = full_data_type OR (label in ('inc', 'pkg'))`

#### API 声明提取（func-export.json）

| 规则 | 声明类型 | libclang CursorKind | Tree-sitter 节点 |
|---|---|---|---|
| A1 | `FunctionDecl` | `FUNCTION_DECL`, `FUNCTION_TEMPLATE` | `function_definition`, `declaration`(含 func_declarator), `template_declaration` |
| A2 | `CXXMethodDecl` | `CXX_METHOD`, `CONSTRUCTOR`, `DESTRUCTOR` | 同上 + `field_declaration` 含 func_declarator |
| A3 | `macro-function` | — | `preproc_function_def` |

**type 字段约束：**
- 不含 `template<...>` 前缀（角度括号深度匹配截断）
- 不含函数体 `{ ... }`（body node / initializer_list 截断 + 字符串兜底）
- 不含 doxygen 注释（byte-offset 精确索引）
- libclang `PARSE_SKIP_FUNCTION_BODIES` 模式自动剥离函数体

#### 类型声明提取（data-export.json）

| 规则 | 声明类型 | libclang CursorKind | Tree-sitter 节点 |
|---|---|---|---|
| T1 | `macro` | — | `preproc_def`（排除头文件防护宏） |
| T2 | `struct` | `STRUCT_DECL` | `struct_specifier`, `type_definition`(struct) |
| T3 | `class` | `CLASS_DECL` | `class_specifier` |
| T4 | `union` | `UNION_DECL` | `union_specifier` |
| T5 | `enum` | `ENUM_DECL` | `enum_specifier` |

**头文件防护宏排除规则：** `#ifndef GUARD` + `#define GUARD` 配对（检查 `preproc_ifdef` 内嵌子节点 + 兄弟节点）。额外排除 `__UNDEF_` / `__DEF_` 前缀的内部追踪宏。

#### 性能优化规则

| 规则 | 措施 | 效果 |
|---|---|---|
| P1 | 复用 libclang `Index`（全局单例） | 消除每文件创建开销 |
| P2 | 显式 `del tu` + 每 500 文件 `gc.collect()` | 防止 C++ 堆泄漏导致速度递减 |
| P3 | cursor kind 预过滤（仅处理 8 种目标 kind） | 遍历量降低 90%+ |
| P4 | STL 预检（E1.1 子规则） | 避免 libclang AST 爆炸（32.6% 文件绕开） |
| P5 | walk_preorder 超时检测（15ms/500 cursors） | 兜底保护 |
| P6 | `-D` 宏按组件缓存 | 避免每文件重复扫描 |

#### 精确度保障规则

| 规则 | 措施 |
|---|---|
| Q1 | **byte-offset 源提取**：`cursor.extent.offset` 是 byte offset → 用 `source_bytes[offset]` 索引后 `.decode()`。中文等多字节 UTF-8 字符下不使用 `str[offset]` |
| Q2 | **template 前缀剥离**：检查 `raw.startswith('template ')` 而非仅 `FUNCTION_TEMPLATE`，覆盖 `CXX_METHOD` 含模版上下文 |
| Q3 | **`[[deprecated]]` 检测**：libclang extent 不含属性 → 向前搜索 200 bytes |
| Q4 | **cursor 去重**：`walk_preorder()` 可能重复 → `(file, line, kind_name, spelling)` 去重 |
| Q5 | **函数体兜底截断**：字符串级 `{` 截断（处理 Tree-sitter 无法正确解析的 `__aicore__` 边界情况） |
| Q6 | **参数文本回退**：当 `get_arguments()` 返回空/残缺时，用 `_text_extract_params` 从 type 文本解析参数。libclang 检测占位符（name+type 均为空）、参数数量不足等情况自动触发 |
| Q7 | **Tree-sitter declarator 路径修正**：`declaration`/`function_definition` 节点的 `declarator` 字段可能指向 `init_declarator` 等包装节点，需遍历子节点查找内嵌 `function_declarator` 后提取参数 |

### AR4 — 结果输出

**func-export.json：**
```jsonc
{
  "rel/path/to/file.h": [{
    "func_name": "tsDevSendMsgAsync",
    "kind": "FunctionDecl",                  // FunctionDecl | CXXMethodDecl | macro-function
    "label": "inc",                          // inc | pkg | internal
    "location": "/abs/path/file.h:17:0",     // path:line:col
    "extra_info": {
      "deprecated": "",                       // "deprecated" if detected
      "is_definition": false,
      "parameters": [["name", "type", "", "defaultValue"], ...],
      "return": "void",
      "type": "void tsDevSendMsgAsync(const uint32_t eventType, const uint32_t waitType = 3)",
      "visibility": "extern"                 // extern | internal | macro | public | protected | private
    }
  }]
}
```

**data-export.json：**
```jsonc
{
  "rel/path/to/file.h": [{
    "data_name": "TS_INNER_SUCCESS",
    "kind": "macro",                         // macro | struct | class | union | enum
    "path": "ts_api.h",
    "label": "inc",
    "location": "/abs/path/file.h:17:0",
    "source": "#define TS_INNER_SUCCESS 1"
  }]
}
```

**位置格式（统一规则）：** 全部使用 1-based line:col。libclang `cursor.location.line`/`column` 已是 1-based；Tree-sitter `node.start_point` 是 0-based → `+1`；Python `ast node.lineno` 是 1-based 但 `col_offset` 是 0-based → `+1`。

## API declaration style coverage

19 styles tested, 27 test cases, 100% pass rate. Run: `python3 -m unittest tests/test_ar3_accuracy.py -v`

| # | Category | Engine |
|---|---|---|
| S01 | `extern "C"` block | libclang |
| S02 | extern C function | libclang |
| S03 | Chinese comments + function (byte-offset test) | libclang |
| S04 | extern C++ function | libclang |
| S05 | inline function definition | libclang |
| S06 | static function | libclang |
| S07 | `__aicore__` function | libclang |
| S08 | `__aicore__` constructor + init list | libclang |
| S09 | template function | libclang |
| S10 | template class member | libclang |
| S11 | class member (public/protected/private) | libclang |
| S12 | explicit constructor | libclang |
| S13 | class-outside method definition | libclang |
| S14 | template class-outside definition | libclang |
| S15 | operator overload | libclang |
| S16 | `[[deprecated]]` attribute | libclang |
| S17 | struct/class/enum/union/typedef (7 variants) | libclang |
| S18 | macro function | Tree-sitter |
| S19 | object macro + header guard exclusion | Tree-sitter |

## Technology assumptions

- **Dependencies**: `pip install clang tree-sitter tree-sitter-c tree-sitter-cpp`
- **libclang**: macOS `/Library/Developer/CommandLineTools/usr/lib/libclang.dylib`、`/opt/homebrew/opt/llvm/lib/libclang.dylib`；Linux `libclang.so`
- **Parse mode**: `PARSE_SKIP_FUNCTION_BODIES | PARSE_INCOMPLETE`（不编译，不链接，纯静态）
- **C++ standard**: `-std=c++17 -fsyntax-only`
- **Output**: JSON（func-export.json, data-export.json）+ CSV（file-list.csv）

## Known limitations

| 限制 | 原因 | 影响 | 状态 |
|---|---|---|---|
| 构造函数初始化列表被剥离 | `PARSE_SKIP_FUNCTION_BODIES` 将 `: x(a), y(b)` 与 `{}` 一起跳过 | `type` 字段缺少初始化列表 | 已知 |
| `is_definition` 可能为 `False` | body 被跳过 | 不影响 `type` 准确性 | 已知 |
| 参数提取 1 例残留 (0.007%) | libclang 极端边界 case | 仅 1 个函数 | 待修复 |
| 对象式宏不纳入 | design.md SR2 约束 | 宏常量不可见 | 设计约束 |

详细优化方案见 `design/impl_plan.md`。

## libclang cursor kind mapping

| libclang `CursorKind` | Output `kind` | Notes |
|---|---|---|
| `FUNCTION_DECL` | `FunctionDecl` | Global, static, inline, extern functions |
| `CXX_METHOD` | `CXXMethodDecl` | Class member functions |
| `CONSTRUCTOR` | `CXXMethodDecl` | |
| `DESTRUCTOR` | `CXXMethodDecl` | |
| `FUNCTION_TEMPLATE` | `FunctionDecl` | `template<...>` prefix stripped from `type` |
| `STRUCT_DECL` | `struct` | C/C++ `struct` |
| `CLASS_DECL` | `class` | C++ `class`（独立类别，区别于 struct） |
| `UNION_DECL` | `union` | |
| `ENUM_DECL` | `enum` | |
