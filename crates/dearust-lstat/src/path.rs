//! Safe bounded path copying, independently testable without guest TLS.
#![forbid(unsafe_code)]
pub const CAPACITY: usize = 4096;
pub fn copy(read: impl Fn(usize) -> u8) -> Result<[u8; CAPACITY], i64> {
    let mut output = [0; CAPACITY];
    for (index, byte) in output.iter_mut().enumerate() {
        *byte = read(index);
        if *byte == 0 {
            return Ok(output);
        }
    }
    Err(-63) // Darwin ENAMETOOLONG
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn stops_at_nul_without_reading_after_terminator() {
        assert_eq!(&copy(|i| b"abc\0"[i]).unwrap()[..4], b"abc\0");
        assert_eq!(
            copy(|i| {
                assert_eq!(i, 0);
                0
            })
            .unwrap(),
            [0; CAPACITY]
        );
    }
    #[test]
    fn accepts_last_byte_terminator_and_rejects_unterminated_capacity() {
        assert!(copy(|i| if i == CAPACITY - 1 { 0 } else { b'a' }).is_ok());
        assert_eq!(
            copy(|i| {
                assert!(i < CAPACITY);
                b'a'
            }),
            Err(-63)
        );
    }
    #[test]
    fn preserves_non_utf8_path_bytes() {
        assert_eq!(
            &copy(|i| [0xff, 0xfe, b'/', 0][i]).unwrap()[..4],
            &[0xff, 0xfe, b'/', 0]
        );
    }
}
