"""AR3 — AST 解析与声明提取（混合引擎）

引擎分配策略：
  - .h / .hpp → libclang (clang.cindex)  语义级精确解析，处理 __aicore__ 等非标准属性
  - .cpp / .cxx / .cc / .c → Tree-sitter 语法级解析，专注宏定义和复杂模版返回类型
  - .py → Python ast 模块

libclang 特性：
  - PARSE_SKIP_FUNCTION_BODIES：跳过函数体，仅提取声明
  - 自动检测源文件中的 __xxx__ 模式，生成 -D 参数以消除未知属性错误
  - 严格路径过滤：仅保留声明位置属于当前文件的函数
  - 解析失败时自动回退 Tree-sitter

输入：file-list.csv
输出：func_data (dict), type_data (dict) — 供 AR4 格式化写入
"""

import ast as py_ast
import csv
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Set


# ═══════════════════════════════════════════════════════════════════════
#  标准 C/C++ 预定义宏（排除在自动检测之外）
# ═══════════════════════════════════════════════════════════════════════

_STD_MACROS: Set[str] = {
    '__cplusplus', '__FILE__', '__LINE__', '__func__', '__FUNCTION__',
    '__PRETTY_FUNCTION__', '__GNUC__', '__clang__', '__STDC__',
    '__STDC_VERSION__', '__has_include', '__has_feature',
    '__has_builtin', '__has_attribute', '__has_cpp_attribute',
    '__extension__', '__restrict', '__restrict__', '__inline',
    '__inline__', '__const', '__const__', '__volatile', '__volatile__',
    '__attribute__', '__declspec', '__builtin_available',
    '__BEGIN_DECLS', '__END_DECLS', '__THROW', '__THROWNL',
    '__nonnull', '__wur', '__format__', '__asm__',
    '__BEGIN_NAMESPACE', '__END_NAMESPACE', '__typeof__',
    '__forceinline__', '__linux__', '__host__',
}

# CANN 已知核心属性（即使不在源码中出现也会预定义）
_KNOWN_CANN_ATTRS = {
    '__aicore__', '__gm__', '__ubuf__', '__cbuf__', '__fbuf__',
    '__simd_callee__', '__simd_vf__', '__simt_vf__', '__simt_callee__',
    '__local_mem__', '__global__', '__ca__', '__cc__', '__cb__',
}


# ═══════════════════════════════════════════════════════════════════════
#  libclang 后端
# ═══════════════════════════════════════════════════════════════════════

_LIBCLANG_AVAILABLE = False
_LIBCLANG_CINDEX = None  # 缓存的 clang.cindex 模块引用
_LIBCLANG_INDEX = None   # 复用的 clang Index 实例（避免每文件创建）


def _init_libclang():
    """初始化 libclang，配置动态库路径。缓存 Index 和 cindex 模块引用。"""
    global _LIBCLANG_AVAILABLE, _LIBCLANG_CINDEX, _LIBCLANG_INDEX
    if _LIBCLANG_AVAILABLE:
        return True
    try:
        from clang import cindex as cidx

        # macOS: 尝试标准路径
        candidates = [
            '/Library/Developer/CommandLineTools/usr/lib/libclang.dylib',
            '/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/libclang.dylib',
            '/opt/homebrew/opt/llvm/lib/libclang.dylib',
        ]
        for path in candidates:
            if os.path.exists(path):
                cidx.Config.set_library_file(path)
                _LIBCLANG_AVAILABLE = True
                _LIBCLANG_CINDEX = cidx
                _LIBCLANG_INDEX = cidx.Index.create()
                return True

        # Linux: 自动查找
        try:
            cidx.Config.set_library_file('libclang.so')
            _LIBCLANG_AVAILABLE = True
            _LIBCLANG_CINDEX = cidx
            _LIBCLANG_INDEX = cidx.Index.create()
            return True
        except Exception:
            pass

        # 最后尝试：不设置路径，使用系统默认
        try:
            _LIBCLANG_AVAILABLE = True
            _LIBCLANG_CINDEX = cidx
            _LIBCLANG_INDEX = cidx.Index.create()
            return True
        except Exception:
            pass

        print("[AR3] 警告：未找到 libclang 动态库，回退至 Tree-sitter")
        return False
    except ImportError:
        print("[AR3] 警告：clang 未安装（pip install clang），回退至 Tree-sitter")
        return False


def _extract_attrs_from_source(source_text: str) -> Set[str]:
    """扫描源码前 4KB，提取所有 __xxx__ 模式（非标准宏）。"""
    attrs: Set[str] = set()
    for m in re.finditer(r'\b(__[a-z][a-z_]+__)\b', source_text):
        name = m.group(1)
        if name not in _STD_MACROS:
            attrs.add(name)
    return attrs


_ATTR_CACHE: Dict[str, Set[str]] = {}  # 按文件路径缓存属性检测结果


def _get_cached_attrs(file_abs: str, source_text: str) -> Set[str]:
    """按组件目录缓存属性检测结果（避免每文件重复扫描前 4KB）。"""
    comp_dir = os.path.dirname(file_abs)
    if comp_dir in _ATTR_CACHE:
        return _ATTR_CACHE[comp_dir]
    attrs = _KNOWN_CANN_ATTRS | _extract_attrs_from_source(source_text)
    _ATTR_CACHE[comp_dir] = attrs
    return attrs


def _get_libclang_defines(file_abs: str, source_text: str) -> List[str]:
    """获取 libclang -D 参数列表（自动检测 + 组件级缓存）。"""
    all_attrs = _get_cached_attrs(file_abs, source_text)
    return [f'-D{attr}=' for attr in sorted(all_attrs)]


