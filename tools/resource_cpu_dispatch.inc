// SPDX-License-Identifier: GPL-3.0-or-later
// Standalone Linux/glibc CPU timer derivative. No runtime ownership activation.
#ifndef DEARUST_RESOURCE_CPU_DISPATCH_INC
#define DEARUST_RESOURCE_CPU_DISPATCH_INC
#include <atomic>
#include <cerrno>
#include <condition_variable>
#include <cstdint>
#include <exception>
#include <limits>
#include <memory>
#include <mutex>
#include <new>
#include <pthread.h>
#include <poll.h>
#include <signal.h>
#include <sys/eventfd.h>
#include <sys/signalfd.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
#include <unordered_map>

namespace DearustResources {
class CpuTimer;
class CpuTimerDispatcher {
    friend class CpuTimer;
    struct Diagnostics { std::atomic<int> error{0}; };
    struct Arm {
        std::mutex lock;
        std::shared_ptr<void> fd_owner;
        std::shared_ptr<Diagnostics> diagnostics;
        int pidfd;
        uint64_t eid, generation, deadline;
        uintptr_t token = 0;
        timer_t timer{};
        bool timer_live = false, active = false, delivered = false;
        Arm(std::shared_ptr<void> fd, std::shared_ptr<Diagnostics> d,
            int descriptor, uint64_t identity, uint64_t version, uint64_t time):
            fd_owner(std::move(fd)), diagnostics(std::move(d)), pidfd(descriptor),
            eid(identity), generation(version), deadline(time) {}
    };
    std::mutex registry_lock_, startup_lock_;
    std::condition_variable startup_changed_;
    std::unordered_map<uintptr_t, std::shared_ptr<Arm>> arms_;
    const int signal_;
    pthread_t thread_{};
    pid_t tid_ = 0;
    bool thread_live_ = false, ready_ = false;
    int signals_fd_ = -1, stop_fd_ = -1;
    std::atomic<bool> stopping_{false};
    std::atomic<int> error_{0};

