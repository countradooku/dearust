//! Allocation-free descriptor-based ftruncate preserving the original syscall ABI.
#![no_std]
#![deny(unsafe_code)]
#[cfg(not(all(target_arch = "x86_64", target_pointer_width = "64")))]
compile_error!("dearust-ftruncate supports only the x86_64 Darling ABI");
#[allow(unsafe_code)]
mod abi;
pub use abi::sys_ftruncate;
