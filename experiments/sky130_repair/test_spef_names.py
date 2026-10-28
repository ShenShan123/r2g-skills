import unittest
from normalize_spef_names import normalize, decode


class SpefNamesTest(unittest.TestCase):
    def test_negative_index_preserves_connectivity_and_values(self):
        original = ("*NAME_MAP\n*119 cfgfb[\\-1]\n*120 cfgfb[0]\n"
                    "*PORTS\ncfgfb[\\-1] I\n*D_NET *119 0.125\n"
                    "*CONN\n*P cfgfb[\\-1] I\n*CAP\n1 cfgfb[\\-1] 0.125\n"
                    "*RES\n1 cfgfb[\\-1] *119:1 42.5\n*END\n")
        repaired, mappings = normalize(original)
        self.assertEqual(mappings, {r'cfgfb[\-1]': r'cfgfb\[\-1\]'})
        self.assertEqual([decode(t) for t in original.split()], [decode(t) for t in repaired.split()])
        self.assertIn('*120 cfgfb[0]', repaired)
        self.assertIn('0.125', repaired)
        self.assertIn('42.5', repaired)
        self.assertEqual(normalize(repaired), (repaired, {}))

    def test_positive_names_and_metadata_are_unchanged(self):
        text = '*DESIGN "bus[-1]"\n*NAME_MAP\n*1 a[1]\n*2 normal\n*PORTS\na[1] I\n'
        self.assertEqual(normalize(text), (text, {}))


if __name__ == '__main__':
    unittest.main()