    explicit CpuTimerDispatcher(int reserved_signal):signal_(reserved_signal) {}
    static void* entry(void* context) noexcept {
        static_cast<CpuTimerDispatcher*>(context)->receive();
        return nullptr;
    }
    void receive() noexcept {
        // Establish the actual receiver mask before publishing its TID. Thread
        // creation adapters must not make an inherited-mask assumption part of
        // notification safety, even when explicit pthread attributes were used.
        sigset_t notification; sigemptyset(&notification); sigaddset(&notification, signal_);
        const int mask_error = pthread_sigmask(SIG_BLOCK, &notification, nullptr);
        const auto tid = static_cast<pid_t>(syscall(SYS_gettid));
        {
            std::lock_guard<std::mutex> guard(startup_lock_);
            tid_ = tid;
            if (mask_error || tid <= 0) error_.store(mask_error ? -mask_error : -EIO, std::memory_order_release);
            ready_ = true;
        }
        startup_changed_.notify_one();
        if (mask_error || tid <= 0) return;
        for (;;) {
            pollfd descriptors[2] = {{signals_fd_, POLLIN, 0}, {stop_fd_, POLLIN, 0}};
            const int result = poll(descriptors, 2, -1);
            if (result < 0) {
                if (errno == EINTR) continue;
                const int error = -errno;
                std::lock_guard<std::mutex> guard(startup_lock_);
                error_.store(error, std::memory_order_release);
                return;
            }
            if (descriptors[1].revents & POLLIN) {
                if (stopping_.load(std::memory_order_acquire)) return;
            }
            if ((descriptors[0].revents | descriptors[1].revents) & (POLLERR | POLLHUP | POLLNVAL)) {
                std::lock_guard<std::mutex> guard(startup_lock_);
                error_.store(-EIO, std::memory_order_release); return;
            }
            if (!(descriptors[0].revents & POLLIN)) continue;
            signalfd_siginfo info{};
            const auto bytes = read(signals_fd_, &info, sizeof(info));
            if (bytes < 0 && (errno == EAGAIN || errno == EINTR)) continue;
            if (bytes != sizeof(info)) {
                const int error = bytes < 0 ? -errno : -EIO;
                std::lock_guard<std::mutex> guard(startup_lock_);
                error_.store(error, std::memory_order_release); return;
            }
            // Shutdown has a separate eventfd. A reserved signal from any
            // userspace origin, including a copied token, is not an expiry.
            if (info.ssi_signo != static_cast<unsigned>(signal_) || info.ssi_code != SI_TIMER) continue;
            const auto token = static_cast<uintptr_t>(info.ssi_ptr);
#ifdef DEARUST_RESOURCE_CPU_TEST_EVENT
            DEARUST_RESOURCE_CPU_TEST_EVENT(token);
#endif
            std::shared_ptr<Arm> arm;
            {
                std::lock_guard<std::mutex> guard(registry_lock_);
                const auto found = arms_.find(token);
                if (found == arms_.end()) continue;
                arm = found->second;
            }
            {
                std::lock_guard<std::mutex> guard(arm->lock);
                if (arm->active && !arm->delivered) {
                    // The token identifies this bound timer's lifetime, not a
                    // generation. A queued wake may predate a successful rearm.
                    // Query the exact bound kernel timer under the publication
                    // lock: nonzero remaining time cannot signal the new limit.
                    struct itimerspec remaining{};
                    if (timer_gettime(arm->timer, &remaining) != 0) {
                        arm->diagnostics->error.store(-errno, std::memory_order_release);
                    } else if (!remaining.it_value.tv_sec && !remaining.it_value.tv_nsec) {
                        arm->delivered = true;
                        const int error = send(*arm);
                        if (error) arm->diagnostics->error.store(error, std::memory_order_release);
                    }
                }
            }
            // Keep the registry pin through the kernel binding's lifetime.
            // Only disable/destruction retires it; an old wake cannot erase a
            // later generation's registration after its owner rearmed it.
        }
    }
    int start() {
        sigset_t signals; sigemptyset(&signals); sigaddset(&signals, signal_);
        signals_fd_ = signalfd(-1, &signals, SFD_NONBLOCK | SFD_CLOEXEC);
        if (signals_fd_ < 0) return -errno;
        stop_fd_ = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);
        if (stop_fd_ < 0) return -errno;
        pthread_attr_t attr;
        int error = pthread_attr_init(&attr);
        if (error) return -error;
        sigset_t mask;
        error = pthread_sigmask(SIG_SETMASK, nullptr, &mask);
        if (!error && sigaddset(&mask, signal_) != 0) error = errno;
        if (!error) error = pthread_attr_setsigmask_np(&attr, &mask);
        if (!error) error = pthread_create(&thread_, &attr, entry, this);
        if (pthread_attr_destroy(&attr) != 0) std::terminate();
        if (error) return -error;
        thread_live_ = true;
        std::unique_lock<std::mutex> guard(startup_lock_);
        startup_changed_.wait(guard, [&] { return ready_; });
        return error_.load(std::memory_order_acquire);
    }
    int enroll(const std::shared_ptr<Arm>& arm) {
        std::lock_guard<std::mutex> guard(registry_lock_);
        // IDs are not reused even if the last owner destroys this service and
        // a later owner constructs another one in the same process.
        static std::atomic<uintptr_t> next{1};
        auto token = next.load(std::memory_order_relaxed);
        for (;;) {
            if (token == std::numeric_limits<uintptr_t>::max()) return -EOVERFLOW;
            if (next.compare_exchange_weak(token, token + 1, std::memory_order_relaxed)) break;
        }
        arms_.emplace(token, arm);
        arm->token = token;
        return 0;
    }
    void remove(uintptr_t token) noexcept {
        std::lock_guard<std::mutex> guard(registry_lock_);
        arms_.erase(token);
    }
    static int signal_pidfd(int fd, int signal) noexcept {
#ifdef DEARUST_RESOURCE_CPU_TEST_SIGNAL
        // Standalone native fixtures may instrument this typed syscall boundary.
        // Production builds neither define this adapter nor add a libc symbol.
        return DEARUST_RESOURCE_CPU_TEST_SIGNAL(fd, signal);
#else
        if (syscall(SYS_pidfd_send_signal, fd, signal, nullptr, 0) < 0) return -errno;
        return 0;
#endif
    }
    static int send(const Arm& arm) noexcept { return signal_pidfd(arm.pidfd, SIGXCPU); }
    static void delete_timer(Arm& arm) noexcept {
        if (!arm.timer_live) return;
        if (timer_delete(arm.timer) != 0) std::terminate();
        arm.timer_live = false;
    }
    void retire(const std::shared_ptr<Arm>& arm) noexcept {
        if (!arm) return;
        {
            std::lock_guard<std::mutex> guard(arm->lock);
            arm->active = false;
            delete_timer(*arm);
        }
        remove(arm->token);
    }
public:
    /// Caller exclusively reserves this RT signal and preserves its non-ignored
    /// disposition for this service's entire lifetime. SIG_IGN discards timer
    /// notifications even while blocked, so an ignored reservation is rejected.
    /// No process-wide disposition or caller signal mask changes.
    /// Construct before publishing any timers. A failure returns a negative
    /// Linux errno without exposing a partially started receiver.
    static std::shared_ptr<CpuTimerDispatcher> create(int reserved_signal, int& error) {
        error = 0;
        if (reserved_signal < SIGRTMIN || reserved_signal > SIGRTMAX) {
            error = -EINVAL; return {};
        }
        struct sigaction disposition{};
        if (sigaction(reserved_signal, nullptr, &disposition) != 0) {
            error = -errno; return {};
        }
        if (disposition.sa_handler == SIG_IGN) { error = -EINVAL; return {}; }
        try {
            auto service = std::shared_ptr<CpuTimerDispatcher>(new CpuTimerDispatcher(reserved_signal));
            error = service->start();
            if (error) return {};
            return service;
        } catch (const std::bad_alloc&) { error = -ENOMEM; return {}; }
    }
    CpuTimerDispatcher(const CpuTimerDispatcher&) = delete;
    CpuTimerDispatcher& operator=(const CpuTimerDispatcher&) = delete;
    ~CpuTimerDispatcher() {
        // Only timer owners pin the service. Arms/receiver never own it, so the
        // last shared_ptr cannot disappear on the receiver thread itself.
        {
            std::lock_guard<std::mutex> guard(registry_lock_);
            if (!arms_.empty()) std::terminate();
        }
        if (thread_live_) {
            stopping_.store(true, std::memory_order_release);
            // This is the only write to an initially empty private eventfd.
            // Its counter cannot overflow; waking does not allocate sigqueue
            // storage and still works when RT signal queues are exhausted.
            const uint64_t one = 1;
            ssize_t bytes;
            do { bytes = write(stop_fd_, &one, sizeof(one)); } while (bytes < 0 && errno == EINTR);
            if (bytes != sizeof(one)) std::terminate();
            if (pthread_join(thread_, nullptr) != 0) std::terminate();
        }
        if (signals_fd_ >= 0) close(signals_fd_);
        if (stop_fd_ >= 0) close(stop_fd_);
    }
    int last_error() const noexcept { return error_.load(std::memory_order_acquire); }
};

