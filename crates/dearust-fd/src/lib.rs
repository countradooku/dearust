//! Allocation-free descriptor operations preserving the Darling x86_64 ABI.
#![no_std]
#![deny(unsafe_code)]
#[cfg(not(all(target_arch = "x86_64", target_pointer_width = "64")))]
compile_error!("dearust-fd supports only the x86_64 Darling ABI");
#[allow(unsafe_code)]
mod abi;
pub use abi::{
    sys_fchmod, sys_fdatasync, sys_flock, sys_fsync, sys_fsync_nocancel, sys_ftruncate,
    sys_getdtablesize, sys_lseek, sys_umask,
};
