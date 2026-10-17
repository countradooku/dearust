//! Pure result policy, separate from kernel calls and errno side effects.
#![forbid(unsafe_code)]

use core::ffi::{c_int, c_long};
use dearust_errno::linux_to_bsd;

/// The original C code first assigns the long syscall return to an int.
#[inline]
pub(crate) fn direct(raw: c_long) -> c_int {
    let result = raw as c_int;
    if result < 0 {
        linux_to_bsd(result)
    } else {
        result
    }
}

/// A negative result requests cerror with a positive Darwin errno.
#[inline]
pub(crate) fn with_errno(raw: c_long) -> Result<c_int, c_int> {
    let result = raw as c_int;
    if result < 0 {
        // Real Linux errors are -4095..=-1. Wrapping also defines the behavior
        // for INT_MIN, for which the original C's unary negation was undefined.
        Err(linux_to_bsd(result.wrapping_neg()))
    } else {
        Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn successes_including_wake_counts_are_unchanged() {
        for raw in [0, 1, 42, c_int::MAX as c_long] {
            assert_eq!(direct(raw), raw as c_int);
            assert_eq!(with_errno(raw), Ok(raw as c_int));
        }
    }

    #[test]
    fn darwin_error_policy_keeps_sign_and_separates_errno() {
        for (linux, darwin) in [(11, 35), (110, 60), (38, 78), (14, 14), (511, 511)] {
            assert_eq!(direct(-linux), -(darwin as c_int));
            assert_eq!(with_errno(-linux), Err(darwin as c_int));
        }
    }

    #[test]
    fn long_result_narrows_before_sign_test_as_in_c() {
        assert_eq!(direct(0x1_0000_0000), 0);
        assert_eq!(with_errno(0x1_0000_0000), Ok(0));
        assert_eq!(direct(0xffff_fff5), -35);
        assert_eq!(with_errno(0xffff_fff5), Err(35));
        assert_eq!(direct(c_int::MIN as c_long), c_int::MIN);
        assert_eq!(with_errno(c_int::MIN as c_long), Err(c_int::MIN));
    }
}
