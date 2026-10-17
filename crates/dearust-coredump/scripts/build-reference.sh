#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Usage: build-reference.sh UPSTREAM_GIT_CHECKOUT OUTPUT_DIRECTORY
# Requires g++, and the cctools-port include tree populated in UPSTREAM_GIT_CHECKOUT.
# Only main.cpp and the register definitions are taken from the pinned commit.
# cctools supplies ABI-only Mach headers; forced includes supply the constants
# normally brought in by Darling's SDK headers. CPU_SUBTYPE_X86_ALL is ABI value 3.
set -eu
upstream=$(realpath "$1")
mkdir -p "$2"
output=$(realpath "$2")
baseline=60ba801decee7a00782f74f6be4c8ffb013f79ff
mkdir -p "$output/include/coredump"
git -C "$upstream" show "$baseline:src/hosttools/src/coredump/main.cpp" > "$output/main.cpp"
git -C "$upstream" show "$baseline:src/hosttools/include/coredump/x86_64.h" > "$output/include/coredump/x86_64.h"
printf '#define LIBEXEC_PATH "/usr/libexec/darling"\n' > "$output/include/darling-config.h"
cctools="$upstream/src/external/cctools-port/cctools/include"
"${CXX:-g++}" -O2 -std=gnu++17 -DCPU_SUBTYPE_X86_ALL=3 -I"$output/include" -I"$cctools" -idirafter "$cctools/foreign" -include mach/machine.h -include mach/vm_prot.h "$output/main.cpp" -o "$output/darling-coredump"
printf 'Reference built: %s/darling-coredump\n' "$output"
