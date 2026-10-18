// SPDX-License-Identifier: GPL-3.0-or-later
//! Pin reconstruction selections until output completes. On descriptor pressure,
//! snapshot only their requested ranges into an unlinked private scratch file.
use super::{Area, Options, add, open_backing};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{File, Metadata};
use std::io::{self, Seek, SeekFrom, Write};
use std::os::unix::fs::MetadataExt;
use std::path::PathBuf;

fn scratch() -> io::Result<File> {
    // O_TMPFILE creates an inode without a directory entry. No pathname unlink
    // race can leave captured core bytes in a named file, even in shared TMPDIR.
    Ok(rustix::fs::open(
        std::env::temp_dir(),
        rustix::fs::OFlags::TMPFILE | rustix::fs::OFlags::RDWR | rustix::fs::OFlags::CLOEXEC,
        rustix::fs::Mode::RUSR | rustix::fs::Mode::WUSR,
    )?
    .into())
}

pub(super) fn aliases(a: &Metadata, b: &Metadata) -> bool {
    a.dev() == b.dev() && a.ino() == b.ino()
}
fn check_alias(file: &File, destination: &Metadata) -> io::Result<()> {
    if aliases(&file.metadata()?, destination) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "output aliases a backing file",
        ));
    }
    Ok(())
}

enum Source {
    Pinned(File),
    // (source offset, requested length) -> (scratch offset, available bytes).
    Captured(BTreeMap<(u64, u64), (u64, u64)>),
    Missing,
}
pub(super) struct BackingFiles {
    sources: BTreeMap<PathBuf, Source>,
    scratch: Option<File>,
}
impl BackingFiles {
    pub(super) fn prepare(
        areas: &[Area],
        options: &Options,
        destination: &Metadata,
        input: &File,
    ) -> io::Result<Self> {
        Self::with_limit(areas, options, destination, input, 64)
    }
    fn with_limit(
        areas: &[Area],
        options: &Options,
        destination: &Metadata,
        input: &File,
        limit: usize,
    ) -> io::Result<Self> {
        let mut ranges: BTreeMap<PathBuf, BTreeSet<(u64, u64)>> = BTreeMap::new();
        for area in areas {
            if let Some(path) = &area.path {
                if area.length != 0 && !path.starts_with("/dev") {
                    ranges
                        .entry(path.clone())
                        .or_default()
                        .insert((area.offset, area.length));
                }
            }
        }
        let mut result = Self {
            sources: BTreeMap::new(),
            scratch: None,
        };
        // One reserved descriptor lets an EMFILE transition open scratch without
        // abandoning an already selected source. It never reads the input.
        let mut reserve = None;
        let mut pinned = 0;
        for (path, wanted) in &ranges {
            if pinned == limit && result.scratch.is_none() {
                drop(reserve.take());
                result.capture_pinned(&ranges)?;
            }
            let file = match open_backing(options, path) {
                Err(error)
                    if error.raw_os_error() == Some(24)
                        && pinned != 0
                        && result.scratch.is_none() =>
                {
                    drop(reserve.take());
                    result.capture_pinned(&ranges)?;
                    open_backing(options, path)?
                }
                other => other?,
            };
            let source = if let Some(file) = file {
                check_alias(&file, destination)?;
                if pinned == 0 && ranges.len() > 1 && result.scratch.is_none() {
                    // Missing-only reconstructions need no spare descriptor.
                    // If no spare exists, leave a single selected file pinned;
                    // a subsequent real source reports resource exhaustion.
                    reserve = match input.try_clone() {
                        Ok(file) => Some(file),
                        Err(error) if error.raw_os_error() == Some(24) => None,
                        Err(error) => return Err(error),
                    };
                }
                if let Some(scratch) = &mut result.scratch {
                    Self::capture(file, wanted, scratch)?
                } else {
                    pinned += 1;
                    Source::Pinned(file)
                }
            } else {
                Source::Missing
            };
            result.sources.insert(path.clone(), source);
        }
        Ok(result)
    }
    fn capture_pinned(
        &mut self,
        ranges: &BTreeMap<PathBuf, BTreeSet<(u64, u64)>>,
    ) -> io::Result<()> {
        let mut scratch = scratch()?;
        for (path, source) in &mut self.sources {
            if let Source::Pinned(_) = source {
                let Source::Pinned(file) = std::mem::replace(source, Source::Missing) else {
                    unreachable!()
                };
                *source = Self::capture(file, &ranges[path], &mut scratch)?;
            }
        }
        self.scratch = Some(scratch);
        Ok(())
    }
    fn capture(
        mut file: File,
        ranges: &BTreeSet<(u64, u64)>,
        scratch: &mut File,
    ) -> io::Result<Source> {
        let mut captured = BTreeMap::new();
        for &(offset, length) in ranges {
            file.seek(SeekFrom::Start(offset))?;
            let start = scratch.stream_position()?;
            add(start, length)?;
            let copied = io::copy(&mut (&mut file).take(length), scratch)?;
            captured.insert((offset, length), (start, copied));
        }
        Ok(Source::Captured(captured))
    }
    pub(super) fn copy(&mut self, area: &Area, out: &mut impl Write) -> io::Result<u64> {
        let Some(path) = &area.path else {
            unreachable!()
        };
        match self.sources.get_mut(path) {
            Some(Source::Pinned(file)) => {
                file.seek(SeekFrom::Start(area.offset))?;
                io::copy(&mut file.take(area.length), out)
            }
            Some(Source::Captured(ranges)) => {
                let &(offset, length) = &ranges[&(area.offset, area.length)];
                let scratch = self.scratch.as_mut().expect("captured source owns scratch");
                scratch.seek(SeekFrom::Start(offset))?;
                let copied = io::copy(&mut scratch.take(length), out)?;
                if copied != length {
                    return Err(io::Error::new(
                        io::ErrorKind::UnexpectedEof,
                        "truncated backing scratch",
                    ));
                }
                Ok(copied)
            }
            Some(Source::Missing) | None => Ok(0),
        }
    }
}

