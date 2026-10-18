//! Decisions over a bounded kernel copy; no guest addresses or unsafe code.
#![forbid(unsafe_code)]
pub const CAPACITY: usize = 4096;
/// A readable prefix containing NUL is sufficient even when the next page faults.
pub fn finish_copy(output: &[u8; CAPACITY], copied: usize) -> Result<(), i64> {
    let copied = copied.min(CAPACITY);
    let mut index = 0;
    while index < copied {
        if output[index] == 0 {
            return Ok(());
        }
        index += 1;
    }
    if copied == CAPACITY {
        Err(-63)
    } else {
        Err(-14)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn nul_in_readable_prefix_does_not_require_the_next_page() {
        let mut bytes = [b'x'; CAPACITY];
        bytes[7] = 0;
        assert_eq!(finish_copy(&bytes, 8), Ok(()));
        assert_eq!(finish_copy(&bytes, 7), Err(-14));
        assert_eq!(finish_copy(&bytes, 0), Err(-14));
        bytes[7] = b'x';
        bytes[63] = 0;
        assert_eq!(finish_copy(&bytes, 64), Ok(()));
        assert_eq!(finish_copy(&bytes, 63), Err(-14));
        bytes[63] = b'x';
        bytes[64] = 0;
        assert_eq!(finish_copy(&bytes, 64), Err(-14));
        assert_eq!(finish_copy(&bytes, 65), Ok(()));
    }
    #[test]
    fn accepts_last_byte_terminator_and_rejects_unterminated_capacity() {
        let mut bytes = [b'x'; CAPACITY];
        assert_eq!(finish_copy(&bytes, CAPACITY), Err(-63));
        bytes[CAPACITY - 1] = 0;
        assert_eq!(finish_copy(&bytes, CAPACITY), Ok(()));
        assert_eq!(finish_copy(&bytes, CAPACITY - 1), Err(-14));
    }
    #[test]
    fn preserves_non_utf8_path_bytes() {
        let mut bytes = [0xff; CAPACITY];
        bytes[..4].copy_from_slice(&[0xff, 0xfe, b'/', 0]);
        assert_eq!(finish_copy(&bytes, 4), Ok(()));
        assert_eq!(&bytes[..4], &[0xff, 0xfe, b'/', 0]);
    }
}
