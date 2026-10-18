//! Process queries in the caller's Linux PID/mount namespace. No allocator.
#![no_std]
#![deny(unsafe_code)]
#[cfg(not(all(
    target_arch = "x86_64",
    target_pointer_width = "64",
    any(target_os = "linux", target_os = "macos")
)))]
compile_error!("dearust-process implements only the x86_64 Linux/Darling ABI");
#[cfg(feature = "queries")]
#[allow(unsafe_code)]
mod abi;
#[cfg(feature = "queries")]
mod info;
#[cfg(feature = "queries")]
mod parse;
#[cfg(feature = "queries")]
pub use abi::sys_proc_info;
#[cfg(feature = "identifiers")]
#[allow(unsafe_code)]
mod identifiers;
#[cfg(feature = "identifiers")]
pub use identifiers::{sys_getpgid, sys_getpgrp, sys_getpid, sys_getppid, sys_getsid};
