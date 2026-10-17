//! Guarded descriptor bookkeeping. See SAFETY.txt for the C runtime contract.
#![no_std]
#![deny(unsafe_code)]
#[cfg(not(all(target_arch = "x86_64", target_pointer_width = "64")))]
compile_error!("dearust-guarded currently supports the x86_64 runtime ABI only");
#[allow(unsafe_code)]
mod abi;
#[cfg(not(feature = "dyld"))]
mod table;
pub use abi::*;
