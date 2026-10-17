#[path = "../tests/common/mod.rs"]
mod common;
use darling_buildtools::Metadata;
use std::{hint::black_box, time::Instant};

fn main() {
    let thin = common::thin(true, false, Some([42; 16]), Some(b"libexample.so.1\0"));
    let fat = common::fat(&[thin.clone(), thin.clone()], true, true);
    for (name, bytes) in [("thin64", thin), ("fat64-2", fat)] {
        let iterations = 1_000_000;
        let start = Instant::now();
        for _ in 0..iterations {
            black_box(Metadata::parse(black_box(&bytes)).unwrap());
        }
        println!(
            "{name}: {:.1} ns/parse ({iterations} iterations)",
            start.elapsed().as_nanos() as f64 / iterations as f64
        );
    }
}
