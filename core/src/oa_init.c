/*
 * XUANJI — 全局初始化/清理模块
 *
 * 框架生命周期管理
 * 初始化日志、确保目录结构、设置全局状态
 *
 * 纯C11，仅依赖 oa_pal.h
 */

#include "XUANJI.h"
#include "oa_pal.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ============================================================
 * 全局状态
 * ============================================================ */

/* 版本字符串 */
static char g_version_str[32] = {0};

/* 初始化状态（原子保护） */
static volatile int32_t g_initialized = 0;

/* 启动时间戳 */
static uint64_t g_start_time_ms = 0;

/* ============================================================
 * 公共API实现
 * ============================================================ */

OA_API const char* oa_version(void) {
    if (g_version_str[0] == '\0') {
        snprintf(g_version_str, sizeof(g_version_str),
                 "%d.%d.%d",
                 OA_VERSION_MAJOR,
                 OA_VERSION_MINOR,
                 OA_VERSION_PATCH);
    }
    return g_version_str;
}

OA_API oa_err_t oa_init(void) {
    /* 防止重复初始化 */
    if (!oa_pal_atomic_cas(&g_initialized, 0, 1)) {
        return OA_ERR_EXISTS;  /* 已经初始化过 */
    }

    g_start_time_ms = oa_pal_time_ms();

    /* 确保 XUANJI 数据目录存在 */
    char* home = oa_pal_path_home();
    if (home) {
        char* oa_dir = oa_pal_path_join(home, ".XUANJI");
        if (oa_dir) {
            oa_pal_path_mkdir(oa_dir);

            /* 创建子目录 */
            char* logs_dir = oa_pal_path_join(oa_dir, "logs");
            if (logs_dir) {
                oa_pal_path_mkdir(logs_dir);
                free(logs_dir);
            }

            char* data_dir = oa_pal_path_join(oa_dir, "data");
            if (data_dir) {
                oa_pal_path_mkdir(data_dir);
                free(data_dir);
            }

            char* tmp_dir = oa_pal_path_join(oa_dir, "tmp");
            if (tmp_dir) {
                oa_pal_path_mkdir(tmp_dir);
                free(tmp_dir);
            }

            free(oa_dir);
        }
        free(home);
    }

    return OA_OK;
}

OA_API void oa_shutdown(void) {
    /* 防止重复清理 */
    if (!oa_pal_atomic_cas(&g_initialized, 1, 0)) {
        return;  /* 未初始化或已清理 */
    }

    /* 清理全局状态 */
    g_start_time_ms = 0;
}
