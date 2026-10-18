"""Structural gates for the copied RPC transaction boundary."""
import unittest
from build_rpc_fork_bridge import patch_rpc

WRAPPER = '''int dserver_rpc_explicit_test(int server_socket) {
    char controlbuf[DSERVER_RPC_HOOKS_CMSG_SPACE(sizeof(fds))];
    long int long_status;
retry_send:
    long_status = dserver_rpc_hooks_send_message(server_socket, &callmsg);
retry_receive:
    long_status = dserver_rpc_hooks_receive_message(server_socket, &replymsg);
    return long_status;
};
'''


class RpcForkBridgeTests(unittest.TestCase):
    def test_transaction_cancellation_preserves_completed_responses(self):
        result, count = patch_rpc('\n' + WRAPPER)
        self.assertEqual(count, 1)
        self.assertIn('dearust_generation = dearust_rpc_current_generation()', result)
        self.assertIn('(long_status < 0 && dearust_rpc_abandoned(dearust_generation))', result)
        self.assertIn('dearust_rpc_discard_rights(&replymsg, sizeof(controlbuf))', result)
        self.assertIn('replymsg.msg_controllen = sizeof(controlbuf)', result)
        self.assertLess(result.index('dearust_rpc_current_generation'), result.index('retry_send:'))

    def test_atomic_calls_restore_the_existing_mask_on_both_cancellation_paths(self):
        original = '\n' + WRAPPER.replace('    long int long_status;',
            '    dserver_rpc_hooks_atomic_save_t atomic_save;\n    long int long_status;')
        result, _ = patch_rpc(original)
        self.assertEqual(result.count('dserver_rpc_hooks_atomic_end(&atomic_save)'), 2)

    def test_reject_missing_boundaries_and_noncanonical_generated_functions(self):
        for text in ('\n' + WRAPPER.replace('long_status = dserver_rpc_hooks_receive_message(server_socket, &replymsg);',''),
                     '\n' + WRAPPER.replace('int dserver_rpc_explicit_test(int server_socket) {',
                                             'int dserver_rpc_explicit_test(int server_socket)\n{')):
            with self.assertRaises(ValueError):
                patch_rpc(text)


if __name__ == '__main__':
    unittest.main()
