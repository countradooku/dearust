#!/usr/bin/env python3
"""Consume each suspension's wake permission before entering its saved context.

Reserved queue retries are scheduling hints, not permission to resume a later
wait. Apply after work/impersonation handoff. The dedicated native cleanup lane
retains its independent wake flag and does not use this reserved-queue gate.
"""


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('resume consumption anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


ADMISSION = '''    // A queued retry can outlive the suspension whose wake queued it.
    // Only a current wake or a real pending call authorizes saved-context entry.
    if (_suspended && !_resumePending && !_pendingCall && !_terminating &&
            !resourceCleanupLane(this)) {
        _rwlock.unlock();
        return;
    }
'''
HANDOFF_GATE = '''    if (_suspended && !_resumePending && !_pendingCall &&
            !resourceCleanupLane(this)) return;
'''


def patch_resume_consumption(files):
    files = dict(files)
    files['thread.hpp'] = once(files['thread.hpp'], '\t\tbool _suspended = false;',
        '\t\tbool _suspended = false;\n        bool _resumePending = false;')
    source = files['thread.cpp']
    source = once(source, '\tif (_terminating) {\n\t\tgoto doneWorking;',
        ADMISSION + '\n\tif (_terminating) {\n\t\tgoto doneWorking;')
    begin = source.index('void DarlingServer::Thread::doWork() {')
    end = source.index('void DarlingServer::Thread::suspend(', begin)
    work = source[begin:end]
    if work.count('\t\t\t_suspended = false;') != 2:
        raise ValueError('resume selection anchors changed')
    work = work.replace('\t\t\t_suspended = false;',
        '\t\t\t_resumePending = false;\n\t\t\t_suspended = false;')
    source = source[:begin] + work + source[end:]
    if source.count('\t_suspended = true;') != 2:
        raise ValueError('suspension/setup publication anchors changed')
    source = source.replace('\t_suspended = true;',
        '\t_resumePending = false;\n\t_suspended = true;')
    source = once(source, '    std::shared_lock lock(_rwlock);\n    if (expectedGeneration',
        '    std::unique_lock lock(_rwlock);\n    if (expectedGeneration')
    source = once(source, '    // holding this shared lock through enqueue introduces no reverse lock edge.',
        '    // holding this lock through enqueue introduces no reverse lock edge.')
    source = once(source, '    if (!_suspended) return;\n    auto owner =',
        '    if (!_suspended) return;\n    _resumePending = true;\n    auto owner =')
    source = once(source, '    if (_terminating || (_dead && !_activeCall)) return;\n    auto owner =',
        '    if (_terminating || (_dead && !_activeCall)) return;\n' + HANDOFF_GATE + '    auto owner =')
    files['thread.cpp'] = source
    return files
