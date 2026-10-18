//! Bounded copying directly into the caller's path storage.
#![forbid(unsafe_code)]
pub const CAPACITY: usize = 4096;
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
    fn copies_only_through_nul_and_preserves_tail() {
        let mut out = [0x55; CAPACITY];
        copy_into(&mut out, |i| b"abc\0"[i]).unwrap();
        assert_eq!(&out[..5], b"abc\0\x55");
        copy_into(&mut out, |i| {
            assert_eq!(i, 0);
            0
        })
        .unwrap();
        assert_eq!(&out[..4], b"\0bc\0");
    }
    #[test]
    fn bounds_and_arbitrary_bytes() {
        let mut out = [0; CAPACITY];
        copy_into(&mut out, |i| [0xff, 0xfe, b'/', 0][i]).unwrap();
        assert_eq!(&out[..4], &[0xff, 0xfe, b'/', 0]);
        assert!(copy_into(&mut out, |i| if i == CAPACITY - 1 { 0 } else { b'x' }).is_ok());
        assert_eq!(
            copy_into(&mut out, |i| {
                assert!(i < CAPACITY);
                b'x'
            }),
            Err(-63)
        );
    }
}
