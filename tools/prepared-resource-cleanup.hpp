// Owned candidate cleanup infrastructure. No installed/reference source changes.
#ifndef DEARUST_PREPARED_RESOURCE_CLEANUP_HPP
#define DEARUST_PREPARED_RESOURCE_CLEANUP_HPP

#include <pthread.h>
#include <cstdlib>

namespace DarlingServer {
struct PreparedResourceCleanupJob {
    PreparedResourceCleanupJob* next = nullptr;
    void (*run)(void*) noexcept = nullptr;
    void* context = nullptr;
    bool queued = false;
};

// Intrusive ownership: the submitting object's preexisting self-reference keeps
// the job alive. Neither submission, waiting nor removal allocates storage.
class PreparedResourceCleanupQueue {
    pthread_mutex_t _lock = PTHREAD_MUTEX_INITIALIZER;
    pthread_cond_t _available = PTHREAD_COND_INITIALIZER;
    PreparedResourceCleanupJob* _head = nullptr;
    PreparedResourceCleanupJob* _tail = nullptr;

    static void check(int error) noexcept { if (error) std::abort(); }
public:
    PreparedResourceCleanupQueue() = default;
    PreparedResourceCleanupQueue(const PreparedResourceCleanupQueue&) = delete;
    PreparedResourceCleanupQueue& operator=(const PreparedResourceCleanupQueue&) = delete;

    void push(PreparedResourceCleanupJob& job) noexcept {
        check(pthread_mutex_lock(&_lock));
        if (job.queued || !job.run) std::abort();
        job.next = nullptr;
        job.queued = true;
        if (_tail) _tail->next = &job;
        else _head = &job;
        _tail = &job;
        check(pthread_mutex_unlock(&_lock));
        check(pthread_cond_signal(&_available));
    }

    PreparedResourceCleanupJob* take() noexcept {
        check(pthread_mutex_lock(&_lock));
        while (!_head) check(pthread_cond_wait(&_available, &_lock));
        auto* job = _head;
        _head = job->next;
        if (!_head) _tail = nullptr;
        job->next = nullptr;
        job->queued = false;
        check(pthread_mutex_unlock(&_lock));
        return job;
    }
};
}
#endif
