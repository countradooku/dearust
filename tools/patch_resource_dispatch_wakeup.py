#!/usr/bin/env python3
"""Keep the producer eventfd armed across an empty-queue snapshot race."""


def patch_dispatch_wakeup(text):
    old = 'settings.events = (_canWrite || !_outbox.empty()) ? (EPOLLIN | EPOLLONESHOT) : 0;'
    if text.count(old) != 1:
        raise ValueError('dispatch eventfd rearm anchor not unique')
    return text.replace(old, '''// A producer can enqueue and write the counter after the queue-empty
        // snapshot but before this MOD. Always rearm: epoll observes an already
        // nonzero counter, and an idle zero counter does not generate work.
        settings.events = EPOLLIN | EPOLLONESHOT;''', 1)


def patch_dispatch_wakeup_files(files):
    files = dict(files)
    files['server.cpp'] = patch_dispatch_wakeup(files['server.cpp'])
    return files
