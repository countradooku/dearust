//! Safe value conversion. Constants are from pinned Darwin headers, never libc.
#![forbid(unsafe_code)]

/// Length of the original sparse translation table (including unmapped entries).
pub const TRANSLATION_ARRAY_LENGTH: usize = 512;

/// Translate supported Linux errno numbers, preserving the sign and unknown values.
///
/// `i32::MIN` is unknown and passes through without signed-negation overflow.
#[must_use]
pub const fn linux_to_bsd(err: i32) -> i32 {
    let translated = match err.unsigned_abs() {
        1 => 1,    // LINUX_EPERM -> EPERM
        2 => 2,    // LINUX_ENOENT -> ENOENT
        3 => 3,    // LINUX_ESRCH -> ESRCH
        4 => 4,    // LINUX_EINTR -> EINTR
        5 => 5,    // LINUX_EIO -> EIO
        6 => 6,    // LINUX_ENXIO -> ENXIO
        8 => 8,    // LINUX_ENOEXEC -> ENOEXEC
        9 => 9,    // LINUX_EBADF -> EBADF
        10 => 10,  // LINUX_ECHILD -> ECHILD
        11 => 35,  // LINUX_EAGAIN -> EAGAIN
        12 => 12,  // LINUX_ENOMEM -> ENOMEM
        13 => 13,  // LINUX_EACCES -> EACCES
        14 => 14,  // LINUX_EFAULT -> EFAULT
        15 => 15,  // LINUX_ENOTBLK -> ENOTBLK
        16 => 16,  // LINUX_EBUSY -> EBUSY
        17 => 17,  // LINUX_EEXIST -> EEXIST
        18 => 18,  // LINUX_EXDEV -> EXDEV
        19 => 19,  // LINUX_ENODEV -> ENODEV
        20 => 20,  // LINUX_ENOTDIR -> ENOTDIR
        21 => 21,  // LINUX_EISDIR -> EISDIR
        22 => 22,  // LINUX_EINVAL -> EINVAL
        23 => 23,  // LINUX_ENFILE -> ENFILE
        24 => 24,  // LINUX_EMFILE -> EMFILE
        25 => 25,  // LINUX_ENOTTY -> ENOTTY
        26 => 26,  // LINUX_ETXTBSY -> ETXTBSY
        27 => 27,  // LINUX_EFBIG -> EFBIG
        28 => 28,  // LINUX_ENOSPC -> ENOSPC
        29 => 29,  // LINUX_ESPIPE -> ESPIPE
        30 => 30,  // LINUX_EROFS -> EROFS
        31 => 31,  // LINUX_EMLINK -> EMLINK
        32 => 32,  // LINUX_EPIPE -> EPIPE
        33 => 33,  // LINUX_EDOM -> EDOM
        34 => 34,  // LINUX_ERANGE -> ERANGE
        35 => 11,  // LINUX_EDEADLK -> EDEADLK
        36 => 63,  // LINUX_ENAMETOOLONG -> ENAMETOOLONG
        37 => 77,  // LINUX_ENOLCK -> ENOLCK
        38 => 78,  // LINUX_ENOSYS -> ENOSYS
        39 => 66,  // LINUX_ENOTEMPTY -> ENOTEMPTY
        40 => 62,  // LINUX_ELOOP -> ELOOP
        42 => 91,  // LINUX_ENOMSG -> ENOMSG
        43 => 90,  // LINUX_EIDRM -> EIDRM
        60 => 99,  // LINUX_ENOSTR -> ENOSTR
        61 => 96,  // LINUX_ENODATA -> ENODATA
        62 => 101, // LINUX_ETIME -> ETIME
        63 => 98,  // LINUX_ENOSR -> ENOSR
        66 => 71,  // LINUX_EREMOTE -> EREMOTE
        67 => 97,  // LINUX_ENOLINK -> ENOLINK
        71 => 100, // LINUX_EPROTO -> EPROTO
        72 => 95,  // LINUX_EMULTIHOP -> EMULTIHOP
        74 => 94,  // LINUX_EBADMSG -> EBADMSG
        75 => 84,  // LINUX_EOVERFLOW -> EOVERFLOW
        84 => 92,  // LINUX_EILSEQ -> EILSEQ
        87 => 68,  // LINUX_EUSERS -> EUSERS
        88 => 38,  // LINUX_ENOTSOCK -> ENOTSOCK
        89 => 39,  // LINUX_EDESTADDRREQ -> EDESTADDRREQ
        90 => 40,  // LINUX_EMSGSIZE -> EMSGSIZE
        91 => 41,  // LINUX_EPROTOTYPE -> EPROTOTYPE
        92 => 42,  // LINUX_ENOPROTOOPT -> ENOPROTOOPT
        93 => 43,  // LINUX_EPROTONOSUPPORT -> EPROTONOSUPPORT
        94 => 44,  // LINUX_ESOCKTNOSUPPORT -> ESOCKTNOSUPPORT
        95 => 102, // LINUX_EOPNOTSUPP -> EOPNOTSUPP
        96 => 46,  // LINUX_EPFNOSUPPORT -> EPFNOSUPPORT
        97 => 47,  // LINUX_EAFNOSUPPORT -> EAFNOSUPPORT
        98 => 48,  // LINUX_EADDRINUSE -> EADDRINUSE
        99 => 49,  // LINUX_EADDRNOTAVAIL -> EADDRNOTAVAIL
        100 => 50, // LINUX_ENETDOWN -> ENETDOWN
        101 => 51, // LINUX_ENETUNREACH -> ENETUNREACH
        102 => 52, // LINUX_ENETRESET -> ENETRESET
        103 => 53, // LINUX_ECONNABORTED -> ECONNABORTED
        104 => 54, // LINUX_ECONNRESET -> ECONNRESET
        105 => 55, // LINUX_ENOBUFS -> ENOBUFS
        106 => 56, // LINUX_EISCONN -> EISCONN
        107 => 57, // LINUX_ENOTCONN -> ENOTCONN
        108 => 58, // LINUX_ESHUTDOWN -> ESHUTDOWN
        109 => 59, // LINUX_ETOOMANYREFS -> ETOOMANYREFS
        110 => 60, // LINUX_ETIMEDOUT -> ETIMEDOUT
        111 => 61, // LINUX_ECONNREFUSED -> ECONNREFUSED
        112 => 64, // LINUX_EHOSTDOWN -> EHOSTDOWN
        113 => 65, // LINUX_EHOSTUNREACH -> EHOSTUNREACH
        114 => 37, // LINUX_EALREADY -> EALREADY
        115 => 36, // LINUX_EINPROGRESS -> EINPROGRESS
        116 => 70, // LINUX_ESTALE -> ESTALE
        122 => 69, // LINUX_EDQUOT -> EDQUOT
        125 => 89, // LINUX_ECANCELED -> ECANCELED
        _ => return err,
    };
    if err < 0 { -translated } else { translated }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Full observed positive output for 0..=133 in the original UNIX03 ABI.
    // In particular Linux-only holes pass through, rather than mapping to zero.
    const ORIGINAL_OUTPUT: [i32; 134] = [
        0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 35, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24,
        25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 11, 63, 77, 78, 66, 62, 41, 91, 90, 44, 45, 46, 47,
        48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 99, 96, 101, 98, 64, 65, 71, 97, 68, 69,
        70, 100, 95, 73, 94, 84, 76, 77, 78, 79, 80, 81, 82, 83, 92, 85, 86, 68, 38, 39, 40, 41,
        42, 43, 44, 102, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 64, 65,
        37, 36, 70, 117, 118, 119, 120, 121, 69, 123, 124, 89, 126, 127, 128, 129, 130, 131, 132,
        133,
    ];

    #[test]
    fn all_supported_numbers_holes_and_signs_match_original() {
        for (input, expected) in ORIGINAL_OUTPUT.into_iter().enumerate() {
            let input = input as i32;
            assert_eq!(linux_to_bsd(input), expected, "positive errno {input}");
            assert_eq!(linux_to_bsd(-input), -expected, "negative errno {input}");
        }
        // The remainder of the original 512-slot table contains only holes.
        for input in 134..TRANSLATION_ARRAY_LENGTH as i32 {
            assert_eq!(linux_to_bsd(input), input);
            assert_eq!(linux_to_bsd(-input), -input);
        }
    }

    #[test]
    fn out_of_table_values_and_integer_extremes_pass_through() {
        for input in [
            i32::MIN,
            i32::MIN + 1,
            -65536,
            -513,
            -512,
            512,
            513,
            65536,
            i32::MAX - 1,
            i32::MAX,
        ] {
            assert_eq!(linux_to_bsd(input), input);
        }
    }

    #[test]
    fn linux_alias_and_darwin_discrete_socket_errors_stay_distinct() {
        assert_eq!(linux_to_bsd(95), 102); // EOPNOTSUPP, not Darwin ENOTSUP=45.
        assert_eq!(linux_to_bsd(102), 52); // ENETRESET overwrites alias designator.
        assert_eq!(linux_to_bsd(11), 35); // EAGAIN is not Darwin EDEADLK=11.
        assert_eq!(linux_to_bsd(35), 11);
    }
}
