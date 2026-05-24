/*
 * XUANJI — 具身智能多Agent框架 C底座
 * 
 * 公共API头文件
 * 所有平台统一接口，PAL层吃掉OS差异
 * 
 * 版本: 0.1.0
 * 日期: 2026-05-15
 */

#ifndef XUANJI_H
#define XUANJI_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#ifdef _WIN32
    #ifdef OA_BUILD_DLL
        #define OA_API __declspec(dllexport)
    #else
        #define OA_API __declspec(dllimport)
    #endif
#else
    #define OA_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* ============================================================
 * 版本信息
 * ============================================================ */

#define OA_VERSION_MAJOR 0
#define OA_VERSION_MINOR 1
#define OA_VERSION_PATCH 0

OA_API const char* oa_version(void);

/* ============================================================
 * 错误码
 * ============================================================ */

typedef enum {
    OA_OK = 0,
    OA_ERR_NOMEM = -1,
    OA_ERR_INVALID = -2,
    OA_ERR_TIMEOUT = -3,
    OA_ERR_FULL = -4,
    OA_ERR_EMPTY = -5,
    OA_ERR_DENIED = -6,
    OA_ERR_DEAD = -7,
    OA_ERR_EXISTS = -8,
    OA_ERR_NOTFOUND = -9,
    OA_ERR_PLATFORM = -10,
} oa_err_t;

/* ============================================================
 * 消息总线 (oa_bus)
 * 无锁环形缓冲区，Agent间通信
 * ============================================================ */

#define OA_MSG_MAX_PAYLOAD 65536

typedef struct {
    uint32_t from_agent;
    uint32_t to_agent;       /* 0 = 广播 */
    uint32_t msg_type;
    uint32_t priority;
    uint64_t timestamp;
    uint64_t trace_id;
    uint32_t payload_len;
    uint8_t  payload[OA_MSG_MAX_PAYLOAD];
} oa_msg_t;

typedef struct oa_bus oa_bus_t;

OA_API oa_bus_t*  oa_bus_create(uint32_t capacity);
OA_API void       oa_bus_destroy(oa_bus_t* bus);
OA_API oa_err_t   oa_bus_publish(oa_bus_t* bus, const oa_msg_t* msg);
OA_API oa_err_t   oa_bus_receive(oa_bus_t* bus, uint32_t agent_id,
                                  oa_msg_t* out, int timeout_ms);
OA_API uint32_t   oa_bus_pending(oa_bus_t* bus, uint32_t agent_id);

/* ============================================================
 * 调度器 (oa_sched)
 * 优先级队列，任务分配
 * ============================================================ */

typedef struct {
    uint64_t task_id;
    uint32_t agent_id;
    uint32_t priority;       /* 0=最高 */
    uint64_t deadline;       /* 超时时间戳 */
    uint32_t payload_len;
    uint8_t  payload[4096];
} oa_task_t;

typedef struct oa_sched oa_sched_t;

OA_API oa_sched_t* oa_sched_create(uint32_t capacity);
OA_API void        oa_sched_destroy(oa_sched_t* sched);
OA_API oa_err_t    oa_sched_push(oa_sched_t* sched, const oa_task_t* task);
OA_API oa_err_t    oa_sched_pop(oa_sched_t* sched, oa_task_t* out);
OA_API uint32_t    oa_sched_size(oa_sched_t* sched);

/* ============================================================
 * 资源管理器 (oa_res)
 * 分区 + 租约 + 仲裁
 * ============================================================ */

typedef struct {
    uint64_t lease_id;
    uint32_t agent_id;
    uint32_t resource_type;  /* 文件/GPU/端口/屏幕/鼠标... */
    uint64_t granted_at;
    uint64_t expires_at;
    char     resource_name[256];
} oa_lease_t;

