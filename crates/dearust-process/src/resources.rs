//! Resource ABI conversion over the caller's Linux resource/accounting policy.
//! All conversion is over owned initialized storage; guest access lives in abi.
pub(crate) const INFINITY: u64 = i64::MAX as u64;
const POSIX: u32 = 0x1000;

pub(crate) fn resource(which: u32) -> Option<u32> {
    match which & !POSIX {
        value @ 0..=4 => Some(value),
        5 => Some(9), // Darwin AS/RSS -> Linux address space, not ignored RSS.
        6 => Some(8), // MEMLOCK
        7 => Some(6), // NPROC
        8 => Some(7), // NOFILE: no obsolete hidden LKM descriptor reservation.
        _ => None,
    }
}

/// Preserve each finite unsigned value only when the host and guest sentinel
/// domains and pair order admit a lossless representation. Never emit a guest
/// soft limit above its hard limit after translating Linux infinity.
pub(crate) fn from_linux(limit: [u64; 2]) -> Result<[u64; 2], i64> {
    if limit.contains(&INFINITY) {
        return Err(-84);
    }
    let converted = limit.map(|value| if value == u64::MAX { INFINITY } else { value });
    if converted[0] > converted[1] {
        return Err(-84);
    }
    Ok(converted)
}

/// Linux reserves u64::MAX for infinity, so full unsigned Darwin values cannot
/// be represented bijectively. Never silently change finite values into infinity
/// or reverse soft/hard ordering. Report nonrepresentability before mutation.
pub(crate) fn to_linux(limit: [u64; 2]) -> Result<[u64; 2], i64> {
    if limit[0] > limit[1] {
        return Err(-22);
    }
    if limit.contains(&u64::MAX) {
        return Err(-84);
    }
    let converted = limit.map(|value| if value == INFINITY { u64::MAX } else { value });
    if converted[0] > converted[1] {
        return Err(-84);
    }
    Ok(converted)
}

pub(crate) fn decode_limit(bytes: &[u8; 16]) -> [u64; 2] {
    [
        u64::from_ne_bytes(bytes[..8].try_into().unwrap()),
        u64::from_ne_bytes(bytes[8..].try_into().unwrap()),
    ]
}

pub(crate) fn encode_limit(limit: [u64; 2]) -> [u8; 16] {
    let mut output = [0; 16];
    output[..8].copy_from_slice(&limit[0].to_ne_bytes());
    output[8..].copy_from_slice(&limit[1].to_ne_bytes());
    output
}

pub(crate) fn usage(linux: &mut [i64; 18]) {
    // The two LP64 layouts have identical field offsets. Convert the owned
    // initialized kernel result in place: Darwin's usec is signed32 followed by
    // zero padding, and ru_maxrss changes KiB to bytes. All other counters are
    // already signed64. This avoids a second144-byte buffer and full-data copy.
    for index in [1, 3] {
        linux[index] = i64::from(linux[index] as u32);
    }
    // XNU narrows its resident-size counter to native long; preserve modulo64
    // bits rather than inventing a saturating cap or risking an overflow panic.
    linux[4] = linux[4].wrapping_mul(1024);
}

#[cfg(test)]
mod tests {
    extern crate std;
    use super::*;
    use std::vec::Vec;

    #[test]
    fn resource_domains_and_posix_flag_match_pinned_xnu() {
        for (darwin, linux) in [0, 1, 2, 3, 4, 9, 8, 6, 7].into_iter().enumerate() {
            assert_eq!(resource(darwin as u32), Some(linux));
            assert_eq!(resource(darwin as u32 | POSIX), Some(linux));
        }
        for value in [9, 10, 0x2000, 0x1009, u32::MAX] {
            assert_eq!(resource(value), None);
        }
    }

    #[test]
    fn limits_preserve_full_width_and_translate_exact_infinity() {
        for value in [0, 1, u32::MAX as u64 + 1, INFINITY - 1, INFINITY] {
            let input = [value, INFINITY];
            assert_eq!(from_linux(to_linux(input).unwrap()), Ok(input));
            assert_eq!(decode_limit(&encode_limit(input)), input);
        }
        assert_eq!(to_linux([2, 1]), Err(-22));
        assert_eq!(to_linux([u64::MAX, 1]), Err(-22));
        assert_eq!(to_linux([0, u64::MAX]), Err(-84));
        assert_eq!(to_linux([INFINITY + 1, u64::MAX]), Err(-84));
        assert_eq!(from_linux([INFINITY + 1, u64::MAX]), Err(-84));
        assert_eq!(from_linux([0, INFINITY]), Err(-84));
        assert_eq!(to_linux([INFINITY, INFINITY + 1]), Err(-84));
        assert_eq!(
            from_linux([INFINITY + 1, INFINITY + 2]),
            Ok([INFINITY + 1, INFINITY + 2])
        );
        assert_eq!(
            to_linux([INFINITY + 1, INFINITY + 2]),
            Ok([INFINITY + 1, INFINITY + 2])
        );
    }

    #[test]
    fn usage_converts_rss_units_and_timeval_padding_without_touching_counters() {
        let mut input = [0_i64; 18];
        input[..5].copy_from_slice(&[0x123456789, 123456, -7, 999999, 123]);
        for (i, v) in input.iter_mut().enumerate().skip(5) {
            *v = -(i as i64);
        }
        usage(&mut input);
        let output: Vec<u8> = input.iter().flat_map(|v| v.to_ne_bytes()).collect();
        assert_eq!(
            &output[..16],
            &[
                0x89, 0x67, 0x45, 0x23, 1, 0, 0, 0, 0x40, 0xe2, 1, 0, 0, 0, 0, 0
            ]
        );
        assert_eq!(&output[16..24], &(-7_i64).to_ne_bytes());
        assert_eq!(&output[24..28], &999999_i32.to_ne_bytes());
        assert_eq!(&output[28..32], &[0; 4]);
        assert_eq!(&output[32..40], &125952_i64.to_ne_bytes());
        for (i, value) in input.iter().enumerate().skip(5) {
            assert_eq!(&output[i * 8..i * 8 + 8], &value.to_ne_bytes());
        }
    }

    #[test]
    fn usage_extrema_have_defined_narrowing_and_no_overflow_panic() {
        let mut input = [i64::MAX; 18];
        input[3] = i64::MIN;
        usage(&mut input);
        let output: Vec<u8> = input.iter().flat_map(|v| v.to_ne_bytes()).collect();
        assert_eq!(&output[8..12], &(-1_i32).to_ne_bytes());
        assert_eq!(&output[24..28], &0_i32.to_ne_bytes());
        assert_eq!(&output[32..40], &(-1024_i64).to_ne_bytes());
        assert_eq!(&output[12..16], &[0; 4]);
        assert_eq!(&output[28..32], &[0; 4]);
    }
}
