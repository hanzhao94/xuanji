/*
 * XUANJI — 心跳检测模块
 *
 * 存活监控 + 超时回收
 * GREEN:  < 30s  无心跳
 * YELLOW: 30-60s 无心跳
 * ORANGE: 60-120s 无心跳
 * RED:    > 120s 无心跳
 *
 * 纯C11，仅依赖 oa_pal.h
 */

#include "XUANJI.h"
#include "oa_pal.h"
#include <stdlib.h>
#include <string.h>

/* ============================================================
 * 内部数据结构
 * ============================================================ */

/* 超时阈值（毫秒） */
#define OA_HEART_YELLOW_MS  30000   /* 30秒 */
#define OA_HEART_ORANGE_MS  60000   /* 60秒 */
#define OA_HEART_RED_MS    120000   /* 120秒 */

typedef struct {
    uint32_t agent_id;
    uint64_t last_beat_ms;      /* 最后一次心跳的时间戳 */
    bool     active;            /* 是否已注册 */
} oa_heart_entry_t;

struct oa_heart {
    oa_heart_entry_t* entries;  /* Agent心跳记录数组 */
    uint32_t          max_agents;
    uint32_t          count;    /* 当前注册数 */
    uint64_t          start_ms; /* 创建时间，用于计算 uptime */
    oa_pal_mutex_t*   lock;     /* 线程安全互斥锁 */
};

/* ============================================================
 * 内部辅助
 * ============================================================ */

/* 根据 agent_id 找到 entry 的索引，未找到返回 -1 */
static int heart_find(oa_heart_t* heart, uint32_t agent_id) {
    for (uint32_t i = 0; i < heart->max_agents; i++) {
        if (heart->entries[i].active && heart->entries[i].agent_id == agent_id) {
            return (int)i;
        }
    }
    return -1;
}

/* 找到第一个空闲槽位，未找到返回 -1 */
static int heart_find_free(oa_heart_t* heart) {
    for (uint32_t i = 0; i < heart->max_agents; i++) {
        if (!heart->entries[i].active) {
            return (int)i;
        }
    }
    return -1;
}

/* 根据时间差计算健康等级 */
static oa_health_level_t heart_calc_level(uint64_t elapsed_ms) {
    if (elapsed_ms >= OA_HEART_RED_MS)    return OA_HEALTH_RED;
    if (elapsed_ms >= OA_HEART_ORANGE_MS) return OA_HEALTH_ORANGE;
    if (elapsed_ms >= OA_HEART_YELLOW_MS) return OA_HEALTH_YELLOW;
    return OA_HEALTH_GREEN;
}

/* ============================================================
 * 公共API实现
 * ============================================================ */

OA_API oa_heart_t* oa_heart_create(uint32_t max_agents) {
    if (max_agents == 0) return NULL;

    oa_heart_t* heart = (oa_heart_t*)calloc(1, sizeof(oa_heart_t));
    if (!heart) return NULL;

    heart->entries = (oa_heart_entry_t*)calloc(max_agents, sizeof(oa_heart_entry_t));
    if (!heart->entries) {
        free(heart);
        return NULL;
    }

    heart->lock = oa_pal_mutex_create("oa_heart");
    if (!heart->lock) {
        free(heart->entries);
        free(heart);
        return NULL;
    }

    heart->max_agents = max_agents;
    heart->count = 0;
    heart->start_ms = oa_pal_time_ms();

    return heart;
}

OA_API void oa_heart_destroy(oa_heart_t* heart) {
    if (!heart) return;
    if (heart->lock)    oa_pal_mutex_destroy(heart->lock);
    if (heart->entries)  free(heart->entries);
    free(heart);
}

OA_API oa_err_t oa_heart_register(oa_heart_t* heart, uint32_t agent_id) {
    if (!heart) return OA_ERR_INVALID;

    oa_pal_mutex_lock(heart->lock, -1);

    /* 检查是否已注册 */
    if (heart_find(heart, agent_id) >= 0) {
        oa_pal_mutex_unlock(heart->lock);
        return OA_ERR_EXISTS;
    }

    /* 找空闲槽位 */
    int slot = heart_find_free(heart);
    if (slot < 0) {
        oa_pal_mutex_unlock(heart->lock);
        return OA_ERR_FULL;
    }

    heart->entries[slot].agent_id = agent_id;
    heart->entries[slot].last_beat_ms = oa_pal_time_ms();
    heart->entries[slot].active = true;
    heart->count++;

    oa_pal_mutex_unlock(heart->lock);
    return OA_OK;
}

OA_API oa_err_t oa_heart_beat(oa_heart_t* heart, uint32_t agent_id) {
    if (!heart) return OA_ERR_INVALID;

    oa_pal_mutex_lock(heart->lock, -1);

    int idx = heart_find(heart, agent_id);
    if (idx < 0) {
        oa_pal_mutex_unlock(heart->lock);
        return OA_ERR_NOTFOUND;
    }

    heart->entries[idx].last_beat_ms = oa_pal_time_ms();

    oa_pal_mutex_unlock(heart->lock);
    return OA_OK;
}

OA_API oa_health_level_t oa_heart_check(oa_heart_t* heart, uint32_t agent_id) {
    if (!heart) return OA_HEALTH_RED;

    oa_pal_mutex_lock(heart->lock, -1);

    int idx = heart_find(heart, agent_id);
    if (idx < 0) {
        oa_pal_mutex_unlock(heart->lock);
        return OA_HEALTH_RED;  /* 未注册视为最差状态 */
    }

    uint64_t now = oa_pal_time_ms();
    uint64_t elapsed = now - heart->entries[idx].last_beat_ms;
    oa_health_level_t level = heart_calc_level(elapsed);

    oa_pal_mutex_unlock(heart->lock);
    return level;
}

OA_API oa_health_t oa_heart_snapshot(oa_heart_t* heart) {
    oa_health_t snap;
    memset(&snap, 0, sizeof(snap));

    if (!heart) return snap;

    oa_pal_mutex_lock(heart->lock, -1);

    uint64_t now = oa_pal_time_ms();
    snap.uptime_ms = now - heart->start_ms;
    snap.agent_count = heart->count;

    /* 统计健康Agent数 */
    uint32_t healthy = 0;
    for (uint32_t i = 0; i < heart->max_agents; i++) {
        if (!heart->entries[i].active) continue;
        uint64_t elapsed = now - heart->entries[i].last_beat_ms;
        if (heart_calc_level(elapsed) <= OA_HEALTH_YELLOW) {
            healthy++;
        }
    }
    snap.agent_healthy = healthy;

    oa_pal_mutex_unlock(heart->lock);

    /* 系统资源信息：从PAL层获取 */
    oa_pal_sysinfo_t sysinfo;
    oa_pal_sysinfo(&sysinfo);

    if (sysinfo.mem_total > 0) {
        snap.mem_percent = (float)(sysinfo.mem_total - sysinfo.mem_avail)
                           / (float)sysinfo.mem_total * 100.0f;
    }

    /* CPU/GPU/磁盘 使用率需要更复杂的采集，这里暂设0 */
    snap.cpu_percent = 0.0f;
    snap.disk_percent = 0.0f;
    snap.gpu_mem_percent = 0.0f;

    /* 任务统计需要调度器配合，这里设0 */
    snap.tasks_total = 0;
    snap.tasks_failed = 0;

    return snap;
}
