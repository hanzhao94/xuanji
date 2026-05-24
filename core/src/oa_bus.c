/*
 * XUANJI — 消息总线 (oa_bus)
 *
 * 基于无锁环形缓冲区的Agent间通信
 * 使用CAS原子操作实现多生产者无锁写入
 * 每个Agent独立读游标，支持按agent_id过滤、广播、超时等待
 *
 * 设计要点：
 * - write_cursor: 全局单调递增，CAS抢占
 * - read_cursor:  每Agent独立，无竞争推进
 * - 槽位复用：当所有reader的游标都越过某位置后，该槽位可被覆写
 * - committed标记：写入完成后才对消费者可见
 *
 * 版本: 0.1.0
 * 日期: 2026-05-15
 */

#include "XUANJI.h"
#include "oa_pal.h"
#include <stdlib.h>
#include <string.h>

/* ============================================================
 * 内部常量与结构
 * ============================================================ */

#define OA_BUS_MAX_AGENTS 256

typedef struct {
    volatile int32_t  committed;  /* 0=未提交/可覆写, 1=已提交可读 */
    oa_msg_t          msg;
} oa_bus_slot_t;

typedef struct {
    uint32_t          agent_id;
    volatile int32_t  read_cursor;  /* 该Agent下次读取位置 */
    volatile int32_t  active;       /* 1=已注册 */
} oa_bus_reader_t;

struct oa_bus {
    oa_bus_slot_t*    slots;
    uint32_t          capacity;     /* 2的幂 */
    uint32_t          mask;         /* capacity - 1 */
    volatile int32_t  write_cursor; /* 下一个写入位置（单调递增） */
    oa_bus_reader_t   readers[OA_BUS_MAX_AGENTS];
    volatile int32_t  alive;
};

/* ============================================================
 * 内部工具
 * ============================================================ */

static uint32_t next_pow2(uint32_t v)
{
    if (v == 0) return 1;
    v--;
    v |= v >> 1;  v |= v >> 2;
    v |= v >> 4;  v |= v >> 8;
    v |= v >> 16;
    return v + 1;
}

/*
 * 所有活跃reader中最小的read_cursor。
 * 如果没有活跃reader，返回write_cursor（意味着缓冲区可全部覆写）。
 */
static int32_t min_read_cursor(oa_bus_t* bus)
{
    int32_t wc = oa_pal_atomic_load(&bus->write_cursor);
    int32_t min_rc = wc;

    for (int i = 0; i < OA_BUS_MAX_AGENTS; i++) {
        if (oa_pal_atomic_load(&bus->readers[i].active)) {
            int32_t rc = oa_pal_atomic_load(&bus->readers[i].read_cursor);
            if (rc < min_rc) {
                min_rc = rc;
            }
        }
    }
    return min_rc;
}

/* 查找已注册的reader */
static oa_bus_reader_t* bus_find_reader(oa_bus_t* bus, uint32_t agent_id)
{
    for (int i = 0; i < OA_BUS_MAX_AGENTS; i++) {
        if (oa_pal_atomic_load(&bus->readers[i].active) &&
            bus->readers[i].agent_id == agent_id) {
            return &bus->readers[i];
        }
    }
    return NULL;
}

/* 查找或注册reader */
static oa_bus_reader_t* bus_get_reader(oa_bus_t* bus, uint32_t agent_id)
{
    /* 先查已有 */
    oa_bus_reader_t* r = bus_find_reader(bus, agent_id);
    if (r) return r;

    /* 注册新的：CAS抢空位 */
    for (int i = 0; i < OA_BUS_MAX_AGENTS; i++) {
        if (oa_pal_atomic_cas(&bus->readers[i].active, 0, 1)) {
            bus->readers[i].agent_id = agent_id;
            /* 新reader从当前write_cursor开始，不读历史 */
            oa_pal_atomic_store(&bus->readers[i].read_cursor,
                                oa_pal_atomic_load(&bus->write_cursor));
            return &bus->readers[i];
        }
    }
    return NULL;  /* 超过最大Agent数 */
}

/* ============================================================
 * 公共API
 * ============================================================ */

OA_API oa_bus_t* oa_bus_create(uint32_t capacity)
{
    if (capacity == 0) return NULL;
    capacity = next_pow2(capacity);

    oa_bus_t* bus = (oa_bus_t*)calloc(1, sizeof(oa_bus_t));
    if (!bus) return NULL;

    bus->slots = (oa_bus_slot_t*)calloc(capacity, sizeof(oa_bus_slot_t));
    if (!bus->slots) {
        free(bus);
        return NULL;
    }

    bus->capacity = capacity;
    bus->mask = capacity - 1;
    oa_pal_atomic_store(&bus->write_cursor, 0);
    oa_pal_atomic_store(&bus->alive, 1);

    for (uint32_t i = 0; i < capacity; i++) {
        oa_pal_atomic_store(&bus->slots[i].committed, 0);
    }
    for (int i = 0; i < OA_BUS_MAX_AGENTS; i++) {
        oa_pal_atomic_store(&bus->readers[i].active, 0);
    }

    return bus;
}

OA_API void oa_bus_destroy(oa_bus_t* bus)
{
    if (!bus) return;
    oa_pal_atomic_store(&bus->alive, 0);
    free(bus->slots);
    free(bus);
}

/*
 * 发布消息到总线（多生产者安全）
 *
 * 1. 读write_cursor，检查缓冲区是否满
 * 2. CAS抢占写位置
 * 3. 写入消息，设committed=1
 */
