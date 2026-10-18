//! Bounded, allocation-free pathname operations preserving the Darling x86_64 ABI.
#![no_std]
#![deny(unsafe_code)]
#[cfg(not(all(target_arch = "x86_64", target_pointer_width = "64")))]
compile_error!("dearust-path supports only the x86_64 Darling ABI");
#[allow(unsafe_code)]
mod abi;
mod path;
pub use abi::{
    do_linux_fchmodat, sys_faccessat, sys_fchmodat, sys_linkat, sys_mkdirat, sys_readlinkat,
    sys_renameat, sys_rmdir, sys_symlinkat, sys_truncate, sys_unlinkat,
};
