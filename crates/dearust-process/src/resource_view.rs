//! Coupled client view of an authoritative resource owner.
//!
//! This contains no process identity or Linux-limit decoding. The integration
//! must initialize from its owner before exposing an API, hold a transaction
//! across mutation RPC and publication, serialize fork with those transactions,
//! and reload after exec. Callers must prevent signal-handler reentry while a
//! transaction is held, and must not abandon a guard through foreign cancellation,
//! thread exit or longjmp (which do not run Rust Drop). A guard reserves one local
//! publication before any mutation RPC and is consumed by publication. All owner
//! mutations must use this same transaction domain; gaps require a full snapshot
//! refresh, not a partial reply. This module alone supplies no lifecycle hooks.
use crate::resource_policy::{Limits, Policy, RESOURCE_COUNT, State};
use core::marker::PhantomData;
use core::sync::atomic::{AtomicBool, AtomicU64, Ordering, fence};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Snapshot {
    pub generation: u64,
    pub state: State,
    pub stack_top: u64,
    pub custom_stack: bool,
    pub policy: Policy,
}

impl Snapshot {
    #[inline]
    pub fn decode(words: [u64; 26]) -> Option<Self> {
        if words[0] == 0 || words[20] > 1 {
            return None;
        }
        let limits = core::array::from_fn(|index| Limits {
            cur: words[1 + 2 * index],
            max: words[2 + 2 * index],
        });
        Some(Self {
            generation: words[0],
            state: State::new(limits).ok()?,
            stack_top: words[19],
            custom_stack: words[20] != 0,
            policy: Policy {
                max_data: words[21],
                max_stack: words[22],
                max_proc: words[23],
                max_proc_per_uid: words[24],
                max_files_per_proc: words[25],
            },
        })
    }
    #[inline]
    pub fn encode(self) -> [u64; 26] {
        let mut words = [0; 26];
        words[0] = self.generation;
        for (index, pair) in self.state.limits.iter().enumerate() {
            words[1 + 2 * index] = pair.cur;
            words[2 + 2 * index] = pair.max;
        }
        words[19] = self.stack_top;
        words[20] = u64::from(self.custom_stack);
        words[21..].copy_from_slice(&[
            self.policy.max_data,
            self.policy.max_stack,
            self.policy.max_proc,
            self.policy.max_proc_per_uid,
            self.policy.max_files_per_proc,
        ]);
        words
    }
}

pub struct View {
    writer: AtomicBool,
    sequence: AtomicU64,
    words: [AtomicU64; 26],
}

impl Default for View {
    #[inline]
    fn default() -> Self {
        Self::new()
    }
}