OA_API oa_err_t oa_bus_publish(oa_bus_t* bus, const oa_msg_t* msg)
{
    if (!bus || !msg) return OA_ERR_INVALID;
    if (!oa_pal_atomic_load(&bus->alive)) return OA_ERR_DEAD;
    if (msg->payload_len > OA_MSG_MAX_PAYLOAD) return OA_ERR_INVALID;

    int spins = 0;
    const int max_spins = 64;

    for (;;) {
        int32_t pos = oa_pal_atomic_load(&bus->write_cursor);
        int32_t min_rc = min_read_cursor(bus);

        /* 缓冲区满检查：写入位置不能超过最慢reader一整圈 */
        if ((uint32_t)(pos - min_rc) >= bus->capacity) {
            return OA_ERR_FULL;
        }

        /* CAS抢占位置 */
        if (!oa_pal_atomic_cas(&bus->write_cursor, pos, pos + 1)) {
            if (++spins > max_spins) {
                return OA_ERR_FULL;  /* 竞争太激烈，放弃 */
            }
            continue;
        }

        /* 抢占成功，写入消息 */
        uint32_t idx = (uint32_t)pos & bus->mask;
        oa_bus_slot_t* slot = &bus->slots[idx];

        /* 清除旧committed标记（上一轮的数据已被所有reader越过） */
        oa_pal_atomic_store(&slot->committed, 0);

        /* 复制消息 */
        memcpy(&slot->msg, msg, sizeof(oa_msg_t));
        if (slot->msg.timestamp == 0) {
            slot->msg.timestamp = oa_pal_time_ms();
        }

        /* 发布：标记为已提交 */
        oa_pal_atomic_store(&slot->committed, 1);

        return OA_OK;
    }
}

/*
 * 接收消息（按agent_id过滤）
 *
 * 匹配规则：
 * - to_agent == agent_id  → 点对点消息
 * - to_agent == 0         → 广播消息
 *
 * 每个Agent独立推进read_cursor，非匹配消息跳过。
 * timeout_ms <= 0 为非阻塞模式。
 */
OA_API oa_err_t oa_bus_receive(oa_bus_t* bus, uint32_t agent_id,
                                oa_msg_t* out, int timeout_ms)
{
    if (!bus || !out || agent_id == 0) return OA_ERR_INVALID;
    if (!oa_pal_atomic_load(&bus->alive)) return OA_ERR_DEAD;

    oa_bus_reader_t* reader = bus_get_reader(bus, agent_id);
    if (!reader) return OA_ERR_FULL;

    uint64_t start = oa_pal_time_ms();
    int backoff = 1;

    for (;;) {
        int32_t wc = oa_pal_atomic_load(&bus->write_cursor);
        int32_t rc = oa_pal_atomic_load(&reader->read_cursor);

        /* 扫描从read_cursor到write_cursor */
        while (rc < wc) {
            uint32_t idx = (uint32_t)rc & bus->mask;
            oa_bus_slot_t* slot = &bus->slots[idx];

            /* 等待该位置的写入提交 */
            if (!oa_pal_atomic_load(&slot->committed)) {
                /*
                 * 写者已抢占位置但还没提交。
                 * 短暂自旋等待提交完成。
                 */
                int wait_spins = 0;
                while (!oa_pal_atomic_load(&slot->committed) &&
                       wait_spins < 1000) {
                    wait_spins++;
                }
                if (!oa_pal_atomic_load(&slot->committed)) {
                    /* 写者可能崩溃了，跳过这个位置 */
                    rc++;
                    oa_pal_atomic_store(&reader->read_cursor, rc);
                    continue;
                }
            }

            /* 检查消息是否匹配 */
            if (slot->msg.to_agent == agent_id ||
                slot->msg.to_agent == 0) {
                /* 匹配！复制消息 */
                memcpy(out, &slot->msg, sizeof(oa_msg_t));
                rc++;
                oa_pal_atomic_store(&reader->read_cursor, rc);
                return OA_OK;
            }

            /* 不匹配，跳过 */
            rc++;
        }

        /* 更新游标 */
        oa_pal_atomic_store(&reader->read_cursor, rc);

        /* 非阻塞模式 */
        if (timeout_ms <= 0) {
            return OA_ERR_EMPTY;
        }

        /* 超时检查 */
        uint64_t elapsed = oa_pal_time_ms() - start;
        if ((int64_t)elapsed >= (int64_t)timeout_ms) {
            return OA_ERR_TIMEOUT;
        }

        /* 自适应退避 */
        oa_pal_sleep_ms(backoff);
        if (backoff < 16) backoff *= 2;
    }
}

/*
 * 查询指定Agent的未读匹配消息数
 */
OA_API uint32_t oa_bus_pending(oa_bus_t* bus, uint32_t agent_id)
{
    if (!bus || agent_id == 0) return 0;
    if (!oa_pal_atomic_load(&bus->alive)) return 0;

    oa_bus_reader_t* reader = bus_find_reader(bus, agent_id);
    if (!reader) return 0;

    int32_t wc = oa_pal_atomic_load(&bus->write_cursor);
    int32_t rc = oa_pal_atomic_load(&reader->read_cursor);
    uint32_t count = 0;

    for (int32_t pos = rc; pos < wc; pos++) {
        uint32_t idx = (uint32_t)pos & bus->mask;
        oa_bus_slot_t* slot = &bus->slots[idx];

        if (oa_pal_atomic_load(&slot->committed)) {
            if (slot->msg.to_agent == agent_id ||
                slot->msg.to_agent == 0) {
                count++;
            }
        }
    }

    return count;
}