def _parse_with_libclang(
    file_abs: str,
    label: str,
    component: str,
    full_data_type: bool = False,
) -> Tuple[List[Dict], List[Dict]]:
    """使用 libclang 解析单个 .h/.hpp 文件。

    性能优化:
      - 复用全局 Index（避免每文件创建）
      - 显式释放 TranslationUnit（防止 C++ 堆泄漏）
      - 跳过外部文件子树（避免 99% 无效遍历）
      - -D 宏按组件缓存

    Args:
        full_data_type: 本文件是否需要提取类型声明（由主循环根据开关+label计算）

    Returns:
        (func_items, type_items)
    """
    cidx = _LIBCLANG_CINDEX

    # 读取源码（bytes 用于 byte-offset 索引，str 用于文本匹配）
    try:
        with open(file_abs, 'rb') as f:
            source_bytes = f.read()
        source_text = source_bytes.decode('utf-8', errors='ignore')
    except (IOError, PermissionError):
        return [], []

    # 获取 -D 参数（组件级缓存）
    defines = _get_libclang_defines(file_abs, source_text)

    # 复用全局 Index 解析
    args = ['-x', 'c++', '-std=c++17', '-fsyntax-only'] + defines
    options = (
        cidx.TranslationUnit.PARSE_SKIP_FUNCTION_BODIES
        | cidx.TranslationUnit.PARSE_INCOMPLETE
    )

    tu = None
    try:
        tu = _LIBCLANG_INDEX.parse(file_abs, args=args, options=options)
    except Exception:
        return [], []

    func_items: List[Dict] = []
    type_items: List[Dict] = []
    seen_cursors: Set[tuple] = set()

    # 性能优化: walk_preorder 超时检测（STL 头文件可能产生 100k+ 节点）
    _walk_start = time.time()
    _walk_timeout = 0.015  # 15ms 超时
    _total_cursors = 0

    for cursor in tu.cursor.walk_preorder():
        _total_cursors += 1

        # 超时检查：每 500 个 cursor 检查一次
        if _total_cursors % 500 == 0 and (time.time() - _walk_start) > _walk_timeout:
            del tu
            raise TimeoutError(f"libclang walk_preorder timeout ({_total_cursors} cursors, {time.time()-_walk_start:.0f}ms)")

        kind = cursor.kind

        # 跳过来自其他文件的整个子树（排除 TRANSLATION_UNIT 根）
        if kind != cidx.CursorKind.TRANSLATION_UNIT and kind not in (
            cidx.CursorKind.FUNCTION_DECL, cidx.CursorKind.CXX_METHOD,
            cidx.CursorKind.CONSTRUCTOR, cidx.CursorKind.DESTRUCTOR,
            cidx.CursorKind.FUNCTION_TEMPLATE,
            cidx.CursorKind.STRUCT_DECL, cidx.CursorKind.CLASS_DECL,
            cidx.CursorKind.UNION_DECL, cidx.CursorKind.ENUM_DECL,
            cidx.CursorKind.NAMESPACE,
        ):
            continue

        # 去重
        cursor_key = (cursor.location.file.name if cursor.location.file else '',
                       cursor.location.line,
                       kind.name,
                       cursor.spelling)
        if cursor_key in seen_cursors:
            continue
        seen_cursors.add(cursor_key)

        # ── 函数声明 → FunctionDecl ──
        if kind == cidx.CursorKind.FUNCTION_DECL:
            item = _libclang_to_func_item(cursor, file_abs, label, source_bytes, source_text, 'FunctionDecl')
            if item:
                func_items.append(item)

        # ── 类成员函数 → CXXMethodDecl ──
        elif kind == cidx.CursorKind.CXX_METHOD:
            item = _libclang_to_func_item(cursor, file_abs, label, source_bytes, source_text, 'CXXMethodDecl')
            if item:
                func_items.append(item)

        # ── 构造函数 ──
        elif kind == cidx.CursorKind.CONSTRUCTOR:
            item = _libclang_to_func_item(cursor, file_abs, label, source_bytes, source_text, 'CXXMethodDecl')
            if item:
                func_items.append(item)

        # ── 析构函数 ──
        elif kind == cidx.CursorKind.DESTRUCTOR:
            item = _libclang_to_func_item(cursor, file_abs, label, source_bytes, source_text, 'CXXMethodDecl')
            if item:
                func_items.append(item)

        # ── 函数模版 ──
        elif kind == cidx.CursorKind.FUNCTION_TEMPLATE:
            item = _libclang_to_func_item(cursor, file_abs, label, source_bytes, source_text, 'FunctionDecl')
            if item:
                func_items.append(item)

        # ── struct ──
        elif full_data_type and kind == cidx.CursorKind.STRUCT_DECL:
            item = _libclang_to_type_item(cursor, file_abs, label, source_bytes, source_text, 'struct')
            if item:
                type_items.append(item)

        # ── class (C++ class → kind="class") ──
        elif full_data_type and kind == cidx.CursorKind.CLASS_DECL:
            item = _libclang_to_type_item(cursor, file_abs, label, source_bytes, source_text, 'class')
            if item:
                type_items.append(item)

        # ── union ──
        elif full_data_type and kind == cidx.CursorKind.UNION_DECL:
            item = _libclang_to_type_item(cursor, file_abs, label, source_bytes, source_text, 'union')
            if item:
                type_items.append(item)

        # ── enum ──
        elif full_data_type and kind == cidx.CursorKind.ENUM_DECL:
            item = _libclang_to_type_item(cursor, file_abs, label, source_bytes, source_text, 'enum')
            if item:
                type_items.append(item)

    # 性能优化: 显式释放 TranslationUnit (防止 C++ 堆内存泄漏)
    if tu is not None:
        try:
            del tu
        except Exception:
            pass

    return func_items, type_items


def _cursor_has_file(cursor, file_abs: str) -> bool:
    """检查 cursor 的声明位置是否属于当前文件。"""
    loc = cursor.location
    if loc.file is None:
        return False
    return os.path.abspath(loc.file.name) == os.path.abspath(file_abs)


def _libclang_source_range(cursor, source_bytes: bytes) -> str:
    """从 cursor.extent 提取源码字符串（使用 byte offset，避免中文等多字节字符错位）。"""
    extent = cursor.extent
    if extent.start.file is None:
        return ''
    try:
        raw = source_bytes[extent.start.offset:extent.end.offset]
        return raw.decode('utf-8', errors='ignore')
    except (IndexError, UnicodeDecodeError):
        return ''


def _libclang_location(cursor, file_abs: str) -> str:
    """生成 location 字符串: path:line:col"""
    return f"{file_abs}:{cursor.location.line}:{cursor.location.column}"


def _libclang_visibility(cursor, source_bytes: bytes) -> str:
    """推断函数可见性。"""
    cidx = _LIBCLANG_CINDEX
    access = cursor.access_specifier
    if access == cidx.AccessSpecifier.PUBLIC:
        return 'public'
    elif access == cidx.AccessSpecifier.PROTECTED:
        return 'protected'
    elif access == cidx.AccessSpecifier.PRIVATE:
        return 'private'
    # 检查存储类
    raw = _libclang_source_range(cursor, source_bytes)
    first_line = raw.split('\n')[0] if raw else ''
    if 'static' in first_line:
        return 'internal'
    return 'extern'


def _libclang_deprecated(cursor, source_bytes: bytes) -> str:
    """检测 deprecated 标记。

    libclang 的 extent 不包含 [[deprecated]] 属性（属性在 extent 之前），
    因此需要向前扩展搜索范围。
    """
    extent = cursor.extent
    if extent.start.file is None:
        return ''
    # extent 前最多搜索 200 bytes 寻找 deprecated 属性
    search_start = max(0, extent.start.offset - 200)
    search_end = extent.end.offset
    try:
        raw = source_bytes[search_start:search_end].decode('utf-8', errors='ignore')
    except (IndexError, UnicodeDecodeError):
        return ''
    if re.search(r'\[\[deprecated.*?\]\]|__attribute__\s*\(\s*\(\s*deprecated', raw):
        return 'deprecated'
    return ''


