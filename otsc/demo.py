"""Clearly synthetic replay data; never presented as a live model evaluation."""

from otsc.models import (
    Artifact,
    Assistance,
    ConversationResponse,
    DiagramEdge,
    DiagramNode,
    LineAnnotation,
    ObservedFile,
)
from otsc.scheduler import Cancelled


def seed_demo(context, design=False):
    context.clear()
    if design:
        context.set_goal("Design reliable webhook delivery with retries and duplicate protection.")
        context.add("note", "Could we just retry every failed webhook immediately?", "system", "other_people")
        context.add(
            "note",
            "I want a design I can explain and implement, starting with the delivery path.",
            "microphone",
            "primary_user",
        )
    else:
        context.set_goal("Make the mean helper handle empty input without confusing missing data with zero.")
        context.add("screen", "stats.py, lines 1–2:\ndef mean(values):\n    return sum(values) / len(values)", "screen")
        context.add("note", "Could we just return zero when the input is empty?", "system", "other_people")
        context.add(
            "note",
            "Zero is a real measurement here. Missing data needs a different result.",
            "microphone",
            "primary_user",
        )


class DemoProvider:
    def generate(self, snapshot, lane, token, progress):
        delay = 0.15 if lane == "quick" else 1.2
        if token.event.wait(delay):
            raise Cancelled()
        design = any(w in snapshot.goal.lower() for w in ("webhook", "design", "queue"))
        sources = [item.id for item in snapshot.observations]
        other = next((o.id for o in reversed(snapshot.observations) if o.speaker == "other_people"), sources[-1])
        if design:
            summary = "Separate accepting a webhook from delivering it. A durable queue absorbs bursts; workers retry with backoff."
            reply = "Immediate retries can amplify an outage. Use capped exponential backoff with jitter, and a dead-letter queue for repeated failures."
            artifact = Artifact(
                id="delivery-design",
                kind="diagram",
                title="A delivery path that survives retries",
                content="Accept once, deliver at least once. Persist the event before acknowledging it; use a stable event ID to deduplicate downstream effects.\n\n"
                "An HTTP success completes delivery. Transient failures return to the delayed queue; repeated failures go to a dead-letter queue for inspection.",
                language="",
                path="",
                basis="example",
                source_ids=sources,
                annotations=[],
                nodes=[
                    DiagramNode(id=i, label=label)
                    for i, label in [
                        ("producer", "Webhook producer"),
                        ("ingress", "Ingress + event ID"),
                        ("queue", "Durable queue"),
                        ("workers", "Delivery workers"),
                        ("receiver", "Receiver + deduplication"),
                        ("dead", "Dead-letter queue"),
                    ]
                ],
                edges=[
                    DiagramEdge(source=a, target=b, label=label)
                    for a, b, label in [
                        ("producer", "ingress", "POST"),
                        ("ingress", "queue", "Persist"),
                        ("queue", "workers", "Claim"),
                        ("workers", "receiver", "Deliver"),
                        ("workers", "queue", "Backoff + jitter"),
                        ("workers", "dead", "Retry limit"),
                    ]
                ],
            )
            observed = []
        else:
            summary = "Use None for missing data so an empty input stays distinct from a legitimate average of zero."
            reply = "Zero would blur missing data and a real measurement. Return None for empty input, and let the caller decide how to display or reject it."
            code = "def mean(values):\n    if not values:\n        return None\n    return sum(values) / len(values)\n"
            notes = [
                "Define a helper that accepts a sequence of measurements.",
                "Check for empty input before dividing by the number of values.",
                "Represent missing data explicitly instead of inventing a numeric measurement.",
                "Compute the arithmetic mean when at least one measurement exists.",
            ]
            artifact = Artifact(
                id="mean-helper",
                kind="code",
                title="Keep missing data distinct from zero",
                content=code,
                language="python",
                path="stats.py",
                basis="observed_fragment",
                source_ids=sources,
                annotations=[LineAnnotation(line=i, explanation=n) for i, n in enumerate(notes, 1)],
                nodes=[],
                edges=[],
            )
            screen = next((o for o in snapshot.observations if o.kind == "screen" and "stats.py" in o.text), None)
            observed = (
                [
                    ObservedFile(
                        path="stats.py",
                        content="def mean(values):\n    return sum(values) / len(values)",
                        first_line=1,
                        source_ids=[screen.id],
                        confidence="high",
                    )
                ]
                if screen
                else []
            )
        return Assistance(
            task=snapshot.goal,
            summary=summary,
            conversation=[
                ConversationResponse(
                    source_id=other,
                    action="challenge",
                    text=reply,
                    artifact_effect="The proposal incorporates this distinction.",
                )
            ],
            artifacts=[artifact] if lane == "deep" else [],
            observed_files=observed,
            open_questions=["Confirm the caller's handling of missing data."] if not design else [],
        )
