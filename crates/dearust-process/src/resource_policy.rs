//! Pure XNU resource policy over authoritative owned state.
//!
//! Plans describe required effects; preparing or committing a plan does not
//! perform them. The owner must serialize preparation, enforcement and commit,
//! and must not publish a plan whose physical effects failed.
pub const INFINITY: u64 = i64::MAX as u64;
pub const POSIX: u32 = 0x1000;
pub const RESOURCE_COUNT: usize = 9;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(C)]
pub struct Limits {
    pub cur: u64,
    pub max: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(C)]
pub struct Policy {
    pub max_data: u64,
    pub max_stack: u64,
    pub max_proc: u64,
    pub max_proc_per_uid: u64,
    pub max_files_per_proc: u64,
}
impl Policy {
    /// x86 XNU defaults. Process-count policy can be scaled by the owner.
    pub const X86_4K: Self = Self {
        max_data: INFINITY,
        max_stack: 64 * 1024 * 1024 - 4096,
        max_proc: 1044,
        max_proc_per_uid: 522,
        max_files_per_proc: 10240,
    };
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(C)]
pub struct StackMetadata {
    pub top: u64,
    pub sp: u64,
    pub page_size: u64,
    pub custom: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Error {
    Invalid,
    Permission,
    Stale,
}
impl Error {
    pub const fn darwin_errno(self) -> i64 {
        match self {
            Self::Invalid => -22,
            Self::Permission => -1,
            Self::Stale => -35,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CpuEffect {
    Unchanged,
    Disable,
    /// Absolute process user+system CPU deadline, not elapsed wall time.
    DeadlineNs(u64),
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CpuAction {
    Unchanged,
    Disable,
    SignalNow,
    ArmAbsolute(u64),
}
impl CpuEffect {
    pub const fn action(self, current_cpu_ns: u64) -> CpuAction {
        match self {
            Self::Unchanged => CpuAction::Unchanged,
            Self::Disable => CpuAction::Disable,
            Self::DeadlineNs(deadline) if current_cpu_ns >= deadline => CpuAction::SignalNow,
            Self::DeadlineNs(deadline) => CpuAction::ArmAbsolute(deadline),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum StackEffect {
    Unchanged,
    Protect {
        address: u64,
        length: u64,
        accessible: bool,
    },
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct HostProjection {
    pub linux_resource: u32,
    pub soft: u64,
}
/// Physical host limits are independent constraints, never guest hard limits.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(C)]
pub struct HostEnvelope {
    pub soft: u64,
    pub hard: u64,
}
impl HostProjection {
    /// Produce a host update without raising its hard limit or hiding denial.
    pub const fn within(self, envelope: HostEnvelope) -> Result<HostEnvelope, Error> {
        if self.soft > envelope.hard {
            Err(Error::Permission)
        } else {
            Ok(HostEnvelope {
                soft: self.soft,
                hard: envelope.hard,
            })
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Plan {
    pub resource: u32,
    pub old: Limits,
    pub limits: Limits,
    pub cpu: CpuEffect,
    pub stack: StackEffect,
    pub wire_limit: Option<u64>,
    pub host: Option<HostProjection>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(C)]
pub struct State {
    pub limits: [Limits; RESOURCE_COUNT],
}
impl State {
    #[inline]
    pub fn new(limits: [Limits; RESOURCE_COUNT]) -> Result<Self, Error> {
        if limits.iter().any(|pair| pair.cur > pair.max) {
            Err(Error::Invalid)
        } else {
            Ok(Self { limits })
        }
    }
    pub fn get(&self, which: u32) -> Result<Limits, Error> {
        Ok(self.limits[index(which)?])
    }
    /// Commit only after all effects succeeded under the owner's transaction.
    pub fn commit(&mut self, plan: &Plan) -> Result<(), Error> {
        if plan.limits.cur > plan.limits.max {
            return Err(Error::Invalid);
        }
        let slot = self
            .limits
            .get_mut(plan.resource as usize)
            .ok_or(Error::Invalid)?;
        if *slot != plan.old {
            return Err(Error::Stale);
        }
        *slot = plan.limits;
        Ok(())
    }
}

#[inline]
fn index(which: u32) -> Result<usize, Error> {
    let resource = which & !POSIX;
    if resource < RESOURCE_COUNT as u32 {
        Ok(resource as usize)
    } else {
        Err(Error::Invalid)
    }
}
#[inline]
fn clamp(pair: Limits, cap: u64) -> Limits {
    Limits {
        cur: pair.cur.min(cap),
        max: pair.max.min(cap),
    }
}
#[inline]
fn rounded(value: u64, page_size: u64) -> Result<u64, Error> {
    value
        .checked_add(page_size - 1)
        .map(|v| v & !(page_size - 1))
        .ok_or(Error::Invalid)
}
#[inline]
fn stack_effect(old: Limits, new: Limits, meta: StackMetadata) -> Result<StackEffect, Error> {
    if meta.custom || !meta.page_size.is_power_of_two() {
        return Err(Error::Invalid);
    }
    if old.cur == new.cur {
        return Ok(StackEffect::Unchanged);
    }
    let old_size = rounded(old.cur, meta.page_size)?;
    let new_size = rounded(new.cur, meta.page_size)?;
    let old_low = meta.top.checked_sub(old_size).ok_or(Error::Invalid)?;
    let new_low = meta.top.checked_sub(new_size).ok_or(Error::Invalid)?;
    if new.cur < old.cur && !(meta.sp <= meta.top && meta.sp > old_low && meta.sp > new_low) {
        return Err(Error::Invalid);
    }
    if new_size == old_size {
        return Ok(StackEffect::Unchanged);
    }
    Ok(if new_size > old_size {
        StackEffect::Protect {
            address: new_low,
            length: new_size - old_size,
            accessible: true,
        }
    } else {
        // The pinned C mistakenly subtracts old_size from itself here.
        StackEffect::Protect {
            address: old_low,
            length: old_size - new_size,
            accessible: false,
        }
    })
}

/// Prepare the exact logical limits and their resource-specific effects.
/// Caller performs copyin before invoking this function and retains the prior
/// state until every side effect succeeds. Invalid selector precedes pair checks;
/// pair ordering precedes privilege checks, and privilege precedes clipping.
#[inline]
pub fn prepare_set(
    which: u32,
    requested: Limits,
    old: Limits,
    privileged: bool,
    policy: Policy,
    stack: StackMetadata,
) -> Result<Plan, Error> {
    let resource = index(which)? as u32;
    if requested.cur > requested.max {
        return Err(Error::Invalid);
    }
    if !privileged && (requested.cur > old.max || requested.max > old.max) {
        return Err(Error::Permission);
    }
    let limits = match resource {
        2 => clamp(requested, policy.max_data),
        3 => {
            if which & POSIX != 0
                && (requested.cur > policy.max_stack || requested.max > policy.max_stack)
            {
                return Err(Error::Invalid);
            }
            clamp(requested, policy.max_stack)
        }
        7 => clamp(
            requested,
            if privileged {
                policy.max_proc
            } else {
                policy.max_proc_per_uid
            },
        ),
        _ => requested,
    };
    let cpu = if resource != 0 {
        CpuEffect::Unchanged
    } else if limits.cur == INFINITY {
        CpuEffect::Disable
    } else {
        CpuEffect::DeadlineNs(limits.cur.min(i32::MAX as u64) * 1_000_000_000)
    };
    let host = match resource {
        // All accessible file offsets are below signed off_t maximum. Values
        // at or above it impose no additional reachable file-size restriction.
        1 | 4 => Some(HostProjection {
            linux_resource: resource,
            soft: if limits.cur >= INFINITY {
                u64::MAX
            } else {
                limits.cur
            },
        }),
        8 => Some(HostProjection {
            linux_resource: 7,
            soft: limits.cur.min(policy.max_files_per_proc),
        }),
        _ => None,
    };
    Ok(Plan {
        resource,
        old,
        limits,
        cpu,
        stack: if resource == 3 {
            stack_effect(old, limits, stack)?
        } else {
            StackEffect::Unchanged
        },
        wire_limit: if resource == 6 {
            Some(limits.cur)
        } else {
            None
        },
        host,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    const P: Policy = Policy::X86_4K;
    const S: StackMetadata = StackMetadata {
        top: 0x1_0000_0000,
        sp: 0xffff_f000,
        page_size: 4096,
        custom: false,
    };
    const OLD: Limits = Limits {
        cur: 8 * 1024 * 1024,
        max: INFINITY,
    };
    fn pair(value: u64) -> Limits {
        Limits {
            cur: value,
            max: value,
        }
    }
    fn plan(resource: u32, limits: Limits) -> Plan {
        prepare_set(resource, limits, OLD, true, P, S).unwrap()
    }

    #[test]
    fn validation_order_privilege_and_unsigned_values() {
        let invalid_pair = Limits { cur: 2, max: 1 };
        assert_eq!(
            prepare_set(9, invalid_pair, pair(0), false, P, S),
            Err(Error::Invalid)
        );
        assert_eq!(
            prepare_set(2, invalid_pair, pair(0), false, P, S),
            Err(Error::Invalid)
        );
        assert_eq!(
            prepare_set(2, pair(u64::MAX), pair(0), false, P, S),
            Err(Error::Permission)
        );
        for value in [0, 1, INFINITY - 1, INFINITY, INFINITY + 1, u64::MAX] {
            for resource in [0, 1, 4, 5, 6, 8] {
                assert_eq!(plan(resource, pair(value)).limits, pair(value));
            }
            assert_eq!(plan(2, pair(value)).limits, pair(value.min(INFINITY)));
            assert_eq!(plan(7, pair(value)).limits, pair(value.min(P.max_proc)));
        }
        for selector in [9, POSIX | 9, 0x2000, u32::MAX] {
            assert_eq!(
                prepare_set(selector, pair(0), OLD, true, P, S),
                Err(Error::Invalid)
            );
        }
        assert_eq!(plan(POSIX, pair(5)).resource, 0);
    }

    #[test]
    fn cpu_deadlines_are_absolute_one_shot_without_a_hard_kill_effect() {
        assert_eq!(plan(0, pair(INFINITY)).cpu.action(1), CpuAction::Disable);
        assert_eq!(plan(0, pair(0)).cpu.action(0), CpuAction::SignalNow);
        let cpu = plan(0, Limits { cur: 2, max: 3 }).cpu;
        assert_eq!(
            cpu.action(1_999_999_999),
            CpuAction::ArmAbsolute(2_000_000_000)
        );
        assert_eq!(cpu.action(2_000_000_000), CpuAction::SignalNow);
        for finite in [i32::MAX as u64, i32::MAX as u64 + 1, INFINITY + 1, u64::MAX] {
            assert_eq!(
                plan(0, pair(finite)).cpu,
                CpuEffect::DeadlineNs(i32::MAX as u64 * 1_000_000_000)
            );
        }
        assert_eq!(plan(1, pair(10)).cpu, CpuEffect::Unchanged);
    }

    #[test]
    fn stack_ranges_round_without_overflow_and_fix_upstream_shrink() {
        let old = pair(16384);
        let stack = StackMetadata {
            top: 0x1_0000,
            sp: 0xffff,
            ..S
        };
        let shrink = prepare_set(3, pair(8192), old, true, P, stack).unwrap();
        assert_eq!(
            shrink.stack,
            StackEffect::Protect {
                address: 0xc000,
                length: 8192,
                accessible: false
            }
        );
        let grow = prepare_set(3, pair(24576), old, true, P, stack).unwrap();
        assert_eq!(
            grow.stack,
            StackEffect::Protect {
                address: 0xa000,
                length: 8192,
                accessible: true
            }
        );
        let equal_pages = prepare_set(3, pair(16383), old, true, P, stack).unwrap();
        assert_eq!(equal_pages.stack, StackEffect::Unchanged);
        for sp in [0, 0xc000, 0xe000, 0x1_0001] {
            assert_eq!(
                prepare_set(3, pair(8192), old, true, P, StackMetadata { sp, ..stack }),
                Err(Error::Invalid)
            );
        }
        assert_eq!(
            prepare_set(
                3,
                pair(8192),
                old,
                true,
                P,
                StackMetadata {
                    custom: true,
                    ..stack
                }
            ),
            Err(Error::Invalid)
        );
        assert_eq!(
            prepare_set(
                3,
                old,
                old,
                true,
                P,
                StackMetadata {
                    custom: true,
                    ..stack
                }
            ),
            Err(Error::Invalid)
        );
        for page_size in [0, 3, 4095, u64::MAX] {
            assert_eq!(
                prepare_set(
                    3,
                    pair(8192),
                    old,
                    true,
                    P,
                    StackMetadata { page_size, ..stack }
                ),
                Err(Error::Invalid)
            );
        }
        let unbounded = Policy {
            max_stack: u64::MAX,
            ..P
        };
        assert_eq!(
            prepare_set(3, pair(u64::MAX), old, true, unbounded, stack),
            Err(Error::Invalid)
        );
        assert_eq!(
            prepare_set(
                3,
                pair(24576),
                old,
                true,
                P,
                StackMetadata { top: 1, ..stack }
            ),
            Err(Error::Invalid)
        );
    }

    #[test]
    fn stack_clipping_and_privilege_precede_machine_constraints() {
        let too_big = pair(P.max_stack + 1);
        assert_eq!(plan(3, too_big).limits, pair(P.max_stack));
        assert_eq!(
            prepare_set(3 | POSIX, too_big, OLD, true, P, S),
            Err(Error::Invalid)
        );
        assert_eq!(
            prepare_set(3 | POSIX, too_big, pair(0), false, P, S),
            Err(Error::Permission)
        );
        let user = prepare_set(7, pair(INFINITY), pair(INFINITY), false, P, S).unwrap();
        assert_eq!(user.limits, pair(P.max_proc_per_uid));
        assert_eq!(plan(7, pair(INFINITY)).limits, pair(P.max_proc));
    }

    #[test]
    fn host_projection_is_separate_and_never_raises_host_hard_limit() {
        for resource in [1, 4] {
            for finite in [INFINITY, INFINITY + 1, u64::MAX] {
                assert_eq!(plan(resource, pair(finite)).host.unwrap().soft, u64::MAX);
            }
            let projection = plan(resource, pair(7)).host.unwrap();
            assert_eq!(
                projection.within(HostEnvelope { soft: 2, hard: 6 }),
                Err(Error::Permission)
            );
            assert_eq!(
                projection.within(HostEnvelope { soft: 2, hard: 10 }),
                Ok(HostEnvelope { soft: 7, hard: 10 })
            );
        }
        assert_eq!(
            plan(8, pair(u64::MAX)).host,
            Some(HostProjection {
                linux_resource: 7,
                soft: P.max_files_per_proc
            })
        );
        for informational in [2, 5] {
            assert_eq!(plan(informational, pair(1)).host, None);
        }
        assert_eq!(plan(6, pair(u64::MAX)).wire_limit, Some(u64::MAX));
    }

    #[test]
    fn state_is_an_exact_fork_snapshot_and_commit_detects_stale_prior_value() {
        let mut parent = State::new([OLD; RESOURCE_COUNT]).unwrap();
        let child = parent;
        let update = plan(5, pair(u64::MAX));
        parent.commit(&update).unwrap();
        assert_eq!(parent.get(5), Ok(pair(u64::MAX)));
        assert_eq!(parent.get(5 | POSIX), Ok(pair(u64::MAX)));
        assert_eq!(child.get(5), Ok(OLD));
        assert_eq!(parent.commit(&update), Err(Error::Stale));
        assert_eq!(parent.get(9), Err(Error::Invalid));
        assert_eq!(
            State::new([Limits { cur: 1, max: 0 }; RESOURCE_COUNT]),
            Err(Error::Invalid)
        );
    }

    #[test]
    fn layouts_and_page_ranges_are_exact_across_many_stack_sizes() {
        assert_eq!(core::mem::size_of::<Limits>(), 16);
        assert_eq!(core::mem::align_of::<Limits>(), 8);
        assert_eq!(core::mem::offset_of!(Limits, max), 8);
        assert_eq!(core::mem::size_of::<State>(), 144);
        let top = 1_u64 << 40;
        let metadata = StackMetadata {
            top,
            sp: top - 8,
            ..S
        };
        for a in 1..=127_u64 {
            for b in 1..=127_u64 {
                let old_size = a * 997;
                let new_size = b * 1013;
                let old = Limits {
                    cur: old_size,
                    max: P.max_stack,
                };
                let plan = prepare_set(
                    3,
                    Limits {
                        cur: new_size,
                        max: P.max_stack,
                    },
                    old,
                    true,
                    P,
                    metadata,
                )
                .unwrap();
                let old_rounded = old_size.div_ceil(4096) * 4096;
                let new_rounded = new_size.div_ceil(4096) * 4096;
                match plan.stack {
                    StackEffect::Unchanged => assert_eq!(old_rounded, new_rounded),
                    StackEffect::Protect {
                        address,
                        length,
                        accessible,
                    } => {
                        assert_eq!(length, old_rounded.abs_diff(new_rounded));
                        assert!(length > 0 && address % 4096 == 0);
                        assert_eq!(address + length, top - old_rounded.min(new_rounded));
                        assert_eq!(address, top - old_rounded.max(new_rounded));
                        assert_eq!(accessible, new_rounded > old_rounded);
                        assert!(address + length < metadata.sp);
                    }
                }
            }
        }
    }

    #[test]
    fn broad_pairs_preserve_order_clipping_and_never_mutate_on_prepare() {
        let original = State::new([pair(u64::MAX); RESOURCE_COUNT]).unwrap();
        let mut random = 0xdead_beef_0123_4567_u64;
        for _ in 0..20000 {
            random ^= random << 13;
            random ^= random >> 7;
            random ^= random << 17;
            let a = random;
            random = random.wrapping_mul(6364136223846793005).wrapping_add(1);
            let b = random;
            let requested = Limits {
                cur: a.min(b),
                max: a.max(b),
            };
            for resource in [0, 1, 2, 4, 5, 6, 7, 8] {
                let plan = prepare_set(resource, requested, pair(u64::MAX), false, P, S).unwrap();
                assert!(plan.limits.cur <= plan.limits.max);
                if ![2, 7].contains(&resource) {
                    assert_eq!(plan.limits, requested);
                }
                let mut child = original;
                child.commit(&plan).unwrap();
                assert_eq!(child.get(resource), Ok(plan.limits));
                assert_eq!(original.get(resource), Ok(pair(u64::MAX)));
            }
        }
    }
}
