import unittest

from otsc.context import ContextStore
from otsc.context_builder import ContextUpdate, apply_update
from otsc.delivery import prepare_response
from otsc.models import Assistance, ObservedFile, ScreenReading
from otsc.scheduler import Cancellation


def reading(raw='40  def total(values):\n41      return sum(values)', *, separator='  ', path='maths.py', blocks=None):
    return ScreenReading(visible_text=f'{path}\n{raw}', facts=[], inferred_task='', uncertainties=[], important_details=[],
                         code_blocks=blocks if blocks is not None else [{'path': path, 'raw_text': raw,
                                                                         'gutter_separator': separator}])


def source_scene(value):
    context = ContextStore()
    source = context.add('screen', value.visible_text, 'screen', reading=value)
    return context, source


def fragment(source, content='def total(values):\n    return sum(values)', *, path='maths.py', first_line=40):
    return ObservedFile(path=path, content=content, first_line=first_line, source_ids=[source.id], confidence='high')


def validate(source, item):
    return Assistance(task='', summary='', conversation=[], artifacts=[], observed_files=[item], open_questions=[]).validate_sources([source])


class SourceMappingTests(unittest.TestCase):
    def test_new_inference_requires_mapping_field_but_archived_readings_still_load(self):
        import jsonschema

        schema = ScreenReading.inference_schema()
        raw = reading().model_dump()
        jsonschema.validate(raw, schema)
        raw.pop('code_blocks')
        self.assertIsNone(ScreenReading.model_validate(raw).code_blocks)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(raw, schema)
        def strict_objects(node):
            if not isinstance(node, dict):
                return
            if node.get('type') == 'object':
                self.assertEqual(set(node['properties']), set(node['required']))
                self.assertIs(node['additionalProperties'], False)
            for value in node.values():
                for child in value if isinstance(value, list) else [value]:
                    strict_objects(child)
        strict_objects(schema)

    def test_memory_delivery_and_diff_preserve_original_code_and_line_positions(self):
        context, source = source_scene(reading())
        before = fragment(source)
        delta = ContextUpdate(upsert=[], remove=[], observed_files=[before], retire_files=[])
        changed, notes = apply_update(context, context.snapshot(), delta)
        self.assertTrue(changed)
        self.assertEqual(notes, [])
        self.assertEqual(context.files['maths.py'][0].content, before.content)
        self.assertFalse(context.verified_files)
        after = 'def total(values):\n    return sum(values) if values else None'
        raw = dict(task='Handle missing input', summary='Proposed change', conversation=[], open_questions=[],
                   observed_files=[before.model_dump()], artifacts=[dict(id='proposal', kind='code', title='Code',
                   content=after, language='python', path='maths.py', basis='observed_fragment', source_ids=[source.id],
                   annotations=[dict(line=1, explanation='Accept the input.'), dict(line=2, explanation='Keep missing separate.')],
                   nodes=[], edges=[])])
        delivered = prepare_response(raw, context.snapshot(), 'deep', Cancellation(), lambda text: None)
        self.assertEqual([a.kind for a in delivered.artifacts], ['code', 'patch'])
        patch = delivered.artifacts[1]
        self.assertEqual(patch.diff_context.before, before.content)
        self.assertEqual(patch.diff_context.after, after)
        self.assertEqual(patch.diff_context.first_line, 40)
        self.assertIn('@@ -40,2 +40,2 @@', patch.content)
        self.assertNotIn('Keep missing separate', patch.clean_text())

    def test_mutations_of_indentation_operator_path_origin_and_citations_fail(self):
        _, source = source_scene(reading())
        valid = fragment(source)
        for changes in ({'content': valid.content.replace('    return', 'return')},
                        {'content': valid.content.replace('sum', 'max')}, {'path': 'unseen.py'},
                        {'first_line': 1}, {'first_line': None}, {'source_ids': ['unknown']}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate(source, valid.model_copy(update=changes))

    def test_sub_excerpt_has_derived_offset_and_keeps_indentation(self):
        _, source = source_scene(reading())
        validate(source, fragment(source, '    return sum(values)', first_line=41))
        with self.assertRaises(ValueError):
            validate(source, fragment(source, '    return sum(values)', first_line=40))

    def test_digit_width_blank_lines_comments_and_numeric_source_remain_exact(self):
        value = reading(' 9 | # a real source comment\n10 | \n11 |     500', separator=' | ')
        _, source = source_scene(value)
        validate(source, fragment(source, '# a real source comment\n\n    500', first_line=9))
        self.assertEqual(len(value.code_blocks), 1)
        self.assertIn('11 |     500', source.text)

    def test_unnumbered_code_has_no_invented_origin_and_keeps_literal_numbers(self):
        _, source = source_scene(reading('    123\n    return 4', separator=None))
        validate(source, fragment(source, '    123\n    return 4', first_line=None))
        with self.assertRaises(ValueError):
            validate(source, fragment(source, '123\nreturn 4', first_line=None))
        with self.assertRaises(ValueError):
            validate(source, fragment(source, '    123\n    return 4', first_line=1))

    def test_logs_relative_numbers_and_gapped_regions_cannot_create_a_source_base(self):
        for raw in ('0  return 0\n1  return 1', '40  return 0\n42  return 1', '40  return 0'):
            value = reading(raw)
            self.assertEqual(value.code_blocks, [])
            self.assertTrue(value.uncertainties)
        _, source = source_scene(reading('40  status 500\n41  status 503', blocks=[]))
        with self.assertRaises(ValueError):
            validate(source, fragment(source, '40  status 500\n41  status 503'))

    def test_unquoted_and_overlapping_blocks_are_isolated_without_losing_ocr(self):
        value = reading(blocks=[{'path': 'maths.py', 'raw_text': 'invented()', 'gutter_separator': None},
                                {'path': 'maths.py', 'raw_text': '40  def total(values):\n41      return sum(values)', 'gutter_separator': '  '}])
        self.assertEqual(len(value.code_blocks), 1)
        value2 = reading(blocks=[value.code_blocks[0].model_dump(), value.code_blocks[0].model_dump()])
        self.assertEqual(value2.code_blocks, [])
        self.assertIn('return sum(values)', value2.visible_text)

    def test_distinct_panels_cannot_be_spliced_or_assigned_each_others_paths(self):
        left, right = '1  x = 1\n2  y = 2', '8  z = 3\n9  w = 4'
        value = ScreenReading(visible_text=f'a.py\n{left}\nb.py\n{right}', facts=[], inferred_task='', uncertainties=[],
                              important_details=[], code_blocks=[dict(path='a.py', raw_text=left, gutter_separator='  '),
                                                                  dict(path='b.py', raw_text=right, gutter_separator='  ')])
        _, source = source_scene(value)
        validate(source, fragment(source, 'x = 1\ny = 2', path='a.py', first_line=1))
        for content, path, first in [('z = 3\nw = 4', 'a.py', 8), ('x = 1\ny = 2\nz = 3', 'a.py', 1)]:
            with self.assertRaises(ValueError):
                validate(source, fragment(source, content, path=path, first_line=first))

    def test_spoken_sources_or_mismatched_reading_payloads_cannot_pass(self):
        _, source = source_scene(reading())
        item = fragment(source)
        with self.assertRaises(ValueError):
            validate(source.model_copy(update={'kind': 'speech'}), item)
        with self.assertRaises(ValueError):
            validate(source.model_copy(update={'text': 'maths.py\nother_code()'}), item)

    def test_historical_readings_remain_loadable_without_adding_a_mapping(self):
        old = dict(visible_text='maths.py\nreturn 1', facts=[], inferred_task='', uncertainties=[], important_details=[])
        loaded = ScreenReading.model_validate(old)
        self.assertIsNone(loaded.code_blocks)
        _, source = source_scene(loaded)
        validate(source, fragment(source, 'return 1', first_line=None))


if __name__ == '__main__':
    unittest.main()