class CpuTimer {
    using Arm = CpuTimerDispatcher::Arm;
    using Diagnostics = CpuTimerDispatcher::Diagnostics;
    std::mutex lock_;
    std::shared_ptr<CpuTimerDispatcher> dispatcher_;
    std::shared_ptr<void> fd_owner_;
    std::shared_ptr<Diagnostics> diagnostics_ = std::make_shared<Diagnostics>();
    std::shared_ptr<Arm> current_;
    int pidfd_, clock_error_ = 0;
    clockid_t clock_{};
    uint64_t eid_;
    bool stopped_ = false;
public:
    /// fd_owner must pin this exact pidfd for the same actual process identified
    /// by host_pid and its CPU clock. The caller establishes that identity before
    /// construction. It must not own Process, CpuTimer or CpuTimerDispatcher,
    /// directly or indirectly: expiry can release its last pin on the receiver.
    /// A descriptor-only shared owner is appropriate. Foreign APIs do not unwind.
    CpuTimer(pid_t host_pid, int pidfd, std::shared_ptr<void> fd_owner, uint64_t eid,
             std::shared_ptr<CpuTimerDispatcher> dispatcher):
        dispatcher_(std::move(dispatcher)), fd_owner_(std::move(fd_owner)), pidfd_(pidfd), eid_(eid) {
        if (pidfd < 0 || !fd_owner_ || !dispatcher_) { clock_error_ = EINVAL; return; }
        clock_error_ = clock_getcpuclockid(host_pid, &clock_);
    }
    CpuTimer(const CpuTimer&) = delete;
    CpuTimer& operator=(const CpuTimer&) = delete;
    ~CpuTimer() { disable(); }
    int last_error() const noexcept {
        const auto error = diagnostics_->error.load(std::memory_order_acquire);
        return error ? error : (dispatcher_ ? dispatcher_->last_error() : -EINVAL);
    }
    int disable() noexcept {
        std::lock_guard<std::mutex> guard(lock_);
        if (dispatcher_) dispatcher_->retire(current_);
        current_.reset();
        diagnostics_->error.store(0, std::memory_order_release);
        return 0;
    }
    void shutdown() noexcept {
        std::lock_guard<std::mutex> guard(lock_);
        stopped_ = true;
        if (dispatcher_) dispatcher_->retire(current_);
        current_.reset();
        diagnostics_->error.store(0, std::memory_order_release);
    }
    /// Absolute one-shot process user+system CPU deadline. All allocation and
    /// receiver creation occur before publication. Failed preparation retains
    /// the previous arm; expiry only locks, pins existing storage, and sends.
    int set_deadline(uint64_t deadline_ns, uint64_t generation) {
        std::lock_guard<std::mutex> guard(lock_);
        if (stopped_) return -ESRCH;
        if (clock_error_) return -clock_error_;
        if (const int error = dispatcher_->last_error()) return error;
        // Linux converts this timespec to signed ktime and otherwise silently
        // clamps a larger absolute deadline, causing a premature notification.
        if (deadline_ns > static_cast<uint64_t>(std::numeric_limits<int64_t>::max())) return -EOVERFLOW;
        struct timespec now{};
        if (clock_gettime(clock_, &now) != 0) return -errno;
        if (now.tv_sec < 0 || now.tv_nsec < 0 || now.tv_nsec >= 1000000000) return -EIO;
        const uint64_t seconds = static_cast<uint64_t>(now.tv_sec);
        const bool expired_now = seconds > deadline_ns / 1000000000 ||
            (seconds == deadline_ns / 1000000000 &&
             static_cast<uint64_t>(now.tv_nsec) >= deadline_ns % 1000000000);
        if (expired_now) {
            Arm immediate(fd_owner_, diagnostics_, pidfd_, eid_, generation, deadline_ns);
            const int error = CpuTimerDispatcher::send(immediate);
            if (error) return error;
            dispatcher_->retire(current_); current_.reset();
            diagnostics_->error.store(0, std::memory_order_release);
            return 0;
        }
        if (current_) {
            // The kernel timer pins the originally validated struct pid. Reuse
            // that binding rather than creating another through the numeric PID.
            // No allocation or registry mutation occurs on this path.
            std::lock_guard<std::mutex> arm_guard(current_->lock);
            struct itimerspec value{};
            value.it_value.tv_sec = static_cast<time_t>(deadline_ns / 1000000000);
            value.it_value.tv_nsec = static_cast<long>(deadline_ns % 1000000000);
            if (timer_settime(current_->timer, TIMER_ABSTIME, &value, nullptr) != 0) return -errno;
            current_->generation = generation;
            current_->deadline = deadline_ns;
            current_->delivered = false;
            current_->active = true;
            diagnostics_->error.store(0, std::memory_order_release);
            return 0;
        }
        std::shared_ptr<Arm> next;
        try {
            next = std::make_shared<Arm>(fd_owner_, diagnostics_, pidfd_, eid_, generation, deadline_ns);
            const int error = dispatcher_->enroll(next);
            if (error) return error;
        } catch (const std::bad_alloc&) { return -ENOMEM; }
        std::unique_lock<std::mutex> next_guard(next->lock);
        struct sigevent event{};
        event.sigev_notify = SIGEV_THREAD_ID;
        event.sigev_signo = dispatcher_->signal_;
        event.sigev_value.sival_ptr = reinterpret_cast<void*>(next->token);
        event._sigev_un._tid = dispatcher_->tid_;
        {
            // Pair receiver failure with target lookup: the receiver cannot exit
            // and have its numeric TID reused while timer_create binds it.
            std::lock_guard<std::mutex> service_guard(dispatcher_->startup_lock_);
            int error = dispatcher_->last_error();
            if (!error && timer_create(clock_, &event, &next->timer) != 0) error = -errno;
            if (error) {
                next_guard.unlock(); dispatcher_->remove(next->token); return error;
            }
        }
        next->timer_live = true;
        // A process CPU clock ID encodes a numeric PID. Bind the unarmed timer
        // first, then prove the original pidfd's process is still alive. That
        // process could not have died and had its PID reused before the binding
        // if it is alive now. Failure deletes only this private prepared timer.
        if (const int error = CpuTimerDispatcher::signal_pidfd(next->pidfd, 0)) {
            CpuTimerDispatcher::delete_timer(*next);
            next_guard.unlock(); dispatcher_->remove(next->token); return error;
        }
        struct itimerspec value{};
        value.it_value.tv_sec = static_cast<time_t>(deadline_ns / 1000000000);
        value.it_value.tv_nsec = static_cast<long>(deadline_ns % 1000000000);
        if (timer_settime(next->timer, TIMER_ABSTIME, &value, nullptr) != 0) {
            const int error = -errno;
            CpuTimerDispatcher::delete_timer(*next);
            next_guard.unlock(); dispatcher_->remove(next->token); return error;
        }
        dispatcher_->retire(current_);
        current_ = next;
        diagnostics_->error.store(0, std::memory_order_release);
        next->active = true;
        return 0;
    }
};
} // namespace DearustResources
#endif
