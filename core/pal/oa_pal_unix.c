/*
 * XUANJI PAL — Unix实现 (Linux + macOS)
 * 
 * POSIX API封装
 */

#if defined(__linux__) || defined(__APPLE__)

#include "oa_pal.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <unistd.h>
#include <signal.h>
#include <errno.h>
#include <time.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <sys/stat.h>
#include <sys/mman.h>
#include <sys/file.h>
#include <fcntl.h>
#include <dlfcn.h>
#include <pthread.h>

#ifdef __APPLE__
#include <mach/mach_time.h>
#include <sys/sysctl.h>
#else
#include <sys/sysinfo.h>
#endif

/* ============================================================
 * 进程管理
 * ============================================================ */

struct oa_pal_proc {
    pid_t pid;
};

oa_pal_proc_t* oa_pal_proc_spawn(const char* cmd, const char* workdir) {
    pid_t pid = fork();
    
    if (pid < 0) return NULL;
    
    if (pid == 0) {
        /* 子进程 */
        if (workdir) {
            if (chdir(workdir) != 0) _exit(127);
        }
        /* 新会话，脱离终端 */
        setsid();
        execl("/bin/sh", "sh", "-c", cmd, (char*)NULL);
        _exit(127);
    }
    
    /* 父进程 */
    oa_pal_proc_t* proc = (oa_pal_proc_t*)calloc(1, sizeof(*proc));
    if (!proc) return NULL;
    proc->pid = pid;
    return proc;
}

bool oa_pal_proc_kill(oa_pal_proc_t* proc) {
    if (!proc) return false;
    /* 先SIGTERM优雅关闭 */
    if (kill(proc->pid, SIGTERM) == 0) {
        /* 等1秒 */
        usleep(1000000);
        if (oa_pal_proc_is_alive(proc)) {
            /* 还没死，SIGKILL强杀 */
            kill(proc->pid, SIGKILL);
        }
        return true;
    }
    return false;
}

bool oa_pal_proc_is_alive(oa_pal_proc_t* proc) {
    if (!proc) return false;
    return kill(proc->pid, 0) == 0;
}

int oa_pal_proc_wait(oa_pal_proc_t* proc, int timeout_ms) {
    if (!proc) return -1;
    
    if (timeout_ms < 0) {
        /* 无限等待 */
        int status;
        waitpid(proc->pid, &status, 0);
        return WIFEXITED(status) ? WEXITSTATUS(status) : -1;
    }
    
    /* 带超时等待：轮询 */
    int elapsed = 0;
    int step = 50; /* 50ms步进 */
    while (elapsed < timeout_ms) {
        int status;
        pid_t ret = waitpid(proc->pid, &status, WNOHANG);
        if (ret > 0) {
            return WIFEXITED(status) ? WEXITSTATUS(status) : -1;
        }
        usleep(step * 1000);
        elapsed += step;
    }
    return -1; /* 超时 */
}

uint32_t oa_pal_proc_pid(oa_pal_proc_t* proc) {
    return proc ? (uint32_t)proc->pid : 0;
}

void oa_pal_proc_free(oa_pal_proc_t* proc) {
    free(proc);
}

uint32_t oa_pal_getpid(void) {
    return (uint32_t)getpid();
}

/* ============================================================
 * 共享内存
 * ============================================================ */

struct oa_pal_shm {
    int    fd;
    void*  ptr;
    size_t size;
    char   name[256];
};

oa_pal_shm_t* oa_pal_shm_create(const char* name, size_t size) {
    oa_pal_shm_t* shm = (oa_pal_shm_t*)calloc(1, sizeof(*shm));
    if (!shm) return NULL;
    
    snprintf(shm->name, sizeof(shm->name), "/oa_%s", name);
    shm->size = size;
    
    shm->fd = shm_open(shm->name, O_CREAT | O_RDWR, 0600);
    if (shm->fd < 0) { free(shm); return NULL; }
    
    if (ftruncate(shm->fd, (off_t)size) != 0) {
        close(shm->fd);
        shm_unlink(shm->name);
        free(shm);
        return NULL;
    }
    
    shm->ptr = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, shm->fd, 0);
    if (shm->ptr == MAP_FAILED) {
        close(shm->fd);
        shm_unlink(shm->name);
        free(shm);
        return NULL;
    }
    
    return shm;
}

oa_pal_shm_t* oa_pal_shm_open(const char* name) {
    oa_pal_shm_t* shm = (oa_pal_shm_t*)calloc(1, sizeof(*shm));
    if (!shm) return NULL;
    
    snprintf(shm->name, sizeof(shm->name), "/oa_%s", name);
    
    shm->fd = shm_open(shm->name, O_RDWR, 0600);
    if (shm->fd < 0) { free(shm); return NULL; }
    
    /* 获取大小 */
    struct stat st;
    fstat(shm->fd, &st);
    shm->size = (size_t)st.st_size;
    
    shm->ptr = mmap(NULL, shm->size, PROT_READ | PROT_WRITE, MAP_SHARED, shm->fd, 0);
    if (shm->ptr == MAP_FAILED) {
        close(shm->fd);
        free(shm);
        return NULL;
    }
    
    return shm;
}

