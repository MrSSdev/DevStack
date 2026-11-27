"""Fast checks of packaging metadata; no packages or network required."""
import unittest
from bubblewrap_backport_compat import adapt_control


SOURCE = """Source: bubblewrap
Section: admin
Build-Depends: debhelper-compat (= 14),
 meson,
 libcap-dev
Rules-Requires-Root: no
"""
BINARY = """Package: bubblewrap
Architecture: linux-any
Description: fixture sandbox
 Long description.
"""


class BackportCompatTests(unittest.TestCase):
    def test_missing_dependency_fields(self):
        result = adapt_control(SOURCE + '\n' + BINARY)
        self.assertIn('Build-Depends: debhelper-compat (= 13),\n meson,\n libcap-dev', result)
        self.assertIn('Depends: ${shlibs:Depends}, ${misc:Depends}\n', result)
        self.assertIn('Pre-Depends: ${misc:Pre-Depends}\n', result)
        self.assertIn('Rules-Requires-Root: no', result)
        self.assertIn('Description: fixture sandbox\n Long description.', result)

    def test_preserve_existing_fields_and_multiline_dependencies(self):
        original = SOURCE + '\n' + BINARY.replace(
            'Description:', 'Depends: libcap2,\n ${shlibs:Depends}\n'
            'Pre-Depends: example-predependency\nDescription:')
        result = adapt_control(original)
        self.assertIn('Depends: ${misc:Depends}, libcap2,\n ${shlibs:Depends}\n', result)
        self.assertIn('Pre-Depends: ${misc:Pre-Depends}, example-predependency\n', result)
        self.assertEqual(result.count('${shlibs:Depends}'), 1)

    def test_existing_substvars_not_duplicated(self):
        original = SOURCE + '\n' + BINARY.replace(
            'Description:', 'Depends: ${shlibs:Depends}, ${misc:Depends}\n'
            'Pre-Depends: ${misc:Pre-Depends}\nDescription:')
        self.assertEqual(adapt_control(original), original.replace('= 14', '= 13'))

    def test_unexpected_packaging_is_refused(self):
        originals = [
            SOURCE.replace('= 14', '= 13') + '\n' + BINARY,
            SOURCE + '\n' + BINARY.replace('Package: bubblewrap', 'Package: other'),
            SOURCE + '\n' + BINARY + '\nPackage: extra\nArchitecture: all\n',
            SOURCE + '\n' + BINARY.replace('Description:', 'Depends: a\nDepends: b\nDescription:'),
        ]
        for original in originals:
            with self.subTest(original=original):
                with self.assertRaises(ValueError):
                    adapt_control(original)


if __name__ == '__main__':
    unittest.main()
