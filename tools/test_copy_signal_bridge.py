"""Structural tests for conservative original-source signal-hook injection."""
import unittest

from build_copy_signal_bridge import hook_function, patch_sigexc


class CopySignalBridgeTests(unittest.TestCase):
    def test_definition_only_and_async_hook_before_rpc(self):
        original = ('void test(int linux_signum, struct linux_siginfo* info, void* ctxt);\n'
                    'static void test(int linux_signum, struct linux_siginfo* info, void* ctxt) {\n'
                    'dserver_rpc_interrupt_enter();\n}\n')
        result = hook_function(original, 'test', 'linux_signum')
        self.assertEqual(result.count('dearust_copy_signal_enter'), 1)
        self.assertLess(result.index('dearust_copy_signal_enter'),
                        result.index('dserver_rpc_interrupt_enter'))
        self.assertIn(original.splitlines()[0], result)

    def test_reject_duplicate_or_missing_handler(self):
        for original in ('void test(void);', 'void test(void) {}\nvoid test(void) {}'):
            with self.assertRaises(ValueError):
                hook_function(original, 'test', 'signal')

    def test_readiness_only_after_fault_install_and_altstack(self):
        original = '''static void handle_rt_signal(int signum)
{
    rv = LINUX_SYSCALL(__NR_rt_sigaction, signum,
\t\t\t&sa, NULL,
\t\t\tsizeof(sa.sa_mask));
}
void sigexc_handler(int linux_signum, void* info, void* ctxt)
{
    status = dserver_rpc_interrupt_exit();
    rpc();
    linux_sigset_t set = sig_masks[linux_signum];
    if (!(sig_flags[linux_signum] & LINUX_SA_NODEFER)) {}
}
void sigrt_handler(int signum, void* info, void* ctxt)
{
    status = dserver_rpc_interrupt_exit();
    rpc();
}
void darling_sigexc_self(void)
{
    LINUX_SYSCALL(__NR_rt_sigaction, i,
				&sa, NULL,
				sizeof(sa.sa_mask));
    sys_sigaltstack(&newstack, NULL);
}
void unrelated_thread_setup(void) {
    sys_sigaltstack(&newstack, NULL);
}
'''
        result, declarations = patch_sigexc(original)
        self.assertNotIn('linux_sigset_t set = sig_masks[linux_signum]', result)
        self.assertIn('sigset_bsd_to_linux(&sig_masks[linux_signum], &set)', result)
        self.assertIn('set |= ctxt->uc_sigmask;', result)
        self.assertIn('sig_flags[linux_signum] & BSD_SA_NODEFER', result)
        self.assertNotIn('sig_flags[linux_signum] & LINUX_SA_NODEFER', result)
        self.assertNotIn('i == 7 || i == 11', result)
        self.assertIn('~3u, __ATOMIC_ACQ_REL', result)
        self.assertIn('== 15u', declarations)
        self.assertIn('signum == SIGNAL_SIGEXC_SUSPEND ? 4u', result)
        self.assertIn('signum == SIGNAL_S2C ? 8u', result)
        self.assertIn('if (rv >= 0)', result)
        self.assertIn('~dearust_rt_bit, __ATOMIC_ACQ_REL', result)
        self.assertIn('dearust_install_result < 0', result)
        self.assertIn('dearust_fault_handlers_ok && dearust_altstack_result == 0', result)
        self.assertEqual(result.count('dearust_copy_signal_enter'), 2)
        self.assertEqual(result.count('long dearust_signal_process = dearust_signal_process_identity();'), 2)
        self.assertEqual(result.count('(dearust_signal_process_identity() == dearust_signal_process) ? dserver_rpc_interrupt_exit() : 0;'), 2)
        self.assertEqual(result.count('sys_sigaltstack(&newstack, NULL);'), 2)
        self.assertIn('__ATOMIC_ACQUIRE', declarations)
        self.assertEqual(result.count('__ATOMIC_RELEASE'), 2)
        self.assertEqual(result.count('__ATOMIC_ACQ_REL'), 2)
        self.assertTrue(result.endswith('void unrelated_thread_setup(void) {\n'
                                        '    sys_sigaltstack(&newstack, NULL);\n}\n'))


if __name__ == '__main__':
    unittest.main()
