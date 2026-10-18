//! Fault-contained leaf assembly. Guest addresses never become Rust references.
use core::arch::global_asm;
use core::ffi::c_void;

unsafe extern "C" {
    fn dearust_copy_guarded(output: *mut u8, input: *const u8, length: usize, path: usize) -> i64;
    fn dearust_copy_signals_ready() -> i32;
    static dearust_copy_region_begin: u8;
    static dearust_copy_region_end: u8;
    static dearust_copy_guest_read: u8;
    static dearust_copy_guest_write: u8;
    static dearust_copy_error: u8;
    static dearust_copy_fallback: u8;
}

// Darwin's subsections_via_symbols must keep recovery labels in the same atom
// as the leaf entry: no linker reordering or dead stripping may split its range.
#[cfg(target_os = "macos")]
global_asm!(
    ".alt_entry {begin}",
    ".alt_entry {end}",
    ".alt_entry {read}",
    ".alt_entry {write}",
    ".alt_entry {error}",
    ".alt_entry {fallback}",
    begin = sym dearust_copy_region_begin,
    end = sym dearust_copy_region_end,
    read = sym dearust_copy_guest_read,
    write = sym dearust_copy_guest_write,
    error = sym dearust_copy_error,
    fallback = sym dearust_copy_fallback,
);

// Only volatile registers are used. The stack has the same forty-byte allocation
// at every PC in the recovery region, including the mask-query syscall. Signals
// redirect to a local return stub; there is no unwinding through Rust frames.
global_asm!(
    ".globl {entry}",
    ".globl {begin}",
    ".globl {end}",
    ".globl {read}",
    ".globl {write}",
    ".globl {error}",
    ".globl {fallback}",
    ".p2align 4",
    "{entry}:",
    // A valid unaligned guest address must not become EFAULT merely because
    // the caller enabled x86 alignment checking. Inspect flags before entering
    // the recovery region: its invariant stack frame must not include pushes.
    "pushfq",
    "pop rax",
    "test eax, 0x40000",
    "jz 6f",
    "mov rax, -4096",
    "ret",
    "6:",
    "sub rsp, 40",
    "{begin}:",
    "mov [rsp + 8], rdi",
    "mov [rsp + 16], rsi",
    "mov [rsp + 24], rdx",
    "mov [rsp + 32], rcx",
    // Raw Linux rt_sigprocmask(SIG_BLOCK, NULL, &mask, 8): query only.
    "xor edi, edi",
    "xor esi, esi",
    "mov rdx, rsp",
    "mov r10d, 8",
    "mov eax, 14",
    "syscall",
    "test rax, rax",
    "js {fallback}",
    // Linux SIGBUS7 and SIGSEGV11 must both be deliverable.
    "test qword ptr [rsp], 0x440",
    "jnz {fallback}",
    "mov rdi, [rsp + 8]",
    "mov rsi, [rsp + 16]",
    "mov rcx, [rsp + 24]",
    "cmp qword ptr [rsp + 32], 0",
    "je 3f",
    "2:",
    "{read}:",
    "mov dl, byte ptr [rsi]",
    "mov byte ptr [rdi], dl",
    "inc rsi",
    "inc rdi",
    "test dl, dl",
    "jz 5f",
    "dec rcx",
    "jnz 2b",
    "mov rax, -63",
    "jmp {end}",
    "3:",
    // Both supported metadata layouts are multiples of four bytes. A bounded
    // word loop avoids reading past the initialized source; other lengths use
    // the kernel path rather than adding another recoverable instruction site.
    "test rcx, 3",
    "jnz {fallback}",
    "test rcx, rcx",
    "jz 5f",
    "4:",
    "mov edx, dword ptr [rsi]",
    "{write}:",
    "mov dword ptr [rdi], edx",
    "add rsi, 4",
    "add rdi, 4",
    "sub rcx, 4",
    "jnz 4b",
    "5:",
    "xor eax, eax",
    "jmp {end}",
    "{fallback}:",
    "mov rax, -4096",
    "jmp {end}",
    "{error}:",
    "mov rax, -14",
    "{end}:",
    "add rsp, 40",
    "ret",
    entry = sym dearust_copy_guarded,
    begin = sym dearust_copy_region_begin,
    end = sym dearust_copy_region_end,
    read = sym dearust_copy_guest_read,
    write = sym dearust_copy_guest_write,
    error = sym dearust_copy_error,
    fallback = sym dearust_copy_fallback,
);

/// Return a recovery PC for the existing Linux signal bridge, or zero to retain
/// ordinary dispatch. Positive-code SIGSEGV/SIGBUS are consumed only at the two
/// exact guest-access instructions. Every other signal within the region aborts
/// the optimization before ordinary dispatch, closing asynchronous mask/fork
/// changes between the query and the copy. Stubs preserve the interrupted stack.
#[unsafe(no_mangle)]
pub extern "C" fn dearust_copy_signal_fixup(pc: usize, signal: i32, code: i32) -> usize {
    let synchronous_fault = (signal == 11 || signal == 7) && code > 0;
    let read = core::ptr::addr_of!(dearust_copy_guest_read) as usize;
    let write = core::ptr::addr_of!(dearust_copy_guest_write) as usize;
    if synchronous_fault {
        if pc == read || pc == write {
            core::ptr::addr_of!(dearust_copy_error) as usize
        } else {
            0
        }
    } else if pc >= core::ptr::addr_of!(dearust_copy_region_begin) as usize
        && pc < core::ptr::addr_of!(dearust_copy_region_end) as usize
    {
        core::ptr::addr_of!(dearust_copy_fallback) as usize
    } else {
        0
    }
}

pub(super) fn copy_path(
    output: &mut super::memory::PathBuffer,
    guest: *const u8,
) -> Option<Result<(), i64>> {
    // SAFETY: readiness is published only after every supported Linux signal
    // entry has installed the recovery hook. It never calls into guest code.
    if unsafe { dearust_copy_signals_ready() } == 0 {
        return None;
    }
    // SAFETY: output is live local storage, source remains a raw address, and
    // the assembly validates fault delivery and bounds every guest load.
    match unsafe { dearust_copy_guarded(output.as_mut_ptr().cast(), guest, output.len(), 1) } {
        -4096 => None,
        0 => Some(Ok(())),
        error => Some(Err(error)),
    }
}

/// Local input is initialized for `length` bytes; guest output is untrusted.
pub(super) unsafe fn copy_output(
    input: *const u8,
    output: *mut c_void,
    length: usize,
) -> Option<Result<(), i64>> {
    // SAFETY: same bridge-readiness contract as copy_path above.
    if unsafe { dearust_copy_signals_ready() } == 0 {
        return None;
    }
    // SAFETY: caller guarantees the local input; guest stores are guarded at
    // their exact instruction and partial output retains EFAULT semantics.
    match unsafe { dearust_copy_guarded(output.cast(), input, length, 0) } {
        -4096 => None,
        0 => Some(Ok(())),
        error => Some(Err(error)),
    }
}
