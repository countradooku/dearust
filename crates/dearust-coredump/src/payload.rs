// SPDX-License-Identifier: GPL-3.0-or-later
//! Exact dumped-data transfer without mapping mutable input into Rust memory.
use std::fs::File;
use std::io::{self, Write};
use std::os::unix::fs::FileExt;

#[derive(Default)]
pub(super) struct CopyBuffer(Vec<u8>);
impl CopyBuffer {
    fn reserve(&mut self, length: u64) -> io::Result<()> {
        let size = length.min(1024 * 1024) as usize;
        if self.0.len() < size {
            self.0
                .try_reserve(size - self.0.len())
                .map_err(io::Error::other)?;
            self.0.resize(size, 0);
        }
        Ok(())
    }
    fn buffered(
        &mut self,
        file: &File,
        mut offset: u64,
        mut length: u64,
        out: &mut impl Write,
    ) -> io::Result<()> {
        if length == 0 {
            return Ok(());
        }
        self.reserve(length)?;
        while length != 0 {
            let take = length.min(self.0.len() as u64) as usize;
            file.read_exact_at(&mut self.0[..take], offset)?;
            out.write_all(&self.0[..take])?;
            offset += take as u64; // input range was checked before output
            length -= take as u64;
        }
        Ok(())
    }
    #[cfg(target_os = "linux")]
    fn spliced(
        &mut self,
        file: &File,
        offset: &mut u64,
        length: u64,
        out: &mut io::BufWriter<File>,
        requested_capacity: usize,
    ) -> io::Result<u64> {
        use rustix::pipe::{
            PipeFlags, SpliceFlags, fcntl_getpipe_size, fcntl_setpipe_size, pipe_with, splice,
        };
        let (reader, writer) = match pipe_with(PipeFlags::CLOEXEC) {
            Ok(pipe) => pipe,
            // This is an optional transfer mechanism: no source bytes have
            // moved. The exact-read fallback needs neither extra descriptor.
            Err(_) => return Ok(0),
        };
        let capacity = match fcntl_setpipe_size(&writer, requested_capacity)
            .or_else(|_| fcntl_getpipe_size(&writer))
        {
            Ok(size) if size != 0 => size.min(1024 * 1024),
            _ => return Ok(0),
        };
        out.flush()?;
        let flags = SpliceFlags::MOVE | SpliceFlags::MORE;
        let mut copied = 0;
        while copied < length {
            let count = (length - copied).min(capacity as u64) as usize;
            let got = match splice(file, Some(offset), &writer, None, count, flags) {
                Ok(0) => break, // exact fallback verifies EOF, including old kernels
                Ok(got) => got,
                Err(rustix::io::Errno::INTR) => continue,
                Err(error) if unsupported_copy(error) => break,
                Err(error) => return Err(error.into()),
            };
            let mut pending = got;
            while pending != 0 {
                match splice(&reader, None, out.get_ref(), None, pending, flags) {
                    Ok(0) => {
                        return Err(io::Error::new(
                            io::ErrorKind::WriteZero,
                            "short pipe transfer",
                        ));
                    }
                    Ok(written) => pending -= written,
                    Err(rustix::io::Errno::INTR) => continue,
                    Err(error) if unsupported_copy(error) => {
                        // Input offset already includes this queued batch. Drain
                        // every remaining byte before resuming ordinary reads;
                        // otherwise a write-side fallback would silently lose it.
                        use std::io::Read;
                        let mut queued = File::from(reader);
                        self.reserve(pending as u64)?;
                        while pending != 0 {
                            let take = pending.min(self.0.len());
                            queued.read_exact(&mut self.0[..take])?;
                            out.write_all(&self.0[..take])?;
                            pending -= take;
                        }
                        return Ok(copied + got as u64);
                    }
                    Err(error) => return Err(error.into()),
                }
            }
            copied += got as u64;
        }
        Ok(copied)
    }

