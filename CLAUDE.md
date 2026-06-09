# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This project builds a static API compatibility scanner for the [CANN](https://gitcode.com/cann) open-source ecosystem. It parses C/C++/Python source files across all CANN components to extract a baseline inventory of every public API declaration — used in PR pipelines to detect API changes (additions, modifications, deletions).

## Repository structure

```
design/
  requirements-func.md   # Full functional requirements (AR1–AR4)
  inc-pkg-conf.csv       # Component → include/pkg directory mapping
scripts/                 # Implementation scripts (empty — not yet built)
output/                  # Generated artifacts (empty — not yet built)
```

## Input configuration

`design/inc-pkg-conf.csv` maps each CANN component to its controlled include (`inc`) and package (`pkg`) directories, using semicolon-delimited glob patterns. Headers and configured sources in these directories are considered public API surface (`label: "inc"` or `"pkg"`). All other files within a component are `"internal"`.

## Pipeline (AR1 → AR4)

The processing pipeline has four stages, each feeding the next:

1. **AR1 — User input:** The user provides one or more component absolute paths as input (no intermediate file is generated at this stage).

2. **AR2 — File inventory:** Walk the provided component directories and produce `file-list.csv` (`component, label, filename, path`). Include:
   - All files in directories configured by `inc-pkg-conf.csv`: C (`.h`), C++ (`.h`, `.hpp`, `.cpp`, `.cxx`, `.cc`), Python (`.py`)
   - All header files outside configured directories: C (`.h`), C++ (`.h`, `.hpp`), Python (`.py`)
   - Exclude `*/experimental/*`, `*/tests/*`, `*/build/*`, `*/output/*`, `*/stub/*`.

3. **AR3 — AST extraction:** Parse every file in `file-list.csv` and extract declarations into two output files:
   - **`func-export.json`** — API declarations: `FunctionDecl` (C functions, global functions, template functions), `CXXMethodDecl` (class member functions), `macro-function` (function-like macros)
   - **`data-export.json`** — type declarations: `macro` (object-like macros), `struct`, `union`, `enum`
   - **C/C++ files:** Use [Tree-sitter](https://tree-sitter.github.io/tree-sitter/) (Python bindings). The grammar produces syntax-level AST nodes; map them to the required `kind` values:
     - `FunctionDecl`: `function_definition` nodes at `translation_unit` / `namespace` scope *(excluding those with `::` qualifiers — see post-processing below)*
     - `CXXMethodDecl`: `function_definition` nodes inside `class_specifier` / `struct_specifier` bodies; **plus** file-scope `function_definition` nodes whose declarator contains `::`（类外成员函数定义，后处理修正）
     - `macro-function`: `preproc_function_def` nodes
     - `macro`: `preproc_def` nodes
     - `struct`: `struct_specifier` nodes
     - `union`: `union_specifier` nodes
     - `enum`: `enum_specifier` nodes
   - **Constraints:** No template instantiation; no macro expansion. Template functions are parsed as `template_declaration` → traverse child to locate the inner `function_definition` and map to `FunctionDecl`.
   - **Python files:** Use Python's `ast` module. Extract function definitions (`FunctionDef`, `AsyncFunctionDef`), class definitions (`ClassDef`), and method definitions within classes.
   - Each declaration records its `kind`, `data_name`/`func_name`, source text snippet, and exact file location (path:line:col).

4. **AR4 — Output:** Write two JSON files keyed by relative file path. See `design/requirements-func.md` AR4 for the complete schemas.

   **`func-export.json`** — API declarations:
   ```jsonc
   {
     "include/driver/ts_api.h": [{
       "func_name": "tsDevSendMsgAsync",  // API name
       "kind": "FunctionDecl",            // FunctionDecl | CXXMethodDecl | macro-function
       "label": "inc",
       "location": "/path/to/file.h:17:0",
       "extra_info": {
         "deprecated": "",                // deprecation marker if present
         "is_definition": false,
         "parameters": [
           ["paramName", "const uint32_t", "", "3"]  // [name, type, reserved, defaultValue]
         ],
         "return": "void",                // return type
         "type": "void tsDevSendMsgAsync(...)",  // full declaration string
         "visibility": "extern"           // extern | internal | macro | public | protected | private
       }
     }]
   }
   ```

   **`data-export.json`** — type declarations:
   ```jsonc
   {
     "include/driver/ts_api.h": [{
       "data_name": "TS_INNER_SUCCESS",   // declaration name
       "kind": "macro",                   // macro | struct | union | enum
       "path": "ts_api.h",
       "label": "inc",
       "location": "/path/to/file.h:17:0",
       "source": "#define TS_INNER_SUCCESS 1"  // original source string
     }]
   }
   ```

## Technology assumptions

- Target languages: C (`.h`), C++ (`.h`, `.hpp`, `.cpp`, `.cxx`, `.cc`, `.c`), Python (`.py`)
- AST parsing must be **pure static analysis** — no compilation required, no build system integration
  - C/C++: [Tree-sitter](https://tree-sitter.github.io/tree-sitter/) with `tree-sitter-c` and `tree-sitter-cpp` grammars (Python bindings via `pip install tree-sitter`). Lightweight (~5MB), zero compilation context, fast incremental parsing.
  - Python: standard library `ast` module
- Python files are only parsed for public API declarations (functions, classes, methods); macro/struct/union/enum extraction applies to C/C++ only
- Output format: JSON (`func-export.json`, `data-export.json`) and CSV (intermediate files)
- The scanner is intended to run in CI/CD (PR pipelines) for automated API compatibility checking

## Key design decisions

- **Visibility model:** Three-tier — `inc` (public API headers in controlled include dirs), `pkg` (package-level headers), `internal` (everything else). This labeling allows downstream consumers to filter by impact.
- **Macro functions** are treated as API declarations alongside regular functions — they affect the public surface. However, macro expansion is **not** performed: only the `#define` site itself is recorded; macros that generate new declarations through expansion are not traced.
- **Template instantiation is not performed.** Template functions are recorded by their primary template declaration (`template_declaration` → inner `function_definition`), mapped to `FunctionDecl`. Implicit/explicit specializations are not expanded.
- **Filtering directories** (`experimental`, `tests`, `build`, `output`, `stub`) is applied consistently at AR2 so downstream stages never touch those files.
- **Tree-sitter over Clang:** Chosen because the project does not require template instantiation or macro expansion, and Tree-sitter provides true zero-compilation pure static analysis with minimal CI footprint. The known limitation (class-outside-definition methods initially parsed as free functions) is corrected via a post-processing rule that checks for `::` in the function declarator.
