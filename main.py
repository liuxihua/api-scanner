#!/usr/bin/env python3
"""CANN API Scanner — 主入口

按照 AR1→AR4 流水线顺序执行：
  AR1: 接收并验证用户输入的组件绝对路径
  AR2: 遍历组件目录，生成待分析文件清单 file-list.csv
  AR3: 使用 Tree-sitter / ast 解析每个文件，提取 API 与类型声明
  AR4: 按照规范格式输出 func-export.json 和 data-export.json

用法:
  python main.py /path/to/cann/component-a /path/to/cann/component-b

依赖:
  pip install tree-sitter tree-sitter-c tree-sitter-cpp
"""

import argparse
import os
import sys
import time

from scripts.ar1 import get_component_paths
from scripts.ar2 import generate_file_list
from scripts.ar3 import extract_declarations_with_root_map
from scripts.ar4 import write_output


def main():
    parser = argparse.ArgumentParser(
        description="CANN API Scanner — 静态 API 兼容性基线扫描工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py /home/user/cann/runtime /home/user/cann/ops-math
  python main.py --config design/inc-pkg-conf.csv /path/to/comp
  python main.py --output-dir results/ /path/to/comp1 /path/to/comp2
        """,
    )
    parser.add_argument(
        "component_paths",
        nargs="+",
        help="一个或多个组件绝对路径",
    )
    parser.add_argument(
        "--config",
        default="design/inc-pkg-conf.csv",
        help="inc-pkg-conf.csv 配置文件路径（默认: design/inc-pkg-conf.csv）",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="输出目录（默认: output）",
    )
    parser.add_argument(
        "--skip-ar3",
        action="store_true",
        help="仅执行 AR1 + AR2（生成 file-list.csv 后停止）",
    )
    parser.add_argument(
        "--full-data-type",
        action="store_true",
        default=False,
        help="开启全量数据类型提取（macro/struct/union/enum），默认仅提取 API 声明",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("CANN API Scanner")
    print("=" * 60)

    start_time = time.time()

    # —— AR1 ——
    components = get_component_paths(args.component_paths)

    # —— AR2 ——
    file_list_csv = os.path.join(args.output_dir, "file-list.csv")
    generate_file_list(components, args.config, file_list_csv)

    if args.skip_ar3:
        elapsed = time.time() - start_time
        print(f"\n[完成] AR1+AR2 执行完毕（耗时 {elapsed:.1f}s），跳过 AR3+AR4")
        return

    # —— AR3 ——
    component_path_map = {c["component"]: c["path"] for c in components}
    func_data, type_data = extract_declarations_with_root_map(
        file_list_csv,
        component_path_map,
        full_data_type=args.full_data_type,
    )

    # —— AR4 ——
    func_path, data_path = write_output(func_data, type_data, args.output_dir)

    elapsed = time.time() - start_time
    print(f"\n[完成] 全流水线执行完毕（耗时 {elapsed:.1f}s）")
    print(f"  API 声明: {func_path}")
    print(f"  类型声明: {data_path}")


if __name__ == "__main__":
    main()
