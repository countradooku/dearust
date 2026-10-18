//! Linux x86_64 stat144 to Darwin legacy stat120 / stat64 translation.
//! Offsets and narrowing follow pinned XNU stat.h and conversion/stat/common.c.
//! Reserved fields, birth time and ABI padding remain deterministically zero.
#![forbid(unsafe_code)]

pub(super) fn convert(input: &[u8; 144], output: &mut [u8; 144], wide: bool, uid: i32, gid: i32) {
    // Every other output byte is assigned below. Zero only reserved regions
    // and the unused legacy tail, avoiding a full buffer clear before writes.
    if wide {
        output[28..32].fill(0);
        output[80..96].fill(0);
        output[116..144].fill(0);
    } else {
        output[92..144].fill(0);
    }
    output[0..4].copy_from_slice(&input[0..4]); // dev_t truncates u64 to i32
    let (mode, links, inode, owner, rdev, times, size, blocks, blocksize) = if wide {
        (4, 6, 8, 16, 24, 32, 96, 104, 112)
    } else {
        (8, 10, 4, 12, 20, 24, 72, 80, 88)
    };
    output[mode..mode + 2].copy_from_slice(&input[24..26]);
    output[links..links + 2].copy_from_slice(&input[16..18]);
    if wide {
        output[inode..inode + 8].copy_from_slice(&input[8..16]);
    } else {
        output[inode..inode + 4].copy_from_slice(&input[8..12]);
    }
    output[owner..owner + 4].copy_from_slice(&uid.to_ne_bytes());
    output[owner + 4..owner + 8].copy_from_slice(&gid.to_ne_bytes());
    output[rdev..rdev + 4].copy_from_slice(&input[40..44]);
    output[times..times + 48].copy_from_slice(&input[72..120]);
    output[size..size + 8].copy_from_slice(&input[48..56]);
    output[blocks..blocks + 8].copy_from_slice(&input[64..72]);
    output[blocksize..blocksize + 4].copy_from_slice(&input[56..60]);
}

#[cfg(test)]
mod tests {
    use super::convert;

    // Generated with unchanged pinned conversion/stat/common.c, initialized
    // reserved bytes and non-host credentials. Covers every ABI field, Linux
    // padding exclusion and all legacy narrowing without guest pointer access.
    const GOLDEN: [&str; 2] = [
        "0001020308090a0b18191011a9cbed8f1032547628292a2b48494a4b4c4d4e4f505152535455565758595a5b5c5d5e5f606162636465666768696a6b6c6d6e6f70717273747576773031323334353637404142434445464738393a3b00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
        "000102031819101108090a0b0c0d0e0fa9cbed8f1032547628292a2b0000000048494a4b4c4d4e4f505152535455565758595a5b5c5d5e5f606162636465666768696a6b6c6d6e6f7071727374757677000000000000000000000000000000003031323334353637404142434445464738393a3b00000000000000000000000000000000000000000000000000000000",
    ];
    #[test]
    fn exact_original_c_layout_and_credentials() {
        let input = core::array::from_fn(|i| i as u8);
        for (wide, golden) in GOLDEN.iter().enumerate() {
            let mut output = [0xa5; 144];
            convert(
                &input,
                &mut output,
                wide != 0,
                0x8fed_cba9_u32 as i32,
                0x7654_3210,
            );
            for (actual, pair) in output.iter().zip(golden.as_bytes().chunks_exact(2)) {
                let hex = |n: u8| if n <= b'9' { n - b'0' } else { n - b'a' + 10 };
                assert_eq!(*actual, hex(pair[0]) * 16 + hex(pair[1]));
            }
        }
    }
}
