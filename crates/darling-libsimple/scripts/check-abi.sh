#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Usage: check-abi.sh UPSTREAM_GIT_CHECKOUT RUST_STATICLIB OUTPUT_DIRECTORY
# Compiles committed upstream source, never its potentially modified worktree.
set -eu
upstream=$(realpath "$1")
archive=$(realpath "$2")
mkdir -p "$3"
output=$(realpath "$3")
crate=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
baseline=60ba801decee7a00782f74f6be4c8ffb013f79ff
mkdir -p "$output/include/libsimple"
git -C "$upstream" show "$baseline:src/libsimple/src/lock.c" > "$output/lock.c"
for header in base.h lock.h; do
    git -C "$upstream" show "$baseline:src/libsimple/include/libsimple/$header" > "$output/include/libsimple/$header"
    cmp "$output/include/libsimple/$header" "$crate/include/libsimple/$header"
done
"${CC:-cc}" -O2 -std=gnu11 -Wno-attributes -DLIBSIMPLE_LINUX=1 -I"$output/include" -pthread "$crate/tests/abi.c" "$output/lock.c" -o "$output/upstream-abi"
"${CC:-cc}" -O2 -std=gnu11 -Wno-attributes -I"$output/include" -pthread "$crate/tests/abi.c" "$archive" -ldl -lm -o "$output/rust-abi"
timeout 25 "$output/upstream-abi" > "$output/upstream.txt"
timeout 25 "$output/rust-abi" > "$output/rust.txt"
cmp "$output/upstream.txt" "$output/rust.txt"
cat "$output/rust.txt"
# Every function declared in the installed public header must be exported by both.
for symbol in libsimple_lock_lock libsimple_lock_unlock libsimple_lock_try_lock libsimple_once libsimple_rwlock_lock_read libsimple_rwlock_try_lock_read libsimple_rwlock_unlock_read libsimple_rwlock_lock_write libsimple_rwlock_try_lock_write libsimple_rwlock_unlock_write libsimple_condvar_wait libsimple_condvar_notify_one libsimple_condvar_notify_all; do
    nm --defined-only "$output/rust-abi" | awk '{print $3}' | grep -x "$symbol" > /dev/null
    nm --defined-only "$output/upstream-abi" | awk '{print $3}' | grep -x "$symbol" > /dev/null
done
