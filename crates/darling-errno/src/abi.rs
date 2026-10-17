//! The only unsafe constructs are exported symbol attributes (Rust 2024).

use core::ffi::c_int;

/// Retains the externally linked `const size_t` object from the original C file.
// SAFETY: This runtime slice owns this original symbol. Do not link errno.c too.
#[allow(non_upper_case_globals)]
#[unsafe(no_mangle)]
pub static length_of_translation_array: usize = crate::TRANSLATION_ARRAY_LENGTH;

/// Preserves the original C scalar ABI; all `int` values are valid inputs.
// SAFETY: This runtime slice owns this original symbol. Do not link errno.c too.
#[unsafe(no_mangle)]
pub extern "C" fn errno_linux_to_bsd(err: c_int) -> c_int {
    crate::linux_to_bsd(err)
}
