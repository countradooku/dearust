#!/usr/bin/env python3
"""Apply allocation-safe FD ownership to already-owned resource Process copies.

This addresses descriptor acquisition only. Duct-tape construction, Registry
factory side effects, eternal-ID initialization and runtime OOM gates remain open.
"""


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('resource process FD anchor not unique: ' + old[:100])
    return text.replace(old, new)


def patch_process_header(text):
    return once(text, 'Process(ID id, NSID nsid, Architecture architecture, int pipe = -1);',
                'Process(ID id, NSID nsid, Architecture architecture, FD pipe = FD());')


def patch_process_source(text):
    text = once(text, 'Process::Process(ID id, NSID nsid, Architecture architecture, int pipe):',
                'Process::Process(ID id, NSID nsid, Architecture architecture, FD pipe):')
    text = once(text, 'if (pipe >= 0) _resourceLifetimePipe = std::make_shared<FD>(pipe);',
                'if (pipe.fd() >= 0) _resourceLifetimePipe = std::make_shared<FD>(std::move(pipe));')
    text = once(text, 'int pidfd = syscall(SYS_pidfd_open, _pid, 0);\n\tif (pidfd < 0)',
                'FD pidfd(syscall(SYS_pidfd_open, _pid, 0));\n\tif (pidfd.fd() < 0)')
    text = once(text, '_pidfd = std::make_shared<FD>(pidfd);',
                '_pidfd = std::make_shared<FD>(std::move(pidfd));')
    return once(text,
                '_resourceProcDirectory = std::make_shared<FD>(open(("/proc/" + std::to_string(_pid)).c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC));\n'
                '\tif (_resourceProcDirectory->fd() < 0) throw std::system_error(errno, std::generic_category());',
                'FD procDirectory(open(("/proc/" + std::to_string(_pid)).c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC));\n'
                '\tif (procDirectory.fd() < 0) throw std::system_error(errno, std::generic_category());\n'
                '\t_resourceProcDirectory = std::make_shared<FD>(std::move(procDirectory));')


def patch_call_source(text):
    text = once(text, '\t\t\tint lifetimePipe = -1;', '\t\t\tFD lifetimePipe;')
    text = once(text, 'lifetimePipe = requestMessage.extractDescriptorAtIndex(checkinCall->body.lifetime_listener_pipe);',
                'lifetimePipe = FD(requestMessage.extractDescriptorAtIndex(checkinCall->body.lifetime_listener_pipe));')
    return once(text,
                'tmp = std::make_shared<Process>(requestMessage.pid(), header->pid, static_cast<Process::Architecture>(header->architecture), lifetimePipe);',
                'tmp = std::make_shared<Process>(requestMessage.pid(), header->pid, static_cast<Process::Architecture>(header->architecture), std::move(lifetimePipe));')


def patch_state(text):
    return once(text,
                'auto candidateFd = std::make_shared<FD>(syscall(SYS_pidfd_open, candidate, 0));\n'
                '            if (candidateFd->fd() < 0) { if (errno == ESRCH) continue; return resourceErrno(-errno); }',
                'FD candidateOwner(syscall(SYS_pidfd_open, candidate, 0));\n'
                '            if (candidateOwner.fd() < 0) { if (errno == ESRCH) continue; return resourceErrno(-errno); }\n'
                '            auto candidateFd = std::make_shared<FD>(std::move(candidateOwner));')