void* oa_pal_shm_ptr(oa_pal_shm_t* shm) {
    return shm ? shm->ptr : NULL;
}

size_t oa_pal_shm_size(oa_pal_shm_t* shm) {
    return shm ? shm->size : 0;
}

void oa_pal_shm_close(oa_pal_shm_t* shm) {
    if (!shm) return;
    if (shm->ptr && shm->ptr != MAP_FAILED) munmap(shm->ptr, shm->size);
    if (shm->fd >= 0) close(shm->fd);
    free(shm);
}

void oa_pal_shm_destroy(oa_pal_shm_t* shm) {
    if (!shm) return;
    char name[256];
    strncpy(name, shm->name, sizeof(name));
    oa_pal_shm_close(shm);
    shm_unlink(name);
}

/* ============================================================
 * 互斥锁（基于文件锁实现跨进程）
 * ============================================================ */

struct oa_pal_mutex {
    int fd;
    char path[300];
};

oa_pal_mutex_t* oa_pal_mutex_create(const char* name) {
    oa_pal_mutex_t* mtx = (oa_pal_mutex_t*)calloc(1, sizeof(*mtx));
    if (!mtx) return NULL;
    
    snprintf(mtx->path, sizeof(mtx->path), "/tmp/oa_mtx_%s.lock", name);
    
    mtx->fd = open(mtx->path, O_CREAT | O_RDWR, 0600);
    if (mtx->fd < 0) { free(mtx); return NULL; }
    
    return mtx;
}

oa_pal_mutex_t* oa_pal_mutex_open(const char* name) {
    return oa_pal_mutex_create(name); /* 同一个文件 */
}

bool oa_pal_mutex_lock(oa_pal_mutex_t* mtx, int timeout_ms) {
    if (!mtx) return false;
    
    if (timeout_ms < 0) {
        return flock(mtx->fd, LOCK_EX) == 0;
    }
    
    /* 带超时：轮询 */
    int elapsed = 0;
    int step = 10;
    while (elapsed < timeout_ms) {
        if (flock(mtx->fd, LOCK_EX | LOCK_NB) == 0) return true;
        usleep(step * 1000);
        elapsed += step;
    }
    return false;
}

void oa_pal_mutex_unlock(oa_pal_mutex_t* mtx) {
    if (mtx) flock(mtx->fd, LOCK_UN);
}

void oa_pal_mutex_destroy(oa_pal_mutex_t* mtx) {
    if (!mtx) return;
    if (mtx->fd >= 0) close(mtx->fd);
    unlink(mtx->path);
    free(mtx);
}

/* ============================================================
 * 文件锁
 * ============================================================ */

static int g_flock_fd = -1;

bool oa_pal_flock(const char* path, int timeout_ms) {
    g_flock_fd = open(path, O_CREAT | O_RDWR, 0600);
    if (g_flock_fd < 0) return false;
    
    if (timeout_ms < 0) {
        return flock(g_flock_fd, LOCK_EX) == 0;
    }
    
    int elapsed = 0;
    while (elapsed < timeout_ms) {
        if (flock(g_flock_fd, LOCK_EX | LOCK_NB) == 0) return true;
        usleep(10000);
        elapsed += 10;
    }
    close(g_flock_fd);
    g_flock_fd = -1;
    return false;
}

void oa_pal_funlock(const char* path) {
    (void)path;
    if (g_flock_fd >= 0) {
        flock(g_flock_fd, LOCK_UN);
        close(g_flock_fd);
        g_flock_fd = -1;
    }
}

/* ============================================================
 * 时间
 * ============================================================ */

uint64_t oa_pal_time_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (uint64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

uint64_t oa_pal_time_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
}

void oa_pal_sleep_ms(int ms) {
    usleep(ms * 1000);
}

/* ============================================================
 * 原子操作
 * ============================================================ */

int32_t oa_pal_atomic_load(volatile int32_t* ptr) {
    return __atomic_load_n(ptr, __ATOMIC_SEQ_CST);
}

void oa_pal_atomic_store(volatile int32_t* ptr, int32_t val) {
    __atomic_store_n(ptr, val, __ATOMIC_SEQ_CST);
}

int32_t oa_pal_atomic_add(volatile int32_t* ptr, int32_t val) {
    return __atomic_fetch_add(ptr, val, __ATOMIC_SEQ_CST);
}