impl View {
    #[inline]
    pub const fn new() -> Self {
        Self {
            writer: AtomicBool::new(false),
            sequence: AtomicU64::new(0),
            words: [const { AtomicU64::new(0) }; 26],
        }
    }
    /// Reserve one publication BEFORE issuing a mutation RPC. Busy means another
    /// mutation/fork holds the guard; a native wait may retry without retaining
    /// any guard. Exhausted is terminal for mutations on this view: the caller
    /// must return an error without changing the owner. Reads remain available.
    #[inline]
    pub fn try_transaction(&self) -> Result<Transaction<'_>, BeginError> {
        self.writer
            .compare_exchange(false, true, Ordering::Acquire, Ordering::Relaxed)
            .map_err(|_| BeginError::Busy)?;
        // The preceding writer releases this same lock only after publication.
        // Therefore these relaxed loads see its completed even sequence/state.
        let sequence = self.sequence.load(Ordering::Relaxed);
        let generation = self.words[0].load(Ordering::Relaxed);
        let Some(next_sequence) = sequence.checked_add(2) else {
            self.writer.store(false, Ordering::Release);
            return Err(BeginError::Exhausted);
        };
        if generation == u64::MAX {
            self.writer.store(false, Ordering::Release);
            return Err(BeginError::Exhausted);
        }
        Ok(Transaction {
            _same_thread: PhantomData,
            view: self,
            sequence,
            next_sequence,
            generation,
        })
    }
    #[inline]
    fn read<const N: usize>(&self, indexes: [usize; N]) -> [u64; N] {
        loop {
            let before = self.sequence.load(Ordering::Acquire);
            if before & 1 != 0 {
                core::hint::spin_loop();
                continue;
            }
            let values = indexes.map(|index| self.words[index].load(Ordering::Relaxed));
            // If any relaxed word load observes an in-progress publication,
            // this acquire fence synchronizes with its preceding release fence.
            // The odd sequence then happens-before the second sequence load,
            // so atomic coherence prevents accepting the earlier even sequence.
            fence(Ordering::Acquire);
            if before == self.sequence.load(Ordering::Relaxed) {
                return values;
            }
        }
    }
    #[inline]
    pub fn snapshot(&self) -> Option<Snapshot> {
        Snapshot::decode(self.read(core::array::from_fn(|index| index)))
    }
    /// A single coherent pair with its owner generation; no RPC or host query.
    #[inline]
    pub fn get(&self, which: u32) -> Option<(u64, Limits)> {
        let index = (which & !crate::resource_policy::POSIX) as usize;
        if index >= RESOURCE_COUNT {
            return None;
        }
        let [generation, cur, max] = self.read([0, 1 + 2 * index, 2 + 2 * index]);
        (generation != 0).then_some((generation, Limits { cur, max }))
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BeginError {
    Busy,
    Exhausted,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PublishError {
    Invalid,
    Stale,
}

/// Own this guard across exactly one owner mutation and matching publication.
/// Publication consumes the guard, preventing a second RPC from reusing its
/// preflighted capacity. Ordinary errors/unwinding release the writer lock;
/// integrations must also handle foreign exits/cancellation that skip Rust Drop.
///
/// ```compile_fail,E0382
/// use dearust_process::resource_view::{Snapshot, Transaction};
/// fn cannot_publish_twice(transaction: Transaction<'_>, snapshot: Snapshot) {
///     transaction.initialize(snapshot).unwrap();
///     transaction.refresh(snapshot).unwrap();
/// }
/// ```
///
/// ```compile_fail,E0277
/// use dearust_process::resource_view::View;
/// let view = View::new();
/// std::thread::scope(|scope| {
///     let transaction = view.try_transaction().unwrap();
///     scope.spawn(move || drop(transaction));
/// });
/// ```
///
/// ```compile_fail,E0277
/// use dearust_process::resource_view::View;
/// let view = View::new();
/// std::thread::scope(|scope| {
///     let transaction = view.try_transaction().unwrap();
///     scope.spawn(|| { let _ = &transaction; });
/// });
/// ```
pub struct Transaction<'a> {
    // The caller's blocked mask and per-thread RPC endpoint belong to this same
    // host thread. A guard cannot migrate or be shared with another thread.
    _same_thread: PhantomData<*mut ()>,
    view: &'a View,
    sequence: u64,
    next_sequence: u64,
    generation: u64,
}

impl Transaction<'_> {
    #[inline]
    pub fn initialize(self, snapshot: Snapshot) -> Result<(), PublishError> {
        if self.generation != 0 {
            return Err(PublishError::Stale);
        }
        let words = snapshot.encode();
        Snapshot::decode(words).ok_or(PublishError::Invalid)?;
        self.publish(words);
        Ok(())
    }
    /// Replace all26 words from the SAME authoritative owner while its lifecycle
    /// identity/transaction is held. Accept equal/newer generations, never an
    /// older snapshot. This contains no PID cache or owner-identity validation.
    #[inline]
    pub fn refresh(self, snapshot: Snapshot) -> Result<(), PublishError> {
        let words = snapshot.encode();
        Snapshot::decode(words).ok_or(PublishError::Invalid)?;
        if self.generation == 0 {
            return Err(PublishError::Invalid);
        }
        if snapshot.generation < self.generation {
            return Err(PublishError::Stale);
        }
        self.publish(words);
        Ok(())
    }
    #[inline]
    pub fn commit(self, which: u32, generation: u64, limits: Limits) -> Result<(), PublishError> {
        let index = (which & !crate::resource_policy::POSIX) as usize;
        if index >= RESOURCE_COUNT || limits.cur > limits.max {
            return Err(PublishError::Invalid);
        }
        let mut snapshot = self.view.snapshot().ok_or(PublishError::Invalid)?;
        // Begin preflight excluded u64::MAX while holding the same writer lock.
        let expected = self.generation + 1;
        // A partial update cannot skip an owner mutation to another resource.
        // Gaps require a complete refresh under the owner's lifecycle identity.
        if generation != expected {
            return Err(PublishError::Stale);
        }
        snapshot.generation = generation;
        snapshot.state.limits[index] = limits;
        self.publish(snapshot.encode());
        Ok(())
    }
    #[inline]
    fn publish(self, words: [u64; 26]) {
        // Capacity was reserved before the caller could mutate the owner. This
        // consuming operation performs no fallible work after marking odd.
        self.view
            .sequence
            .store(self.sequence + 1, Ordering::Relaxed);
        // Pair with the reader's acquire fence through any observed word store;
        // this prevents a mixed snapshot passing an unchanged old sequence.
        fence(Ordering::Release);
        for (slot, value) in self.view.words.iter().zip(words) {
            slot.store(value, Ordering::Relaxed);
        }
        // A reader's initial acquire load of this even value also publishes all
        // preceding words. Sequence never wraps, so an old reader cannot see ABA.
        self.view
            .sequence
            .store(self.next_sequence, Ordering::Release);
    }
}