def _libclang_func_type_text(cursor, source_bytes: bytes) -> str:
    """提取函数声明字符串（不含函数体）。

    对于 FUNCTION_TEMPLATE，去除 template<...> 前缀。
    """
    cidx = _LIBCLANG_CINDEX

    raw = _libclang_source_range(cursor, source_bytes)

    # 去除 template<...> 前缀（FUNCTION_TEMPLATE 或含模版的 CXX_METHOD）
    if raw.startswith('template '):
        # 匹配尖括号：template <typename T, ...> → 找到末尾 >
        depth = 0
        decl_start = 0
        for i, ch in enumerate(raw):
            if ch == '<':
                depth += 1
            elif ch == '>':
                depth -= 1
                if depth == 0:
                    decl_start = i + 1
                    break
        if decl_start > 0:
            raw = raw[decl_start:].strip()

    return raw.strip()


def _libclang_to_func_item(
    cursor, file_abs: str, label: str, source_bytes: bytes, source_text: str, kind: str
) -> Optional[Dict]:
    """将 libclang cursor 转换为 func-export.json 条目。"""
    cidx = _LIBCLANG_CINDEX

    # 严格路径过滤
    if not _cursor_has_file(cursor, file_abs):
        return None

    func_name = cursor.spelling
    if not func_name:
        return None

    # 过滤：libclang 误将 for 循环识别为函数 → 跳过
    if func_name == 'for' or func_name in ('if', 'while', 'switch'):
        return None

    # 修复：libclang 将模板类成员函数的 spelling 返回为 Name<T,...>
    # 去除模板参数，保留纯函数名
    func_name = _strip_template_args(func_name)

    # 类型文本（使用 source_bytes 确保 byte-offset 正确）
    type_text = _libclang_func_type_text(cursor, source_bytes)

    # 返回类型
    ret_type = ''
    try:
        result_type = cursor.result_type
        if result_type and result_type.kind != cidx.TypeKind.INVALID:
            ret_type = result_type.spelling
    except:
        pass

    # 参数
    params = []
    for arg in cursor.get_arguments():
        param_name = arg.spelling or ''
        param_type = arg.type.spelling if arg.type else ''
        default_val = ''
        params.append([param_name, param_type, '', default_val])

    # 修复: libclang 无法解析 __aicore__ 等非标准属性的函数参数 → 文本级回退
    # design.md 规则 5: 双引擎合并 — 比较 get_arguments() 与文本提取，取更优结果
    _text_params = _text_extract_params(type_text) if type_text and '(' in type_text else []
    # 判断使用文本提取的条件:
    # 1. get_arguments() 返回空 → 用文本提取
    # 2. 文本提取返回更多参数 → libclang 可能遗漏，用文本提取
    # 3. get_arguments() 中存在占位符（name+type 均为空）→ 用文本提取
    _has_placeholder = any((not p[0] and not p[1]) for p in params)
    _text_has_more = len(_text_params) > len(params) and len(params) > 0
    if len(params) == 0 or _has_placeholder or _text_has_more or (len(_text_params) > 0 and len(params) > 0 and sum(1 for p in params if not p[0]) > len(_text_params)):
        if _text_params and (len(_text_params) >= len(params)):
            params = _text_params

    # 修复：type 文本因复杂默认值被截断 → 扩展搜索 source_bytes 补充关闭 )
    if len(params) == 0 and type_text.count('(') > type_text.count(')'):
        extent = cursor.extent
        search_start = extent.end.offset
        search_end = min(search_start + 200, len(source_bytes))
        for i in range(search_start, search_end):
            ch = source_bytes[i:i+1].decode('utf-8', errors='ignore')
            if ch == ')':
                expanded = type_text + source_bytes[search_start:i+1].decode('utf-8', errors='ignore')
                expanded_params = _text_extract_params(expanded)
                if expanded_params:
                    params = expanded_params
                    type_text = expanded
                break

    # 修复：libclang extent 完全错位时，从源码行正则搜索函数声明
    if len(params) == 0 and '(' in type_text:
        line_params, line_type = _extract_from_source_lines(
            source_text, func_name, cursor.location.line
        )
        if line_params:
            params = line_params
            if line_type:
                type_text = line_type

    # 是否为定义
    is_def = cursor.is_definition()

    # 可见性
    vis = _libclang_visibility(cursor, source_bytes)

    # 废弃标记
    deprecated = _libclang_deprecated(cursor, source_bytes)

    return {
        "func_name": func_name,
        "kind": kind,
        "label": label,
        "location": _libclang_location(cursor, file_abs),
        "extra_info": {
            "deprecated": deprecated,
            "is_definition": is_def,
            "parameters": params,
            "return": ret_type,
            "type": type_text,
            "visibility": vis,
        },
    }


def _libclang_to_type_item(
    cursor, file_abs: str, label: str, source_bytes: bytes, source_text: str, kind: str
) -> Optional[Dict]:
    """将 libclang cursor 转换为 data-export.json 条目。"""
    if not _cursor_has_file(cursor, file_abs):
        return None

    data_name = cursor.spelling
    if not data_name:
        return None

    source = _libclang_source_range(cursor, source_bytes).strip()
    if not source:
        source = cursor.displayname

    return {
        "data_name": data_name,
        "kind": kind,
        "path": os.path.basename(file_abs),
        "label": label,
        "location": _libclang_location(cursor, file_abs),
        "source": source,
    }


# ═══════════════════════════════════════════════════════════════════════
#  Tree-sitter 后端（.cpp / .cxx / .cc / .c）
# ═══════════════════════════════════════════════════════════════════════

_TS_AVAILABLE = False
_PARSER_C = None
_PARSER_CPP = None


def _init_tree_sitter():
    """初始化 Tree-sitter 解析器。"""
    global _TS_AVAILABLE, _PARSER_C, _PARSER_CPP
    if _TS_AVAILABLE:
        return True
    try:
        import tree_sitter_c as tsc
        import tree_sitter_cpp as tscpp
        from tree_sitter import Language, Parser

        _PARSER_C = Parser(Language(tsc.language()))
        _PARSER_CPP = Parser(Language(tscpp.language()))
        _TS_AVAILABLE = True
        return True
    except ImportError:
        print("[AR3] 错误：缺少 Tree-sitter 依赖。pip install tree-sitter tree-sitter-c tree-sitter-cpp")
        return False


