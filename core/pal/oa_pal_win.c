/*
 * XUANJI PAL — Windows实现
 * 
 * Win32 API封装，对上层提供统一POSIX风格接口
 */

#ifdef _WIN32

#include "oa_pal.h"
#include <windows.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

/* ============================================================
 * 进程管理
 * ============================================================ */

struct oa_pal_proc {
    HANDLE handle;
    DWORD  pid;
    HANDLE stdin_w;   /* 可选：写stdin */
    HANDLE stdout_r;  /* 可选：读stdout */
};

oa_pal_proc_t* oa_pal_proc_spawn(const char* cmd, const char* workdir) {
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    
    memset(&si, 0, sizeof(si));
    si.cb = sizeof(si);
    memset(&pi, 0, sizeof(pi));
    
    /* 复制cmd因为CreateProcess可能修改它 */
    char* cmd_copy = _strdup(cmd);
    if (!cmd_copy) return NULL;
    
    BOOL ok = CreateProcessA(
        NULL, cmd_copy, NULL, NULL,
        FALSE, CREATE_NO_WINDOW, NULL,
        workdir, &si, &pi
    );
    
    free(cmd_copy);
    
    if (!ok) return NULL;
    
    oa_pal_proc_t* proc = (oa_pal_proc_t*)calloc(1, sizeof(*proc));
    if (!proc) {
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
        return NULL;
    }
    
    proc->handle = pi.hProcess;
    proc->pid = pi.dwProcessId;
    CloseHandle(pi.hThread);
    
    return proc;
}

bool oa_pal_proc_kill(oa_pal_proc_t* proc) {
    if (!proc) return false;
    return TerminateProcess(proc->handle, 1) != 0;
}

bool oa_pal_proc_is_alive(oa_pal_proc_t* proc) {
    if (!proc) return false;
    DWORD code;
    if (!GetExitCodeProcess(proc->handle, &code)) return false;
    return code == STILL_ACTIVE;
}

int oa_pal_proc_wait(oa_pal_proc_t* proc, int timeout_ms) {
    if (!proc) return -1;
    DWORD ms = (timeout_ms < 0) ? INFINITE : (DWORD)timeout_ms;
    DWORD ret = WaitForSingleObject(proc->handle, ms);
    if (ret == WAIT_OBJECT_0) {
        DWORD code;
        GetExitCodeProcess(proc->handle, &code);
        return (int)code;
    }
    return -1; /* 超时或错误 */
}

uint32_t oa_pal_proc_pid(oa_pal_proc_t* proc) {
    return proc ? proc->pid : 0;
}

void oa_pal_proc_free(oa_pal_proc_t* proc) {
    if (!proc) return;
    if (proc->handle) CloseHandle(proc->handle);
    if (proc->stdin_w) CloseHandle(proc->stdin_w);
    if (proc->stdout_r) CloseHandle(proc->stdout_r);
    free(proc);
}

uint32_t oa_pal_getpid(void) {
    return (uint32_t)GetCurrentProcessId();
}

/* ============================================================
 * 共享内存
 * ============================================================ */

struct oa_pal_shm {
    HANDLE handle;
    void*  ptr;
    size_t size;
    char   name[256];
};

oa_pal_shm_t* oa_pal_shm_create(const char* name, size_t size) {
    oa_pal_shm_t* shm = (oa_pal_shm_t*)calloc(1, sizeof(*shm));
    if (!shm) return NULL;
    
    /* Windows共享内存名带Global\前缀 */
    snprintf(shm->name, sizeof(shm->name), "Global\\oa_%s", name);
    shm->size = size;
    
    shm->handle = CreateFileMappingA(
        INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE,
        (DWORD)(size >> 32), (DWORD)(size & 0xFFFFFFFF),
        shm->name
    );
    
    if (!shm->handle) {
        free(shm);
        return NULL;
    }
    
    shm->ptr = MapViewOfFile(shm->handle, FILE_MAP_ALL_ACCESS, 0, 0, size);
    if (!shm->ptr) {
        CloseHandle(shm->handle);
        free(shm);
        return NULL;
    }
    
    return shm;
}

