//! Descriptor-relative unlink with bounded, allocation-free path handling.
#![no_std]
#![deny(unsafe_code)]
#[cfg(not(all(target_arch = "x86_64", target_pointer_width = "64")))]
compile_error!("dearust-unlinkat supports only the x86_64 Darling ABI");
#[allow(unsafe_code)]
mod abi;
mod path;
pub use abi::sys_unlinkat;