impl Drop for Transaction<'_> {
    #[inline]
    fn drop(&mut self) {
        self.view.writer.store(false, Ordering::Release);
    }
}

#[cfg(test)]
mod tests {
    extern crate std;
    use super::*;
    use crate::resource_policy::INFINITY;
    use std::{sync::Arc, thread};

    fn initial() -> Snapshot {
        Snapshot {
            generation: 1,
            state: State::new(
                [Limits {
                    cur: 0,
                    max: INFINITY,
                }; 9],
            )
            .unwrap(),
            stack_top: 1 << 40,
            custom_stack: false,
            policy: Policy::X86_4K,
        }
    }

    #[test]
    fn shared_view_remains_send_sync_while_guards_stay_on_the_owner_thread() {
        fn assert_send_sync<T: Send + Sync>() {}
        assert_send_sync::<View>();
    }

    #[test]
    fn exact_snapshot_unsigned_pairs_and_transaction_failures() {
        let view = View::new();
        assert_eq!(view.get(0), None);
        let transaction = view.try_transaction().unwrap();
        assert_eq!(view.try_transaction().err(), Some(BeginError::Busy));
        transaction.initialize(initial()).unwrap();
        assert_eq!(
            view.try_transaction().unwrap().initialize(initial()),
            Err(PublishError::Stale)
        );
        view.try_transaction()
            .unwrap()
            .commit(
                5,
                2,
                Limits {
                    cur: INFINITY + 1,
                    max: u64::MAX,
                },
            )
            .unwrap();
        let prior = view.snapshot().unwrap();
        assert_eq!(Snapshot::decode(prior.encode()), Some(prior));
        assert_eq!(view.get(5 | 0x1000), Some((2, prior.state.limits[5])));
        for generation in [0, 1, 2, 4, u64::MAX] {
            assert_eq!(
                view.try_transaction()
                    .unwrap()
                    .commit(5, generation, Limits { cur: 0, max: 0 }),
                Err(PublishError::Stale)
            );
        }
        assert_eq!(
            view.try_transaction()
                .unwrap()
                .commit(5, 3, Limits { cur: 1, max: 0 }),
            Err(PublishError::Invalid)
        );
        for selector in [9, 0x1009, 0x2000, u32::MAX] {
            assert_eq!(view.get(selector), None);
            assert_eq!(
                view.try_transaction()
                    .unwrap()
                    .commit(selector, 3, Limits { cur: 0, max: 0 }),
                Err(PublishError::Invalid)
            );
        }
        assert_eq!(view.snapshot(), Some(prior));
        assert!(view.try_transaction().is_ok());
    }

    #[test]
    fn all26_snapshot_words_roundtrip_and_malformed_inputs_leave_view_empty() {
        let mut words = initial().encode();
        words[0] = u64::MAX - 1;
        for index in 0..RESOURCE_COUNT {
            words[1 + index * 2] = INFINITY + index as u64;
            words[2 + index * 2] = u64::MAX - index as u64;
        }
        words[19] = 0xfedc_ba98_7654_3210;
        words[20] = 1;
        for (index, word) in words[21..].iter_mut().enumerate() {
            *word = INFINITY + 17 + index as u64;
        }
        let snapshot = Snapshot::decode(words).unwrap();
        assert_eq!(snapshot.encode(), words);
        let view = View::new();
        view.try_transaction()
            .unwrap()
            .initialize(snapshot)
            .unwrap();
        assert_eq!(view.snapshot(), Some(snapshot));
        for at in [0, 20, 1] {
            let mut bad = words;
            bad[at] = match at {
                0 => 0,
                20 => 2,
                _ => u64::MAX,
            };
            if at == 1 {
                bad[2] = 0;
            }
            assert!(Snapshot::decode(bad).is_none());
        }
        let empty = View::new();
        let mut invalid = initial();
        invalid.generation = 0;
        assert_eq!(
            empty.try_transaction().unwrap().initialize(invalid),
            Err(PublishError::Invalid)
        );
        assert_eq!(empty.snapshot(), None);
        assert!(empty.try_transaction().is_ok());
    }

