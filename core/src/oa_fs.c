/*
 * XUANJI — 文件安全模块
 *
 * 沙箱 + 文件锁
 * 硬编码敏感路径黑名单，系统目录禁止访问
 *
 * 纯C11，仅依赖 oa_pal.h
 */

#include "XUANJI.h"
#include "oa_pal.h"
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

/* ============================================================
 * 敏感路径黑名单
 * ============================================================ */

/* 文件名/目录名黑名单（不区分大小写匹配） */
static const char* FS_BLACKLIST_NAMES[] = {
    ".ssh",
    ".env",
    ".gnupg",
    ".pgp",
    ".aws",
    ".azure",
    ".kube",
    ".docker",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".gitconfig",
    ".git-credentials",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
    "authorized_keys",
    "known_hosts",
    "password",
    "passwords",
    "passwd",
    "shadow",
    "secret",
    "secrets",
    "credentials",
    "token",
    "tokens",
    "private_key",
    "private.key",
    "master.key",
    ".env.local",
    ".env.production",
    ".env.development",
    "wp-config.php",
    "web.config",
    "appsettings.json",
    NULL
};

/* 系统目录前缀黑名单 */
static const char* FS_SYSTEM_DIRS[] = {
    /* Windows */
    "C:\\Windows",
    "C:\\Program Files",
    "C:\\Program Files (x86)",
    "C:\\ProgramData",
    /* Unix */
    "/etc",
    "/boot",
    "/sbin",
    "/usr/sbin",
    "/var/log",
    "/proc",
    "/sys",
    "/dev",
    "/root",
    NULL
};

/* ============================================================
 * 内部辅助
 * ============================================================ */

/* 大小写不敏感的字符串比较 */
static int fs_stricmp(const char* a, const char* b) {
    while (*a && *b) {
        int ca = tolower((unsigned char)*a);
        int cb = tolower((unsigned char)*b);
        if (ca != cb) return ca - cb;
        a++;
        b++;
    }
    return tolower((unsigned char)*a) - tolower((unsigned char)*b);
}

/* 大小写不敏感的前缀匹配 */
static bool fs_starts_with_i(const char* str, const char* prefix) {
    while (*prefix) {
        if (tolower((unsigned char)*str) != tolower((unsigned char)*prefix))
            return false;
        str++;
        prefix++;
    }
    return true;
}

/* 从路径中提取文件名（最后一个分隔符之后的部分） */
static const char* fs_basename(const char* path) {
    const char* last_sep = NULL;
    for (const char* p = path; *p; p++) {
        if (*p == '/' || *p == '\\') last_sep = p;
    }
    return last_sep ? last_sep + 1 : path;
}

/* 检查路径中是否包含黑名单中的目录名 */
static bool fs_path_contains_blacklisted(const char* path) {
    /* 拷贝路径进行分析 */
    size_t len = strlen(path);
    char* buf = (char*)malloc(len + 1);
    if (!buf) return true;  /* 安全侧：内存不足则拒绝 */
    memcpy(buf, path, len + 1);

    /* 归一化分隔符 */
    for (size_t i = 0; i < len; i++) {
        if (buf[i] == '\\') buf[i] = '/';
    }

    /* 逐段检查 */
    char* token = buf;
    while (*token) {
        /* 跳过连续分隔符 */
        while (*token == '/') token++;
        if (!*token) break;

        /* 找到当前段的结尾 */
        char* end = token;
        while (*end && *end != '/') end++;

        char saved = *end;
        *end = '\0';

        /* 对比黑名单 */
        for (int i = 0; FS_BLACKLIST_NAMES[i]; i++) {
            if (fs_stricmp(token, FS_BLACKLIST_NAMES[i]) == 0) {
                free(buf);
                return true;
            }
        }

        *end = saved;
        token = (*end) ? end + 1 : end;
    }

    free(buf);
    return false;
}

/* 检查是否为系统目录 */
static bool fs_is_system_dir(const char* path) {
    for (int i = 0; FS_SYSTEM_DIRS[i]; i++) {
        if (fs_starts_with_i(path, FS_SYSTEM_DIRS[i])) {
            /* 精确匹配或路径继续延伸 */
            size_t plen = strlen(FS_SYSTEM_DIRS[i]);
            char next = path[plen];
            if (next == '\0' || next == '/' || next == '\\') {
                return true;
            }
        }
    }
    return false;
}

/* ============================================================
 * 公共API实现
 * ============================================================ */

OA_API bool oa_fs_check(uint32_t agent_id, const char* path, oa_fs_op_t op) {
    (void)agent_id;  /* 未来可做per-agent权限 */

    if (!path || path[0] == '\0') return false;

    /* 1. 检查系统目录 */
    if (fs_is_system_dir(path)) return false;

    /* 2. 检查路径中是否包含敏感名称 */
    if (fs_path_contains_blacklisted(path)) return false;

    /* 3. 检查文件名本身 */
    const char* name = fs_basename(path);
    for (int i = 0; FS_BLACKLIST_NAMES[i]; i++) {
        if (fs_stricmp(name, FS_BLACKLIST_NAMES[i]) == 0) {
            return false;
        }
    }

    /* 4. 写入和删除操作额外检查 */
    if (op & (OA_FS_WRITE | OA_FS_DELETE)) {
        /* 禁止对只读关键文件操作 */
        if (fs_stricmp(name, "XUANJI.h") == 0) return false;
        if (fs_stricmp(name, "oa_pal.h") == 0) return false;
    }

    /* 5. 执行权限额外检查 */
    if (op & OA_FS_EXEC) {
        /* 禁止执行脚本/二进制，除非在安全路径内 */
        /* 简化：仅禁止常见危险扩展 */
        const char* ext = strrchr(name, '.');
        if (ext) {
            if (fs_stricmp(ext, ".bat") == 0 ||
                fs_stricmp(ext, ".cmd") == 0 ||
                fs_stricmp(ext, ".ps1") == 0 ||
                fs_stricmp(ext, ".vbs") == 0 ||
                fs_stricmp(ext, ".wsf") == 0) {
                return false;
            }
        }
    }

    return true;
}

OA_API bool oa_fs_lock(const char* path, int timeout_ms) {
    if (!path) return false;
    return oa_pal_flock(path, timeout_ms);
}

OA_API void oa_fs_unlock(const char* path) {
    if (!path) return;
    oa_pal_funlock(path);
}
