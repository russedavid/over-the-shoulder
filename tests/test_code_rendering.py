import ast
import json
import shutil
import subprocess
import unittest
from xml.etree import ElementTree

from otsc.code_rendering import artifact_document, code_document, comment_lines, commented_code, language_info
from otsc.context import ContextStore
from otsc.delivery import prepare_response
from otsc.diff_rendering import parse_diff, render_diff
from otsc.models import Artifact, Assistance, DiffContext, LineAnnotation, ObservedFile, response_schema
from otsc.scheduler import Cancellation, answer_fingerprint
from otsc.workspace import derive_patches


def annotations(code):
    return [LineAnnotation(line=i, explanation=f"Explain source line {i}.") for i, text in enumerate(code.splitlines(), 1) if text.strip()]


class CodeCommentTests(unittest.TestCase):
    def test_python_comments_are_normal_code_and_clean_source_is_unchanged(self):
        code = "def mean(values):\n    if not values:\n        return None\n    return sum(values) / len(values)\n"
        artifact = Artifact(id="code", kind="code", title="Code", content=code, language="python", path="", basis="example",
                            source_ids=["source"], annotations=annotations(code), nodes=[], edges=[])
        rendered = artifact.annotated_text()
        self.assertIn("# Explain source line 1.\ndef mean(values):", rendered)
        self.assertIn("    # Explain source line 2.\n    if not values:", rendered)
        self.assertNotIn("→", rendered)
        self.assertEqual(ast.dump(ast.parse(code)), ast.dump(ast.parse(rendered)))
        self.assertEqual(artifact.clean_text(), code)
        document = code_document(code, artifact.annotations, language="python")
        self.assertEqual([row.new_line for row in document.rows if row.kind != "comment"], [1, 2, 3, 4])
        self.assertTrue(all(row.new_line is None for row in document.rows if row.kind == "comment"))

    def test_literals_continuations_and_shebangs_keep_their_python_meaning(self):
        samples = [
            'value = """first\nsecond\nthird"""\n',
            'name = "Ada"\nvalue = f"""hi\n{name}\nthere"""\n',
            "value = 1 + \\\n    2\n",
            "#!/usr/bin/env python\n# coding: utf-8\nvalue = 1\n",
            "value = (\n    1 +\n    2\n)\n",
        ]
        for code in samples:
            with self.subTest(code=code):
                rendered = commented_code(code, annotations(code), language="python")
                self.assertEqual(ast.dump(ast.parse(code)), ast.dump(ast.parse(rendered)))
                if code.startswith("#!"):
                    self.assertTrue(rendered.startswith("#!/usr/bin/env python\n# coding: utf-8\n"))

    def test_language_specific_comment_delimiters_and_filename_detection(self):
        for language, source, expected in [
            ("typescript", "const n = 1;", "// Explain source line 1."),
            ("sql", "SELECT 1;", "-- Explain source line 1."),
            ("css", "p { color: red; }", "/* Explain source line 1. */"),
            ("html", "<p>Hello</p>", "<!-- Explain source line 1. -->"),
            ("rust", "let n = 1;", "// Explain source line 1."),
            ("yaml", "count: 1", "# Explain source line 1."),
        ]:
            with self.subTest(language=language):
                self.assertTrue(commented_code(source, annotations(source), language=language).startswith(expected))
        self.assertTrue(commented_code("x = 1", annotations("x = 1"), path="module.py").startswith("# "))

    def test_xml_declaration_and_multiline_attributes_remain_valid(self):
        code = '<?xml version="1.0"?>\n<root\n value="x">text</root>\n'
        rendered = commented_code(code, annotations(code), language="xml")
        self.assertTrue(rendered.startswith('<?xml version="1.0"?>'))
        self.assertEqual(ElementTree.fromstring(rendered).attrib, {"value": "x"})
        self.assertEqual(ElementTree.fromstring(rendered).text, "text")

    @unittest.skipUnless(shutil.which("node"), "Node syntax check for owned JS fixture")
    def test_javascript_template_literal_contents_are_preserved(self):
        code = 'const name = "Ada";\nconst text = `hi\n${name}\nthere`;\nconsole.log(JSON.stringify(text));\n'
        rendered = commented_code(code, annotations(code), language="javascript")
        result = subprocess.run(["node", "-"], input=rendered, text=True, capture_output=True, timeout=5, check=True)
        self.assertEqual(json.loads(result.stdout), "hi\nAda\nthere")

    def test_shell_heredoc_is_not_modified(self):
        code = "#!/bin/sh\ncat <<EOF\nhello\nworld\nEOF\n"
        rendered = commented_code(code, annotations(code), language="bash")
        self.assertIn("cat <<EOF\nhello\nworld\nEOF", rendered)
        self.assertTrue(rendered.startswith("#!/bin/sh\n"))

    def test_comment_escape_sequences_cannot_end_the_annotation_or_splice_a_line(self):
        self.assertEqual(comment_lines("Do */ not close", ("/*", "*/")), ["/* Do * / not close */"])
        self.assertEqual(comment_lines("A -- B", ("<!--", "-->")), ["<!-- A - - B -->"])
        self.assertTrue(comment_lines("Path ends in \\", ("//", ""))[0].endswith("\\."))

    def test_strict_json_and_unknown_language_do_not_get_invented_comment_syntax(self):
        for language in ("json", "unrecognized-language"):
            source = '{"count": 1}'
            self.assertIsNone(language_info(language)[1])
            self.assertEqual(commented_code(source, annotations(source), language=language), source)

    def test_newline_style_and_missing_final_newline_are_preserved_in_commented_copy(self):
        for source in ("x = 1", "x = 1\n", "x = 1\r\ny = 2\r\n"):
            rendered = commented_code(source, annotations(source), language="python")
            self.assertEqual(rendered.endswith("\n"), source.endswith("\n"))
            if "\r\n" in source:
                self.assertNotIn("\n", rendered.replace("\r\n", ""))


