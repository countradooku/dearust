// Test-only real dtape mutex; a kernel microthread owns it during contention.
#include <kern/locks.h>
static dtape_mutex_t dearust_lane_test_mutex;
void dearust_lane_mutex_initialize(void) { dtape_mutex_init(&dearust_lane_test_mutex); }
void dearust_lane_mutex_lock(void) { dtape_mutex_lock(&dearust_lane_test_mutex); }
void dearust_lane_mutex_unlock(void) { dtape_mutex_unlock(&dearust_lane_test_mutex); }
