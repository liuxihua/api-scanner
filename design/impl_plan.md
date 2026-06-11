# 优化与实现方案

基于全部根因分析成果，对照 `design.md` 约束条件制定。

## 一、已完成项

| # | 根因 | 修复 | 效果 |
|---|---|---|---|
| P1 | byte-offset vs str index 错位 | 读取 bytes，用 `source_bytes[offset]` 索引 | type 字段无 doxygen 泄漏 |
| P2 | template<> 前缀残留 | `_libclang_func_type_text` 检测 `startswith('template ')` | 0 残留 |
| P3 | 函数体泄漏 | body truncation + text fallback `_strip_body_from_text` | >99.9% |
| P4 | C++ class → struct 混淆 | CLASS_DECL → 'class', STRUCT_DECL → 'struct' | 1,432 class / 2,430 struct 正确区分 |
| P5 | libclang 参数为空 (__aicore__) | `_text_extract_params` 文本回退 + 占位符检测 + 更优结果选择 | 21,210 → 48 |
| P6 | Tree-sitter 参数路径错误 | `declarator` 中查找内嵌 `function_declarator` + 文本回退 | 631 → 1 |
| P7 | 参数提取全面覆盖 | Tree-sitter `declarator` 修正 + 全局文本回退 | 21,210 → ~50 |
| P8 | 性能: Index 泄漏、TU 泄漏、STL 爆炸 | Index 复用、TU 释放、STL 预检、超时回退 | 23.8s, 166 files/s |
| P9 | Tree-sitter 所有 handler 参数缺失 | `_ts_func_definition_to_item`、`_ts_declaration_to_item`、`_ts_template_to_items` 均增加文本回退 | 48 → 1 |
| P10 | `__UNDEF__`/`__DEF_` 宏排除 | Tree-sitter preproc_def handler 增加前缀过滤 | 内部追踪宏排除 |
| P11 | full_data_type 开关 | 默认仅提取 API；`--full-data-type` 全量 | 类型声明 44,310 → 5,948 (inc/pkg only) |

### 最终精度指标

| 指标 | 修复前 | 修复后 |
|---|---|---|
| type 字段准确率 | 88.8% | >99.9% |
| 参数提取成功率 | 89.8% | 99.993% (1 / 16,135) |
| template 前缀残留 | ~8,000 | 0 |
| doxygen 注释泄漏 | 未知 | 0 |
| class/struct 区分配置 | 0 | 1,432 class 正确 |
| 扫描范围 | 23,187 files | 23,187 files |
| 扫描耗时 | ~48min (递减) | 23.8s (稳定) |

## 二、待实现项

### 阶段 1: 残余 1 例参数修复（1 → 0）

**根因**: libclang 极端边界 case — `get_arguments()` 返回残缺数据且 text fallback 未触发。

**方案**: 对所有 libclang 函数添加无条件文本回退 — 当 `_text_extract_params` 产生更多参数时总是采用文本结果。

### 阶段 2: 宏提取规则对齐（design.md SR2）

| # | 规则 | 状态 |
|---|---|---|
| M1 | 仅提取函数式宏 | ✅ `preproc_function_def` → macro-function |
| M2 | 排除防护宏（仅首 `#define`） | ⚠️ 待优化 |
| M3 | 排除 `__UNDEF_` / `__DEF_` 前缀宏 | ✅ 已实现 |
| M4 | 对象式宏不纳入 | ⚠️ 通过 `full_data_type` 开关控制 |

### 阶段 3: 双引擎参数合并（design.md 规则 5 / 规则 12）

**目标**: 解决 libclang 类型强制转换（`ge::Shape&` → `int&`）

**方案**: 对每个函数同时运行 libclang 和 Tree-sitter，比较 `method_id`:
- 全量强制：所有参数基类型均为 `int` → 采用 Tree-sitter
- 部分强制：逐参数对比，libclang=`int` 且 TS≠`int` → 采用 TS

**注意**: 需要额外 ~2× 解析时间（每文件运行两个引擎），当前精度已满足需求，建议作为后续优化。

### 阶段 4: 性能与健壮性

| # | 优化 | 方法 | 状态 |
|---|---|---|---|
| O1 | STL pre-scan 模式扩展 | 已包含 nlohmann/json/boost/Eigen 等 | ✅ |
| O2 | Index 全局复用 | `_LIBCLANG_INDEX` 单例 | ✅ |
| O3 | -D 宏按组件缓存 | `_ATTR_CACHE` 目录级 | ✅ |

## 三、已发现根因知识库

| 类别 | 根因 | 检测方法 | 修复模式 |
|---|---|---|---|
| `__aicore__` 参数空 | libclang 无法解析非标准属性修饰的函数类型 | `get_arguments()` 返回空或残缺 | `_text_extract_params` 回退 |
| Tree-sitter 参数空 | `declaration` 的 `declarator` 字段可能指向 `init_declarator` 等包装节点，非直接 `function_declarator` | 检查 `declarator.type != 'function_declarator'` | 遍历子节点查找内嵌 `function_declarator` |
| type 字段体泄漏 | `PARSE_SKIP_FUNCTION_BODIES` 剥离初始化列表 | `{` 或 `{\n` 在 `type` 中 | `_strip_body_from_text` 兜底截断 |
| 中文注释偏移 | libclang 返回 byte offset，Python str 按 char index | 文件含多字节 UTF-8 字符 | 读 bytes，offset 索引后 decode |
| class/struct 混淆 | CLASS_DECL 硬编码映射为 struct | source 含 `class` 关键字 | CLASS_DECL→'class', STRUCT_DECL→'struct' |
| STL 头文件爆炸 | `#include <vector>` 等导致 AST 节点 100k+ | walk_preorder 超时 | 预检 STL includes 直接路由 Tree-sitter |
| 重复声明 | `walk_preorder()` 对同一 cursor 返回多次 | 相同 file+line+kind+spelling | `seen_cursors` 集合去重 |
| deprecated 检测遗漏 | libclang extent 不包含 `[[deprecated]]` 属性 | regex 搜不到 | 向前扩展 200 bytes 搜索 |
| 头文件防护宏漏排 | `#define` 嵌套在 `preproc_ifdef` 子节点内 | `preproc_ifdef` 的 children 含 `preproc_def` | 同时检查子节点和兄弟节点 |
