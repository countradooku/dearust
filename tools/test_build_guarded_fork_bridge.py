import unittest
from build_guarded_fork_bridge import patch


class ForkBridgeTests(unittest.TestCase):
    def test_hooks_enclose_native_fork_and_release_before_failure_handling(self):
        source = '\tret = elfcalls()->fork();\n\tif (ret < 0) {\n\t\treturn -1;\n\t}\n'
        result = patch(source)
        self.assertLess(result.index('guard_table_prefork();'), result.index('ret = elfcalls()->fork();'))
        self.assertLess(result.index('if (ret != 0) guard_table_postfork_parent();'), result.index('if (ret < 0)'))
        self.assertIn('if (ret != 0)', result)

    def test_missing_or_duplicate_anchor_fails_closed(self):
        for text in ('ret = raw_fork();', '\tret = elfcalls()->fork();'*2):
            with self.assertRaises(ValueError):
                patch(text)


if __name__ == '__main__':
    unittest.main()
