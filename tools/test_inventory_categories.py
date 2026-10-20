"""Guard source hints without treating names as semantic implementation proof."""
import unittest

from inventory import category


class SourceCategoryTests(unittest.TestCase):
    def test_non_c_implementation_and_generator_inputs(self):
        for path in (
            "OSX/libsecurity_keychain/lib/CCallbackMgr.cp",
            "overlay/CoreFoundation.swift", "java/Consumer.java",
            "fortran/fib.f", "fortran/body.f90", "fortran/unit.F95",
            "parser/lex.l", "parser/asn1parse.y", "arch/entry.S",
        ):
            with self.subTest(path=path):
                self.assertEqual(category(path, "100644"), "source")

    def test_link_and_interface_identity_precedes_suffix(self):
        self.assertEqual(category("SDK/source.swift", "120000"), "symlink")
        self.assertEqual(category("external/project.java", "160000"), "submodule")
        self.assertEqual(category("api/server.defs", "100644"), "abi-header-or-interface")
        self.assertEqual(category("generated.h.in", "100644"), "resource-or-metadata")
        self.assertEqual(category("notice.txt", "100644"), "resource-or-metadata")


if __name__ == "__main__":
    unittest.main()