def _parse_with_tree_sitter(
    file_abs: str,
    parser_type: str,
    label: str,
    component: str,
    full_data_type: bool = False,
) -> Tuple[List[Dict], List[Dict]]:
    """使用 Tree-sitter 解析单个文件（.cpp / .cxx / .cc / .c）。

    专注：函数声明 + 宏定义提取（排除头文件防护宏）。

    Args:
        full_data_type: 是否提取类型声明，False 时仅提取 API 声明
    """
    if not _init_tree_sitter():
        return [], []

    parser = _PARSER_CPP if parser_type == 'cpp' else _PARSER_C

    try:
        with open(file_abs, 'rb') as f:
            source_bytes = f.read()
    except (IOError, PermissionError):
        return [], []

    tree = parser.parse(source_bytes)
    root = tree.root_node

    func_items: List[Dict] = []
    type_items: List[Dict] = []
    seen_macros: Set[str] = set()
    header_guard: Optional[str] = _detect_header_guard(root, source_bytes)

    for node in _walk_ts(root):
        kind = node.type

        # ── 宏函数 ──
        if kind == 'preproc_function_def':
            name_node = node.child_by_field_name('name')
            if name_node is None:
                continue
            func_name = _ts_text(name_node, source_bytes)
            func_items.append({
                "func_name": func_name,
                "kind": "macro-function",
                "label": label,
                "location": _ts_location(file_abs, node),
                "extra_info": {
                    "deprecated": "",
                    "is_definition": True,
                    "parameters": _ts_extract_macro_params(node, source_bytes),
                    "return": "",
                    "type": _ts_text(node, source_bytes).strip(),
                    "visibility": "macro",
                },
            })
            continue

        # ── 对象宏（排除头文件防护宏 + __UNDEF__/__DEF__ 前缀）──
        # design.md SR2: 仅提取函数式宏，对象式宏不纳入。此处保留可选提取。
        if full_data_type and kind == 'preproc_def':
            name_node = node.child_by_field_name('name')
            if name_node is None:
                continue
            data_name = _ts_text(name_node, source_bytes)
            # 排除防护宏
            if data_name == header_guard:
                continue
            # 排除内部追踪宏
            if data_name.startswith('__UNDEF_') or data_name.startswith('__DEF_'):
                continue
            if data_name in seen_macros:
                continue
            seen_macros.add(data_name)
            type_items.append({
                "data_name": data_name,
                "kind": "macro",
                "path": os.path.basename(file_abs),
                "label": label,
                "location": _ts_location(file_abs, node),
                "source": _ts_text(node, source_bytes).strip(),
            })
            continue

        # ── 函数定义 ──
        if kind == 'function_definition':
            item = _ts_func_definition_to_item(node, source_bytes, file_abs, label)
            if item:
                func_items.append(item)
            continue

        # ── 函数声明 ──
        if kind == 'declaration':
            item = _ts_declaration_to_item(node, source_bytes, file_abs, label)
            if item:
                func_items.append(item)
            continue

        # ── 模版函数 ──
        if kind == 'template_declaration':
            items = _ts_template_to_items(node, source_bytes, file_abs, label)
            func_items.extend(items)
            continue

        # ── struct / union / enum / class ──
        if full_data_type and kind in ('struct_specifier', 'union_specifier', 'enum_specifier', 'class_specifier'):
            name_node = node.child_by_field_name('name')
            mapped = kind.replace('_specifier', '')  # struct_specifier→struct, class_specifier→class, ...
            data_name = _ts_text(name_node, source_bytes) if name_node else '<anonymous>'
            type_items.append({
                "data_name": data_name,
                "kind": mapped,
                "path": os.path.basename(file_abs),
                "label": label,
                "location": _ts_location(file_abs, node),
                "source": _ts_text(node, source_bytes).strip(),
            })
            continue

        # ── typedef struct / union / enum ──
        if full_data_type and kind == 'type_definition':
            item = _ts_typedef_to_item(node, source_bytes, file_abs, label)
            if item:
                type_items.append(item)

    return func_items, type_items


# ——— Tree-sitter 工具函数 ———


def _walk_ts(node, max_depth: int = 200):
    """带深度限制的遍历。"""
    if max_depth <= 0:
        return
    yield node
    for child in node.children:
        yield from _walk_ts(child, max_depth - 1)


def _ts_text(node, source_bytes: bytes) -> str:
    """提取节点对应源文本。"""
    return source_bytes[node.start_byte:node.end_byte].decode('utf-8', errors='replace')


def _ts_location(file_abs: str, node) -> str:
    row, col = node.start_point
    return f"{file_abs}:{row + 1}:{col}"


def _ts_extract_innermost_name(declarator_node, source_bytes: bytes, default: str = '???') -> str:
    if declarator_node.type in ('identifier', 'field_identifier'):
        return _ts_text(declarator_node, source_bytes)
    for child in declarator_node.children:
        if child.type in ('identifier', 'field_identifier', 'qualified_identifier'):
            return _ts_text(child, source_bytes)
    for child in declarator_node.children:
        if child.is_named:
            result = _ts_extract_innermost_name(child, source_bytes, '')
            if result:
                return result
    return default


def _ts_has_qualifier(declarator_node, source_bytes: bytes) -> bool:
    for child in _walk_ts(declarator_node):
        if child.type in ('qualified_identifier', 'destructor_name'):
            return True
    return '::' in _ts_text(declarator_node, source_bytes)


def _ts_is_inside_class(node) -> bool:
    p = node.parent
    while p is not None:
        if p.type in ('class_specifier', 'struct_specifier'):
            return True
        p = p.parent
    return False


def _ts_extract_params(func_declarator_node, source_bytes: bytes) -> List[List[str]]:
    params = []
    param_list = func_declarator_node.child_by_field_name('parameters')
    if param_list is None:
        return params
    for child in param_list.children:
        if child.type == 'parameter_declaration':
            type_node = child.child_by_field_name('type')
            param_type = _ts_text(type_node, source_bytes) if type_node else ''
            decl_node = child.child_by_field_name('declarator')
            param_name = _ts_extract_innermost_name(decl_node, source_bytes, '') if decl_node else ''
            default_node = child.child_by_field_name('default_value')
            default_val = _ts_text(default_node, source_bytes).strip() if default_node else ''
            params.append([param_name, param_type, '', default_val])
    return params


def _ts_extract_macro_params(preproc_func_node, source_bytes: bytes) -> List[List[str]]:
    """从 preproc_function_def 节点提取宏函数参数。

    #define ADD(a, b) ((a) + (b))
                ^^^^^ → [['a', '', '', ''], ['b', '', '', '']]
    """
    params = []
    params_node = preproc_func_node.child_by_field_name('parameters')
    if params_node is None:
        return params
    for child in params_node.children:
        if child.type == 'identifier':
            params.append([_ts_text(child, source_bytes), '', '', ''])
    return params


def _ts_find_access_spec(field_list_node, target_node, source_bytes: bytes) -> Optional[str]:
    result = None
    for child in field_list_node.children:
        if child == target_node:
            break
        if child.type == 'access_specifier':
            spec = _ts_text(child, source_bytes).strip().rstrip(':')
            if spec in ('public', 'protected', 'private'):
                result = spec
    return result


def _ts_determine_kind(node, source_bytes: bytes) -> str:
    declarator = node.child_by_field_name('declarator')
    if declarator is None:
        return 'FunctionDecl'
    if _ts_is_inside_class(node):
        return 'CXXMethodDecl'
    if _ts_has_qualifier(declarator, source_bytes):
        return 'CXXMethodDecl'
    return 'FunctionDecl'


