//! Safe bounded path copying, independently testable without guest TLS.
#![forbid(unsafe_code)]
pub const CAPACITY: usize = 4096;
/// Copy directly into initialized expansion storage, leaving bytes after NUL alone.
pub fn copy_into(output: &mut [u8; CAPACITY], read: impl Fn(usize) -> u8) -> Result<(), i64> {
    for (index, byte) in output.iter_mut().enumerate() {
        *byte = read(index);
        if *byte == 0 {
            return Ok(());
        }
    }
    Err(-63) // Darwin ENAMETOOLONG
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn stops_at_nul_without_reading_or_writing_after_terminator() {
        let mut output = [0xa5; CAPACITY];
        copy_into(&mut output, |i| b"abc\0"[i]).unwrap();
        assert_eq!(&output[..4], b"abc\0");
        assert!(output[4..].iter().all(|byte| *byte == 0xa5));
        copy_into(&mut output, |i| {
            assert_eq!(i, 0);
            0
        })
        .unwrap();
        assert_eq!(output[0], 0);
        assert_eq!(output[1], b'b');
    }
    #[test]
    fn accepts_last_byte_terminator_and_rejects_unterminated_capacity() {
        let mut output = [0; CAPACITY];
        copy_into(&mut output, |i| if i == CAPACITY - 1 { 0 } else { b'a' }).unwrap();
        assert!(output[..CAPACITY - 1].iter().all(|byte| *byte == b'a'));
        assert_eq!(output[CAPACITY - 1], 0);
        assert_eq!(
            copy_into(&mut output, |i| {
                assert!(i < CAPACITY);
                b'b'
            }),
            Err(-63)
        );
        assert!(output.iter().all(|byte| *byte == b'b'));
    }
    #[test]
    fn preserves_non_utf8_path_bytes() {
        let mut output = [0; CAPACITY];
        copy_into(&mut output, |i| [0xff, 0xfe, b'/', 0][i]).unwrap();
        assert_eq!(&output[..4], &[0xff, 0xfe, b'/', 0]);
    }
}
