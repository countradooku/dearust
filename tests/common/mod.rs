#![allow(dead_code)]

pub fn put32(data: &mut [u8], offset: usize, value: u32, big: bool) {
    data[offset..offset + 4].copy_from_slice(&if big {
        value.to_be_bytes()
    } else {
        value.to_le_bytes()
    });
}

pub fn put64(data: &mut [u8], offset: usize, value: u64, big: bool) {
    data[offset..offset + 8].copy_from_slice(&if big {
        value.to_be_bytes()
    } else {
        value.to_le_bytes()
    });
}

pub fn thin(wide: bool, big: bool, uuid: Option<[u8; 16]>, name: Option<&[u8]>) -> Vec<u8> {
    let header_size = if wide { 32 } else { 28 };
    let segment_size = if wide { 152 } else { 124 };
    let commands_size =
        usize::from(uuid.is_some()) * 24 + usize::from(name.is_some()) * segment_size;
    let payload_offset = header_size + commands_size;
    let mut data = vec![0; payload_offset + name.map_or(0, <[u8]>::len)];
    put32(
        &mut data,
        0,
        if wide { 0xfeedfacf } else { 0xfeedface },
        big,
    );
    put32(&mut data, 4, if wide { 0x01000007 } else { 7 }, big);
    put32(&mut data, 8, 3, big);
    put32(&mut data, 12, 6, big); // MH_DYLIB
    put32(
        &mut data,
        16,
        u32::from(uuid.is_some()) + u32::from(name.is_some()),
        big,
    );
    put32(&mut data, 20, commands_size as u32, big);
    let mut cursor = header_size;
    if let Some(uuid) = uuid {
        put32(&mut data, cursor, 0x1b, big);
        put32(&mut data, cursor + 4, 24, big);
        data[cursor + 8..cursor + 24].copy_from_slice(&uuid);
        cursor += 24;
    }
    if let Some(name) = name {
        put32(&mut data, cursor, if wide { 0x19 } else { 1 }, big);
        put32(&mut data, cursor + 4, segment_size as u32, big);
        data[cursor + 8..cursor + 14].copy_from_slice(b"__TEXT");
        put32(&mut data, cursor + if wide { 64 } else { 48 }, 1, big);
        let section = cursor + if wide { 72 } else { 56 };
        data[section..section + 9].copy_from_slice(b"__elfname");
        data[section + 16..section + 22].copy_from_slice(b"__TEXT");
        if wide {
            put64(&mut data, section + 40, name.len() as u64, big);
        } else {
            put32(&mut data, section + 36, name.len() as u32, big);
        }
        put32(
            &mut data,
            section + if wide { 48 } else { 40 },
            payload_offset as u32,
            big,
        );
        data[payload_offset..].copy_from_slice(name);
    }
    data
}

pub fn fat(images: &[Vec<u8>], big: bool, wide: bool) -> Vec<u8> {
    let stride = if wide { 32 } else { 20 };
    let table_end = 8 + stride * images.len();
    let mut data = vec![0; table_end];
    put32(
        &mut data,
        0,
        if wide { 0xcafebabf } else { 0xcafebabe },
        big,
    );
    put32(&mut data, 4, images.len() as u32, big);
    for (i, image) in images.iter().enumerate() {
        let arch = 8 + i * stride;
        let inner_big = image[0] == 0xfe;
        for field in [0, 4] {
            let bytes: [u8; 4] = image[4 + field..8 + field].try_into().unwrap();
            put32(
                &mut data,
                arch + field,
                if inner_big {
                    u32::from_be_bytes(bytes)
                } else {
                    u32::from_le_bytes(bytes)
                },
                big,
            );
        }
        let offset = data.len();
        if wide {
            put64(&mut data, arch + 8, offset as u64, big);
            put64(&mut data, arch + 16, image.len() as u64, big);
        } else {
            put32(&mut data, arch + 8, offset as u32, big);
            put32(&mut data, arch + 12, image.len() as u32, big);
        }
        data.extend_from_slice(image);
    }
    data
}
