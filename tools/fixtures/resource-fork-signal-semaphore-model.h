#ifndef RESOURCE_FORK_SIGNAL_MODEL_H
#define RESOURCE_FORK_SIGNAL_MODEL_H
#ifdef __cplusplus
extern "C" {
#endif
#include <stddef.h>
typedef struct modeled_semaphore* semaphore_t;
typedef struct dtape_semaphore {void* owning_task;semaphore_t xnu_semaphore;} dtape_semaphore_t;
typedef int kern_return_t;
#define KERN_SUCCESS 0
#define KERN_TERMINATED 37
void semaphore_reference(semaphore_t);
void semaphore_dereference(semaphore_t);
kern_return_t semaphore_signal(semaphore_t);
void panic(const char*);
void dtape_semaphore_up(dtape_semaphore_t*);
dtape_semaphore_t* model_semaphore_create(void);
void model_semaphore_destroy(dtape_semaphore_t*);
unsigned model_live_semaphores(void);
unsigned model_reference_count(void);
unsigned model_successful_signals(void);
unsigned model_terminated_signals(void);
#ifdef __cplusplus
}
#endif
#endif
