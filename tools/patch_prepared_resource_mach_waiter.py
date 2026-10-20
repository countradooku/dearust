#!/usr/bin/env python3
"""Wake both states of a Mach port-set waiter before joining its death barrier."""


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Mach waiter anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


def patch_mach_waiter(text):
    text=once(text, 'while ((wq = kqchan->waitq) != NULL)',
        'while ((wq = __atomic_load_n(&kqchan->waitq, __ATOMIC_ACQUIRE)) != NULL)')
    text=once(text, '\tkqchan->waitq = NULL;',
        '\t__atomic_store_n(&kqchan->waitq, NULL, __ATOMIC_RELEASE);\n'
        '    // A callback can be running rather than waiting when clear_wait\n'
        '    // executes. Reserve a read-semaphore wake for its *next* wait too,\n'
        '    // so it observes the NULL waitq and reaches the death barrier.\n'
        '    dtape_semaphore_up(kqchan->waiter_read_semaphore);')
    return text
