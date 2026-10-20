#pragma once
#include <stdint.h>
#include <stddef.h>
#include <pthread.h>

#ifdef __cplusplus
extern "C" {
#endif
typedef struct { int references; } xnu_task_model;
typedef struct { xnu_task_model xnu_task; } dtape_task_t;
typedef void (*dtape_kqchan_mach_port_notification_callback_f)(void*);
struct knote {
    uint32_t kn_id;
    uint64_t kn_ext[2], kn_sfflags;
    int kn_filter, kn_flags;
};
typedef struct dtape_kqchan_mach_port {
    int refcount;
    dtape_task_t* task;
    struct knote knote;
    dtape_kqchan_mach_port_notification_callback_f callback;
    void* context;
} dtape_kqchan_mach_port_t;
dtape_kqchan_mach_port_t* dtape_kqchan_mach_port_create(dtape_task_t*, uint32_t, uint64_t, uint64_t, uint64_t, dtape_kqchan_mach_port_notification_callback_f, void*);
void dtape_kqchan_mach_port_destroy(dtape_kqchan_mach_port_t*);
void dtape_kqchan_mach_port_disable_notifications(dtape_kqchan_mach_port_t*);
int dtape_kqchan_mach_port_has_events(dtape_kqchan_mach_port_t*);
void prepared_model_post(dtape_kqchan_mach_port_t*);
void prepared_model_fault(int allocation, int attachment, int has_events);
int prepared_model_live(void);
int prepared_model_detaching(void);
int prepared_model_snapshot(void);
void prepared_model_release_snapshot(void);
void prepared_model_delay_snapshot(int enable);
#ifdef __cplusplus
}
#endif