use std::io::Read;

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::FileExt;
    use std::path::Path;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT: AtomicU64 = AtomicU64::new(0);

    struct Dir(PathBuf);
    impl Dir {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "dearust-backing-test-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            std::fs::create_dir(&path).unwrap();
            Self(path)
        }
        fn file(&self, name: &str, contents: &[u8]) -> PathBuf {
            let path = self.0.join(name);
            std::fs::write(&path, contents).unwrap();
            path
        }
    }
    impl Drop for Dir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }
    fn area(path: &Path, offset: u64, length: u64, size: u64) -> Area {
        Area {
            address: 0x1000,
            size,
            offset,
            length,
            prot: 1,
            path: Some(path.to_owned()),
        }
    }
    fn copy_and_pad(files: &mut BackingFiles, area: &Area) -> Vec<u8> {
        let mut bytes = Vec::new();
        let copied = files.copy(area, &mut bytes).unwrap();
        assert_eq!(copied, bytes.len() as u64);
        super::super::zero(&mut bytes, area.size - copied).unwrap();
        bytes
    }

    #[test]
    fn spill_captures_only_selected_ranges_and_preserves_short_reads() {
        let dir = Dir::new();
        let a = dir.file("a", b"0123456789");
        let b = dir.file("b", b"");
        let c = dir.file("c", b"end");
        let far = 1_u64 << 32;
        let sparse = std::fs::OpenOptions::new().write(true).open(&b).unwrap();
        sparse.write_all_at(b"TAIL", far).unwrap();
        let input = File::open(dir.file("input", b"core")).unwrap();
        let destination = dir.file("destination", b"keep");
        let areas = [
            area(&a, 2, 4, 6),
            area(&a, 8, 8, 10),
            area(&a, 2, 4, 4), // Identical source requests share one capture.
            area(&b, far - 2, 10, 12),
            area(&b, far + 100, 5, 5),
            area(&c, 1, 5, 7),
        ];
        let mut files = BackingFiles::with_limit(
            &areas,
            &Options::default(),
            &std::fs::metadata(&destination).unwrap(),
            &input,
            1,
        )
        .unwrap();
        assert!(
            files
                .sources
                .values()
                .all(|source| matches!(source, Source::Captured(_)))
        );
        // The multi-GiB sparse source hole must never be copied in full.
        assert_eq!(
            files.scratch.as_ref().unwrap().metadata().unwrap().len(),
            14
        );
        for (area, expected) in areas.iter().zip([
            &b"2345\0\0"[..],
            &b"89\0\0\0\0\0\0\0\0"[..],
            &b"2345"[..],
            &b"\0\0TAIL\0\0\0\0\0\0"[..],
            &b"\0\0\0\0\0"[..],
            &b"nd\0\0\0\0\0"[..],
        ]) {
            assert_eq!(copy_and_pad(&mut files, area), expected);
        }
        // Capture offsets must remain correct after reads seek scratch backward.
        assert_eq!(copy_and_pad(&mut files, &areas[0]), b"2345\0\0");
        assert_eq!(std::fs::read(destination).unwrap(), b"keep");
    }

    #[test]
    fn selected_backing_inodes_survive_path_replacement_even_with_destination() {
        let dir = Dir::new();
        let input = File::open(dir.file("input", b"core")).unwrap();
        let destination = dir.file("destination", b"destination bytes");
        for limit in [1, 64] {
            let a = dir.file("a", b"first original");
            let b = dir.file("b", b"second original");
            let areas = [area(&a, 6, 8, 10), area(&b, 7, 8, 8)];
            let mut files = BackingFiles::with_limit(
                &areas,
                &Options::default(),
                &std::fs::metadata(&destination).unwrap(),
                &input,
                limit,
            )
            .unwrap();
            assert_eq!(files.scratch.is_some(), limit == 1);
            std::fs::remove_file(&a).unwrap();
            std::fs::hard_link(&destination, &a).unwrap();
            std::fs::remove_file(&b).unwrap();
            std::os::unix::fs::symlink(&destination, &b).unwrap();
            assert_eq!(copy_and_pad(&mut files, &areas[0]), b"original\0\0");
            assert_eq!(copy_and_pad(&mut files, &areas[1]), b"original");
            assert_eq!(std::fs::read(&destination).unwrap(), b"destination bytes");
            std::fs::remove_file(a).unwrap();
            std::fs::remove_file(b).unwrap();
        }
    }

    #[test]
    fn later_backing_alias_is_rejected_after_earlier_file_spills() {
        let dir = Dir::new();
        let input = File::open(dir.file("input", b"core")).unwrap();
        let destination = dir.file("destination", b"preserve destination");
        let a = dir.file("a", b"first backing");
        let alias = dir.0.join("z-destination-alias");
        std::fs::hard_link(&destination, &alias).unwrap();
        let areas = [area(&a, 0, 13, 13), area(&alias, 0, 20, 20)];
        let result = BackingFiles::with_limit(
            &areas,
            &Options::default(),
            &std::fs::metadata(&destination).unwrap(),
            &input,
            1,
        );
        assert!(matches!(result, Err(error) if error.kind() == io::ErrorKind::InvalidInput));
        assert_eq!(std::fs::read(destination).unwrap(), b"preserve destination");
        assert_eq!(std::fs::read(a).unwrap(), b"first backing");
    }

    #[test]
    fn pinned_and_captured_copies_propagate_output_errors() {
        struct Fails;
        impl Write for Fails {
            fn write(&mut self, _: &[u8]) -> io::Result<usize> {
                Err(io::Error::other("injected output failure"))
            }
            fn flush(&mut self) -> io::Result<()> {
                Ok(())
            }
        }
        let dir = Dir::new();
        let input = File::open(dir.file("input", b"core")).unwrap();
        let destination = dir.file("destination", b"keep");
        let a = dir.file("a", b"first");
        let b = dir.file("b", b"second");
        let areas = [area(&a, 0, 5, 5), area(&b, 0, 6, 6)];
        for limit in [1, 64] {
            let mut files = BackingFiles::with_limit(
                &areas,
                &Options::default(),
                &std::fs::metadata(&destination).unwrap(),
                &input,
                limit,
            )
            .unwrap();
            for area in &areas {
                let error = files.copy(area, &mut Fails).unwrap_err();
                assert_eq!(error.to_string(), "injected output failure");
            }
        }
    }
}
