/*
 * XUANJI — 资源管理器
 *
 * 分区 + 租约 + 仲裁
 * 支持独占资源（屏幕/鼠标/键盘/麦克风/扬声器）排队
 * 租约机制，超时自动释放
 *
 * 纯C11，仅依赖 oa_pal.h
 */

#include "XUANJI.h"
#include "oa_pal.h"
#include <stdlib.h>
#include <string.h>

/* ============================================================
 * 配置常量
 * ============================================================ */

#define OA_RES_MAX_LEASES   256     /* 最大并发租约数 */
#define OA_RES_MAX_WAITERS  64      /* 每种独占资源最大等待队列 */
#define OA_RES_DEFAULT_TTL  300000  /* 默认租约时长：5分钟 */

/* 独占资源类型范围 */
static bool res_is_exclusive(oa_res_type_t type) {
    return type == OA_RES_SCREEN
        || type == OA_RES_MOUSE
        || type == OA_RES_KEYBOARD
        || type == OA_RES_MIC
        || type == OA_RES_SPEAKER;
}

/* ============================================================
 * 内部数据结构
 * ============================================================ */

typedef struct {
    oa_lease_t lease;
    bool       used;
} oa_res_slot_t;

/* 独占资源等待队列项 */
typedef struct {
    uint32_t agent_id;
    bool     waiting;
} oa_res_waiter_t;

/* 独占资源的等待队列 */
typedef struct {
    oa_res_type_t    type;
    oa_res_waiter_t  queue[OA_RES_MAX_WAITERS];
    uint32_t         head;
    uint32_t         tail;
    uint32_t         count;
    uint64_t         current_lease_id;  /* 当前持有的租约ID，0=无人持有 */
} oa_res_excl_t;

struct oa_res {
    oa_res_slot_t   slots[OA_RES_MAX_LEASES];
    uint64_t        next_lease_id;
    oa_res_excl_t   exclusive[5];       /* SCREEN, MOUSE, KEYBOARD, MIC, SPEAKER */
    oa_pal_mutex_t* lock;
};

/* ============================================================
 * 内部辅助
 * ============================================================ */

static uint64_t res_new_lease_id(oa_res_t* res) {
    return ++res->next_lease_id;
}

/* 将独占资源类型映射到 exclusive 数组索引 */
static int res_excl_index(oa_res_type_t type) {
    switch (type) {
        case OA_RES_SCREEN:   return 0;
        case OA_RES_MOUSE:    return 1;
        case OA_RES_KEYBOARD: return 2;
        case OA_RES_MIC:      return 3;
        case OA_RES_SPEAKER:  return 4;
        default:              return -1;
    }
}

/* 找到空闲的租约槽位 */
static int res_find_free_slot(oa_res_t* res) {
    for (int i = 0; i < OA_RES_MAX_LEASES; i++) {
        if (!res->slots[i].used) return i;
    }
    return -1;
}

/* 根据 lease_id 找到槽位索引 */
static int res_find_lease(oa_res_t* res, uint64_t lease_id) {
    for (int i = 0; i < OA_RES_MAX_LEASES; i++) {
        if (res->slots[i].used && res->slots[i].lease.lease_id == lease_id) {
            return i;
        }
    }
    return -1;
}

/* 清理过期租约（在锁内调用） */
static void res_expire_leases(oa_res_t* res) {
    uint64_t now = oa_pal_time_ms();
    for (int i = 0; i < OA_RES_MAX_LEASES; i++) {
        if (!res->slots[i].used) continue;
        if (res->slots[i].lease.expires_at > 0 &&
            res->slots[i].lease.expires_at <= now) {
            /* 过期了，清理 */
            oa_res_type_t type = (oa_res_type_t)res->slots[i].lease.resource_type;
            uint64_t lid = res->slots[i].lease.lease_id;
            res->slots[i].used = false;

            /* 如果是独占资源，检查等待队列 */
            int ei = res_excl_index(type);
            if (ei >= 0 && res->exclusive[ei].current_lease_id == lid) {
                res->exclusive[ei].current_lease_id = 0;
            }
        }
    }
}

