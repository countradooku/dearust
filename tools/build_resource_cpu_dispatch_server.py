#!/usr/bin/env python3
"""Build a fresh CPU-dispatch derivative without activating resource ownership.

The caller explicitly reserves an otherwise unused native realtime signal for
this server process. One shared synchronous receiver serves all live timer owners;
no callback thread or allocation is required when a CPU deadline expires.
"""
import argparse
import hashlib
import json
from pathlib import Path
import signal
import sys


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('CPU dispatcher integration anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


def patch_cpu_dispatch(files, reserved_signal, helper):
    files = dict(files)
    files['resource-cpu-timer.inc'] = helper
    state = files['resource-server-state.inc']
    factory = '''// Explicitly reserved native CPU-timer signal; guest signal policy is unchanged.
std::mutex resourceCpuDispatcherLock;
std::weak_ptr<DearustResources::CpuTimerDispatcher> resourceCpuDispatcherInstance;
std::shared_ptr<DearustResources::CpuTimerDispatcher> resourceCpuDispatcher() {
    std::lock_guard<std::mutex> lock(resourceCpuDispatcherLock);
    if (auto current = resourceCpuDispatcherInstance.lock()) return current;
    int error = 0;
    auto prepared = DearustResources::CpuTimerDispatcher::create(@SIGNAL@, error);
    if (!prepared) {
        if (error >= 0) std::terminate();
        throw std::system_error(-error, std::generic_category());
    }
    resourceCpuDispatcherInstance = prepared;
    return prepared;
}
// Infinite/never-set limits do not need a receiver. Preparation remains fallible
// and private until the first successful arm; terminal death cannot resurrect it.
class ResourceLazyCpuTimer {
    mutable std::mutex lock_;
    std::unique_ptr<DearustResources::CpuTimer> timer_;
    pid_t pid_;
    int pidfd_;
    std::shared_ptr<void> fd_owner_;
    uint64_t eid_;
    bool stopped_ = false;
public:
    ResourceLazyCpuTimer(pid_t pid, int pidfd, std::shared_ptr<void> owner, uint64_t eid):
        pid_(pid), pidfd_(pidfd), fd_owner_(std::move(owner)), eid_(eid) {}
    int last_error() const noexcept {
        std::lock_guard<std::mutex> lock(lock_);
        return timer_ ? timer_->last_error() : 0;
    }
    int disable() noexcept {
        std::lock_guard<std::mutex> lock(lock_);
        return timer_ ? timer_->disable() : 0;
    }
    void shutdown() noexcept {
        std::lock_guard<std::mutex> lock(lock_);
        stopped_ = true;
        if (timer_) timer_->shutdown();
    }
    int set_deadline(uint64_t deadline, uint64_t generation) {
        std::lock_guard<std::mutex> lock(lock_);
        if (stopped_) return -ESRCH;
        if (timer_) return timer_->set_deadline(deadline, generation);
        try {
            auto prepared = std::make_unique<DearustResources::CpuTimer>(
                pid_, pidfd_, fd_owner_, eid_, resourceCpuDispatcher());
            const int error = prepared->set_deadline(deadline, generation);
            if (error) return error;
            timer_ = std::move(prepared);
            return 0;
        } catch (const std::bad_alloc&) { return -ENOMEM; }
          catch (const std::system_error& error) { return -error.code().value(); }
    }
};
'''.replace('@SIGNAL@', str(reserved_signal))
    state = once(state, 'namespace {\nconstexpr uint64_t resourceInfinity',
                 'namespace {\n' + factory + 'constexpr uint64_t resourceInfinity')
    state = once(state, 'std::unique_ptr<DearustResources::CpuTimer> cpu;',
                 'std::unique_ptr<ResourceLazyCpuTimer> cpu;')
    state = once(state,
        'cpu = std::make_unique<DearustResources::CpuTimer>(pid, pidfd->fd(), pidfd, eid);',
        'cpu = std::make_unique<ResourceLazyCpuTimer>(pid, pidfd->fd(), pidfd, eid);')
    state = once(state,
        '// The foreign CPU-clock lookup uses a numeric PID. Check original pidfd\n'
        '        // AFTER that lookup so a clock from a recycled PID is never committed.',
        '// Validate the pinned initial identity. Delayed CPU arms separately\n'
        '        // recheck this pidfd after each unarmed kernel timer binding.')
    files['resource-server-state.inc'] = state
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cpu-signal', type=int, required=True,
                        help='native RT signal exclusively reserved by the caller')
    args = parser.parse_args()
    if not int(signal.SIGRTMIN) <= args.cpu_signal <= int(signal.SIGRTMAX):
        parser.error('cpu-signal must be a non-libc-reserved native realtime signal')
    candidate = args.candidate.resolve(strict=True)
    output = args.output.resolve()
    if output.exists() or output.is_relative_to(candidate) or candidate.is_relative_to(output):
        parser.error('output must be absent and disjoint from the frozen candidate')
    helper = Path(__file__).resolve().with_name('resource_cpu_dispatch.inc')
    inputs = [Path(__file__).resolve(), helper]
    hashes = {str(path): digest(path) for path in inputs}
    import build_resource_lifecycle_server as lifecycle
    import build_resource_lockset_server as lockset
    original_configure = lifecycle.configure

    def configure(builder, selected, server_output, **keywords):
        original_configure(builder, selected, server_output, **keywords)
        previous_patch = builder.patch_checkin
        previous_relink = builder.relink
        builder.EXTRA_TRANSFORM_INPUTS += inputs
        builder.EXTRA_BUILD_RECORDS.append('cpu-dispatch-configuration.json')

        def patch(files):
            return patch_cpu_dispatch(previous_patch(files), args.cpu_signal, helper.read_text())

        def relink(*positional, **options):
            previous_relink(*positional, **options)
            if any(digest(path) != value for path, value in hashes.items()):
                raise ValueError('CPU dispatcher input changed during compilation')
            report = dict(scope=__doc__, source_sha256=hashes,
                cpu_signal=args.cpu_signal, signal_reservation='Caller-reserved native RT signal; not a new guest signal ABI.',
                shared_dispatcher=True, receiver_deferred_until_first_arm=True,
                expiry_allocates=False,
                resource_ownership_activated=False,
                scope_limit='Owned compilation/link and separately recorded ordinary execution; not whole resource or performance acceptance.')
            (server_output/'cpu-dispatch-configuration.json').write_text(json.dumps(report, indent=2)+'\n')

        builder.patch_checkin = patch
        builder.relink = relink

    lifecycle.configure = configure
    sys.argv = ['build_resource_lockset_server', '--candidate', str(candidate),
                '--output', str(output), '--work-queue', 'single']
    try:
        lockset.main()
    finally:
        lifecycle.configure = original_configure


if __name__ == '__main__':
    main()