class DiffRenderingTests(unittest.TestCase):
    def test_old_and_new_line_numbers_distinguish_context_additions_and_removals(self):
        patch, document = render_diff("a\nb\nc\n", "a\nB\nextra\nc\n", path="a.py", first_line=20)
        self.assertIn("@@ -20,3 +20,4 @@", patch)
        self.assertEqual([(r.old_line, r.new_line, r.text) for r in document.rows if r.kind == "context"], [(20, 20, "a"), (22, 23, "c")])
        self.assertEqual([(r.old_line, r.new_line) for r in document.rows if r.kind == "remove"], [(21, None)])
        self.assertEqual([(r.old_line, r.new_line) for r in document.rows if r.kind == "add"], [(None, 21), (None, 22)])
        self.assertEqual((document.added, document.removed), (2, 1))

    def test_insertions_deletions_multiple_hunks_and_missing_newlines(self):
        for before, after, counts in [("", "new\n", (1, 0)), ("old\n", "", (0, 1)), ("old", "new", (1, 1)), ("a\n", "a\n\n", (1, 0))]:
            patch, document = render_diff(before, after, path="a.txt")
            self.assertEqual((document.added, document.removed), counts)
            if before == "old":
                self.assertIn("\\ No newline", patch)
        before = "".join(f"line {i}\n" for i in range(40))
        after = before.replace("line 1\n", "first edit\n").replace("line 35\n", "second edit\n")
        _, document = render_diff(before, after, path="a.txt")
        self.assertEqual(sum(row.kind == "hunk" for row in document.rows), 2)

    def test_malformed_or_wrong_file_diff_is_rejected(self):
        for text in ("--- a/x\n+++ b/x\n@@ -1,2 +1 @@\n-x\n+y\n", "--- a/wrong\n+++ b/wrong\n@@ -1 +1 @@\n-x\n+y\n"):
            with self.assertRaises(ValueError):
                parse_diff(text, path="x")

    def test_host_context_supports_current_and_proposed_code_without_corrupting_patch(self):
        before, after = "x = 1\n", "x = 2\n"
        artifact = Artifact(id="edit", kind="code", title="Edit", content=after, language="python", path="a.py", basis="verified_file",
                            source_ids=["source"], annotations=annotations(after), nodes=[], edges=[])
        response = Assistance(task="Edit", summary="Edit", artifacts=[artifact], conversation=[], observed_files=[], open_questions=[])
        result = derive_patches(response, {"a.py": before}).artifacts[0]
        self.assertEqual(result.diff_context.before, before)
        self.assertEqual(artifact_document(result, view="current").text, "x = 1")
        self.assertIn("# Explain source line 1.", artifact_document(result, view="proposed").text)
        self.assertEqual(result.clean_text(), result.content)
        self.assertNotIn("# Explain", result.content)
        self.assertEqual(ast.dump(ast.parse(result.annotated_text())), ast.dump(ast.parse(after)))
        bad = result.model_dump()
        bad["diff_context"]["before"] = "spoofed\n"
        with self.assertRaises(ValueError):
            Artifact.model_validate(bad)
        self.assertNotIn("diff_context", response_schema()["$defs"]["Artifact"]["properties"])

    def test_cached_observed_baseline_is_used_without_requiring_the_model_to_repeat_it(self):
        context = ContextStore()
        context.set_goal("Update the helper")
        source = context.add("screen", "a.py\nx = 1", "screen")
        fragment = ObservedFile(path="a.py", content="x = 1", first_line=20, source_ids=[source.id], confidence="high")
        context.files["a.py"] = [fragment]
        request = context.add("speech", "Make it two", "microphone", "primary_user")
        code = Artifact(id="edit", kind="code", title="Edit", content="x = 2", language="python", path="a.py", basis="observed_fragment",
                        source_ids=[request.id], annotations=annotations("x = 2"), nodes=[], edges=[])
        raw = Assistance(task="Edit", summary="Edit", artifacts=[code], conversation=[], observed_files=[], open_questions=[]).model_dump()
        result = prepare_response(raw, context.snapshot(), "deep", Cancellation(), lambda text: None)
        patch = next(a for a in result.artifacts if a.kind == "patch")
        self.assertEqual(patch.diff_context.before, fragment.content)
        self.assertEqual(patch.diff_context.first_line, 20)
        self.assertIn(source.id, patch.source_ids)
        self.assertEqual(patch.basis, "observed_fragment")

    def test_model_cannot_supply_its_own_diff_context_and_context_memory_omits_rendering_bytes(self):
        context = ContextStore()
        context.set_goal("Edit a.py")
        context.set_repo("/owned-fixture")
        context.set_verified_files("/owned-fixture", {"a.py": "x = 1\n"})
        code = Artifact(id="edit", kind="code", title="Edit", content="x = 2\n", language="python", path="a.py", basis="verified_file",
                        source_ids=[context.observations[0].id], annotations=annotations("x = 2\n"), nodes=[], edges=[])
        raw = Assistance(task="Edit", summary="Edit", artifacts=[code], conversation=[], observed_files=[], open_questions=[]).model_dump()
        raw["artifacts"][0]["diff_context"] = DiffContext(before="fake", after="fake").model_dump()
        result = prepare_response(raw, context.snapshot(), "deep", Cancellation(), lambda text: None)
        self.assertEqual(result.artifacts[0].diff_context.before, "x = 1\n")
        context.integrate(result)
        self.assertNotIn("diff_context", json.loads(context.previous_answer)["artifacts"][0])
        self.assertEqual(answer_fingerprint(result), answer_fingerprint(json.loads(context.previous_answer)))
