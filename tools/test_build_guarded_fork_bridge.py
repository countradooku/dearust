import unittest
from build_guarded_fork_bridge import patch, RAW_FORK, NATIVE_FORK


class ForkBridgeTests(unittest.TestCase):
    def test_hooks_enclose_native_fork_and_release_before_failure_handling(self):
        source = '\tret = elfcalls()->fork();\n\tif (ret < 0) {\n\t\treturn -1;\n\t}\n'
        result = patch(source)
        self.assertLess(result.index('guard_table_prefork();'), result.index('ret = elfcalls()->fork();'))
        self.assertLess(result.index('if (ret != 0) guard_table_postfork_parent();'), result.index('if (ret < 0)'))
        self.assertIn('if (ret != 0)', result)

    def test_pinned_raw_fork_and_clone_block_is_preserved(self):
        source = RAW_FORK + '\n\tif (ret < 0) ret = errno_linux_to_bsd(ret);'
        result = patch(source)
        self.assertIn(RAW_FORK, result)
        self.assertLess(result.index('guard_table_prefork();'), result.index('#ifdef SYS_fork'))
        self.assertLess(result.index('#endif'), result.index('if (ret != 0) guard_table_postfork_parent();'))
        self.assertLess(result.index('if (ret != 0)'), result.index('if (ret < 0)'))
        self.assertNotIn('elfcalls()->fork()', result)
        with self.assertRaises(ValueError):
            patch(RAW_FORK + NATIVE_FORK)

    def test_missing_or_duplicate_anchor_fails_closed(self):
        for text in ('ret = raw_fork();', '\tret = elfcalls()->fork();'*2):
            with self.assertRaises(ValueError):
                patch(text)


if __name__ == '__main__':
    unittest.main()
