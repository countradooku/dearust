#ifndef DARLING_RUST_ERRNO_H
#define DARLING_RUST_ERRNO_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Original external const size_t object, with value 512. */
extern const size_t length_of_translation_array;

/* Unknown values, including INT_MIN, pass through unchanged. */
int errno_linux_to_bsd(int err);

#ifdef __cplusplus
}
#endif

#endif
