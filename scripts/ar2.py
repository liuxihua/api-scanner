"""AR2 — 文件清单生成

遍历组件目录，基于 inc-pkg-conf.csv 的管控目录配置，生成全量待分析文件清单 file-list.csv。

过滤规则：
  - 包含: inc-pkg-conf.csv 中 inc/pkg 目录下的所有源文件 + 组件内全量头文件
  - 排除: */experimental/*, */tests/*, */build/*, */output/*, */stub/*

输入：component_info (list[dict]), inc_pkg_config_path (str)
输出：file-list.csv (component, label, filename, path)
"""

import csv
import fnmatch
import os
from typing import List, Dict, Set


# ——— 排除目录 ———
EXCLUDE_DIRS = {"experimental", "tests", "build", "output", "stub", "examples"}

# ——— inc 管控目录下需要扫描的源文件扩展名 ———
MANAGED_EXTENSIONS = {".h", ".hpp", ".cpp", ".cxx", ".cc", ".py"}

# ——— 全量头文件扩展名（不限管控目录） ———
HEADER_EXTENSIONS = {".h", ".hpp"}

# ——— C++ 源文件扩展名（用于区分 .h 归属） ———
CPP_SOURCE_EXTENSIONS = {".cpp", ".cxx", ".cc", ".hpp"}


def _load_config(csv_path: str) -> List[Dict[str, str]]:
    """加载 inc-pkg-conf.csv，返回规则列表。

    Returns:
        [{"component": "runtime", "type": "inc", "patterns": ["include/dfx/", "include/log/", ...]}, ...]
    """
    rules = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="|")
        for row in reader:
            component = row["component"].strip()
            rtype = row["type"].strip()
            raw_patterns = row["path"].strip()
            if not component or not rtype or not raw_patterns:
                continue
            patterns = [p.strip() for p in raw_patterns.split(";") if p.strip()]
            rules.append({"component": component, "type": rtype, "patterns": patterns})
    return rules


def _is_cpp_component(component_root: str) -> bool:
    """启发式判断组件是否为 C++ 项目（影响 .h 文件的解析器选择）。

    若组件目录下存在 .cpp / .cxx / .cc / .hpp 文件，视为 C++ 组件。
    """
    cpp_count = 0
    for root, dirs, files in os.walk(component_root):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in CPP_SOURCE_EXTENSIONS:
                cpp_count += 1
                if cpp_count >= 3:
                    return True
    return cpp_count > 0


def _should_exclude(rel_path: str) -> bool:
    """检查相对路径是否命中排除目录。"""
    parts = rel_path.replace("\\", "/").split("/")
    return bool(EXCLUDE_DIRS & set(parts))


def _match_pattern(rel_path: str, patterns: List[str]) -> bool:
    """检查文件相对路径是否匹配任一 inc-pkg-conf 模式。

    模式规则：
      - 末尾带 / 的匹配目录前缀（如 "include/" 匹配 include/ 下所有文件）
      - 否则为 glob 模式（* 匹配单段路径）
    """
    normalized = rel_path.replace("\\", "/")
    for pat in patterns:
        # 目录前缀匹配
        if pat.endswith("/"):
            if normalized.startswith(pat):
                return True
        # fnmatch glob 匹配（文件名级别）
        elif fnmatch.fnmatch(normalized, pat):
            return True
        # 也尝试匹配路径末段（如 "*/op_host/op_api/*.h"）
        elif fnmatch.fnmatch(normalized, pat):
            return True
    return False


def generate_file_list(
    components: List[Dict[str, str]],
    config_csv: str,
    output_csv: str = "output/file-list.csv",
) -> str:
    """遍历组件目录，生成 file-list.csv。

    Args:
        components: AR1 产出的组件信息列表
        config_csv: inc-pkg-conf.csv 的路径
        output_csv: 输出 CSV 文件路径

    Returns:
        输出 CSV 文件的路径
    """
    rules = _load_config(config_csv)

    # 按组件名建立规则索引
    rule_map: Dict[str, Dict[str, List[str]]] = {}  # {component: {"inc": [...], "pkg": [...]}}
    for r in rules:
        comp = r["component"]
        if comp not in rule_map:
            rule_map[comp] = {"inc": [], "pkg": []}
        rule_map[comp][r["type"]] = r["patterns"]

    rows: List[Dict[str, str]] = []
    seen: Set[str] = set()  # 去重：(component, rel_path)

    for comp_info in components:
        comp_name = comp_info["component"]
        comp_root = comp_info["path"]
        comp_rules = rule_map.get(comp_name, {"inc": [], "pkg": []})
        inc_patterns = comp_rules["inc"]
        pkg_patterns = comp_rules["pkg"]

        for dirpath, dirnames, filenames in os.walk(comp_root):
            # 跳过排除目录
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]

            for fname in filenames:
                abs_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(abs_path, comp_root)

                if _should_exclude(rel_path):
                    continue

                ext = os.path.splitext(fname)[1].lower()

                # 判定可见性
                label = "internal"
                if _match_pattern(rel_path, inc_patterns):
                    label = "inc"
                elif _match_pattern(rel_path, pkg_patterns):
                    label = "pkg"

                # 过滤规则（requirements-func.md AR2）：
                # - inc/pkg 管控目录下：收集 MANAGED_EXTENSIONS 中的所有文件
                # - internal（非管控目录）：仅收集头文件
                if label in ("inc", "pkg"):
                    if ext not in MANAGED_EXTENSIONS:
                        continue
                else:
                    if ext not in HEADER_EXTENSIONS:
                        continue

                # 去重
                key = (comp_name, rel_path)
                if key in seen:
                    continue
                seen.add(key)

                rows.append({
                    "component": comp_name,
                    "label": label,
                    "filename": fname,
                    "path": rel_path,
                })

    # 写入 CSV
    os.makedirs(os.path.dirname(output_csv), exist_ok=True)
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["component", "label", "filename", "path"])
        writer.writeheader()
        writer.writerows(rows)

    # 统计
    inc_count = sum(1 for r in rows if r["label"] == "inc")
    pkg_count = sum(1 for r in rows if r["label"] == "pkg")
    internal_count = sum(1 for r in rows if r["label"] == "internal")

    print(f"[AR2] 文件清单已生成: {output_csv}")
    print(f"      总计 {len(rows)} 个文件 (inc: {inc_count}, pkg: {pkg_count}, internal: {internal_count})")

    return output_csv
