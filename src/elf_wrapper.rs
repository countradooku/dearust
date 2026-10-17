// Safe Rust port of Darling src/libelfloader/wrapgen/wrapgen.cpp.
// SPDX-License-Identifier: GPL-3.0-or-later
//! ELF64 wrapper generation without loading or executing the input library.
use std::{collections::BTreeSet, fmt::Write};

#[derive(Debug, PartialEq, Eq)]
pub struct Library<'a> {
    pub soname: Option<&'a str>,
    pub functions: BTreeSet<&'a str>,
    pub variables: BTreeSet<&'a str>,
}
type Result<T> = std::result::Result<T, &'static str>;
fn bytes(data: &[u8], start: u64, len: u64) -> Result<&[u8]> {
    let end = start.checked_add(len).ok_or("ELF range overflow")?;
    data.get(
        usize::try_from(start).map_err(|_| "ELF offset too large")?
            ..usize::try_from(end).map_err(|_| "ELF end too large")?,
    )
    .ok_or("ELF range outside input")
}
fn u16_at(data: &[u8], start: usize) -> Result<u16> {
    Ok(u16::from_le_bytes(
        bytes(data, start as u64, 2)?.try_into().unwrap(),
    ))
}
fn u32_at(data: &[u8], start: usize) -> Result<u32> {
    Ok(u32::from_le_bytes(
        bytes(data, start as u64, 4)?.try_into().unwrap(),
    ))
}
fn u64_at(data: &[u8], start: usize) -> Result<u64> {
    Ok(u64::from_le_bytes(
        bytes(data, start as u64, 8)?.try_into().unwrap(),
    ))
}
fn string_at(data: &[u8], offset: u64) -> Result<&str> {
    let tail = bytes(
        data,
        offset,
        (data.len() as u64)
            .checked_sub(offset)
            .ok_or("string offset outside table")?,
    )?;
    let end = tail
        .iter()
        .position(|&b| b == 0)
        .ok_or("unterminated ELF string")?;
    std::str::from_utf8(&tail[..end]).map_err(|_| "ELF identifier is not UTF-8")
}
fn identifier(name: &str) -> bool {
    let mut chars = name.bytes();
    matches!(chars.next(), Some(b'a'..=b'z' | b'A'..=b'Z' | b'_'))
        && chars.all(|c| c.is_ascii_alphanumeric() || c == b'_')
}
impl<'a> Library<'a> {
    /// Reads little-endian x86-64 shared objects, matching the original target.
    /// Section headers are required, as in upstream. Names are borrowed and sorted.
    pub fn parse(data: &'a [u8]) -> Result<Self> {
        let header = bytes(data, 0, 64)?;
        if &header[..7] != b"\x7fELF\x02\x01\x01"
            || u16_at(header, 16)? != 3
            || u16_at(header, 18)? != 62
            || u32_at(header, 20)? != 1
            || u16_at(header, 52)? != 64
        {
            return Err("expected little-endian ELF64 x86-64 dynamic library");
        }
        let phnum = u16_at(header, 56)? as u64;
        let phstride = u16_at(header, 54)? as u64;
        if phstride != 56 {
            return Err("unsupported ELF program header size");
        }
        let programs = bytes(data, u64_at(header, 32)?, phstride * phnum)?;
        let shnum = u16_at(header, 60)? as u64;
        if shnum == 0 || u16_at(header, 58)? != 64 {
            return Err("ELF section headers are required");
        }
        let sections = bytes(data, u64_at(header, 40)?, 64 * shnum)?;
        let dynamic = programs
            .chunks_exact(56)
            .find(|p| u32_at(p, 0) == Ok(2))
            .ok_or("missing ELF dynamic segment")?;
        let dynamics = bytes(data, u64_at(dynamic, 8)?, u64_at(dynamic, 32)?)?;
        if dynamics.len() % 16 != 0 {
            return Err("partial ELF dynamic entry");
        }
        let (mut straddr, mut strsize, mut soname) = (None, None, None);
        let mut terminated = false;
        for entry in dynamics.chunks_exact(16) {
            let value = u64_at(entry, 8)?;
            match u64_at(entry, 0)? {
                0 => {
                    terminated = true;
                    break;
                }
                5 => straddr = Some(value),
                10 => strsize = Some(value),
                // DT_SONAME is a string-table OFFSET, never a virtual address.
                14 => soname = Some(value),
                _ => {}
            }
        }
        if !terminated {
            return Err("unterminated ELF dynamic table");
        }
        let address = straddr.ok_or("missing ELF dynamic string table")?;
        let size = strsize.ok_or("missing ELF dynamic string table size")?;
        let mut strings = None;
        for program in programs.chunks_exact(56) {
            if u32_at(program, 0)? != 1 {
                continue;
            }
            let start = u64_at(program, 16)?;
            if let Some(delta) = address.checked_sub(start) {
                if delta
                    .checked_add(size)
                    .is_some_and(|end| end <= u64_at(program, 32).unwrap_or(0))
                {
                    strings = Some(bytes(
                        data,
                        u64_at(program, 8)?
                            .checked_add(delta)
                            .ok_or("string offset overflow")?,
                        size,
                    )?);
                    break;
                }
            }
        }
        let strings = strings.ok_or("dynamic strings are not file-backed")?;
        let mut library = Self {
            soname: soname.map(|n| string_at(strings, n)).transpose()?,
            functions: BTreeSet::new(),
            variables: BTreeSet::new(),
        };
        let symbols = sections
            .chunks_exact(64)
            .find(|s| u32_at(s, 4) == Ok(11))
            .ok_or("missing ELF dynamic symbols")?;
        if u64_at(symbols, 56)? != 24 {
            return Err("invalid ELF dynamic symbol stride");
        }
        let syms = bytes(data, u64_at(symbols, 24)?, u64_at(symbols, 32)?)?;
        if syms.len() % 24 != 0 {
            return Err("partial ELF dynamic symbol");
        }
        // Symbol names may repeatedly alias a long shared string or suffixes
        // thereof. Bound total scanning and eventual generated-source size by
        // the input size rather than allowing quadratic amplification.
        let mut name_budget = data.len().saturating_mul(8);
        let mut seen_names = BTreeSet::new();
        for symbol in syms.chunks_exact(24) {
            let kind = symbol[4] & 15;
            if !matches!(kind, 1 | 2)
                || symbol[4] >> 4 != 1
                || symbol[5] & 3 != 0
                || u16_at(symbol, 6)? == 0
                || u64_at(symbol, 8)? == 0
            {
                continue;
            }
            let name_offset = u32_at(symbol, 0)?;
            if !seen_names.insert((kind, name_offset)) {
                continue;
            }
            let name = string_at(strings, name_offset.into())?;
            name_budget = name_budget
                .checked_sub(name.len())
                .ok_or("ELF symbol names exceed bounded parsing/output budget")?;
            if !identifier(name) {
                return Err("symbol cannot be represented as a C identifier");
            }
            if kind == 2 {
                library.functions.insert(name);
            } else {
                library.variables.insert(name);
            }
        }
        if library.functions.is_empty() {
            return Err("no exported functions found");
        }
        Ok(library)
    }
    /// Produces byte-compatible upstream source for ordinary C identifiers.
    pub fn generate(&self, fallback: &str) -> Result<(String, Option<String>)> {
        let soname = self.soname.filter(|s| !s.is_empty()).unwrap_or(fallback);
        // The name crosses both C and assembler string grammars. Reject syntax
        // metacharacters rather than allowing generated-source injection.
        if soname.is_empty()
            || !soname
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || b"._+-/".contains(&c))
        {
            return Err("library name cannot be represented safely in generated assembly");
        }
        let mut output = String::from(
            "#include <elfcalls.h>\nextern struct elf_calls* _elfcalls;\n\nextern const char __elfname[];\n\nstatic void* lib_handle;\n__attribute__((constructor)) static void initializer() {\n\tlib_handle = _elfcalls->dlopen_fatal(__elfname);\n}\n\n__attribute__((destructor)) static void destructor() {\n\t_elfcalls->dlclose_fatal(lib_handle);\n}\n\n",
        );
        for symbol in &self.functions {
            write!(output, "void* {symbol}() {{\n\t__asm__(\".symbol_resolver _{symbol}\");\n\treturn _elfcalls->dlsym_fatal(lib_handle, \"{symbol}\");\n}}\n\n").unwrap();
        }
        writeln!(output, "asm(\".section __TEXT,__elfname\\n.private_extern ___elfname\\n___elfname: .asciz \\\"{soname}\\\"\");").unwrap();
        let header = if self.variables.is_empty() {
            None
        } else {
            let mut header =
                String::from("#pragma once\n\n#ifdef __cplusplus\nextern \"C\" {\n#endif\n\n");
            for symbol in &self.variables {
                write!(output, "void* __elf_get_{symbol}(void) {{\n\treturn _elfcalls->dlsym_fatal(lib_handle, \"{symbol}\");\n}}\n\n").unwrap();
                write!(header, "extern __typeof({symbol})* __elf_get_{symbol}(void);\n#define {symbol} (*__elf_get_{symbol}())\n\n").unwrap();
            }
            header.push_str("\n\n#ifdef __cplusplus\n}\n#endif\n\n");
            Some(header)
        };
        Ok((output, header))
    }
}

