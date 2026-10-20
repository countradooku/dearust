//! Fault-contained leaf assembly. Guest addresses never become Rust references.
use core::arch::global_asm;
use core::ffi::c_void;

unsafe extern "C" {
    fn dearust_copy_guarded(output: *mut u8, input: *const u8, length: usize, path: usize) -> i64;
    // A real reference (not a raw pointer) keeps this initialized mutable local
    // live through the entire opaque foreign call. Page equality never becomes
    // guest pointer provenance or a Rust guest reference.
    fn dearust_copy_guarded_stack(
        output: *mut u8,
        input: *const u8,
        length: usize,
        path: usize,
        anchor: &mut u8,
    ) -> i64;
    fn dearust_copy_signals_ready() -> i32;
    static dearust_copy_region_begin: u8;
    static dearust_copy_region_end: u8;
    static dearust_copy_guest_read: u8;
    static dearust_copy_guest_binary_read: u8;
    static dearust_copy_guest_write: u8;
    static dearust_copy_guest_simd_read: u8;
    static dearust_copy_guest_simd_write: u8;
    static dearust_copy_error: u8;
    static dearust_copy_fallback: u8;
}

// Darwin's subsections_via_symbols must keep recovery labels in the same atom
// as the leaf entry: no linker reordering or dead stripping may split its range.
#[cfg(target_os = "macos")]
global_asm!(
    ".alt_entry {stack_entry}",
    ".alt_entry {begin}",
    ".alt_entry {end}",
    ".alt_entry {read}",
    ".alt_entry {binary_read}",
    ".alt_entry {write}",
    ".alt_entry {simd_read}",
    ".alt_entry {simd_write}",
    ".alt_entry {error}",
    ".alt_entry {fallback}",
    stack_entry = sym dearust_copy_guarded_stack,
    begin = sym dearust_copy_region_begin,
    end = sym dearust_copy_region_end,
    read = sym dearust_copy_guest_read,
    binary_read = sym dearust_copy_guest_binary_read,
    write = sym dearust_copy_guest_write,
    simd_read = sym dearust_copy_guest_simd_read,
    simd_write = sym dearust_copy_guest_simd_write,
    error = sym dearust_copy_error,
    fallback = sym dearust_copy_fallback,
);

// Only volatile registers are used. The stack has the same forty-byte allocation
// at every PC in the recovery region, including the mask-query syscall. Signals
// redirect to a local return stub; there is no unwinding through Rust frames.
global_asm!(
    ".globl {entry}",
    ".globl {stack_entry}",
    ".globl {begin}",
    ".globl {end}",
    ".globl {read}",
    ".globl {binary_read}",
    ".globl {write}",
    ".globl {simd_read}",
    ".globl {simd_write}",
    ".globl {error}",
    ".globl {fallback}",
    ".p2align 4",
    "{entry}:",
    // Unanchored callers retain the original mask-query proof.
    "xor r8d, r8d",
    "{stack_entry}:",
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
    // Positive fixed-binary proof only: this genuine live mutable Rust local
    // must remain readable/writable through the call. x86 Linux R/W and pkey
    // permissions are uniform on a 4KiB page. Revoking this page also invalidates
    // the actual local anchor, already outside the local-storage contract.
    "test r8, r8",
    "jz 9f",
    "mov r9, rdi",
    "test rcx, rcx",
    "jz 10f",
    "cmp rcx, 2",
    "jne 9f",
    "mov r9, rsi",
    "10:",
    "cmp rdx, 16",
    "je 11f",
    "cmp rdx, 144",
    "jne 9f",
    "11:",
    // This is a local store, deliberately NOT a recoverable guest-access PC.
    "mov byte ptr [r8], 0",
    "mov rax, rdx",
    "dec rax",
    "add rax, r9",
    "jc 9f",
    "mov r10, r9",
    "xor r10, r8",
    "xor rax, r8",
    "or rax, r10",
    "shr rax, 12",
    "jnz 9f",
    // Exclude the anchor byte itself; other local-storage non-overlap remains
    // part of the existing unsafe ABI contract. No guest reference is created.
    "mov rax, r8",
    "sub rax, r9",
    "cmp rax, rdx",
    "jb 9f",
    // The proven lengths are exactly one or nine16-byte chunks. SSE2 is part
    // of the x86_64 ABI; XMM0 is volatile and MOVDQU performs no FP arithmetic.
    // AC was checked before the region. Local accesses remain unguarded, while
    // each guest direction has a distinct exact recovery instruction.
    "mov rcx, rdx",
    "cmp qword ptr [rsp + 32], 0",
    "je 13f",
    "jmp 14f",
    "9:",
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
    "cmp qword ptr [rsp + 32], 2",
    "je 7f",
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
    "test rcx, rcx",
    "jz 5f",
    // The fresh mask query established fault delivery for this attempt. Exact
    // SIMD chunks use the same guest-store PC as the anchored path; lengths
    // divisible only by four retain the bounded word loop below. Neither loop
    // reads beyond initialized local storage or adds a recovery instruction.
    "test rcx, 15",
    "jz 13f",
    "test rcx, 3",
    "jnz {fallback}",
    "4:",
    "mov edx, dword ptr [rsi]",
    "{write}:",
    "mov dword ptr [rdi], edx",
    "add rsi, 4",
    "add rdi, 4",
    "sub rcx, 4",
    "jnz 4b",
    "jmp 5f",
    "7:",
    // Binary copyin has a distinct guest-load PC. The local destination store
    // must never be mistaken for a recoverable guest fault.
    "test rcx, rcx",
    "jz 5f",
    "test rcx, 15",
    "jz 14f",
    "test rcx, 3",
    "jnz {fallback}",
    "8:",
    "{binary_read}:",
    "mov edx, dword ptr [rsi]",
    "mov dword ptr [rdi], edx",
    "add rsi, 4",
    "add rdi, 4",
    "sub rcx, 4",
    "jnz 8b",
    "jmp 5f",
    "13:",
    "movdqu xmm0, xmmword ptr [rsi]",
    "{simd_write}:",
    "movdqu xmmword ptr [rdi], xmm0",
    "add rsi, 16",
    "add rdi, 16",
    "sub rcx, 16",
    "jnz 13b",
    "jmp 5f",
    "14:",
    "{simd_read}:",
    "movdqu xmm0, xmmword ptr [rsi]",
    "movdqu xmmword ptr [rdi], xmm0",
    "add rsi, 16",
    "add rdi, 16",
    "sub rcx, 16",
    "jnz 14b",
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
    stack_entry = sym dearust_copy_guarded_stack,
    begin = sym dearust_copy_region_begin,
    end = sym dearust_copy_region_end,
    read = sym dearust_copy_guest_read,
    binary_read = sym dearust_copy_guest_binary_read,
    write = sym dearust_copy_guest_write,
    simd_read = sym dearust_copy_guest_simd_read,
    simd_write = sym dearust_copy_guest_simd_write,
    error = sym dearust_copy_error,
    fallback = sym dearust_copy_fallback,
);

