/**
 * CANN API 声明样式测试用例 — .cpp 源文件
 * [S18] 宏函数  [S19] 对象宏
 */

/* ================================================================
 * [S18] 宏函数
 * ================================================================ */
#define ADD(a, b) ((a) + (b))
#define MAX_OF_THREE(x, y, z) \
    (((x) > (y)) ? (((x) > (z)) ? (x) : (z)) : (((y) > (z)) ? (y) : (z)))

/* 头文件防护宏 — 应被排除 */
#ifndef TEST_MACROS_H
#define TEST_MACROS_H

/* ================================================================
 * [S19] 对象宏
 * ================================================================ */
#define VERSION_MAJOR 1
#define VERSION_MINOR 0
#define PLATFORM_NAME "Ascend"

/* ================================================================
 * 函数实现
 * ================================================================ */
extern int32_t NormalizeScalar(float *data, uint64_t count, float epsilon) {
    ADD(1, 2);
    return 0;
}

extern HcclResult HcclAllReduce(void *sendBuf, void *recvBuf, uint64_t count,
                                 HcclDataType dataType, HcclReduceOp op,
                                 HcclComm comm, aclrtStream stream) {
    return HCCL_SUCCESS;
}

OpDef::OpDef(const char *name) {}

#endif /* TEST_MACROS_H */
