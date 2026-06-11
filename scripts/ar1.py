"""AR1 — 组件路径输入与验证

接收用户输入的组件绝对路径列表，验证路径存在并返回标准化的路径信息。

输入：命令行参数（组件绝对路径列表）
输出：list[dict] — 每个元素包含 component（路径末级目录名）和 path（绝对路径）
"""

import os
import sys
from typing import List, Dict


def get_component_paths(raw_paths: List[str]) -> List[Dict[str, str]]:
    """验证并标准化用户输入的组件路径。

    Args:
        raw_paths: 用户输入的路径列表（字符串）

    Returns:
        标准化后的组件信息列表：[{"component": "ops-math", "path": "/abs/path/to/ops-math"}, ...]

    Raises:
        SystemExit: 存在无效路径时直接退出
    """
    if not raw_paths:
        print("[AR1] 错误：未提供任何组件路径。用法: python main.py /path/to/comp1 /path/to/comp2 ...")
        sys.exit(1)

    components = []
    errors = []

    for raw in raw_paths:
        abs_path = os.path.abspath(raw)
        if not os.path.isdir(abs_path):
            errors.append(f"  路径不存在或不是目录: {abs_path}")
            continue

        component_name = os.path.basename(abs_path.rstrip("/"))
        components.append({
            "component": component_name,
            "path": abs_path,
        })

    if errors:
        print("[AR1] 错误：以下路径无效:\n" + "\n".join(errors))
        sys.exit(1)

    print(f"[AR1] 已接收 {len(components)} 个组件路径:")
    for c in components:
        print(f"  • {c['component']} → {c['path']}")

    return components