/// Return a recovery PC for the existing Linux signal bridge, or zero to retain
/// ordinary dispatch. Positive-code SIGSEGV/SIGBUS are consumed only at the five
/// exact guest-access instructions. Every other signal within the region aborts
/// the optimization before ordinary dispatch, closing asynchronous mask/fork
/// changes between the query and the copy. Stubs preserve the interrupted stack.
#[unsafe(no_mangle)]
pub extern "C" fn dearust_copy_signal_fixup(pc: usize, signal: i32, code: i32) -> usize {
    let synchronous_fault = (signal == 11 || signal == 7) && code > 0;
    let read = core::ptr::addr_of!(dearust_copy_guest_read) as usize;
    let write = core::ptr::addr_of!(dearust_copy_guest_write) as usize;
    let binary_read = core::ptr::addr_of!(dearust_copy_guest_binary_read) as usize;
    let simd_read = core::ptr::addr_of!(dearust_copy_guest_simd_read) as usize;
    let simd_write = core::ptr::addr_of!(dearust_copy_guest_simd_write) as usize;
    if synchronous_fault {
        if pc == read || pc == write || pc == binary_read || pc == simd_read || pc == simd_write {
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
    match unsafe {
        if matches!(length, 16 | 144) {
            let mut anchor = 0_u8;
            dearust_copy_guarded_stack(output.cast(), input, length, 0, &mut anchor)
        } else {
            dearust_copy_guarded(output.cast(), input, length, 0)
        }
    } {
        -4096 => None,
        0 => Some(Ok(())),
        error => Some(Err(error)),
    }
}

/// Local output is initialized writable storage for `length` bytes; only the
/// source is an untrusted guest address. No Rust reference is formed from it.
pub(super) unsafe fn copy_input(
    output: *mut u8,
    input: *const c_void,
    length: usize,
) -> Option<Result<(), i64>> {
    // SAFETY: readiness has the same signal bridge contract as copy_path.
    if unsafe { dearust_copy_signals_ready() } == 0 {
        return None;
    }
    // SAFETY: caller guarantees live initialized local output, while binary
    // guest loads have their own exact recovery PC. No NUL scanning occurs.
    match unsafe {
        if matches!(length, 16 | 144) {
            let mut anchor = 0_u8;
            dearust_copy_guarded_stack(output, input.cast(), length, 2, &mut anchor)
        } else {
            dearust_copy_guarded(output, input.cast(), length, 2)
        }
    } {
        -4096 => None,
        0 => Some(Ok(())),
        error => Some(Err(error)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn binary_input_recovers_only_at_its_guest_load_pc() {
        let input = core::ptr::addr_of!(dearust_copy_guest_binary_read) as usize;
        let error = core::ptr::addr_of!(dearust_copy_error) as usize;
        let fallback = core::ptr::addr_of!(dearust_copy_fallback) as usize;
        assert_eq!(dearust_copy_signal_fixup(input, 11, 1), error);
        assert_eq!(dearust_copy_signal_fixup(input, 7, 2), error);
        // The following local store is not a valid recoverable guest fault.
        assert_eq!(dearust_copy_signal_fixup(input + 2, 11, 1), 0);
        assert_eq!(dearust_copy_signal_fixup(input, 10, 0), fallback);
        assert_eq!(dearust_copy_signal_fixup(input, 11, 0), fallback);
        let simd_input = core::ptr::addr_of!(dearust_copy_guest_simd_read) as usize;
        let simd_output = core::ptr::addr_of!(dearust_copy_guest_simd_write) as usize;
        for pc in [simd_input, simd_output] {
            assert_eq!(dearust_copy_signal_fixup(pc, 11, 1), error);
            assert_eq!(dearust_copy_signal_fixup(pc, 7, 2), error);
            assert_eq!(dearust_copy_signal_fixup(pc, 10, 0), fallback);
        }
        assert_eq!(dearust_copy_signal_fixup(simd_input + 4, 11, 1), 0);
        assert_eq!(dearust_copy_signal_fixup(simd_output - 4, 11, 1), 0);
    }
}