oa_pal_shm_t* oa_pal_shm_open(const char* name) {
    oa_pal_shm_t* shm = (oa_pal_shm_t*)calloc(1, sizeof(*shm));
    if (!shm) return NULL;
    
    snprintf(shm->name, sizeof(shm->name), "Global\\oa_%s", name);
    
    shm->handle = OpenFileMappingA(FILE_MAP_ALL_ACCESS, FALSE, shm->name);
    if (!shm->handle) {
        free(shm);
        return NULL;
    }
    
    shm->ptr = MapViewOfFile(shm->handle, FILE_MAP_ALL_ACCESS, 0, 0, 0);
    if (!shm->ptr) {
        CloseHandle(shm->handle);
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
    if (shm->ptr) UnmapViewOfFile(shm->ptr);
    if (shm->handle) CloseHandle(shm->handle);
    free(shm);
}

void oa_pal_shm_destroy(oa_pal_shm_t* shm) {
    oa_pal_shm_close(shm);
    /* Windows自动销毁无引用的文件映射 */
}

/* ============================================================
 * 互斥锁
 * ============================================================ */

struct oa_pal_mutex {
    HANDLE handle;
};

oa_pal_mutex_t* oa_pal_mutex_create(const char* name) {
    oa_pal_mutex_t* mtx = (oa_pal_mutex_t*)calloc(1, sizeof(*mtx));
    if (!mtx) return NULL;
    
    char full_name[300];
    snprintf(full_name, sizeof(full_name), "Global\\oa_mtx_%s", name);
    
    mtx->handle = CreateMutexA(NULL, FALSE, full_name);
    if (!mtx->handle) {
        free(mtx);
        return NULL;
    }
    return mtx;
}

oa_pal_mutex_t* oa_pal_mutex_open(const char* name) {
    oa_pal_mutex_t* mtx = (oa_pal_mutex_t*)calloc(1, sizeof(*mtx));
    if (!mtx) return NULL;
    
    char full_name[300];
    snprintf(full_name, sizeof(full_name), "Global\\oa_mtx_%s", name);
    
    mtx->handle = OpenMutexA(MUTEX_ALL_ACCESS, FALSE, full_name);
    if (!mtx->handle) {
        free(mtx);
        return NULL;
    }
    return mtx;
}

bool oa_pal_mutex_lock(oa_pal_mutex_t* mtx, int timeout_ms) {
    if (!mtx) return false;
    DWORD ms = (timeout_ms < 0) ? INFINITE : (DWORD)timeout_ms;
    return WaitForSingleObject(mtx->handle, ms) == WAIT_OBJECT_0;
}

void oa_pal_mutex_unlock(oa_pal_mutex_t* mtx) {
    if (mtx) ReleaseMutex(mtx->handle);
}

void oa_pal_mutex_destroy(oa_pal_mutex_t* mtx) {
    if (!mtx) return;
    if (mtx->handle) CloseHandle(mtx->handle);
    free(mtx);
}

/* ============================================================
 * 文件锁
 * ============================================================ */

bool oa_pal_flock(const char* path, int timeout_ms) {
    HANDLE h = CreateFileA(path, GENERIC_WRITE, 0, NULL,
                           OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return false;
    
    OVERLAPPED ov = {0};
    DWORD flags = LOCKFILE_EXCLUSIVE_LOCK;
    if (timeout_ms == 0) flags |= LOCKFILE_FAIL_IMMEDIATELY;
    
    BOOL ok = LockFileEx(h, flags, 0, 1, 0, &ov);
    /* 注意：句柄需要保存在某处以便后续unlock */
    /* 简化实现：这里只做尝试 */
    if (!ok) {
        CloseHandle(h);
        return false;
    }
    return true;
}

void oa_pal_funlock(const char* path) {
    /* 简化实现：关闭文件自动释放锁 */
    (void)path;
}

/* ============================================================
 * 时间
 * ============================================================ */

uint64_t oa_pal_time_ms(void) {
    FILETIME ft;
    GetSystemTimeAsFileTime(&ft);
    uint64_t t = ((uint64_t)ft.dwHighDateTime << 32) | ft.dwLowDateTime;
    /* FILETIME是100纳秒单位，从1601-01-01起 */
    /* 转换为毫秒，从Unix epoch起 */
    t -= 116444736000000000ULL;
    return t / 10000;
}

uint64_t oa_pal_time_us(void) {
    LARGE_INTEGER freq, count;
    QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&count);
    return (uint64_t)((double)count.QuadPart / freq.QuadPart * 1000000.0);
}

void oa_pal_sleep_ms(int ms) {
    Sleep((DWORD)ms);
}

/* ============================================================
 * 原子操作
 * ============================================================ */

int32_t oa_pal_atomic_load(volatile int32_t* ptr) {
    return InterlockedCompareExchange((volatile LONG*)ptr, 0, 0);
}

void oa_pal_atomic_store(volatile int32_t* ptr, int32_t val) {
    InterlockedExchange((volatile LONG*)ptr, val);
}

int32_t oa_pal_atomic_add(volatile int32_t* ptr, int32_t val) {
    return InterlockedExchangeAdd((volatile LONG*)ptr, val);
}

bool oa_pal_atomic_cas(volatile int32_t* ptr, int32_t expected, int32_t desired) {
    return InterlockedCompareExchange((volatile LONG*)ptr, desired, expected) == expected;
}

int64_t oa_pal_atomic_load64(volatile int64_t* ptr) {
    return InterlockedCompareExchange64((volatile LONG64*)ptr, 0, 0);
}

void oa_pal_atomic_store64(volatile int64_t* ptr, int64_t val) {
    InterlockedExchange64((volatile LONG64*)ptr, val);
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
    if (la > 0 && a[la-1] != '\\' && a[la-1] != '/') {
        result[la] = '\\';
        la++;
    }
    memcpy(result + la, b, lb + 1);
    return result;
}

char* oa_pal_path_temp(void) {
    char buf[MAX_PATH];
    GetTempPathA(MAX_PATH, buf);
    return _strdup(buf);
}

char* oa_pal_path_home(void) {
    const char* home = getenv("USERPROFILE");
    return home ? _strdup(home) : _strdup("C:\\Users\\Default");
}

char* oa_pal_path_cwd(void) {
    char buf[MAX_PATH];
    GetCurrentDirectoryA(MAX_PATH, buf);
    return _strdup(buf);
}

bool oa_pal_path_exists(const char* path) {
    DWORD attr = GetFileAttributesA(path);
    return attr != INVALID_FILE_ATTRIBUTES;
}

bool oa_pal_path_mkdir(const char* path) {
    /* 递归创建 */
    char tmp[MAX_PATH];
    strncpy(tmp, path, MAX_PATH - 1);
    tmp[MAX_PATH - 1] = '\0';
    
    for (char* p = tmp + 1; *p; p++) {
        if (*p == '\\' || *p == '/') {
            *p = '\0';
            CreateDirectoryA(tmp, NULL);
            *p = '\\';
        }
    }
    return CreateDirectoryA(tmp, NULL) || GetLastError() == ERROR_ALREADY_EXISTS;
}

bool oa_pal_path_is_dir(const char* path) {
    DWORD attr = GetFileAttributesA(path);
    return (attr != INVALID_FILE_ATTRIBUTES) && (attr & FILE_ATTRIBUTE_DIRECTORY);
}

/* ============================================================
 * 动态库
 * ============================================================ */

struct oa_pal_lib {
    HMODULE handle;
};

oa_pal_lib_t* oa_pal_lib_load(const char* path) {
    oa_pal_lib_t* lib = (oa_pal_lib_t*)calloc(1, sizeof(*lib));
    if (!lib) return NULL;
    
    lib->handle = LoadLibraryA(path);
    if (!lib->handle) {
        free(lib);
        return NULL;
    }
    return lib;
}

void* oa_pal_lib_symbol(oa_pal_lib_t* lib, const char* name) {
    if (!lib) return NULL;
    return (void*)GetProcAddress(lib->handle, name);
}

void oa_pal_lib_close(oa_pal_lib_t* lib) {
    if (!lib) return;
    if (lib->handle) FreeLibrary(lib->handle);
    free(lib);
}

const char* oa_pal_lib_error(void) {
    static char buf[256];
    FormatMessageA(FORMAT_MESSAGE_FROM_SYSTEM, NULL, GetLastError(),
                   0, buf, sizeof(buf), NULL);
    return buf;
}

/* ============================================================
 * 系统信息
 * ============================================================ */

void oa_pal_sysinfo(oa_pal_sysinfo_t* info) {
    if (!info) return;
    memset(info, 0, sizeof(*info));
    
    strncpy(info->os_name, "windows", sizeof(info->os_name));
    
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    info->cpu_count = si.dwNumberOfProcessors;
    
    #ifdef _M_X64
    strncpy(info->arch, "x86_64", sizeof(info->arch));
    #elif defined(_M_ARM64)
    strncpy(info->arch, "aarch64", sizeof(info->arch));
    #else
    strncpy(info->arch, "x86", sizeof(info->arch));
    #endif
    
    MEMORYSTATUSEX ms;
    ms.dwLength = sizeof(ms);
    GlobalMemoryStatusEx(&ms);
    info->mem_total = ms.ullTotalPhys;
    info->mem_avail = ms.ullAvailPhys;
}

/* ============================================================
 * 环境变量
 * ============================================================ */

const char* oa_pal_getenv(const char* name) {
    return getenv(name);
}

bool oa_pal_setenv(const char* name, const char* value) {
    char buf[4096];
    snprintf(buf, sizeof(buf), "%s=%s", name, value);
    return _putenv(buf) == 0;
}

#endif /* _WIN32 */
