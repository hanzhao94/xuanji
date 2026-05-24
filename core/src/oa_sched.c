/*
 * XUANJI — 调度器 (oa_sched)
 *
 * 基于最小堆的优先级队列，用于Agent任务分配
 * priority越小优先级越高（0=最高）
 * 使用PAL互斥锁保证线程安全
 *
 * 设计要点：
 * - 标准二叉堆：数组存储，parent=i/2, left=2i, right=2i+1
 * - 堆顶永远是priority最小的任务
 * - push/pop均为O(log n)
 * - 同priority按deadline排序（更紧急的优先）
 *
 * 版本: 0.1.0
 * 日期: 2026-05-15
 */

#include "XUANJI.h"
#include "oa_pal.h"
#include <stdlib.h>
#include <string.h>

/* ============================================================
 * 内部结构
 * ============================================================ */

struct oa_sched {
    oa_task_t*        heap;      /* 1-indexed最小堆 (heap[0]不用) */
    uint32_t          capacity;  /* 堆数组大小 */
    uint32_t          count;     /* 当前元素数 */
    oa_pal_mutex_t*   mtx;       /* 线程安全锁 */
};

/* ============================================================
 * 堆操作（内部）
 * ============================================================ */

/*
 * 比较两个任务的优先级
 * 返回 true 如果 a 应排在 b 前面（a优先级更高）
 * 规则：priority小的优先；相同priority则deadline早的优先
 */
static bool task_higher(const oa_task_t* a, const oa_task_t* b)
{
    if (a->priority != b->priority) {
        return a->priority < b->priority;
    }
    return a->deadline < b->deadline;
}

/* 上浮：新插入元素向上调整 */
static void sift_up(oa_task_t* heap, uint32_t pos)
{
    while (pos > 1) {
        uint32_t parent = pos / 2;
        if (task_higher(&heap[pos], &heap[parent])) {
            /* 交换 */
            oa_task_t tmp;
            memcpy(&tmp, &heap[pos], sizeof(oa_task_t));
            memcpy(&heap[pos], &heap[parent], sizeof(oa_task_t));
            memcpy(&heap[parent], &tmp, sizeof(oa_task_t));
            pos = parent;
        } else {
            break;
        }
    }
}

/* 下沉：堆顶删除后向下调整 */
static void sift_down(oa_task_t* heap, uint32_t count, uint32_t pos)
{
    for (;;) {
        uint32_t smallest = pos;
        uint32_t left = pos * 2;
        uint32_t right = pos * 2 + 1;

        if (left <= count && task_higher(&heap[left], &heap[smallest])) {
            smallest = left;
        }
        if (right <= count && task_higher(&heap[right], &heap[smallest])) {
            smallest = right;
        }

        if (smallest == pos) break;

        /* 交换 */
        oa_task_t tmp;
        memcpy(&tmp, &heap[pos], sizeof(oa_task_t));
        memcpy(&heap[pos], &heap[smallest], sizeof(oa_task_t));
        memcpy(&heap[smallest], &tmp, sizeof(oa_task_t));

        pos = smallest;
    }
}

/* ============================================================
 * 公共API
 * ============================================================ */

OA_API oa_sched_t* oa_sched_create(uint32_t capacity)
{
    if (capacity == 0) return NULL;

    oa_sched_t* sched = (oa_sched_t*)calloc(1, sizeof(oa_sched_t));
    if (!sched) return NULL;

    /* +1 因为1-indexed */
    sched->heap = (oa_task_t*)calloc(capacity + 1, sizeof(oa_task_t));
    if (!sched->heap) {
        free(sched);
        return NULL;
    }

    sched->capacity = capacity;
    sched->count = 0;

    /* 创建互斥锁 */
    sched->mtx = oa_pal_mutex_create("oa_sched_lock");
    if (!sched->mtx) {
        free(sched->heap);
        free(sched);
        return NULL;
    }

    return sched;
}

OA_API void oa_sched_destroy(oa_sched_t* sched)
{
    if (!sched) return;

    if (sched->mtx) {
        oa_pal_mutex_destroy(sched->mtx);
    }
    free(sched->heap);
    free(sched);
}

/*
 * 推入任务
 *
 * 将task插入堆尾，然后上浮到正确位置。
 * 返回 OA_ERR_FULL 如果队列已满。
 */
OA_API oa_err_t oa_sched_push(oa_sched_t* sched, const oa_task_t* task)
{
    if (!sched || !task) return OA_ERR_INVALID;
    if (task->payload_len > sizeof(task->payload)) return OA_ERR_INVALID;

    if (!oa_pal_mutex_lock(sched->mtx, 5000)) {
        return OA_ERR_TIMEOUT;
    }

    if (sched->count >= sched->capacity) {
        oa_pal_mutex_unlock(sched->mtx);
        return OA_ERR_FULL;
    }

    /* 插入堆尾 */
    sched->count++;
    memcpy(&sched->heap[sched->count], task, sizeof(oa_task_t));

    /* 上浮 */
    sift_up(sched->heap, sched->count);

    oa_pal_mutex_unlock(sched->mtx);
    return OA_OK;
}

/*
 * 弹出最高优先级任务
 *
 * 取堆顶（priority最小），将堆尾移到堆顶后下沉。
 * 返回 OA_ERR_EMPTY 如果队列为空。
 */
OA_API oa_err_t oa_sched_pop(oa_sched_t* sched, oa_task_t* out)
{
    if (!sched || !out) return OA_ERR_INVALID;

    if (!oa_pal_mutex_lock(sched->mtx, 5000)) {
        return OA_ERR_TIMEOUT;
    }

    if (sched->count == 0) {
        oa_pal_mutex_unlock(sched->mtx);
        return OA_ERR_EMPTY;
    }

    /* 取堆顶 */
    memcpy(out, &sched->heap[1], sizeof(oa_task_t));

    /* 堆尾移到堆顶 */
    if (sched->count > 1) {
        memcpy(&sched->heap[1], &sched->heap[sched->count], sizeof(oa_task_t));
    }
    sched->count--;

    /* 下沉 */
    if (sched->count > 0) {
        sift_down(sched->heap, sched->count, 1);
    }

    oa_pal_mutex_unlock(sched->mtx);
    return OA_OK;
}

/*
 * 当前队列中的任务数
 */
OA_API uint32_t oa_sched_size(oa_sched_t* sched)
{
    if (!sched) return 0;

    if (!oa_pal_mutex_lock(sched->mtx, 1000)) {
        return 0;
    }

    uint32_t sz = sched->count;

    oa_pal_mutex_unlock(sched->mtx);
    return sz;
}
