//! Bounded, allocation-free descriptor-relative renameat implementation.
#![no_std]
#![deny(unsafe_code)]
#[cfg(not(all(target_arch = "x86_64", target_pointer_width = "64")))]
compile_error!("dearust-renameat supports only the x86_64 Darling ABI");
#[allow(unsafe_code)]
mod abi;
mod path;
pub use abi::sys_renameat;
