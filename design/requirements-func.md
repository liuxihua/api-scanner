# desciption
    这是一个CANN（https://gitcode.com/cann）下所有组件的API兼容性分析项目。
    你是开源生态社区的API兼容性分析专家，如果用户提交的PR中存在API变更的情况（add、modify、delete），希望通过本项目构建PR流水线API兼容性检测能力，检测出来这种API变更。
    本项目需求是：遍历项目中全量的头文件（.h、.hpp）,，采用基于源码文件的纯静态全量 AST 解析，完整提取全量API（包括：C函数、类成员函数、全局函数、模版函数、宏函数等），生成静态全量 API 基线清单。

# 约束

    1. 获取CANN下所有组件，对外（面向外部开发者）提供的API均存放在指定的管控目录中，这个管控目录由配置文件（inc-pkg-conf.csv）管理。
    2. 过滤*/experimental/*目录下所有文件，不参与分析。
    3. 过滤*/tests/*、*/build/*、*/output/*、*/stub/*、*/examples/*等目录下所有文件，不参与分析。

# requirements

    描述项目所需要实现的需求清单

## AR1 用户输入：组件绝对路径


## AR2 基于组件绝对路径获取组件，遍历组件下全量目录，获取全量头文件及管控目录下的源码文件清单:file-list.csv
    
    需要解析的文件清单包括：
    1. 组件指定目录下的所有源文件（基于inc-pkg-conf.csv配置过滤），源语言范围：C（.h）、C++（.h/.hpp/.cpp/.cxx/.cc 等）、Python（.py）。
    2. 组件内，指定目录外的全量头文件，源语言范围：C（.h）、C++（.h/.hpp）。


    源码文件可见性：
    1. inc，inc-pkg-conf.csv中组件指定inc目录下的文件
    2. pkg，inc-pkg-conf.csv中组件指定pkg目录下的文件
    3. internal，组件内但不在nc-pkg-conf.csv指定目录下的文件

    file-list.csv文件字段包括：
    1. component，组件名称
    2. label，文件的可见性inc、pkg、internal
    3. filename，文件名称
    4. path，组件相对路径



## AR3 遍历组件file-list.csv清单、通过静态语法树解析获取全量的API、宏、数据结构、Union、enum等声明清单

### 开关条件
    1. 增加full-data-type变量开关，支持用户启动本项目时输入开关的值
    2. full-data-type开关默认值为false。
    3. 当full-data-type关闭（false）时，遍历源文件，只获取全量API声明 + 仅在inc-pkg-conf.csv管控目录下源文件中定义的宏、数据结构、Union、enum声明。
    4. 当full-data-type打开（true）时，遍历源文件，获取全量API、宏、数据结构、Union、enum声明。

### 遍历文件，静态语法树解析输出约束
    API声明清单输出到func-export.json文件中，API声明包括：
    1. FunctionDecl（C函数、全局函数、模版函数）
    2. CXXMethodDecl（类成员函数）
    3. mcro-function（宏函数）

    类型声明清单输出到data-export.json文件中，包括：
    1. macro,宏定义。
    2. struct 声明。
    3. union 声明。
    4. enum 声明。

    API、宏、数据结构、Union、enum等声明可见性定义:
    1. inc，inc-pkg-conf.csv中组件指定inc目录下的文件内的声明
    2. pkg，inc-pkg-conf.csv中组件指定pkg目录下的文件内的声明
    3. internal，组件内但不在nc-pkg-conf.csv指定目录下的文件内的声明

## AR4 严格按照下面格式要求输出分析结果
   1.   data-export.json文件格式：
  {
    "include/driver/ts_api.h":[
        {
            "data_name": "TS_INNER_SUCCESS", //macro、struct、union、enum声明的名称
            "kind": "macro", // 声明类型清单：macro、struct、union、enum
            "path": "ts_api.h", //文件名称
            "label": "inc",     //文件可见性
            "location": "/home/wate/cann/runtime/include/driver/ts_api.h:17:0", //文件绝对路径:声明在文件中的起始行号:声明在行中起始字符位置
            "source": "#define TS_INNER_SUCCESS 1" //声明在文件中的原始字符串
        }

    ]
  }

  2.    func-export.json文件格式：
  {
     "include/driver/ts_api.h":[
        {
            "func_name": "tsDevSendMsgAsync", //API声明的名称
            "kind": "FunctionDecl", // 声明类型清单：FunctionDecl、CXXMethodDecl、mcro-function
            "location": "/home/wate/cann/runtime/include/driver/ts_api.h:17:0", //文件绝对路径:API声明在文件中的起始行号:API声明在行中起始字符位置
            "extra_info":{
                "deprecated": "",  //该函数是否有废弃标志
                "is_definition": false, 
                "parameters": [
                    [
                        "eventType",  //参数名称
                        "const uint32_t",  //参数类型
                        ""      
                    ],
                    [
                        "waitType",
                        "const uint32_t",
                        “”，//暂时未使用，模版、宏穿透后解析出来的类型
                        "3"   //缺省值、默认值，字符串为“”，或当前index没有，则表示没有
                    ]
                ],
                "return": "void",  //返回类型
                "type": "void tsDevSendMsgAsync(const uint32_t eventType, const uint32_t waitType = 3)", //源文件中API声明的全量静态字符串
                "visibility": "extern" //函数可见性标志，`extern`、`internal`、`macro`、`public`、 `protected`、 `private`
            }
        }
    ]
  }