    pub(super) fn copy(
        &mut self,
        file: &File,
        mut offset: u64,
        mut length: u64,
        out: &mut io::BufWriter<File>,
        regular_output: bool,
    ) -> io::Result<u64> {
        let total = length;
        #[cfg(target_os = "linux")]
        if regular_output && length >= 65536 {
            // Destination and source have congruent page offsets. Transfer the
            // unaligned edge normally, then offload whole pages. Do not ask the
            // filesystem to implement an unaligned clone via costly buffering.
            let head = (4096 - (offset & 4095)) & 4095;
            self.buffered(file, offset, head, out)?;
            offset += head;
            length -= head;
            let mut body = length & !4095;
            if body != 0 {
                out.flush()?;
                while body != 0 {
                    let count = body.min(0x7fff_f000) as usize;
                    match rustix::fs::copy_file_range(
                        file,
                        Some(&mut offset),
                        out.get_ref(),
                        None,
                        count,
                    ) {
                        // Some older kernels report zero for unsupported virtual
                        // files. Verify the remainder with reads instead of
                        // inventing bytes or treating zero as successful EOF.
                        Ok(0) => break,
                        Ok(copied) => {
                            body -= copied as u64;
                            length -= copied as u64;
                        }
                        Err(rustix::io::Errno::INTR) => continue,
                        Err(error) if unsupported_copy(error) => break,
                        Err(error) => return Err(error.into()),
                    }
                }
            }
        }
        #[cfg(target_os = "linux")]
        if regular_output && length >= 65536 {
            // Only regular destinations: queued pages must be committed before
            // return, rather than remain source-dependent in an external pipe.
            length -= self.spliced(file, &mut offset, length, out, 1024 * 1024)?;
        }
        self.buffered(file, offset, length, out)?;
        Ok(total)
    }
}

