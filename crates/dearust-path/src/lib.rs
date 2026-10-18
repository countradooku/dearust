//! Bounded, allocation-free pathname operations preserving the Darling x86_64 ABI.
#![no_std]
#![deny(unsafe_code)]
#[cfg(not(all(target_arch = "x86_64", target_pointer_width = "64")))]
compile_error!("dearust-path supports only the x86_64 Darling ABI");
#[allow(unsafe_code)]
mod abi;
mod path;
pub use abi::{
    do_linux_fchmodat, sys_access, sys_chmod, sys_faccessat, sys_fchmodat, sys_fstat, sys_fstat64,
    sys_fstatat, sys_fstatat64, sys_link, sys_linkat, sys_lstat, sys_lstat64, sys_mkdir,
    sys_mkdirat, sys_readlink, sys_readlinkat, sys_rename, sys_renameat, sys_rmdir, sys_stat,
    sys_stat64, sys_symlink, sys_symlinkat, sys_truncate, sys_unlink, sys_unlinkat,
};