/* 为独占资源的等待队列添加等待者 */
static bool res_excl_enqueue(oa_res_excl_t* excl, uint32_t agent_id) {
    if (excl->count >= OA_RES_MAX_WAITERS) return false;
    uint32_t idx = excl->tail % OA_RES_MAX_WAITERS;
    excl->queue[idx].agent_id = agent_id;
    excl->queue[idx].waiting = true;
    excl->tail++;
    excl->count++;
    return true;
}

/* 从独占资源等待队列取出下一个 */
static bool res_excl_dequeue(oa_res_excl_t* excl, uint32_t* agent_id) {
    while (excl->count > 0) {
        uint32_t idx = excl->head % OA_RES_MAX_WAITERS;
        excl->head++;
        excl->count--;
        if (excl->queue[idx].waiting) {
            *agent_id = excl->queue[idx].agent_id;
            excl->queue[idx].waiting = false;
            return true;
        }
    }
    return false;
}

/* 创建一个租约记录 */
static oa_err_t res_create_lease(oa_res_t* res, uint32_t agent_id,
                                  oa_res_type_t type, const char* name,
                                  oa_lease_t* out) {
    int slot = res_find_free_slot(res);
    if (slot < 0) return OA_ERR_FULL;

    uint64_t now = oa_pal_time_ms();
    oa_lease_t* lease = &res->slots[slot].lease;

    lease->lease_id = res_new_lease_id(res);
    lease->agent_id = agent_id;
    lease->resource_type = (uint32_t)type;
    lease->granted_at = now;
    lease->expires_at = now + OA_RES_DEFAULT_TTL;
    if (name) {
        strncpy(lease->resource_name, name, sizeof(lease->resource_name) - 1);
        lease->resource_name[sizeof(lease->resource_name) - 1] = '\0';
    } else {
        lease->resource_name[0] = '\0';
    }

    res->slots[slot].used = true;

    /* 如果是独占资源，标记当前持有 */
    int ei = res_excl_index(type);
    if (ei >= 0) {
        res->exclusive[ei].current_lease_id = lease->lease_id;
    }

    if (out) {
        memcpy(out, lease, sizeof(oa_lease_t));
    }

    return OA_OK;
}

/* ============================================================
 * 公共API实现
 * ============================================================ */

OA_API oa_res_t* oa_res_create(void) {
    oa_res_t* res = (oa_res_t*)calloc(1, sizeof(oa_res_t));
    if (!res) return NULL;

    res->lock = oa_pal_mutex_create("oa_res");
    if (!res->lock) {
        free(res);
        return NULL;
    }

    res->next_lease_id = 0;

    /* 初始化独占资源 */
    oa_res_type_t excl_types[] = {
        OA_RES_SCREEN, OA_RES_MOUSE, OA_RES_KEYBOARD,
        OA_RES_MIC, OA_RES_SPEAKER
    };
    for (int i = 0; i < 5; i++) {
        res->exclusive[i].type = excl_types[i];
        res->exclusive[i].head = 0;
        res->exclusive[i].tail = 0;
        res->exclusive[i].count = 0;
        res->exclusive[i].current_lease_id = 0;
    }

    return res;
}

OA_API void oa_res_destroy(oa_res_t* res) {
    if (!res) return;
    if (res->lock) oa_pal_mutex_destroy(res->lock);
    free(res);
}