/// Resolve a Linux library name without executing its constructors. Explicit
/// paths are used as supplied. Bare names search LD_LIBRARY_PATH, the system
/// ldconfig cache and common x86-64 directories. This does not emulate caller
/// DT_RPATH/DT_RUNPATH, audit modules, or every dynamic-loader hardware policy.
pub fn resolve_library(name: &std::ffi::OsStr) -> std::io::Result<std::path::PathBuf> {
    use std::{
        io,
        path::{Path, PathBuf},
        process::Command,
    };
    let name_path = Path::new(name);
    if name_path.components().count() != 1 || name_path.is_absolute() {
        std::fs::File::open(name_path)?;
        return Ok(name_path.to_path_buf());
    }
    // Preserve upstream's preference for an accessible current-directory file.
    let mut candidates = vec![PathBuf::from(name)];
    if let Some(paths) = std::env::var_os("LD_LIBRARY_PATH") {
        candidates.extend(std::env::split_paths(&paths).map(|p| p.join(name)));
    }
    // Honor the earlier search locations before spawning the cache reader.
    for candidate in candidates.drain(..) {
        if candidate_is_x86_64(&candidate) {
            return Ok(candidate);
        }
    }
    for ldconfig in ["/sbin/ldconfig", "/usr/sbin/ldconfig"] {
        let Ok(output) = Command::new(ldconfig).arg("-p").env("LC_ALL", "C").output() else {
            continue;
        };
        if !output.status.success() {
            continue;
        }
        for line in String::from_utf8_lossy(&output.stdout).lines() {
            if let Some((label, path)) = line.trim().split_once(" => ") {
                if label
                    .split_whitespace()
                    .next()
                    .is_some_and(|n| std::ffi::OsStr::new(n) == name)
                    && label.contains("x86-64")
                {
                    candidates.push(PathBuf::from(path));
                }
            }
        }
        break;
    }
    for directory in [
        "/lib/x86_64-linux-gnu",
        "/usr/lib/x86_64-linux-gnu",
        "/lib64",
        "/usr/lib64",
        "/lib",
        "/usr/lib",
    ] {
        candidates.push(Path::new(directory).join(name));
    }
    for candidate in candidates {
        if candidate_is_x86_64(&candidate) {
            return Ok(candidate);
        }
    }
    Err(io::Error::new(
        io::ErrorKind::NotFound,
        format!(
            "cannot resolve library {:?}; supply its explicit path",
            name
        ),
    ))
}

fn candidate_is_x86_64(candidate: &std::path::Path) -> bool {
    use std::io::Read;
    // Skip another architecture as the loader does. Resolution only reads the
    // identification bytes and never loads or executes an input library.
    let Ok(mut file) = std::fs::File::open(candidate) else {
        return false;
    };
    let mut header = [0u8; 20];
    file.read_exact(&mut header).is_ok()
        && &header[..7] == b"\x7fELF\x02\x01\x01"
        && header[18..20] == [62, 0]
}
