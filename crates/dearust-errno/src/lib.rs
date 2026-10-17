//! Linux to Darling/Darwin errno conversion, independent of host errno headers.
//!
//! This crate has no allocator, OS calls, panic handler or runtime initialization.
//! See SAFETY.txt for the pinned source and C ABI contract.
#![no_std]
#![deny(unsafe_code)]

#[allow(unsafe_code)]
mod abi;
mod conversion;

pub use conversion::{TRANSLATION_ARRAY_LENGTH, linux_to_bsd};
