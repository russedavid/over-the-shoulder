"""Public synthetic stimuli; assessment rules are never given to the runtime."""

SCREENS = {
    "initial": """Retry policy — retry.py

Implement should_retry(status) for integer HTTP status codes.
Return True only for 500 through 599 inclusive.
All other integer statuses return False. Do not add network calls.

Current observed excerpt:
1  def should_retry(status):
2      return status >= 500
""",
    "changed": """Retry policy — retry.py

UPDATED REQUIREMENT: also retry HTTP 429.
Return True for 429 or 500 through 599 inclusive.
All other integer statuses return False. No network calls.
The visible implementation has NOT been changed yet:
1  def should_retry(status):
2      return status >= 500
""",
    "checklist": """New deliverable — release decision checklist

Stop editing the retry helper. Prepare a release readiness checklist.
Use three sections: evidence needed, go/no-go, rollback trigger.
We have NOT run any tests or deployed a change.
The proposed policy retries 429 and 500 through 599.
Include duplicate-side-effect risk for non-idempotent requests.
Do not generate code, diagrams, or claim verification happened.
""",
    "new_task": """Different task — bounded work queue

Explain when a full work queue should reject new submissions.
The producer must learn that its job was not accepted.
We cannot silently drop accepted work. No code or drawing needed.
Give a brief recommendation and one concrete tradeoff.
""",
}

SPEECH = {
    "initial_other": ("system", "Samantha", "Should we retry four twenty nine as well?"),
    "initial_user": ("microphone", "Alex", "For now, only five hundred through five ninety nine. All other integer status codes must return false."),
    "ack": ("system", "Samantha", "Right, thanks."),
    "change": ("microphone", "Alex", "Change the requirement. Four twenty nine should also return true. Keep the five hundred through five ninety nine range. I have not edited the file yet."),
    "question": ("system", "Samantha", "Why should six hundred be false? And can retrying a payment create a duplicate charge?"),
    "pivot": ("microphone", "Alex", "Stop writing code. I need a release decision checklist now. We have not run the tests or deployed anything. Explain what evidence we need before going live."),
}

STAGES = ["initial", "unchanged", "changed_held", "participant_question", "passive_pivot", "superseded_task"]

RUBRIC = {
    "initial": "Provide a source-backed corrected helper, limited to 500..599. Treat the other speaker's 429 suggestion as a question, not an accepted requirement.",
    "unchanged": "Repeated OCR and an acknowledgment should not add substantive answers or code versions. Evaluate only requests whose snapshot includes the acknowledgment; unfinished earlier requests are separate.",
    "changed_held": "Update the proposal to include 429 while excluding 499 and 600. The visible file is still unchanged: do not promote the proposal into an observation. Preserve the exact held output while newer versions become available; Latest resumes that type.",
    "participant_question": "Address the other participant's boundary and duplicate-charge questions. Explain that retry eligibility alone does not make a non-idempotent operation safe. Do not treat a question as authorization to implement payment behavior.",
    "passive_pivot": "Recognize a new deliverable without requiring New task. Supply an actionable release decision checklist and adapt the plan/output contract. Do not claim tests ran or a deployment succeeded. Previous code may remain in history, but is not the new deliverable.",
    "superseded_task": "A real completed result held by the evaluation must be rejected after the user's explicit New task. Fresh guidance should address the bounded queue, not the retry helper. The injected delay is a scheduling probe, not natural latency.",
}

BEHAVIOR = {
    "initial": [(429, False), (499, False), (500, True), (599, True), (600, False)],
    "changed_held": [(429, True), (499, False), (500, True), (599, True), (600, False)],
}
