import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('support', Path(__file__).parents[1] / 'experiment4_progressive_support.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class SupportTests(unittest.TestCase):
    def test_dynamic_contract(self):
        raw = {'edge_index': {'kind': 'tensor', 'trailing_shape': [123]},
               'io_pin_directions': {'required_value': ['INPUT']}}
        new = m.normalize_contract(raw)
        self.assertEqual(new['edge_index']['shape'], [2, 'E'])
        self.assertNotIn('required_value', new['io_pin_directions'])
        self.assertEqual(raw['edge_index']['trailing_shape'], [123])

    def test_incremental(self):
        self.assertEqual(m.source_response(json.dumps({'edits': [{'old': 'x = 1', 'new': 'x = 2'}]}), 'x = 1\n'), 'x = 2\n')

    def test_atomic_reject(self):
        with self.assertRaises(ValueError):
            m.source_response(json.dumps({'edits': [{'old': '1', 'new': '2'}, {'old': 'missing', 'new': '3'}]}), 'x = 1')

    def test_ambiguous_reject(self):
        with self.assertRaises(ValueError):
            m.source_response(json.dumps({'python_source': 'x=1', 'edits': []}), '')

    def test_empty_graph_cannot_gain_coverage(self):
        f = m.semantic_feedback([{'verified_core_status': 'FAIL', 'checks': [
            {'stage': 'route', 'group': 'identity', 'check': 'gate', 'status': 'FAIL',
             'evidence': {'tp': 0, 'fp': 0, 'fn': 211, 'f1': 0}},
            {'stage': 'route', 'group': 'mask', 'check': 'gate', 'status': 'PASS', 'evidence': {}}]}])
        self.assertEqual(m.rank(f, 1), (0, 0, 0, 0, 0, 1))

    def test_error_not_model_failure(self):
        with self.assertRaises(RuntimeError):
            m.semantic_feedback([{'status': 'ORACLE_ERROR'}])

    def test_missing_label_counts(self):
        f = m.semantic_feedback([{'checks': [{'stage': 'route', 'group': 'label', 'check': 'cap',
            'status': 'FAIL', 'evidence': {'expected': 10, 'matched': 6, 'incorrect': 1}}]}])
        self.assertEqual(f['coverage']['label'], 0.5)

    def test_real_entities_beat_template(self):
        f = m.semantic_feedback([{'checks': [{'stage': 'route', 'group': 'identity', 'check': 'gate',
            'status': 'PASS', 'evidence': {'tp': 200, 'fp': 0, 'fn': 0, 'f1': 1}}]}])
        empty = m.semantic_feedback([{'checks': []}])
        self.assertGreater(m.rank(f, 0.1), m.rank(empty, 1.0))


if __name__ == '__main__':
    unittest.main()
