"""AR3 混合引擎准确性测试

覆盖 19 种 CANN API 声明样式，验证：
  - kind 类型正确
  - type 字段不含 template<> 前缀
  - type 字段不含函数体
  - visibility 正确
  - is_definition 正确
  - 函数名正确提取
"""

import csv
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from scripts.ar3 import extract_declarations_with_root_map

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')


def _run_scan(file_list):
    """运行扫描，返回 (func_data, type_data)"""
    comp_map = {'test_component': FIXTURES}
    csv_path = os.path.join(tempfile.gettempdir(), 'test_file_list.csv')
    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['component', 'label', 'filename', 'path'])
        writer.writeheader()
        for entry in file_list:
            writer.writerow({
                'component': 'test_component',
                'label': entry['label'],
                'filename': entry['filename'],
                'path': entry['path'],
            })
    func_data, type_data = extract_declarations_with_root_map(csv_path, comp_map, full_data_type=True)
    os.unlink(csv_path)
    return func_data, type_data


def _find_item(items, func_name):
    """按名称查找声明条目。"""
    return next((i for i in items if i['func_name'] == func_name), None)


def _find_type(items, data_name):
    """按名称查找类型条目。"""
    return next((i for i in items if i['data_name'] == data_name), None)


class TestAPIHeaderStyles(unittest.TestCase):
    """测试 .h 头文件中的 API 声明样式（libclang 引擎）"""

    @classmethod
    def setUpClass(cls):
        cls.func_data, cls.type_data = _run_scan([
            {'label': 'inc', 'filename': 'api_styles.h', 'path': 'api_styles.h'},
            {'label': 'inc', 'filename': 'api_styles.cpp', 'path': 'api_styles.cpp'},
        ])
        cls.funcs = cls.func_data.get('api_styles.h', [])
        cls.cpp_funcs = cls.func_data.get('api_styles.cpp', [])
        cls.types = cls.type_data.get('api_styles.h', [])
        cls.cpp_types = cls.type_data.get('api_styles.cpp', [])

    # ═══ S02: extern C 函数 ═══
    def test_s02_extern_c_func(self):
        """[S02] extern int32_t NormalizeScalar(...); — 全局函数"""
        item = _find_item(self.funcs, 'NormalizeScalar')
        self.assertIsNotNone(item, 'NormalizeScalar 未找到')
        self.assertEqual(item['kind'], 'FunctionDecl')
        self.assertFalse(item['extra_info']['is_definition'])
        t = item['extra_info']['type']
        self.assertNotIn('{\n', t, 'type 不应含函数体')
        self.assertIn('NormalizeScalar', t)

    # ═══ S03: 含中文注释的函数声明 ═══
    def test_s03_chinese_comment_no_body(self):
        """[S03] 中文注释 + extern int32_t HcommWrite(...); — type 无偏移错位"""
        item = _find_item(self.funcs, 'HcommWrite')
        self.assertIsNotNone(item, 'HcommWrite 未找到')
        self.assertEqual(item['kind'], 'FunctionDecl')
        t = item['extra_info']['type']
        self.assertNotIn('@param', t, 'type 不应含 doxygen 注释（byte offset 错位）')
        self.assertNotIn('@return', t, 'type 不应含 doxygen 注释')
        self.assertIn('HcommWrite', t)

    # ═══ S07: __aicore__ 函数 ═══
    def test_s07_aicore_func_no_body(self):
        """[S07] __aicore__ inline void CheckSysWorkspace(...) { } — 不含函数体"""
        item = _find_item(self.funcs, 'CheckSysWorkspace')
        self.assertIsNotNone(item, 'CheckSysWorkspace 未找到')
        t = item['extra_info']['type']
        self.assertNotIn('{\n', t, 'type 不应含函数体')
        self.assertNotIn('AscendCExit', t, 'type 不应含函数体实现代码')
        self.assertIn('CheckSysWorkspace', t)

    # ═══ S08: __aicore__ 构造函数 + 初始化列表 ═══
    def test_s08_aicore_constructor_no_body(self):
        """[S08] __aicore__ inline Tensor(int a, int b) : x(a), y(b) {} — 构造函数
        已知限制: PARSE_SKIP_FUNCTION_BODIES 会连带初始化列表一起跳过"""
        items = [i for i in self.funcs if i['func_name'] == 'Tensor']
        self.assertGreaterEqual(len(items), 1, 'Tensor 构造函数未找到')
        item = items[0]
        self.assertEqual(item['kind'], 'CXXMethodDecl')
        t = item['extra_info']['type']
        self.assertNotIn('{\n', t, 'type 不应含函数体')
        self.assertIn('Tensor', t)

    # ═══ S09: 模版函数（不含 template<> 前缀） ═══
    def test_s09_template_no_prefix(self):
        """[S09] template<typename T> T MaxValue(...) { } — 不含 template<> 前缀"""
        item = _find_item(self.funcs, 'MaxValue')
        self.assertIsNotNone(item, 'MaxValue 未找到')
        self.assertEqual(item['kind'], 'FunctionDecl')
        t = item['extra_info']['type']
        self.assertFalse(t.startswith('template '), f'type 不应以 template 开头: {repr(t[:60])}')
        self.assertIn('MaxValue', t)

    # ═══ S11: 类成员函数（public / protected / private） ═══
    def test_s11a_public_method(self):
        """[S11] public: int32_t Process(...); — visibility = public"""
        item = _find_item(self.funcs, 'Process')
        self.assertIsNotNone(item, 'Process 未找到')
        self.assertEqual(item['kind'], 'CXXMethodDecl')
        self.assertEqual(item['extra_info']['visibility'], 'public')

    def test_s11b_protected_virtual(self):
        """[S11] protected: virtual int64_t GetTiling(...) = 0; — visibility = protected"""
        item = _find_item(self.funcs, 'GetTiling')
        self.assertIsNotNone(item, 'GetTiling 未找到')
        self.assertEqual(item['kind'], 'CXXMethodDecl')
        self.assertEqual(item['extra_info']['visibility'], 'protected')
        t = item['extra_info']['type']
        self.assertNotIn('{\n', t)

    def test_s11c_private_method(self):
        """[S11] private: void InternalReset(); — visibility = private"""
        item = _find_item(self.funcs, 'InternalReset')
        self.assertIsNotNone(item, 'InternalReset 未找到')
        self.assertEqual(item['kind'], 'CXXMethodDecl')
        self.assertEqual(item['extra_info']['visibility'], 'private')

    # ═══ S12: explicit 构造函数 ═══
    def test_s12_explicit_constructor(self):
        """[S12] explicit OpDef(const char *name); — 构造函数声明"""
        items = [i for i in self.funcs if i['func_name'] == 'OpDef']
        self.assertGreaterEqual(len(items), 1, 'OpDef 构造函数未找到')
        item = items[0]
        t = item['extra_info']['type']
        self.assertIn('explicit', t)
        self.assertNotIn('{\n', t)

    # ═══ S13: 类外成员函数定义 ═══
    def test_s13_class_outside_method(self):
        """[S13] inline int32_t DataProcessor::Process(...) { } — 类外定义
        已知限制: PARSE_SKIP_FUNCTION_BODIES 导致 is_definition=False"""
        items = [i for i in self.funcs if i['func_name'] == 'Process']
        outside = [i for i in items if 'DataProcessor::' in i['extra_info']['type']]
        self.assertGreaterEqual(len(outside), 1, '类外 Process 定义未找到')
        t = outside[0]['extra_info']['type']
        self.assertNotIn('{\n', t, 'type 不应含函数体')
        self.assertIn('DataProcessor::Process', t)

    # ═══ S14: 模版类外定义 ═══
    def test_s14_template_class_outside_no_prefix(self):
        """[S14] template <typename T> T Calculator<T>::Add(...) — 不含 template<>"""
        items = [i for i in self.funcs if i['func_name'] == 'Add']
        outside = [i for i in items if 'Calculator' in i['extra_info']['type']]
        self.assertGreaterEqual(len(outside), 1, '模版类外 Add 定义未找到')
        t = outside[0]['extra_info']['type']
        self.assertFalse(t.startswith('template '), f'type 不应以 template 开头: {repr(t[:60])}')
        self.assertNotIn('{\n', t, 'type 不应含函数体')

    # ═══ S15: 运算符重载 ═══
    def test_s15a_operator_eq(self):
        """[S15] bool operator==(const Coord &other) const { }"""
        items = [i for i in self.funcs if 'operator' in i['func_name']]
        self.assertGreaterEqual(len(items), 2, f'运算符重载应>=2, 实际: {len(items)}')
        eq_item = _find_item(self.funcs, 'operator==')
        self.assertIsNotNone(eq_item, 'operator== 未找到')

    def test_s15b_operator_assign(self):
        """[S15] Coord &operator=(const Coord &other) { }"""
        item = _find_item(self.funcs, 'operator=')
        self.assertIsNotNone(item, 'operator= 未找到')
        t = item['extra_info']['type']
        self.assertNotIn('{\n', t, 'type 不应含函数体')

    # ═══ S16: [[deprecated]] 属性 ═══
    def test_s16_deprecated(self):
        """[S16] [[deprecated]] void OldFunc(int x); — deprecated 标记"""
        item = _find_item(self.funcs, 'OldFunc')
        self.assertIsNotNone(item, 'OldFunc 未找到')
        self.assertEqual(item['extra_info']['deprecated'], 'deprecated')

    # ═══ S17: 类型声明 ═══
    def test_s17a_struct(self):
        """[S17] struct Point { double x, y; };"""
        item = _find_type(self.types, 'Point')
        self.assertIsNotNone(item, 'Point 未找到')
        self.assertEqual(item['kind'], 'struct')

    def test_s17b_class(self):
        """[S17] class CalculatorV2 { ... }; → kind=class"""
        item = _find_type(self.types, 'CalculatorV2')
        self.assertIsNotNone(item, 'CalculatorV2 未找到')
        self.assertEqual(item['kind'], 'class')

    def test_s17c_enum_class(self):
        """[S17] enum class Status { SUCCESS, FAILURE };"""
        item = _find_type(self.types, 'Status')
        self.assertIsNotNone(item, 'Status 未找到')
        self.assertEqual(item['kind'], 'enum')

    def test_s17d_typedef_enum(self):
        """[S17] typedef enum { COLOR_RED, ... } Color;"""
        item = _find_type(self.types, 'Color')
        self.assertIsNotNone(item, 'Color 未找到')

    def test_s17e_union(self):
        """[S17] union DataUnion { int32_t i; float f; char c[4]; };"""
        item = _find_type(self.types, 'DataUnion')
        self.assertIsNotNone(item, 'DataUnion 未找到')
        self.assertEqual(item['kind'], 'union')

    def test_s17f_typedef_struct(self):
        """[S17] typedef struct { int w, h; } ImageSize;"""
        item = _find_type(self.types, 'ImageSize')
        self.assertIsNotNone(item, 'ImageSize 未找到')

    # ═══ 无函数体残留检查 ═══
    def test_no_body_in_any_type(self):
        """所有 API 声明的 type 字段不应含函数体"""
        all_funcs = self.funcs + self.cpp_funcs
        failures = []
        for item in all_funcs:
            t = item.get('extra_info', {}).get('type', '')
            k = item.get('kind', '')
            if k in ('FunctionDecl', 'CXXMethodDecl') and '{\n' in t:
                failures.append(f"{item['func_name']}: {t[:100]}")
        self.assertEqual(len(failures), 0,
                         f'{len(failures)} 条声明含函数体:\n' + '\n'.join(failures[:5]))

    # ═══ 无 template<> 前缀检查 ═══
    def test_no_template_prefix(self):
        """所有 API 声明的 type 字段不应以 template 开头"""
        all_funcs = self.funcs + self.cpp_funcs
        failures = []
        for item in all_funcs:
            t = item.get('extra_info', {}).get('type', '')
            if t.startswith('template '):
                failures.append(f"{item['func_name']}: {t[:100]}")
        self.assertEqual(len(failures), 0,
                         f'{len(failures)} 条声明含 template 前缀:\n' + '\n'.join(failures[:5]))


