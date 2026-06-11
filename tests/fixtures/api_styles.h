/**
 * CANN API 声明样式测试用例 — 17 种典型声明样式
 *
 * 每类声明标注对应样式编号 [S01] ~ [S17]
 */

#ifndef API_STYLES_H
#define API_STYLES_H

#include <cstdint>

/* ================================================================
 * [S01] extern "C" 块
 * ================================================================ */
#ifdef __cplusplus
extern "C" {
#endif

/* ================================================================
 * [S02] extern C 函数（全局函数）
 * ================================================================ */
extern int32_t NormalizeScalar(float *data, uint64_t count, float epsilon);

/* ================================================================
 * [S03] 函数声明 + 含中文注释
 * ================================================================ */
/**
 * @brief 计算数据归一化
 * @param data 输入数据指针
 * @param len  数据长度（字节）
 * @return 执行结果状态码
 */
extern int32_t HcommWrite(void *dst, const void *src, uint64_t len);

#ifdef __cplusplus
}
#endif

/* ================================================================
 * [S04] extern C++ 函数（不在 extern "C" 内）
 * ================================================================ */
extern HcclResult HcclAllReduce(void *sendBuf, void *recvBuf, uint64_t count,
                                 HcclDataType dataType, HcclReduceOp op,
                                 HcclComm comm, aclrtStream stream);

/* ================================================================
 * [S05] inline 函数定义
 * ================================================================ */
inline bool IsComplexType(const DataType type) {
    return (type >= DT_COMPLEX64 && type <= DT_COMPLEX128);
}

/* ================================================================
 * [S06] static 函数
 * ================================================================ */
static Status MallocMem(MemType type, size_t size, void **ptr);

/* ================================================================
 * [S07] __aicore__ 函数定义
 * ================================================================ */
__aicore__ inline void CheckSysWorkspace(uint8_t *ptr) {
    if (ptr == nullptr) {
        AscendCExit();
    }
}

/* ================================================================
 * [S08] __aicore__ 构造函数 + 初始化列表
 * ================================================================ */
struct Tensor {
    int x, y;
    __aicore__ inline Tensor(int a, int b) : x(a), y(b) {
    }
    __aicore__ inline int Process(int z) {
        return x + y + z;
    }
};

/* ================================================================
 * [S09] 模版函数
 * ================================================================ */
template <typename T>
T MaxValue(T a, T b) {
    return (a > b) ? a : b;
}

/* ================================================================
 * [S10] 模版类 + 成员函数
 * ================================================================ */
template <typename T>
class Calculator {
public:
    Calculator() = default;
    T Add(T a, T b) { return a + b; }
    T Sub(T a, T b) { return a - b; }
protected:
    void Reset() {}
private:
    T value_;
};

/* ================================================================
 * [S11] 类成员函数（public / protected / private）
 * ================================================================ */
class DataProcessor {
public:
    explicit DataProcessor(const char *name);
    int32_t Process(const float *input, float *output, uint64_t len);
    void Finalize();

protected:
    virtual int64_t GetTiling(void *tiling) = 0;

private:
    static int counter_;
    void InternalReset();
};

/* ================================================================
 * [S12] explicit 构造函数
 * ================================================================ */
class OpDef {
public:
    explicit OpDef(const char *name);
    explicit OpDef(const char *name, int version);
};

/* ================================================================
 * [S13] 类外成员函数定义
 * ================================================================ */
inline int32_t DataProcessor::Process(const float *input, float *output, uint64_t len) {
    InternalReset();
    return 0;
}

/* ================================================================
 * [S14] 模版类外成员函数定义
 * ================================================================ */
template <typename T>
inline T Calculator<T>::Add(T a, T b) {
    value_ = a + b;
    return value_;
}

/* ================================================================
 * [S15] 运算符重载
 * ================================================================ */
struct Coord {
    int x, y;
    bool operator==(const Coord &other) const {
        return x == other.x && y == other.y;
    }
    Coord &operator=(const Coord &other) {
        x = other.x;
        y = other.y;
        return *this;
    }
};

/* ================================================================
 * [S16] [[deprecated]] 属性
 * ================================================================ */
[[deprecated("Use NewFunc instead")]]
extern void OldFunc(int x);

/* ================================================================
 * [S17] struct / class / enum / union 类型声明
 * ================================================================ */
struct Point {
    double x;
    double y;
};

class CalculatorV2 {
public:
    int Add(int a, int b);
};

enum class Status {
    SUCCESS = 0,
    FAILURE = 1,
};

typedef enum {
    COLOR_RED = 0,
    COLOR_GREEN = 1,
    COLOR_BLUE = 2,
} Color;

union DataUnion {
    int32_t i;
    float f;
    char c[4];
};

typedef struct {
    int width;
    int height;
} ImageSize;

#endif /* API_STYLES_H */
