//! Process queries in the caller's Linux PID/mount namespace. No allocator.
#![no_std]
#![deny(unsafe_code)]
#[cfg(not(all(
    target_arch = "x86_64",
    target_pointer_width = "64",
    any(target_os = "linux", target_os = "macos")
)))]
compile_error!("darling-process implements only the x86_64 Linux/Darling ABI");
#[allow(unsafe_code)]
mod abi;
mod parse;
pub use abi::sys_proc_info;
