#include "semaphore-model.h"
#include <assert.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdlib.h>
struct modeled_semaphore {atomic_uint references;atomic_bool active;};
static _Atomic(struct modeled_semaphore*) live;
static atomic_uint successes,terminated;
extern void native_fixture_migrate(void);
void panic(const char* message){(void)message;abort();}
void semaphore_reference(semaphore_t s){assert(atomic_fetch_add(&s->references,1)>0);}
void semaphore_dereference(semaphore_t s){if(atomic_fetch_sub(&s->references,1)==1){atomic_store(&live,NULL);free(s);}}
kern_return_t semaphore_signal(semaphore_t s){native_fixture_migrate();assert(atomic_load(&s->references)>0);if(!atomic_load(&s->active)){++terminated;return KERN_TERMINATED;}++successes;return KERN_SUCCESS;}
dtape_semaphore_t* model_semaphore_create(void){dtape_semaphore_t*w=malloc(sizeof(*w));semaphore_t s=malloc(sizeof(*s));assert(w&&s);atomic_init(&s->references,1);atomic_init(&s->active,true);atomic_store(&live,s);w->owning_task=NULL;w->xnu_semaphore=s;return w;}
void model_semaphore_destroy(dtape_semaphore_t*w){atomic_store(&w->xnu_semaphore->active,false);semaphore_dereference(w->xnu_semaphore);free(w);}
unsigned model_live_semaphores(void){return atomic_load(&live)!=NULL;}
unsigned model_reference_count(void){return atomic_load(&atomic_load(&live)->references);}
unsigned model_successful_signals(void){return atomic_load(&successes);}
unsigned model_terminated_signals(void){return atomic_load(&terminated);}
