/*
 * XUANJI — 进程管理模块
 *
 * 进程隔离 + 生命周期 + 自保护
 * 命令黑名单过滤危险操作
 *
 * 纯C11，仅依赖 oa_pal.h
 */

#include "XUANJI.h"
#include "oa_pal.h"
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

/* ============================================================
 * 命令黑名单
 * ============================================================ */

/* 危险命令/子串（大小写不敏感匹配） */
static const char* PROC_CMD_BLACKLIST[] = {
    /* 文件系统破坏 */
    "rm -rf /",
    "rm -rf /*",
    "rm -rf ~",
    "rm -rf .",
    "del /s /q c:\\",
    "del /s /q c:",
    "rmdir /s /q c:\\",
    "rd /s /q c:\\",

    /* 磁盘格式化 */
    "format c:",
    "format d:",
    "format e:",
    "mkfs",
    "dd if=/dev/zero",
    "dd if=/dev/urandom",

    /* 系统关机/重启 */
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
    "init 0",
    "init 6",

    /* 权限提升/用户操作 */
    "chmod 777 /",
    "chown root",
    "passwd",
    "useradd",
    "userdel",
    "adduser",
    "deluser",

    /* 网络危险操作 */
    "iptables -F",
    "iptables --flush",
    "netsh advfirewall set",

    /* 注册表破坏 */
    "reg delete hklm",
    "reg delete hkcu",
    "reg delete hkcr",

    /* 其他危险操作 */
    ":(){ :|:& };:",          /* fork bomb */
    "> /dev/sda",
    "mkfs.ext",
    "wipefs",
    "fdisk",
    "parted",
    "diskpart",

    NULL
};

/* 危险可执行文件名（精确匹配命令的第一个token） */
static const char* PROC_EXEC_BLACKLIST[] = {
    "format",
    "diskpart",
    "bcdedit",
    "sfc",
    "dism",
    "wmic",
    NULL
};

/* ============================================================
 * 内部辅助
 * ============================================================ */

static int proc_tolower_char(int c) {
    return tolower((unsigned char)c);
}

/* 大小写不敏感子串搜索 */
static bool proc_contains_i(const char* haystack, const char* needle) {
    size_t hlen = strlen(haystack);
    size_t nlen = strlen(needle);
    if (nlen > hlen) return false;

    for (size_t i = 0; i <= hlen - nlen; i++) {
        bool match = true;
        for (size_t j = 0; j < nlen; j++) {
            if (proc_tolower_char(haystack[i + j]) != proc_tolower_char(needle[j])) {
                match = false;
                break;
            }
        }
        if (match) return true;
    }
    return false;
}

/* 大小写不敏感的字符串比较 */
static int proc_stricmp(const char* a, const char* b) {
    while (*a && *b) {
        int ca = proc_tolower_char(*a);
        int cb = proc_tolower_char(*b);
        if (ca != cb) return ca - cb;
        a++;
        b++;
    }
    return proc_tolower_char(*a) - proc_tolower_char(*b);
}

/* 从命令行提取第一个 token（可执行文件名） */
static void proc_extract_exe(const char* cmd, char* buf, size_t buflen) {
    /* 跳过前导空格 */
    while (*cmd && isspace((unsigned char)*cmd)) cmd++;

    size_t i = 0;
    /* 处理引号 */
    if (*cmd == '"') {
        cmd++;
        while (*cmd && *cmd != '"' && i < buflen - 1) {
            buf[i++] = *cmd++;
        }
    } else {
        while (*cmd && !isspace((unsigned char)*cmd) && i < buflen - 1) {
            buf[i++] = *cmd++;
        }
    }
    buf[i] = '\0';

    /* 提取纯文件名（去掉路径） */
    char* last_sep = NULL;
    for (char* p = buf; *p; p++) {
        if (*p == '/' || *p == '\\') last_sep = p;
    }
    if (last_sep) {
        memmove(buf, last_sep + 1, strlen(last_sep + 1) + 1);
    }

    /* 去掉扩展名 */
    char* dot = strrchr(buf, '.');
    if (dot) *dot = '\0';
}

/* ============================================================
 * 公共API实现
 * ============================================================ */

OA_API bool oa_proc_is_safe_cmd(const char* cmd) {
    if (!cmd || cmd[0] == '\0') return false;

    /* 1. 检查命令黑名单（子串匹配） */
    for (int i = 0; PROC_CMD_BLACKLIST[i]; i++) {
        if (proc_contains_i(cmd, PROC_CMD_BLACKLIST[i])) {
            return false;
        }
    }

    /* 2. 检查可执行文件名黑名单 */
    char exe[256];
    proc_extract_exe(cmd, exe, sizeof(exe));
    for (int i = 0; PROC_EXEC_BLACKLIST[i]; i++) {
        if (proc_stricmp(exe, PROC_EXEC_BLACKLIST[i]) == 0) {
            return false;
        }
    }

    /* 3. 检查管道到危险命令 */
    const char* pipe = strstr(cmd, "|");
    while (pipe) {
        pipe++;  /* 跳过 | */
        while (*pipe && isspace((unsigned char)*pipe)) pipe++;
        if (*pipe) {
            char pipe_exe[256];
            proc_extract_exe(pipe, pipe_exe, sizeof(pipe_exe));
            for (int i = 0; PROC_EXEC_BLACKLIST[i]; i++) {
                if (proc_stricmp(pipe_exe, PROC_EXEC_BLACKLIST[i]) == 0) {
                    return false;
                }
            }
        }
        pipe = strstr(pipe, "|");
    }

    return true;
}

OA_API oa_proc_t* oa_proc_spawn(const char* cmd, const char* workdir) {
    if (!cmd) return NULL;

    /* 安全检查 */
    if (!oa_proc_is_safe_cmd(cmd)) return NULL;

    /* 委托给PAL层 */
    oa_pal_proc_t* pal_proc = oa_pal_proc_spawn(cmd, workdir);
    if (!pal_proc) return NULL;

    /* oa_proc_t 直接复用 oa_pal_proc_t 指针
     * 这里通过 struct 包装来保持接口独立性 */
    return (oa_proc_t*)pal_proc;
}

OA_API bool oa_proc_is_alive(oa_proc_t* proc) {
    return oa_pal_proc_is_alive((oa_pal_proc_t*)proc);
}

OA_API int oa_proc_wait(oa_proc_t* proc, int timeout_ms) {
    return oa_pal_proc_wait((oa_pal_proc_t*)proc, timeout_ms);
}

OA_API bool oa_proc_kill(oa_proc_t* proc) {
    return oa_pal_proc_kill((oa_pal_proc_t*)proc);
}

OA_API uint32_t oa_proc_pid(oa_proc_t* proc) {
    return oa_pal_proc_pid((oa_pal_proc_t*)proc);
}

OA_API void oa_proc_free(oa_proc_t* proc) {
    oa_pal_proc_free((oa_pal_proc_t*)proc);
}