bool oa_pal_atomic_cas(volatile int32_t* ptr, int32_t expected, int32_t desired) {
    return __atomic_compare_exchange_n(ptr, &expected, desired, false,
                                        __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
}

int64_t oa_pal_atomic_load64(volatile int64_t* ptr) {
    return __atomic_load_n(ptr, __ATOMIC_SEQ_CST);
}

void oa_pal_atomic_store64(volatile int64_t* ptr, int64_t val) {
    __atomic_store_n(ptr, val, __ATOMIC_SEQ_CST);
}

/* ============================================================
 * 路径
 * ============================================================ */

char* oa_pal_path_join(const char* a, const char* b) {
    size_t la = strlen(a);
    size_t lb = strlen(b);
    char* result = (char*)malloc(la + lb + 2);
    if (!result) return NULL;
    
    memcpy(result, a, la);
    if (la > 0 && a[la-1] != '/') {
        result[la] = '/';
        la++;
    }
    memcpy(result + la, b, lb + 1);
    return result;
}

char* oa_pal_path_temp(void) {
    const char* tmp = getenv("TMPDIR");
    if (!tmp) tmp = "/tmp";
    return strdup(tmp);
}

char* oa_pal_path_home(void) {
    const char* home = getenv("HOME");
    return home ? strdup(home) : strdup("/tmp");
}

char* oa_pal_path_cwd(void) {
    char buf[4096];
    if (getcwd(buf, sizeof(buf))) return strdup(buf);
    return strdup(".");
}

bool oa_pal_path_exists(const char* path) {
    return access(path, F_OK) == 0;
}

bool oa_pal_path_mkdir(const char* path) {
    char tmp[4096];
    strncpy(tmp, path, sizeof(tmp) - 1);
    tmp[sizeof(tmp) - 1] = '\0';
    
    for (char* p = tmp + 1; *p; p++) {
        if (*p == '/') {
            *p = '\0';
            mkdir(tmp, 0755);
            *p = '/';
        }
    }
    return mkdir(tmp, 0755) == 0 || errno == EEXIST;
}

bool oa_pal_path_is_dir(const char* path) {
    struct stat st;
    if (stat(path, &st) != 0) return false;
    return S_ISDIR(st.st_mode);
}

/* ============================================================
 * 动态库
 * ============================================================ */

struct oa_pal_lib {
    void* handle;
};

oa_pal_lib_t* oa_pal_lib_load(const char* path) {
    oa_pal_lib_t* lib = (oa_pal_lib_t*)calloc(1, sizeof(*lib));
    if (!lib) return NULL;
    
    lib->handle = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!lib->handle) {
        free(lib);
        return NULL;
    }
    return lib;
}

void* oa_pal_lib_symbol(oa_pal_lib_t* lib, const char* name) {
    if (!lib) return NULL;
    return dlsym(lib->handle, name);
}

void oa_pal_lib_close(oa_pal_lib_t* lib) {
    if (!lib) return;
    if (lib->handle) dlclose(lib->handle);
    free(lib);
}

const char* oa_pal_lib_error(void) {
    return dlerror();
}

/* ============================================================
 * 系统信息
 * ============================================================ */

void oa_pal_sysinfo(oa_pal_sysinfo_t* info) {
    if (!info) return;
    memset(info, 0, sizeof(*info));
    
    #ifdef __APPLE__
    strncpy(info->os_name, "macos", sizeof(info->os_name));
    #else
    strncpy(info->os_name, "linux", sizeof(info->os_name));
    #endif
    
    #if defined(__x86_64__) || defined(__amd64__)
    strncpy(info->arch, "x86_64", sizeof(info->arch));
    #elif defined(__aarch64__)
    strncpy(info->arch, "aarch64", sizeof(info->arch));
    #elif defined(__arm__)
    strncpy(info->arch, "arm", sizeof(info->arch));
    #else
    strncpy(info->arch, "unknown", sizeof(info->arch));
    #endif
    
    info->cpu_count = (uint32_t)sysconf(_SC_NPROCESSORS_ONLN);
    
    #ifdef __APPLE__
    int64_t memsize;
    size_t len = sizeof(memsize);
    sysctlbyname("hw.memsize", &memsize, &len, NULL, 0);
    info->mem_total = (uint64_t)memsize;
    /* macOS没有简单的可用内存API，用vm_stat太复杂 */
    info->mem_avail = info->mem_total / 2; /* 粗略估计 */
    #else
    struct sysinfo si;
    sysinfo(&si);
    info->mem_total = (uint64_t)si.totalram * si.mem_unit;
    info->mem_avail = (uint64_t)si.freeram * si.mem_unit;
    #endif
}

/* ============================================================
 * 环境变量
 * ============================================================ */

const char* oa_pal_getenv(const char* name) {
    return getenv(name);
}

bool oa_pal_setenv(const char* name, const char* value) {
    return setenv(name, value, 1) == 0;
}

#endif /* __linux__ || __APPLE__ */