#[cfg(target_os = "linux")]
fn unsupported_copy(error: rustix::io::Errno) -> bool {
    matches!(
        error,
        rustix::io::Errno::NOSYS
            | rustix::io::Errno::OPNOTSUPP
            | rustix::io::Errno::INVAL
            | rustix::io::Errno::XDEV
            | rustix::io::Errno::PERM
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT: AtomicU64 = AtomicU64::new(0);
    struct Dir(PathBuf);
    impl Dir {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "dearust-payload-test-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            std::fs::create_dir(&path).unwrap();
            Self(path)
        }
        fn source(&self, data: &[u8]) -> File {
            let path = self.0.join("source");
            std::fs::write(&path, data).unwrap();
            File::open(path).unwrap()
        }
    }
    impl Drop for Dir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn buffered_copies_exact_unaligned_ranges_and_reuses_storage() {
        let dir = Dir::new();
        let data: Vec<_> = (0..2 * 1024 * 1024 + 200)
            .map(|i| (i % 251) as u8)
            .collect();
        let file = dir.source(&data);
        let mut copier = CopyBuffer::default();
        let mut out = Vec::new();
        let length = 2 * 1024 * 1024 + 71;
        copier.buffered(&file, 37, length, &mut out).unwrap();
        assert_eq!(out, data[37..37 + length as usize]);
        assert_eq!(copier.0.len(), 1024 * 1024);
        let capacity = copier.0.capacity();
        out.clear();
        copier.buffered(&file, 3, 17, &mut out).unwrap();
        assert_eq!(out, data[3..20]);
        assert_eq!(copier.0.capacity(), capacity);
        copier.buffered(&file, u64::MAX, 0, &mut out).unwrap();
        assert_eq!(out, data[3..20]);
    }

    #[test]
    fn buffered_truncation_and_read_errors_never_fabricate_bytes() {
        let dir = Dir::new();
        let original = vec![0x5a; 2 * 1024 * 1024];
        let file = dir.source(&original);
        let path = dir.0.join("source");
        let writer = std::fs::OpenOptions::new().write(true).open(&path).unwrap();
        writer.set_len(1024 * 1024 + 13).unwrap();
        let mut copier = CopyBuffer::default();
        let mut out = Vec::new();
        let error = copier
            .buffered(&file, 0, original.len() as u64, &mut out)
            .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::UnexpectedEof);
        assert_eq!(out, original[..1024 * 1024]);
        out.clear();
        let error = copier
            .buffered(&file, original.len() as u64, 4, &mut out)
            .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::UnexpectedEof);
        assert!(out.is_empty());
        let error = copier.buffered(&writer, 0, 4, &mut out).unwrap_err();
        assert_eq!(error.raw_os_error(), Some(9)); // EBADF: write-only descriptor.
        assert!(out.is_empty());
    }

    #[test]
    fn buffered_propagates_partial_writer_failure() {
        struct PartialWriter(Vec<u8>);
        impl Write for PartialWriter {
            fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
                if self.0.len() >= 17 {
                    return Err(io::Error::new(
                        io::ErrorKind::BrokenPipe,
                        "injected write failure",
                    ));
                }
                let count = bytes.len().min(17 - self.0.len());
                self.0.extend_from_slice(&bytes[..count]);
                Ok(count)
            }
            fn flush(&mut self) -> io::Result<()> {
                Ok(())
            }
        }
        let dir = Dir::new();
        let data: Vec<_> = (0..100).collect();
        let file = dir.source(&data);
        let mut out = PartialWriter(Vec::new());
        let error = CopyBuffer::default()
            .buffered(&file, 7, 80, &mut out)
            .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::BrokenPipe);
        assert_eq!(error.to_string(), "injected write failure");
        assert_eq!(out.0, data[7..24]);
    }

    #[test]
    #[cfg(target_os = "linux")]
    fn kernel_copy_and_transfer_output_survive_source_overwrite_and_truncation() {
        let dir = Dir::new();
        let data: Vec<_> = (0..256 * 1024).map(|i| (i % 251) as u8).collect();
        let file = dir.source(&data);
        let cloned_path = dir.0.join("kernel-copy");
        let cloned = File::create(&cloned_path).unwrap();
        let mut offset = 4096;
        let copied =
            rustix::fs::copy_file_range(&file, Some(&mut offset), &cloned, None, 65536).unwrap();
        assert_eq!(copied, 65536);
        let transferred_path = dir.0.join("transfer");
        let mut transferred = io::BufWriter::new(File::create(&transferred_path).unwrap());
        transferred.write_all(&[0; 123]).unwrap();
        let count = CopyBuffer::default()
            .copy(&file, 123, 128 * 1024 + 117, &mut transferred, true)
            .unwrap();
        assert_eq!(count, 128 * 1024 + 117);
        transferred.flush().unwrap();
        let source_writer = std::fs::OpenOptions::new()
            .write(true)
            .open(dir.0.join("source"))
            .unwrap();
        source_writer
            .write_all_at(&vec![0xa5; data.len()], 0)
            .unwrap();
        source_writer.set_len(0).unwrap();
        assert_eq!(
            std::fs::read(cloned_path).unwrap(),
            data[4096..4096 + 65536]
        );
        let transferred = std::fs::read(transferred_path).unwrap();
        assert_eq!(&transferred[..123], &[0; 123]);
        assert_eq!(&transferred[123..], &data[123..123 + 128 * 1024 + 117]);
    }

    #[test]
    #[cfg(target_os = "linux")]
    fn spliced_exact_ranges_survive_resize_failure_and_source_mutation() {
        let dir = Dir::new();
        let length = 2 * 1024 * 1024 + 117;
        let data: Vec<_> = (0..length + 123 + 97).map(|i| (i % 251) as u8).collect();
        let file = dir.source(&data);
        let mut copier = CopyBuffer::default();
        for (index, capacity) in [4096, usize::MAX].into_iter().enumerate() {
            let path = dir.0.join(format!("spliced-{index}"));
            let mut out = io::BufWriter::new(File::create(&path).unwrap());
            out.write_all(b"prefix").unwrap();
            for _ in 0..2 {
                let mut offset = 123;
                let copied = copier
                    .spliced(&file, &mut offset, length as u64, &mut out, capacity)
                    .unwrap();
                assert_eq!(copied, length as u64);
                assert_eq!(offset, (123 + length) as u64);
            }
            out.flush().unwrap();
            let mut expected = b"prefix".to_vec();
            expected.extend_from_slice(&data[123..123 + length]);
            expected.extend_from_slice(&data[123..123 + length]);
            assert_eq!(std::fs::read(&path).unwrap(), expected);
        }
        let source_writer = std::fs::OpenOptions::new()
            .write(true)
            .open(dir.0.join("source"))
            .unwrap();
        source_writer
            .write_all_at(&vec![0xa5; data.len()], 0)
            .unwrap();
        source_writer.set_len(0).unwrap();
        for index in 0..2 {
            let bytes = std::fs::read(dir.0.join(format!("spliced-{index}"))).unwrap();
            assert_eq!(&bytes[..6], b"prefix");
            assert_eq!(&bytes[6..6 + length], &data[123..123 + length]);
            assert_eq!(&bytes[6 + length..], &data[123..123 + length]);
        }
    }

    #[test]
    #[cfg(target_os = "linux")]
    fn append_destination_drains_queued_splice_before_buffered_remainder() {
        let dir = Dir::new();
        let length = 2 * 1024 * 1024 + 117;
        let data: Vec<_> = (0..length + 123 + 73).map(|i| (i % 251) as u8).collect();
        let file = dir.source(&data);
        let path = dir.0.join("append-output");
        std::fs::write(&path, b"existing prefix").unwrap();
        let append_file = std::fs::OpenOptions::new()
            .append(true)
            .open(&path)
            .unwrap();
        let mut out = io::BufWriter::new(append_file);
        out.write_all(b"buffered prefix").unwrap();
        let mut copier = CopyBuffer::default();
        let mut offset = 123;
        let copied = copier
            .spliced(&file, &mut offset, length as u64, &mut out, 4096)
            .unwrap();
        assert!(copied > 0 && copied < length as u64);
        assert_eq!(offset, 123 + copied);
        copier
            .buffered(&file, offset, length as u64 - copied, &mut out)
            .unwrap();
        out.write_all(b"output tail").unwrap();
        out.flush().unwrap();
        let mut expected = b"existing prefixbuffered prefix".to_vec();
        expected.extend_from_slice(&data[123..123 + length]);
        expected.extend_from_slice(b"output tail");
        assert_eq!(std::fs::read(path).unwrap(), expected);
    }

    #[test]
    #[cfg(target_os = "linux")]
    fn spliced_write_errors_and_copy_truncation_propagate() {
        let dir = Dir::new();
        let data = vec![0x5a; 2 * 1024 * 1024];
        let file = dir.source(&data);
        let readonly_path = dir.0.join("readonly-output");
        std::fs::write(&readonly_path, b"preserve").unwrap();
        let mut out = io::BufWriter::new(File::open(&readonly_path).unwrap());
        let mut offset = 123;
        let error = CopyBuffer::default()
            .spliced(&file, &mut offset, 65536, &mut out, 4096)
            .unwrap_err();
        assert_eq!(error.raw_os_error(), Some(9));
        assert_eq!(std::fs::read(&readonly_path).unwrap(), b"preserve");
        let source_writer = std::fs::OpenOptions::new()
            .write(true)
            .open(dir.0.join("source"))
            .unwrap();
        source_writer.set_len(65536 + 13).unwrap();
        let path = dir.0.join("truncated-output");
        let mut out = io::BufWriter::new(File::create(&path).unwrap());
        let error = CopyBuffer::default()
            .copy(&file, 123, data.len() as u64 - 123, &mut out, true)
            .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::UnexpectedEof);
        out.flush().unwrap();
        let bytes = std::fs::read(path).unwrap();
        assert!(!bytes.is_empty());
        assert!(bytes.len() <= 65536 + 13 - 123);
        assert!(bytes.iter().all(|byte| *byte == 0x5a));
    }
}
