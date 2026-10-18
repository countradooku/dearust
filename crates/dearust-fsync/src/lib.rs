//! Original Darling fsync ABI over the Linux raw syscall boundary.
#![no_std]
#![deny(unsafe_code)]
#[cfg(not(all(target_arch = "x86_64", target_pointer_width = "64")))]
compile_error!("dearust-fsync supports only the x86_64 Darling ABI");
#[allow(unsafe_code)]
mod abi;
pub use abi::{sys_fsync, sys_fsync_nocancel};
