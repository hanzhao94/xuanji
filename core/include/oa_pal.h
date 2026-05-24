/*
 * XUANJI — 平台抽象层 (PAL)
 * 
 * 隔离OS差异，上层代码不碰OS API
 * Windows: Win32 API
 * Linux/macOS: POSIX
 * 
 * 版本: 0.1.0
 * 日期: 2026-05-15
 */

#ifndef OA_PAL_H
#define OA_PAL_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ============================================================
 * 进程管理
 * ============================================================ */

typedef struct oa_pal_proc oa_pal_proc_t;

oa_pal_proc_t* oa_pal_proc_spawn(const char* cmd, const char* workdir);
bool           oa_pal_proc_kill(oa_pal_proc_t* proc);
bool           oa_pal_proc_is_alive(oa_pal_proc_t* proc);
int            oa_pal_proc_wait(oa_pal_proc_t* proc, int timeout_ms);
uint32_t       oa_pal_proc_pid(oa_pal_proc_t* proc);
void           oa_pal_proc_free(oa_pal_proc_t* proc);

/* 当前进程PID */
uint32_t       oa_pal_getpid(void);

/* ============================================================
 * 共享内存
 * ============================================================ */

typedef struct oa_pal_shm oa_pal_shm_t;

oa_pal_shm_t* oa_pal_shm_create(const char* name, size_t size);
oa_pal_shm_t* oa_pal_shm_open(const char* name);
void*          oa_pal_shm_ptr(oa_pal_shm_t* shm);
size_t         oa_pal_shm_size(oa_pal_shm_t* shm);
void           oa_pal_shm_close(oa_pal_shm_t* shm);
void           oa_pal_shm_destroy(oa_pal_shm_t* shm);

/* ============================================================
 * 互斥锁（跨进程）
 * ============================================================ */

typedef struct oa_pal_mutex oa_pal_mutex_t;

oa_pal_mutex_t* oa_pal_mutex_create(const char* name);
oa_pal_mutex_t* oa_pal_mutex_open(const char* name);
bool            oa_pal_mutex_lock(oa_pal_mutex_t* mtx, int timeout_ms);
void            oa_pal_mutex_unlock(oa_pal_mutex_t* mtx);
void            oa_pal_mutex_destroy(oa_pal_mutex_t* mtx);

/* ============================================================
 * 文件锁
 * ============================================================ */

bool oa_pal_flock(const char* path, int timeout_ms);
void oa_pal_funlock(const char* path);

/* ============================================================
 * 时间
 * ============================================================ */

uint64_t oa_pal_time_ms(void);     /* 毫秒时间戳 */
uint64_t oa_pal_time_us(void);     /* 微秒时间戳 */
void     oa_pal_sleep_ms(int ms);

/* ============================================================
 * 原子操作
 * ============================================================ */

int32_t oa_pal_atomic_load(volatile int32_t* ptr);
void    oa_pal_atomic_store(volatile int32_t* ptr, int32_t val);
int32_t oa_pal_atomic_add(volatile int32_t* ptr, int32_t val);
bool    oa_pal_atomic_cas(volatile int32_t* ptr, 
                           int32_t expected, int32_t desired);

/* 64位版本 */
int64_t oa_pal_atomic_load64(volatile int64_t* ptr);
void    oa_pal_atomic_store64(volatile int64_t* ptr, int64_t val);

/* ============================================================
 * 路径操作
 * 内部统一用 / 分隔，PAL层自动转换
 * ============================================================ */

/* 返回的字符串需要 free() */
char* oa_pal_path_join(const char* a, const char* b);
char* oa_pal_path_temp(void);       /* 临时目录 */
char* oa_pal_path_home(void);       /* 用户目录 */
char* oa_pal_path_cwd(void);        /* 当前目录 */
bool  oa_pal_path_exists(const char* path);
bool  oa_pal_path_mkdir(const char* path);  /* 递归创建 */
bool  oa_pal_path_is_dir(const char* path);

/* ============================================================
 * 动态库加载
 * ============================================================ */

typedef struct oa_pal_lib oa_pal_lib_t;

oa_pal_lib_t* oa_pal_lib_load(const char* path);
void*         oa_pal_lib_symbol(oa_pal_lib_t* lib, const char* name);
void          oa_pal_lib_close(oa_pal_lib_t* lib);
const char*   oa_pal_lib_error(void);

/* ============================================================
 * 系统信息
 * ============================================================ */

typedef struct {
    char os_name[32];       /* "windows" / "linux" / "macos" */
    char arch[16];          /* "x86_64" / "aarch64" */
    uint32_t cpu_count;
    uint64_t mem_total;     /* 总内存(字节) */
    uint64_t mem_avail;     /* 可用内存(字节) */
} oa_pal_sysinfo_t;

void oa_pal_sysinfo(oa_pal_sysinfo_t* info);

/* ============================================================
 * 环境变量
 * ============================================================ */

const char* oa_pal_getenv(const char* name);
bool        oa_pal_setenv(const char* name, const char* value);

#ifdef __cplusplus
}
#endif

#endif /* OA_PAL_H */
