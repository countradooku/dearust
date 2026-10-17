//! Darling's futex adapter: raw Linux syscall ABI and Darwin errno results.
//!
//! No allocator, panic handler, runtime initialization, or Rust pointer access.
//! See SAFETY.txt for the exported C ABI and kernel pointer contracts.
#![no_std]
#![deny(unsafe_code)]

#[cfg(not(all(
    target_arch = "x86_64",
    target_pointer_width = "64",
    any(target_os = "linux", target_os = "macos")
)))]
compile_error!("dearust-futex implements only the LP64 x86_64 Linux/Darling syscall ABI");

#[allow(unsafe_code)]
mod abi;
mod result;

pub use abi::{__linux_futex, __linux_futex_reterr};