    #[test]
    fn generation_gaps_require_complete_refresh_and_reordered_replies_cannot_regress() {
        let view = View::new();
        view.try_transaction()
            .unwrap()
            .initialize(initial())
            .unwrap();
        let mut owner = initial();
        owner.generation = 3;
        owner.state.limits[0] = Limits { cur: 10, max: 100 };
        owner.state.limits[1] = Limits { cur: 20, max: 100 };
        owner.stack_top += 4096;
        owner.custom_stack = true;
        owner.policy.max_data = u64::MAX;
        assert_eq!(
            view.try_transaction()
                .unwrap()
                .commit(1, 3, owner.state.limits[1]),
            Err(PublishError::Stale)
        );
        assert_eq!(view.snapshot(), Some(initial()));
        view.try_transaction().unwrap().refresh(owner).unwrap();
        assert_eq!(view.snapshot(), Some(owner));
        assert_eq!(
            view.try_transaction()
                .unwrap()
                .commit(0, 2, owner.state.limits[0]),
            Err(PublishError::Stale)
        );
        assert_eq!(
            view.try_transaction().unwrap().refresh(initial()),
            Err(PublishError::Stale)
        );
        assert_eq!(view.snapshot(), Some(owner));
        view.try_transaction().unwrap().refresh(owner).unwrap();
        assert_eq!(
            View::new().try_transaction().unwrap().refresh(owner),
            Err(PublishError::Invalid)
        );
        let mut bad = owner;
        bad.state.limits[0] = Limits { cur: 2, max: 1 };
        assert_eq!(
            view.try_transaction().unwrap().refresh(bad),
            Err(PublishError::Invalid)
        );
        assert_eq!(view.snapshot(), Some(owner));
    }

    #[test]
    fn preflight_prevents_owner_mutation_after_sequence_or_generation_exhaustion() {
        let view = View::new();
        view.try_transaction()
            .unwrap()
            .initialize(initial())
            .unwrap();
        view.sequence.store(u64::MAX - 3, Ordering::Relaxed);
        let transaction = view.try_transaction().unwrap();
        transaction.commit(0, 2, Limits { cur: 1, max: 2 }).unwrap();
        assert_eq!(view.sequence.load(Ordering::Relaxed), u64::MAX - 1);
        let mut owner_mutations = 0;
        if let Ok(transaction) = view.try_transaction() {
            owner_mutations += 1;
            drop(transaction);
        }
        assert_eq!(owner_mutations, 0);
        assert_eq!(view.try_transaction().err(), Some(BeginError::Exhausted));
        assert!(!view.writer.load(Ordering::Relaxed));
        assert_eq!(view.get(0), Some((2, Limits { cur: 1, max: 2 })));
        let view = View::new();
        let mut snapshot = initial();
        snapshot.generation = u64::MAX - 1;
        view.try_transaction()
            .unwrap()
            .initialize(snapshot)
            .unwrap();
        view.try_transaction()
            .unwrap()
            .commit(0, u64::MAX, Limits { cur: 1, max: 2 })
            .unwrap();
        assert_eq!(view.try_transaction().err(), Some(BeginError::Exhausted));
        assert!(!view.writer.load(Ordering::Relaxed));
        assert_eq!(view.get(0), Some((u64::MAX, Limits { cur: 1, max: 2 })));
    }

    #[test]
    fn abandoning_unpublished_or_unwinding_transactions_releases_writer() {
        let view = View::new();
        drop(view.try_transaction().unwrap());
        view.try_transaction()
            .unwrap()
            .initialize(initial())
            .unwrap();
        let result = std::panic::catch_unwind(|| {
            let _transaction = view.try_transaction().unwrap();
            panic!("test unwind before RPC/publication");
        });
        assert!(result.is_err());
        assert_eq!(view.snapshot(), Some(initial()));
        assert!(view.try_transaction().is_ok());
    }

    #[test]
    fn concurrent_readers_never_observe_torn_pairs_or_generations() {
        let view = Arc::new(View::new());
        view.try_transaction()
            .unwrap()
            .initialize(initial())
            .unwrap();
        let done = Arc::new(AtomicBool::new(false));
        thread::scope(|scope| {
            for _ in 0..4 {
                let (view, done) = (view.clone(), done.clone());
                scope.spawn(move || {
                    while !done.load(Ordering::Acquire) {
                        let (generation, pair) = view.get(5).unwrap();
                        if generation > 1 {
                            assert_eq!(pair.cur, generation);
                            assert_eq!(pair.max, u64::MAX - generation);
                        }
                    }
                });
            }
            for generation in 2..20_000 {
                view.try_transaction()
                    .unwrap()
                    .commit(
                        5,
                        generation,
                        Limits {
                            cur: generation,
                            max: u64::MAX - generation,
                        },
                    )
                    .unwrap();
            }
            done.store(true, Ordering::Release);
        });
        let inherited = View::new();
        inherited
            .try_transaction()
            .unwrap()
            .initialize(view.snapshot().unwrap())
            .unwrap();
        view.try_transaction()
            .unwrap()
            .commit(5, 20_000, Limits { cur: 0, max: 0 })
            .unwrap();
        assert_eq!(inherited.get(5).unwrap().0, 19_999);
    }
}