typedef enum {
    OA_RES_FILE = 1,
    OA_RES_GPU = 2,
    OA_RES_PORT = 3,
    OA_RES_SCREEN = 4,     /* 独占：操控令牌 */
    OA_RES_MOUSE = 5,      /* 独占 */
    OA_RES_KEYBOARD = 6,   /* 独占 */
    OA_RES_MIC = 7,        /* 独占 */
    OA_RES_SPEAKER = 8,    /* 独占 */
} oa_res_type_t;

typedef struct oa_res oa_res_t;

OA_API oa_res_t*  oa_res_create(void);
OA_API void       oa_res_destroy(oa_res_t* res);
OA_API oa_err_t   oa_res_acquire(oa_res_t* res, uint32_t agent_id,
                                  oa_res_type_t type, const char* name,
                                  int timeout_ms, oa_lease_t* out);
OA_API oa_err_t   oa_res_release(oa_res_t* res, uint64_t lease_id);
OA_API oa_err_t   oa_res_revoke(oa_res_t* res, uint32_t agent_id);
                                  /* 回收某Agent全部资源 */

/* ============================================================
 * 进程管理 (oa_proc)
 * 进程隔离 + 生命周期 + 自保护
 * ============================================================ */

typedef struct oa_proc oa_proc_t;

OA_API oa_proc_t* oa_proc_spawn(const char* cmd, const char* workdir);
OA_API bool       oa_proc_is_alive(oa_proc_t* proc);
OA_API int        oa_proc_wait(oa_proc_t* proc, int timeout_ms);
OA_API bool       oa_proc_kill(oa_proc_t* proc);
OA_API uint32_t   oa_proc_pid(oa_proc_t* proc);
OA_API void       oa_proc_free(oa_proc_t* proc);

/* 命令安全检查 */
OA_API bool       oa_proc_is_safe_cmd(const char* cmd);

/* ============================================================
 * 心跳检测 (oa_heart)
 * 存活监控 + 超时回收
 * ============================================================ */

typedef enum {
    OA_HEALTH_GREEN = 0,
    OA_HEALTH_YELLOW = 1,
    OA_HEALTH_ORANGE = 2,
    OA_HEALTH_RED = 3,
} oa_health_level_t;

typedef struct {
    float    cpu_percent;
    float    mem_percent;
    float    disk_percent;
    float    gpu_mem_percent;
    uint32_t agent_count;
    uint32_t agent_healthy;
    uint32_t tasks_total;
    uint32_t tasks_failed;
    uint64_t uptime_ms;
} oa_health_t;

typedef struct oa_heart oa_heart_t;

OA_API oa_heart_t* oa_heart_create(uint32_t max_agents);
OA_API void        oa_heart_destroy(oa_heart_t* heart);
OA_API oa_err_t    oa_heart_register(oa_heart_t* heart, uint32_t agent_id);
OA_API oa_err_t    oa_heart_beat(oa_heart_t* heart, uint32_t agent_id);
OA_API oa_health_level_t oa_heart_check(oa_heart_t* heart,
                                         uint32_t agent_id);
OA_API oa_health_t oa_heart_snapshot(oa_heart_t* heart);

/* ============================================================
 * 文件安全 (oa_fs)
 * 沙箱 + 文件锁 + 原子写入
 * ============================================================ */

typedef enum {
    OA_FS_READ = 1,
    OA_FS_WRITE = 2,
    OA_FS_DELETE = 4,
    OA_FS_EXEC = 8,
} oa_fs_op_t;

OA_API bool oa_fs_check(uint32_t agent_id, const char* path,
                         oa_fs_op_t op);
OA_API bool oa_fs_lock(const char* path, int timeout_ms);
OA_API void oa_fs_unlock(const char* path);

/* ============================================================
 * 平台抽象层 (PAL) — 见 oa_pal.h
 * ============================================================ */

/* ============================================================
 * 全局初始化/清理
 * ============================================================ */

OA_API oa_err_t oa_init(void);
OA_API void     oa_shutdown(void);

#ifdef __cplusplus
}
#endif

#endif /* XUANJI_H */