OA_API oa_err_t oa_res_acquire(oa_res_t* res, uint32_t agent_id,
                                oa_res_type_t type, const char* name,
                                int timeout_ms, oa_lease_t* out) {
    if (!res) return OA_ERR_INVALID;

    oa_pal_mutex_lock(res->lock, -1);

    /* 先清理过期租约 */
    res_expire_leases(res);

    /* 非独占资源：直接分配 */
    if (!res_is_exclusive(type)) {
        oa_err_t err = res_create_lease(res, agent_id, type, name, out);
        oa_pal_mutex_unlock(res->lock);
        return err;
    }

    /* 独占资源：检查是否有人持有 */
    int ei = res_excl_index(type);
    if (ei < 0) {
        oa_pal_mutex_unlock(res->lock);
        return OA_ERR_INVALID;
    }

    if (res->exclusive[ei].current_lease_id == 0) {
        /* 无人持有，直接授予 */
        oa_err_t err = res_create_lease(res, agent_id, type, name, out);
        oa_pal_mutex_unlock(res->lock);
        return err;
    }

    /* 已被占用 */
    if (timeout_ms == 0) {
        /* 不等待 */
        oa_pal_mutex_unlock(res->lock);
        return OA_ERR_TIMEOUT;
    }

    /* 加入等待队列，然后轮询等待 */
    if (!res_excl_enqueue(&res->exclusive[ei], agent_id)) {
        oa_pal_mutex_unlock(res->lock);
        return OA_ERR_FULL;
    }

    oa_pal_mutex_unlock(res->lock);

    /* 轮询等待，每100ms检查一次 */
    uint64_t start = oa_pal_time_ms();
    uint64_t deadline = (timeout_ms < 0)
                        ? UINT64_MAX
                        : start + (uint64_t)timeout_ms;

    while (oa_pal_time_ms() < deadline) {
        oa_pal_sleep_ms(100);

        oa_pal_mutex_lock(res->lock, -1);
        res_expire_leases(res);

        if (res->exclusive[ei].current_lease_id == 0) {
            /* 资源释放了，检查队列头是不是自己 */
            uint32_t next_id = 0;
            if (res_excl_dequeue(&res->exclusive[ei], &next_id)) {
                if (next_id == agent_id) {
                    oa_err_t err = res_create_lease(res, agent_id, type, name, out);
                    oa_pal_mutex_unlock(res->lock);
                    return err;
                }
                /* 不是自己，放回去（简化处理：实际应给next_id授予） */
                res_excl_enqueue(&res->exclusive[ei], next_id);
            }
        }

        oa_pal_mutex_unlock(res->lock);
    }

    /* 超时，从队列移除自己 */
    oa_pal_mutex_lock(res->lock, -1);
    /* 简化处理：标记为不等待（下次dequeue会跳过） */
    for (uint32_t i = 0; i < OA_RES_MAX_WAITERS; i++) {
        uint32_t idx = (res->exclusive[ei].head + i) % OA_RES_MAX_WAITERS;
        if (i >= res->exclusive[ei].count) break;
        if (res->exclusive[ei].queue[idx].agent_id == agent_id &&
            res->exclusive[ei].queue[idx].waiting) {
            res->exclusive[ei].queue[idx].waiting = false;
            break;
        }
    }
    oa_pal_mutex_unlock(res->lock);

    return OA_ERR_TIMEOUT;
}

OA_API oa_err_t oa_res_release(oa_res_t* res, uint64_t lease_id) {
    if (!res) return OA_ERR_INVALID;

    oa_pal_mutex_lock(res->lock, -1);

    int idx = res_find_lease(res, lease_id);
    if (idx < 0) {
        oa_pal_mutex_unlock(res->lock);
        return OA_ERR_NOTFOUND;
    }

    oa_res_type_t type = (oa_res_type_t)res->slots[idx].lease.resource_type;
    res->slots[idx].used = false;

    /* 如果是独占资源，清除持有标记 */
    int ei = res_excl_index(type);
    if (ei >= 0 && res->exclusive[ei].current_lease_id == lease_id) {
        res->exclusive[ei].current_lease_id = 0;
    }

    oa_pal_mutex_unlock(res->lock);
    return OA_OK;
}

OA_API oa_err_t oa_res_revoke(oa_res_t* res, uint32_t agent_id) {
    if (!res) return OA_ERR_INVALID;

    oa_pal_mutex_lock(res->lock, -1);

    bool found = false;
    for (int i = 0; i < OA_RES_MAX_LEASES; i++) {
        if (!res->slots[i].used) continue;
        if (res->slots[i].lease.agent_id != agent_id) continue;

        found = true;
        oa_res_type_t type = (oa_res_type_t)res->slots[i].lease.resource_type;
        uint64_t lid = res->slots[i].lease.lease_id;
        res->slots[i].used = false;

        /* 清除独占资源持有标记 */
        int ei = res_excl_index(type);
        if (ei >= 0 && res->exclusive[ei].current_lease_id == lid) {
            res->exclusive[ei].current_lease_id = 0;
        }
    }

    oa_pal_mutex_unlock(res->lock);
    return found ? OA_OK : OA_ERR_NOTFOUND;
}
