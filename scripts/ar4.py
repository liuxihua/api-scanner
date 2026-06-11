"""AR4 — 结果格式化输出

按照 requirements-func.md AR4 定义的格式，将 AR3 提取的声明数据写入
func-export.json 和 data-export.json。

输入：func_data (dict), type_data (dict), output_dir (str)
输出：func-export.json, data-export.json
"""

import json
import os
from typing import Any, Dict, List, Tuple


def write_output(
    func_data: Dict[str, List[Dict[str, Any]]],
    type_data: Dict[str, List[Dict[str, Any]]],
    output_dir: str = "output",
) -> Tuple[str, str]:
    """将 AR3 的提取结果按照 AR4 格式写入 JSON 文件。

    func-export.json 格式（API 声明）:
      { "<rel_path>": [{ func_name, kind, label, location, extra_info: {...} }, ...] }

    data-export.json 格式（类型声明）:
      { "<rel_path>": [{ data_name, kind, path, label, location, source }, ...] }

    Args:
        func_data: API 声明字典（key=相对路径, value=声明列表）
        type_data: 类型声明字典（key=相对路径, value=声明列表）
        output_dir: 输出目录

    Returns:
        (func_export_path, data_export_path)
    """
    os.makedirs(output_dir, exist_ok=True)

    func_path = os.path.join(output_dir, "func-export.json")
    data_path = os.path.join(output_dir, "data-export.json")

    # ——— func-export.json ———
    func_output: Dict[str, List[Dict]] = {}
    for rel_path, items in func_data.items():
        formatted = []
        for item in items:
            formatted.append({
                "func_name": item["func_name"],
                "kind": item["kind"],
                "label": item["label"],
                "location": item["location"],
                "extra_info": {
                    "deprecated": item.get("extra_info", {}).get("deprecated", ""),
                    "is_definition": item.get("extra_info", {}).get("is_definition", False),
                    "parameters": item.get("extra_info", {}).get("parameters", []),
                    "return": item.get("extra_info", {}).get("return", ""),
                    "type": item.get("extra_info", {}).get("type", ""),
                    "visibility": item.get("extra_info", {}).get("visibility", "extern"),
                },
            })
        func_output[rel_path] = formatted

    with open(func_path, "w", encoding="utf-8") as f:
        json.dump(func_output, f, ensure_ascii=False, indent=2)

    # ——— data-export.json ———
    data_output: Dict[str, List[Dict]] = {}
    for rel_path, items in type_data.items():
        formatted = []
        for item in items:
            formatted.append({
                "data_name": item["data_name"],
                "kind": item["kind"],
                "path": item["path"],
                "label": item["label"],
                "location": item["location"],
                "source": item["source"],
            })
        data_output[rel_path] = formatted

    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data_output, f, ensure_ascii=False, indent=2)

    func_count = sum(len(v) for v in func_output.values())
    type_count = sum(len(v) for v in data_output.values())

    print(f"[AR4] 结果已输出:")
    print(f"      {func_path} — {func_count} 条 API 声明")
    print(f"      {data_path} — {type_count} 条类型声明")

    return func_path, data_path