class TestCPPMacroStyles(unittest.TestCase):
    """测试 .cpp 文件中的宏声明样式（Tree-sitter 引擎）"""

    @classmethod
    def setUpClass(cls):
        cls.func_data, cls.type_data = _run_scan([
            {'label': 'inc', 'filename': 'api_styles.cpp', 'path': 'api_styles.cpp'},
        ])
        cls.funcs = cls.func_data.get('api_styles.cpp', [])
        cls.types = cls.type_data.get('api_styles.cpp', [])

    def test_s18_macro_function(self):
        """[S18] #define ADD(a, b) ((a) + (b)) — 宏函数"""
        item = _find_item(self.funcs, 'ADD')
        self.assertIsNotNone(item, 'ADD 宏函数未找到')
        self.assertEqual(item['kind'], 'macro-function')
        self.assertEqual(item['extra_info']['visibility'], 'macro')
        self.assertTrue(item['extra_info']['is_definition'])

    def test_s18_max_of_three(self):
        """[S18] #define MAX_OF_THREE(x, y, z) ... — 多行宏函数"""
        item = _find_item(self.funcs, 'MAX_OF_THREE')
        self.assertIsNotNone(item, 'MAX_OF_THREE 未找到')
        self.assertEqual(item['kind'], 'macro-function')

    def test_s19_object_macro(self):
        """[S19] #define VERSION_MAJOR 1 — 对象宏"""
        item = _find_type(self.types, 'VERSION_MAJOR')
        self.assertIsNotNone(item, 'VERSION_MAJOR 未找到')
        self.assertEqual(item['kind'], 'macro')

    def test_header_guard_excluded(self):
        """头文件防护宏 TEST_MACROS_H 应被排除"""
        item = _find_type(self.types, 'TEST_MACROS_H')
        self.assertIsNone(item, '头文件防护宏应被排除')


class TestFunctionImplInCPP(unittest.TestCase):
    """测试 .cpp 中的函数实现（Tree-sitter 引擎）"""

    @classmethod
    def setUpClass(cls):
        cls.func_data, _ = _run_scan([
            {'label': 'inc', 'filename': 'api_styles.cpp', 'path': 'api_styles.cpp'},
        ])
        cls.funcs = cls.func_data.get('api_styles.cpp', [])

    def test_function_impl_in_cpp(self):
        """函数实现 type 不含函数体"""
        item = _find_item(self.funcs, 'NormalizeScalar')
        self.assertIsNotNone(item, 'NormalizeScalar 未找到')
        t = item['extra_info']['type']
        self.assertNotIn('{\n', t, 'type 不应含函数体')
        self.assertIn('NormalizeScalar', t)
        self.assertTrue(item['extra_info']['is_definition'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