def _ts_visibility(node, source_bytes: bytes) -> str:
    text = _ts_text(node, source_bytes)
    if re.search(r'\bstatic\b', text):
        return 'internal'
    if re.search(r'\bextern\b', text):
        return 'extern'
    p = node.parent
    while p is not None:
        if p.type in ('class_specifier', 'struct_specifier'):
            body = p.child_by_field_name('body')
            if body is not None:
                access = _ts_find_access_spec(body, node, source_bytes)
                if access:
                    return access
                return 'private' if p.type == 'class_specifier' else 'public'
        p = p.parent
    return 'extern'


def _ts_deprecated(text: str) -> str:
    if re.search(r'\[\[deprecated.*?\]\]|__attribute__\s*\(\s*\(\s*deprecated', text):
        return 'deprecated'
    return ''


def _ts_func_body_truncate(node, source_bytes: bytes) -> str:
    """截断函数体，仅保留声明部分。"""
    body = node.child_by_field_name('body')
    text = _ts_text(node, source_bytes).strip()
    if body is not None:
        decl_end = body.start_byte
        text = source_bytes[node.start_byte:decl_end].decode('utf-8', errors='replace').strip()
        text = text.rstrip().rstrip('{').strip()
    # 修复：非标准属性导致误解析的 initializer_list 函数体
    init_list = _ts_find_body_init_list(node, source_bytes)
    if init_list is not None:
        text = source_bytes[node.start_byte:init_list.start_byte].decode('utf-8', errors='replace').strip()
        text = text.rstrip().rstrip('{').strip()
    # 兜底：字符串级函数体截断（处理 __aicore__ 等 Tree-sitter 无法正确解析的边界情况）
    if '{' in text:
        text = _strip_body_from_text(text)
    return text


def _ts_find_body_init_list(node, source_bytes: bytes):
    """查找以 '{' 开头的 initializer_list（Tree-sitter 误解析的函数体）。"""
    for child in _walk_ts(node):
        if child.type == 'initializer_list':
            for c in child.children:
                if c.type == '{':
                    return child
    return None


def _strip_template_args(name: str) -> str:
    """去除 func_name 中的模板参数。

    libclang 对模板类成员函数的 cursor.spelling 返回：
      - 'Name<T, U>' → 应变为 'Name'
      - 'Class<T>::methodName' → 应变为 'methodName'
    使用深度匹配找到最外层 <> 并剥离。
    """
    if '<' not in name:
        return name

    # 模式1: Class<T>::methodName → 取 :: 后面的部分
    if '::' in name:
        after = name.rsplit('::', 1)[-1]
        # 去除 methodName 中的模板参数（如 operator< 中的 <）
        depth = 0
        start = -1
        for i, ch in enumerate(after):
            if ch == '<':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == '>':
                depth -= 1
                if depth == 0 and start >= 0:
                    return after[:start]
        return after

    # 模式2: Name<T, U> → 取 < 之前的部分
    depth = 0
    start = -1
    for i, ch in enumerate(name):
        if ch == '<':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '>':
            depth -= 1
            if depth == 0 and start >= 0:
                return name[:start]
    return name


def _extract_from_source_lines(
    source_text: str, func_name: str, line_no: int
) -> Tuple[List[List[str]], str]:
    """从源码行中正则搜索函数声明（libclang extent 完全错位时的最后回退）。

    使用 cursor.location.line 定位，搜索 func_name( 并提取到匹配的 )。
    返回 (params, type_text)。
    """
    lines = source_text.split('\n')
    start_line = max(0, line_no - 1)
    end_line = min(len(lines), line_no + 20)

    for i in range(start_line, end_line):
        line = lines[i]
        match = re.search(r'\b' + re.escape(func_name) + r'\s*\(', line)
        if not match:
            continue

        # 从函数名位置开始拼接多行声明
        decl_start_in_line = match.start()
        # 向前找返回类型（同行 func_name 之前的部分 + 前一行）
        prefix = line[:decl_start_in_line].strip()
        if not prefix and i > 0:
            prefix = lines[i-1].strip()
        full_decl = (prefix + ' ' if prefix else '') + line[decl_start_in_line:]

        # 向后拼接多行直到匹配的 )
        for j in range(i + 1, min(i + 15, len(lines))):
            nline = chr(10)
            has_brace = '{' + nline in full_decl or ' {' in full_decl
            if has_brace:
                break
            full_decl += ' ' + lines[j].strip()
            # 检查是否有匹配的 )
            depth = 0
            found_close = False
            func_start = full_decl.find(func_name + '(')
            if func_start < 0:
                func_start = full_decl.find(func_name + ' (')
            if func_start < 0:
                break
            for k in range(func_start, len(full_decl)):
                ch = full_decl[k]
                if ch == '(': depth += 1
                elif ch == ')':
                    depth -= 1
                    if depth == 0:
                        found_close = True
                        break
            if found_close:
                break

        # 找到 ) 后截取到 )
        depth = 0
        close_idx = -1
        func_start = full_decl.find(func_name)
        if func_start >= 0:
            for k in range(func_start, len(full_decl)):
                if full_decl[k] == '(': depth += 1
                elif full_decl[k] == ')':
                    depth -= 1
                    if depth == 0:
                        close_idx = k
                        break

        if close_idx > 0:
            decl = full_decl[:close_idx + 1].strip()
            params = _text_extract_params(decl)
            return params, decl

    return [], ''


