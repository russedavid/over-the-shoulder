import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from otsc.context import ContextStore
from otsc.models import Artifact, Assistance, ConversationResponse, Observation
from otsc.output_browser import DETAILS, GUIDANCE, REPLIES, BrowserState, OutputBrowser, artifact_key
from otsc.output_history import MAX_OUTPUTS, OutputHistory
from otsc.sessions import checkpoint, load_session, restore_context, save_session


def artifact(identity, content, kind="explanation"):
    return Artifact(id=identity, title=identity.replace("_", " ").title(), kind=kind, content=content, language="", path="",
                    basis="discussion", source_ids=["source"], annotations=[], nodes=[], edges=[])


def answer(*artifacts, summary="Internal task summary", conversation=()):
    return Assistance(task="Task inference", summary=summary, artifacts=list(artifacts), conversation=list(conversation),
                      observed_files=[], open_questions=[])


class OutputBrowserTests(unittest.TestCase):
    def setUp(self):
        self.history = OutputHistory()
        self.browser = OutputBrowser()

    def publish(self, response, lane="deep", sources=()):
        entry = self.history.append(response, session_id="session", goal="task", lane=lane, sources=sources)
        self.browser.ingest(entry)
        return entry

    def test_default_rows_include_actionable_sections_and_hide_internal_plan_and_summary(self):
        self.publish(answer(artifact("code", "Do the work"), artifact("_assistance_plan", '{"reason":"planning metadata"}', "structured")))
        self.assertEqual([row[0] for row in self.browser.rows()], [artifact_key("code")])
        self.assertIn(DETAILS, [row[0] for row in self.browser.rows(True)])
        self.assertIn(artifact_key("_assistance_plan"), [row[0] for row in self.browser.rows(True)])

    def test_back_and_forward_only_visit_selected_types_changed_versions(self):
        code1, note1 = artifact("code", "version 1"), artifact("instructions", "note 1")
        self.publish(answer(code1, note1))
        self.browser.select_type(artifact_key("code"))
        self.publish(answer(code1, artifact("instructions", "note 2")))
        self.publish(answer(artifact("code", "version 2"), artifact("instructions", "note 2")))
        self.publish(answer(artifact("code", "version 3"), artifact("instructions", "note 3")))
        self.assertEqual(self.browser.newer_count, 2)
        self.assertTrue(self.browser.move(1))
        self.assertEqual(self.browser.visible.artifact.content, "version 2")
        self.assertEqual(self.browser.newer_count, 1)
        self.assertTrue(self.browser.move(1))
        self.assertTrue(self.browser.frozen)
        self.assertFalse(self.browser.move(1))
        self.assertTrue(self.browser.move(-1))
        self.assertEqual(self.browser.visible.artifact.id, "code")

    def test_each_type_remembers_its_own_position_and_unseen_count(self):
        self.publish(answer(artifact("code", "c1"), artifact("questions", "q1")))
        code, questions = artifact_key("code"), artifact_key("questions")
        self.assertEqual(self.browser.state.types[questions].newer_count, 1)
        self.browser.select_type(questions)
        self.assertEqual(self.browser.newer_count, 0)
        self.publish(answer(artifact("code", "c2"), artifact("questions", "q2")))
        self.assertEqual(self.browser.state.types[code].newer_count, 1)
        self.assertEqual(self.browser.newer_count, 1)
        self.browser.select_type(code)
        self.assertEqual(self.browser.visible.artifact.content, "c1")
        self.browser.resume()
        self.assertEqual(self.browser.visible.artifact.content, "c2")
        self.assertFalse(self.browser.frozen)
        self.assertEqual(self.browser.state.types[questions].newer_count, 1)
        self.browser.select_type(questions)
        self.assertEqual(self.browser.visible.artifact.content, "q1")

    def test_active_live_type_advances_without_marking_inactive_types_read(self):
        self.publish(answer(artifact("code", "c1"), artifact("notes", "n1")))
        self.publish(answer(artifact("code", "c2"), artifact("notes", "n2")))
        self.assertEqual(self.browser.visible.artifact.content, "c2")
        self.assertEqual(self.browser.newer_count, 0)
        self.assertEqual(self.browser.state.types[artifact_key("notes")].newer_count, 2)

    def test_citation_title_and_inherited_artifact_changes_do_not_inflate_badges(self):
        first = artifact("code", "keep these bytes")
        self.publish(answer(first))
        second = first.model_copy(update={"source_ids": ["different-id"], "title": "Reworded heading"})
        self.publish(answer(second, summary="Different internal summary"))
        self.publish(answer(summary="Quick useful instruction"), lane="quick")
        self.assertEqual(len(self.browser.state.types[artifact_key("code")].versions), 1)
        self.assertEqual(self.browser.state.types[artifact_key("code")].label, "Reworded heading")

    def test_initial_quick_hands_off_to_first_deliverable_unless_user_holds_it(self):
        self.publish(answer(summary="Check the empty-input guard"), lane="quick")
        self.assertEqual(self.browser.active_key, GUIDANCE)
        self.publish(answer(artifact("code", "a real deliverable")))
        self.assertEqual(self.browser.active_key, artifact_key("code"))
        self.browser.clear()
        self.history.clear()
        self.publish(answer(summary="Read this first"), lane="quick")
        self.browser.freeze()
        self.publish(answer(artifact("code", "new code")))
        self.assertEqual(self.browser.active_key, GUIDANCE)
        self.assertEqual(self.browser.state.types[artifact_key("code")].newer_count, 1)

    def test_image_counts_only_completed_versions_and_other_type_cursors_stay_put(self):
        code = artifact("code", "stable")
        pending = artifact("drawing", json.dumps({"status": "pending", "prompt": "Draw it"}), "image")
        self.publish(answer(code, pending))
        self.browser.select_type(artifact_key("code"))
        held = self.browser.visible.id
        self.assertEqual(self.browser.state.types[artifact_key("drawing")].newer_count, 0)
        ready = pending.model_copy(update={"content": json.dumps({"status": "ready", "asset_id": "a" * 32, "sha256": "b" * 64})})
        self.publish(answer(code, ready), lane="image")
        self.assertEqual(self.browser.visible.id, held)
        self.assertEqual(self.browser.state.types[artifact_key("drawing")].newer_count, 1)
        self.assertEqual(len(self.browser.current.versions), 1)

    def test_selecting_pending_image_displays_its_first_completed_version(self):
        pending = artifact("drawing", '{"status":"pending"}', "image")
        self.publish(answer(pending))
        self.browser.select_type(artifact_key("drawing"))
        self.assertIsNone(self.browser.visible)
        self.publish(answer(pending.model_copy(update={"content": '{"status":"ready","sha256":"first"}'})), lane="image")
        self.assertIsNotNone(self.browser.visible)
        self.assertTrue(self.browser.frozen)

    def test_pending_revision_retains_previous_completed_image(self):
        ready = artifact("drawing", '{"status":"ready","sha256":"first"}', "image")
        self.publish(answer(ready))
        first = self.browser.visible.id
        self.publish(answer(ready.model_copy(update={"content": '{"status":"pending","prompt":"revise"}'})))
        self.assertEqual(self.browser.visible.id, first)
        self.assertEqual(self.browser.current.status, "pending")

    def test_replies_preserve_attribution_without_counting_new_observation_ids_alone(self):
        source = Observation(id="s1", kind="speech", text="Why None?", channel="system", speaker="other_people", at=1)
        reply = ConversationResponse(source_id=source.id, action="answer", text="It marks missing data.", artifact_effect="Keep the contract.")
        self.publish(answer(conversation=[reply]), sources=[source])
        newer = source.model_copy(update={"id": "s2", "at": 2})
        self.publish(answer(conversation=[reply.model_copy(update={"source_id": "s2"})]), sources=[newer])
        self.assertEqual(len(self.browser.state.types[REPLIES].versions), 1)
        newer.speaker = "primary_user"
        self.publish(answer(conversation=[reply.model_copy(update={"source_id": "s2"})]), sources=[newer])
        self.assertEqual(len(self.browser.state.types[REPLIES].versions), 2)

    def test_per_type_anchors_survive_other_types_churn_and_retention(self):
        self.publish(answer(artifact("code", "old code"), artifact("notes", "old notes")))
        self.browser.select_type(artifact_key("code"))
        held = self.browser.visible.id
        self.browser.select_type(artifact_key("notes"))
        for n in range(40):
            self.publish(answer(artifact("code", str(n)), artifact("notes", str(n))))
        self.browser.select_type(artifact_key("code"))
        self.assertEqual(self.browser.visible.id, held)
        self.assertEqual(len(self.browser.current.versions), MAX_OUTPUTS + 1)
        self.assertEqual(self.browser.newer_count, MAX_OUTPUTS)
        self.assertTrue(self.browser.move(1))
        self.assertEqual(self.browser.visible.artifact.id, "code")

    def test_session_restores_each_cursor_and_counts_without_rolling_back_context(self):
        context = ContextStore()
        context.set_goal("Saved task")
        self.publish(answer(artifact("code", "c1"), artifact("notes", "n1")))
        self.browser.select_type(artifact_key("notes"))
        self.publish(answer(artifact("code", "c2"), artifact("notes", "n2")))
        current = answer(artifact("code", "c2"), artifact("notes", "n2"))
        context.integrate(current)
        coordinator = SimpleNamespace(current=current, history=[], pinned=False)
        document = checkpoint(context, coordinator, output_history=self.history, output_browser=self.browser)
        with tempfile.TemporaryDirectory() as directory:
            path = save_session(document, Path(directory) / "saved.json")
            saved = load_session(path)
        browser = OutputBrowser(saved.output_browser)
        self.assertEqual(browser.visible.artifact.content, "n1")
        self.assertEqual(browser.newer_count, 1)
        self.assertEqual(browser.state.types[artifact_key("code")].newer_count, 1)
        restored = restore_context(saved)
        self.assertEqual(json.loads(restored.previous_answer)["artifacts"][0]["content"], "c2")

    def test_invalid_saved_cursor_or_disguised_debug_plan_is_rejected(self):
        self.publish(answer(artifact("code", "c1"), artifact("_assistance_plan", '{}', "structured")))
        data = self.browser.state.model_dump()
        data["types"][artifact_key("code")]["cursor"] = "missing"
        with self.assertRaises(ValueError):
            BrowserState.model_validate(data)
        data = self.browser.state.model_dump()
        data["types"][artifact_key("_assistance_plan")]["debug"] = False
        with self.assertRaises(ValueError):
            BrowserState.model_validate(data)
