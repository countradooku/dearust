//! Bounded, allocation-free pathname operations preserving the Darling x86_64 ABI.
#![no_std]
#![deny(unsafe_code)]
#[cfg(feature = "host-filesystem")]
extern crate std;
#[cfg(not(all(target_arch = "x86_64", target_pointer_width = "64")))]
compile_error!("dearust-path supports only the x86_64 Darling ABI");
#[cfg(all(feature = "host-filesystem", not(target_os = "linux")))]
compile_error!("host-filesystem requires the Linux server target; Mach-O clients use runtime");
#[allow(unsafe_code)]
#[cfg(feature = "runtime")]
mod abi;
mod filesystem;
#[cfg(feature = "host-filesystem")]
#[allow(unsafe_code)]
mod host_filesystem;
#[cfg(any(feature = "runtime", test))]
mod path;
#[cfg(feature = "runtime")]
pub use abi::{
    do_linux_fchmodat, sys_access, sys_chmod, sys_faccessat, sys_fchmodat, sys_fstat, sys_fstat64,
    sys_fstatat, sys_fstatat64, sys_link, sys_linkat, sys_lstat, sys_lstat64, sys_mkdir,
    sys_mkdirat, sys_readlink, sys_readlinkat, sys_rename, sys_renameat, sys_rmdir, sys_stat,
    sys_stat64, sys_symlink, sys_symlinkat, sys_truncate, sys_unlink, sys_unlinkat,
};
#[cfg(feature = "runtime")]
pub use abi::{sys_fstatfs, sys_fstatfs64, sys_statfs, sys_statfs64};