def _text_extract_params(type_text: str) -> List[List[str]]:
    """从函数声明文本中提取参数列表（文本级回退）。

    处理 libclang 因 __aicore__ 等非标准属性无法提取参数的情况。
    支持: 'void foo(int a, const std::string& b, float c = 3.0)'
    返回: [['a', 'int', '', ''], ['b', 'const std::string&', '', '3.0'], ['c', 'float', '', '']]
    """
    if '(' not in type_text:
        return []

    # 提取括号内参数列表（处理嵌套括号）
    paren_start = type_text.find('(')
    depth = 0
    paren_end = -1
    for i in range(paren_start, len(type_text)):
        ch = type_text[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                paren_end = i
                break

    if paren_end <= paren_start + 1:
        return []

    inner = type_text[paren_start + 1:paren_end].strip()
    if not inner or inner == 'void':
        return []

    # 按逗号分割（处理嵌套模板、括号）
    parts = _split_params(inner)
    params = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 分离默认值
        default_val = ''
        eq_idx = _find_default_eq(part)
        if eq_idx > 0:
            default_val = part[eq_idx + 1:].strip()
            part = part[:eq_idx].strip()

        # 分离类型和名称：从右向左找最后一个标识符
        param_name, param_type = _split_type_name(part)
        params.append([param_name, param_type, '', default_val])

    return params


def _split_params(inner: str) -> List[str]:
    """按逗号分割参数列表，处理嵌套 <> ()  [[]] {}。"""
    parts = []
    depth = 0  # <> () [] {}
    current_start = 0
    for i, ch in enumerate(inner):
        if ch in '<({[':
            depth += 1
        elif ch in '>)}]':
            depth -= 1
        elif ch == ',' and depth == 0:
            parts.append(inner[current_start:i])
            current_start = i + 1
    if current_start < len(inner):
        parts.append(inner[current_start:])
    return parts


def _find_default_eq(part: str) -> int:
    """在参数片段中找默认值的 '=' 位置（跳过嵌套）。"""
    depth = 0
    for i, ch in enumerate(part):
        if ch in '<({[':
            depth += 1
        elif ch in '>)}]':
            depth -= 1
        elif ch == '=' and depth == 0:
            return i
    return -1


def _split_type_name(part: str) -> Tuple[str, str]:
    """从 'const std::string& name' 中分离 (name, type) 或 ('', whole)。

    策略：从右向左找到不在嵌套中的空白/运算符边界，左侧为类型，右侧为名称。
    """
    part = part.strip()
    if not part:
        return '', ''

    # 从右向左扫描找名称起始位置
    depth = 0
    name_start = -1
    for i in range(len(part) - 1, -1, -1):
        ch = part[i]
        if ch in '>)}]':
            depth += 1
        elif ch in '<({[':
            depth -= 1
        elif depth == 0 and (ch.isspace() or ch in '*&'):
            if i == 0:
                # 全是类型，无名
                return '', part
            # 继续向左找实际标识符起始
            name_start = i
            break
        elif depth == 0 and i > 0 and not ch.isspace() and ch not in '*&':
            # 找到标识符字符，可能是名称的一部分
            continue

    if name_start < 0:
        # 没有找到分隔点 → 可能是只有类型没有名称，或只有名称
        return '', part

    # 跳过空白找到实际标识符起始
    j = name_start
    while j >= 0 and part[j].isspace():
        j -= 1
    while j >= 0 and not part[j].isspace() and part[j] not in '*&':
        j -= 1

    param_type = part[:name_start].strip()
    param_name = part[name_start:].strip().lstrip('*&').strip()
    return param_name, param_type


def _strip_body_from_text(type_text: str) -> str:
    """字符串级兜底：去除函数体（从函数参数列表后的 { 截断）。

    用于处理 Tree-sitter 因 __aicore__ 等非标准属性无法正确识别函数体边界的情况。
    只在 Tree-sitter 的 node-level 截断失败后作为最后手段调用。
    """
    # 找到最后一个未嵌套的 ) 之后紧跟的 {
    # 策略：从后向前扫描，找到函数参数列表结束位置
    depth = 0  # () [] <> 嵌套深度
    last_paren = -1
    for i, ch in enumerate(type_text):
        if ch in '([<':
            depth += 1
        elif ch in ')]>':
            depth -= 1
            if depth == 0 and ch == ')':
                last_paren = i
    if last_paren > 0:
        after_paren = type_text[last_paren + 1:].lstrip()
        if after_paren.startswith('{'):
            return type_text[:last_paren + 1].strip()
    # 备选: 直接找 { 的开始（仅在最高嵌套层级）
    depth = 0
    for i, ch in enumerate(type_text):
        if ch in '([<':
            depth += 1
        elif ch in ')]>':
            depth -= 1
        elif ch == '{' and depth == 0:
            return type_text[:i].strip().rstrip('{').strip()
    return type_text


def _ts_func_definition_to_item(node, source_bytes, file_abs, label) -> Optional[Dict]:
    declarator = node.child_by_field_name('declarator')
    if declarator is None:
        return None
    body = node.child_by_field_name('body')
    func_name = _ts_extract_innermost_name(declarator, source_bytes)
    func_name = _strip_template_args(func_name)  # 去除 libclang/TS 拼入的模板参数
    func_kind = _ts_determine_kind(node, source_bytes)
    ret_type_node = node.child_by_field_name('type')
    ret_type = _ts_text(ret_type_node, source_bytes) if ret_type_node else ''
    # 查找内含的 function_declarator（declarator 可能是包装节点）
    func_decl = declarator
    if func_decl.type != 'function_declarator':
        for child in _walk_ts(declarator):
            if child.type == 'function_declarator':
                func_decl = child
                break
    params = _ts_extract_params(func_decl, source_bytes)
    type_text = _ts_func_body_truncate(node, source_bytes)
    # 文本级回退 + 源码行正则搜索
    if len(params) == 0:
        text_params = _text_extract_params(type_text)
        if text_params:
            params = text_params
        else:
            source_text = source_bytes.decode('utf-8', errors='ignore')
            line_no = node.start_point[0] + 1
            line_params, line_type = _extract_from_source_lines(source_text, func_name, line_no)
            if line_params:
                params = line_params
                if line_type: type_text = line_type
    return {
        "func_name": func_name,
        "kind": func_kind,
        "label": label,
        "location": _ts_location(file_abs, node),
        "extra_info": {
            "deprecated": _ts_deprecated(type_text),
            "is_definition": body is not None,
            "parameters": params,
            "return": ret_type,
            "type": type_text,
            "visibility": _ts_visibility(node, source_bytes),
        },
    }


def _ts_declaration_to_item(node, source_bytes, file_abs, label) -> Optional[Dict]:
    declarator = node.child_by_field_name('declarator')
    if declarator is None:
        return None
    # 查找内含的 function_declarator（declarator 可能是 init_declarator 等包装节点）
    func_decl = declarator
    if func_decl.type != 'function_declarator':
        for child in _walk_ts(declarator):
            if child.type == 'function_declarator':
                func_decl = child
                break
    has_func = (func_decl.type == 'function_declarator')
    if not has_func:
        return None

    func_name = _ts_extract_innermost_name(declarator, source_bytes)
    func_name = _strip_template_args(func_name)
    func_kind = _ts_determine_kind(node, source_bytes)
    ret_type_node = node.child_by_field_name('type')
    ret_type = _ts_text(ret_type_node, source_bytes) if ret_type_node else ''
    params = _ts_extract_params(func_decl, source_bytes)
    type_text = _ts_text(node, source_bytes).strip().rstrip(';').strip()

    is_def = False
    init_list = _ts_find_body_init_list(node, source_bytes)
    if init_list is not None:
        is_def = True
        type_text = source_bytes[node.start_byte:init_list.start_byte].decode('utf-8', errors='replace').strip()
        type_text = type_text.rstrip().rstrip('{').strip()

    # 文本级回退 + 源码行正则搜索
    if len(params) == 0:
        text_params = _text_extract_params(type_text)
        if text_params:
            params = text_params
        else:
            source_text = source_bytes.decode('utf-8', errors='ignore')
            line_no = node.start_point[0] + 1
            line_params, line_type = _extract_from_source_lines(source_text, func_name, line_no)
            if line_params:
                params = line_params
                if line_type: type_text = line_type

    return {
        "func_name": func_name,
        "kind": func_kind,
        "label": label,
        "location": _ts_location(file_abs, node),
        "extra_info": {
            "deprecated": _ts_deprecated(type_text),
            "is_definition": is_def,
            "parameters": params,
            "return": ret_type,
            "type": type_text,
            "visibility": _ts_visibility(node, source_bytes),
        },
    }


def _ts_template_to_items(node, source_bytes, file_abs, label) -> List[Dict]:
    """从 template_declaration 提取函数声明（不含 template<> 前缀及函数体）。"""
    items = []
    for child in node.children:
        if child.type not in ('function_definition', 'declaration'):
            continue
        inner_decl = child.child_by_field_name('declarator')
        if inner_decl is None:
            continue
        body = child.child_by_field_name('body')
        func_name = _ts_extract_innermost_name(inner_decl, source_bytes)
        func_name = _strip_template_args(func_name)
        func_kind = _ts_determine_kind(child, source_bytes)
        ret_type_node = child.child_by_field_name('type')
        ret_type = _ts_text(ret_type_node, source_bytes) if ret_type_node else ''
        params = _ts_extract_params(inner_decl, source_bytes)

        # 使用内层 child 的源文本（不含 template<> 前缀）
        if body is not None:
            type_text = source_bytes[child.start_byte:body.start_byte].decode('utf-8', errors='replace').strip()
            type_text = type_text.rstrip().rstrip('{').strip()
            is_def = True
        else:
            type_text = _ts_text(child, source_bytes).strip().rstrip(';').strip()
            is_def = False
            init_list = _ts_find_body_init_list(child, source_bytes)
            if init_list is not None:
                is_def = True
                type_text = source_bytes[child.start_byte:init_list.start_byte].decode('utf-8', errors='replace').strip()
                type_text = type_text.rstrip().rstrip('{').strip()

        # 文本级回退
        if len(params) == 0:
            text_params = _text_extract_params(type_text)
            if text_params:
                params = text_params

        items.append({
            "func_name": func_name,
            "kind": func_kind,
            "label": label,
            "location": _ts_location(file_abs, node),
            "extra_info": {
                "deprecated": _ts_deprecated(type_text),
                "is_definition": is_def,
                "parameters": params,
                "return": ret_type,
                "type": type_text,
                "visibility": _ts_visibility(child, source_bytes),
            },
        })
        break  # 一个 template_declaration 只取第一个函数
    return items


def _ts_typedef_to_item(node, source_bytes, file_abs, label) -> Optional[Dict]:
    inner = node.child_by_field_name('declarator') or node.child_by_field_name('value')
    if inner is None or inner.type not in ('struct_specifier', 'union_specifier', 'enum_specifier'):
        return None
    decl_name_node = node.child_by_field_name('declarator')
    if decl_name_node is None:
        for child in node.children:
            if child.type in ('type_identifier', 'identifier'):
                decl_name_node = child
                break
    inner_name_node = inner.child_by_field_name('name')
    inner_name = _ts_text(inner_name_node, source_bytes) if inner_name_node else '<anonymous>'
    typedef_name = _ts_text(decl_name_node, source_bytes).strip() if decl_name_node else inner_name
    return {
        "data_name": typedef_name,
        "kind": inner.type.replace('_specifier', ''),
        "path": os.path.basename(file_abs),
        "label": label,
        "location": _ts_location(file_abs, node),
        "source": _ts_text(node, source_bytes).strip(),
    }


def _detect_header_guard(root, source_bytes: bytes) -> Optional[str]:
    """检测头文件防护宏（#ifndef GUARD / #define GUARD 模式）。

    Tree-sitter 将 #define 嵌套在 #ifndef 内部，需同时检查子节点和兄弟节点。
    """
    children = list(root.children) if root.children else []
    for i, child in enumerate(children):
        if child.type == 'preproc_ifdef':
            name_node = child.child_by_field_name('name')
            if name_node is None:
                continue
            ifndef_name = _ts_text(name_node, source_bytes)
            # 检查 preproc_ifdef 内部子节点中的 #define
            for inner in child.children:
                if inner.type == 'preproc_def':
                    inner_name = inner.child_by_field_name('name')
                    if inner_name and _ts_text(inner_name, source_bytes) == ifndef_name:
                        return ifndef_name
            # 也检查兄弟节点中的 #define
            for j in range(i + 1, min(i + 5, len(children))):
                next_child = children[j]
                if next_child.type == 'preproc_def':
                    next_name = next_child.child_by_field_name('name')
                    if next_name and _ts_text(next_name, source_bytes) == ifndef_name:
                        return ifndef_name
    return None


# ═══════════════════════════════════════════════════════════════════════
#  Python ast 后端
# ═══════════════════════════════════════════════════════════════════════


def _parse_with_python_ast(
    file_abs: str,
    label: str,
    component: str,
    full_data_type: bool = False,
) -> Tuple[List[Dict], List[Dict]]:
    """使用 Python ast 模块解析 .py 文件。

    Args:
        full_data_type: 是否提取类型声明，False 时仅提取 API 声明
    """
    try:
        with open(file_abs, 'r', encoding='utf-8') as f:
            source = f.read()
    except (IOError, PermissionError):
        return [], []

    try:
        tree = py_ast.parse(source, filename=file_abs)
    except SyntaxError:
        return [], []

    # 建立 parent 引用
    for node in py_ast.walk(tree):
        for child in py_ast.iter_child_nodes(node):
            child._parent = node  # type: ignore

    func_items: List[Dict] = []
    type_items: List[Dict] = []

    for node in py_ast.walk(tree):
        # ── 函数定义 ──
        if isinstance(node, (py_ast.FunctionDef, py_ast.AsyncFunctionDef)):
            parent = getattr(node, '_parent', None)
            is_method = isinstance(parent, py_ast.ClassDef)

            params = []
            for arg in node.args.args:
                arg_type = py_ast.get_source_segment(source, arg.annotation) if arg.annotation else ''
                params.append([arg.arg, arg_type, '', ''])
            defaults = node.args.defaults
            if defaults:
                offset = len(params) - len(defaults)
                for i, d in enumerate(defaults):
                    params[offset + i][3] = py_ast.get_source_segment(source, d) or ''

            ret_type = py_ast.get_source_segment(source, node.returns) if node.returns else ''
            func_type_text = py_ast.get_source_segment(source, node) or ''

            if is_method:
                func_kind = 'CXXMethodDecl'
                visibility = 'public'
            else:
                func_kind = 'FunctionDecl'
                if node.name.startswith('__') and not node.name.endswith('__'):
                    visibility = 'private'
                elif node.name.startswith('_'):
                    visibility = 'internal'
                else:
                    visibility = 'extern'

            func_items.append({
                "func_name": node.name,
                "kind": func_kind,
                "label": label,
                "location": f"{file_abs}:{node.lineno}:{node.col_offset + 1}",
                "extra_info": {
                    "deprecated": '',
                    "is_definition": True,
                    "parameters": params,
                    "return": ret_type,
                    "type": func_type_text,
                    "visibility": visibility,
                },
            })

        # ── 类定义 ──
        elif full_data_type and isinstance(node, py_ast.ClassDef):
            class_def = py_ast.get_source_segment(source, node) or ''
            type_items.append({
                "data_name": node.name,
                "kind": 'struct',
                "path": os.path.basename(file_abs),
                "label": label,
                "location": f"{file_abs}:{node.lineno}:{node.col_offset + 1}",
                "source": class_def,
            })

    return func_items, type_items


# ═══════════════════════════════════════════════════════════════════════
#  文件分类 & 引擎调度
# ═══════════════════════════════════════════════════════════════════════

_HEADER_EXTS = {'.h', '.hpp'}
_SOURCE_EXTS = {'.cpp', '.cxx', '.cc', '.c'}

# STL/系统头文件模式 — 包含这些头文件的 .h/.hpp 会导致 libclang 产生 10 万+ cursor，
# 改用 Tree-sitter 解析（预检避免 50ms 超时浪费）
_STL_INCLUDE_PATTERN = re.compile(
    r'#include\s*[<"]('
    r'vector|map|set|list|deque|queue|stack|string|memory|'
    r'algorithm|functional|iterator|numeric|utility|tuple|'
    r'thread|mutex|condition_variable|future|atomic|'
    r'iostream|fstream|sstream|regex|chrono|random|'
    r'unordered_map|unordered_set|type_traits|'
    r'nlohmann|json|boost|Eigen|opencv|protobuf|grpc|'
    r'Python\.h|pybind11'
    r')'
)


def _has_heavy_includes(file_abs: str) -> bool:
    """预检文件是否包含 STL/系统头文件（会导致 libclang AST 爆炸）。"""
    try:
        with open(file_abs, 'rb') as f:
            head = f.read(8192)  # 前 8KB 足够覆盖 #include 区域
        return bool(_STL_INCLUDE_PATTERN.search(head.decode('utf-8', errors='ignore')))
    except (IOError, PermissionError):
        return False


def _classify_file(ext: str) -> Optional[str]:
    """根据扩展名分配解析引擎: 'libclang' | 'tree-sitter' | 'python' | None"""
    ext = ext.lower()
    if ext in _HEADER_EXTS:
        return 'libclang'
    if ext in _SOURCE_EXTS:
        return 'tree-sitter'
    if ext == '.py':
        return 'python'
    return None


# ═══════════════════════════════════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════════════════════════════════


def extract_declarations(
    file_list_csv: str,
) -> Tuple[Dict[str, List[Dict]], Dict[str, List[Dict]]]:
    """简化入口（需 component_path_map 时请用 extract_declarations_with_root_map）。"""
    return {}, {}


def extract_declarations_with_root_map(
    file_list_csv: str,
    component_path_map: Dict[str, str],
    full_data_type: bool = False,
) -> Tuple[Dict[str, List[Dict]], Dict[str, List[Dict]]]:
    """混合引擎主入口。

    Args:
        file_list_csv: AR2 产出的 file-list.csv
        component_path_map: {component_name: abs_root_path}
        full_data_type: 全量类型提取开关。
            False（默认）: 仅提取 inc/pkg 管控目录下类型声明 + 全部 API 声明
            True:           提取所有文件的全量类型声明 + 全部 API 声明

    Returns:
        (func_export, data_export)
    """
    libclang_ok = _init_libclang()
    ts_ok = _init_tree_sitter()

    if not ts_ok:
        print("[AR3] 致命错误：Tree-sitter 不可用（兜底引擎必须可用），退出")
        return {}, {}

    print(f"[AR3] 引擎状态: libclang={'✅' if libclang_ok else '❌(回退 TS)'}, tree-sitter={'✅' if ts_ok else '❌'}, full_data_type={full_data_type}")
    print("[AR3] 开始 AST 解析...")

    func_export: Dict[str, List[Dict]] = {}
    data_export: Dict[str, List[Dict]] = {}
    func_total = 0
    type_total = 0
    error_count = 0
    libclang_count = 0
    ts_count = 0
    py_count = 0

    with open(file_list_csv, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    for i, row in enumerate(rows):
        component = row['component']
        label = row['label']
        filename = row['filename']
        rel_path = row['path']

        ext = os.path.splitext(filename)[1].lower()
        engine = _classify_file(ext)

        if engine is None:
            continue

        comp_root = component_path_map.get(component)
        if comp_root is None:
            continue

        file_abs = os.path.join(comp_root, rel_path)
        if not os.path.isfile(file_abs):
            continue

        # 进度 & 周期性 GC（每 500 文件释放 libclang C++ 堆内存）
        if (i + 1) % 100 == 0:
            print(f"  [{i + 1}/{len(rows)}] 文件: {i + 1} | "
                  f"API: {func_total} | 类型: {type_total} | "
                  f"l={libclang_count} t={ts_count} p={py_count}")
        if (i + 1) % 500 == 0:
            import gc
            gc.collect()

        # 类型提取开关: full_data_type=True 或 文件在 inc/pkg 管控目录
        extract_types = full_data_type or (label in ('inc', 'pkg'))

        # 引擎调度
        try:
            if engine == 'libclang' and libclang_ok:
                # 预检：包含 STL/系统头文件 → 直接 Tree-sitter（避免 50ms 超时浪费）
                if _has_heavy_includes(file_abs):
                    parser_type = 'cpp'
                    funcs, types = _parse_with_tree_sitter(file_abs, parser_type, label, component, extract_types)
                    ts_count += 1
                else:
                    try:
                        funcs, types = _parse_with_libclang(file_abs, label, component, extract_types)
                        libclang_count += 1
                    except TimeoutError:
                        parser_type = 'cpp'
                        funcs, types = _parse_with_tree_sitter(file_abs, parser_type, label, component, extract_types)
                        ts_count += 1
            elif engine == 'tree-sitter':
                parser_type = 'cpp' if ext in ('.hpp', '.cpp', '.cxx', '.cc', '.h') else 'c'
                funcs, types = _parse_with_tree_sitter(file_abs, parser_type, label, component, extract_types)
                ts_count += 1
            elif engine == 'python':
                funcs, types = _parse_with_python_ast(file_abs, label, component, extract_types)
                py_count += 1
            else:
                # libclang 不可用时，.h/.hpp 回退 Tree-sitter
                parser_type = 'cpp'
                funcs, types = _parse_with_tree_sitter(file_abs, parser_type, label, component, extract_types)
                ts_count += 1
        except Exception as e:
            print(f"  [警告] 解析失败 {file_abs}: {e}")
            error_count += 1
            # 回退 Tree-sitter
            try:
                if engine == 'libclang':
                    parser_type = 'cpp'
                    funcs, types = _parse_with_tree_sitter(file_abs, parser_type, label, component, extract_types)
                    ts_count += 1
                else:
                    continue
            except:
                continue

        # 归并结果
        key = rel_path.replace('\\', '/')
        for fi in funcs:
            func_items = func_export.setdefault(key, [])
            func_items.append(fi)
            func_total += 1
        for ti in types:
            type_items = data_export.setdefault(key, [])
            type_items.append(ti)
            type_total += 1

    print(f"[AR3] AST 解析完成:")
    print(f"      扫描 {len(rows)} 文件 | 错误 {error_count}")
    print(f"      libclang: {libclang_count} | tree-sitter: {ts_count} | python: {py_count}")
    print(f"      API 声明: {func_total} 条")
    print(f"      类型声明: {type_total} 条")

    return func_export, data_export
