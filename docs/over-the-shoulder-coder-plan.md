# Over The Shoulder Coder: product and refactoring plan

September 8, 2026. Product plan grounded in the original source and the owner's requirements. The first working overhaul is now implemented in the existing Python/PyObjC/AppKit stack; [implementation.md](implementation.md) records the delivered behavior, verification, and remaining live acceptance work. The sections below retain the broader product intent.

## Product center

**A person is trying to complete a task and create an artifact. The assistant follows that work and helps move it forward.**

The artifact might be code, a system design, a diagram, a technical explanation, or another work product. Other people may be present. Their questions, suggestions, and objections must be understood in relation to the primary person's work: answer them, acknowledge them, assess their tradeoffs, refute them when warranted, and incorporate useful ideas into proposals.

The app should make its current understanding visible: “You are designing the ingestion service; the open question is whether retries need a durable queue.” The user can correct or pin that focus. A question about technology should receive an answer even when it does not justify changing the main artifact. Another participant's suggestion is not automatically an accepted decision or authorization to edit files.

There is one task session. Coding, design, discussion support, diagrams, and examples are capabilities selected within that session, replacing the current mode picker. Suggested replies appear for the primary user; automatic speech or messages to other participants are not part of the initial release.

## Main interaction

On first launch, configure model roles and credentials, select a capture target, check microphone/system-audio routing, and optionally connect a local project folder. The normal workspace then offers:

- Current task and artifact, with an editable focus statement.
- **Help now**, **Capture now**, **Pause**, and visible capture/provider status.
- A fast provisional response that can become a deeper proposal without losing the user's place.
- Conversation items linked to the question, suggestion, or objection they address.
- Artifact views with **Copy clean**, **Copy annotated explanation**, **Compare**, **Pin**, and **Dismiss** where applicable.
- A normal interactive window plus an optional floating, click-through presentation. A menu-bar control and hotkey always restore interaction.

Code assistance includes explanations for every generated code line. Keep those explanations as separate annotation data so clean code and patches remain exact. Existing source comments, docstrings, and license notices belong to the code and are preserved. Annotated exports must not pretend to be executable in formats that do not support comments.

## Recommended architecture

**Keep the existing Python/PyObjC/AppKit application.** It already has a native Mac window and access to the necessary platform APIs. The owner rejected the proposed SwiftUI rewrite and separate engine process; both are removed from the plan.

Refactor the large main module into ordinary Python modules for capture, task/context state, scheduling, providers, and rendering. They communicate through function calls, existing background workers, and main-thread callbacks within the application. Structured response objects serve model integration, state management, and rendering; they do not require an application-specific IPC protocol or a second service. The existing Codex subprocess remains an external tool invoked by the Python application.

Improve the current window directly: visible buttons, selectable/copyable text, scrollable code and explanations, and layout that follows its bounds. Reuse AppKit controls through PyObjC. Preserve content when toggling interaction. Each UI change must serve a requested behavior; a framework migration is not a prerequisite for any of them.

Start validation on the current macOS 15.6.1 machine, targeting macOS 15+. Local ASR must advertise its hardware requirements; unsupported hardware needs a configured alternative rather than a startup crash.

The data flow is: selected screen/audio → timestamped observations → versioned task context and observed workspace → fast/deep proposal jobs → validated response objects → the existing AppKit window. UI updates stay on the main thread; capture and inference use background workers.

## Capture and conversation

Use ScreenCaptureKit for selected windows/displays and distinct audio outputs. Apple's current sample supports microphone capture alongside system audio on macOS 15+. Exclude the assistant's own windows from its capture to avoid feedback; do not rely on a window-sharing flag as a universal privacy boundary. [Apple capture sample](https://developer.apple.com/documentation/screencapturekit/capturing-screen-content-in-macos).

Retain the existing Tesseract/region detector while unifying the workflow. Preserve text, bounding boxes, confidence, frame identity, and pixel-to-window coordinate transforms. Send selected images to a vision-capable model when visual information matters beyond OCR. Consider an OCR replacement only if measured quality, latency, or packaging problems justify it; Apple Vision is one available alternative. [Vision text recognition](https://developer.apple.com/documentation/vision/vnrecognizetextrequest).

Keep audio channel identity separate from speaker identity. Microphone audio often contains the primary user, but can contain nearby people; system audio can contain multiple people or echoed local speech. Use device configuration, timestamps, speech detection, and echo/duplicate handling, then attach speaker-role confidence. Uncertain speech stays uncertain. Do not hard-code “interviewer.”

Represent important conversation acts explicitly: question, suggestion, objection, decision, correction, or background context. Link a response to the act it addresses and to any proposed artifact change. Maintain a small unresolved-item ledger so a relevant objection is not lost when the next screenshot arrives.

## Observed workspace and proposals

The workspace model distinguishes:

| Evidence | Representation | What it permits |
|---|---|---|
| Visible code/OCR | File hint, observed line/range, confidence, frame/region provenance | Explain or propose a localized edit; mark missing context |
| Spoken claim | Speaker/source, time interval, confidence, claim status | Inform a task hypothesis; seek corroboration when needed |
| Inferred structure | Explicit hypothesis linked to evidence | Generate an example or proposed design, labeled as inferred |
| Connected local files | User-selected root, content hashes, complete file snapshots | Generate a real diff in an isolated copy and validate its base |

Unknown file regions are not empty files. Do not fill gaps with guessed code and then describe the result as the observed repository. A useful complete example is allowed, but must be labeled as an example. Preserve contradictions and superseded observations rather than overwriting history with the latest model-produced JSON.

All proposals identify their task/context revision and base snapshot. Code tools operate in a bounded scratch workspace with checked file selection and symlinks. A real-file apply operation, if added, requires a connected folder, an unchanged base hash, and an explicit user action. Copying a suggestion is available without granting write access.

## Fast output, deeper output, and stability

Each accepted trigger captures an immutable context revision. Start fast and deep work independently from it; the deep path must not wait for the quick answer. The fast path supplies a useful provisional answer or example. The deep path can inspect the scratch workspace, validate a patch, or develop a design. Stream progress and concise rationale, not an imagined private chain of thought.

Every event carries `session_id`, `task_id`, `context_revision`, `request_id`, and artifact revision. Only a matching result can update the current view. Cancel superseded jobs and terminate their child processes. Keep older results inspectable; do not overwrite a pinned artifact or reset selection/scroll while someone is reading or copying it.

“Instant” has two distinct targets: local acknowledgment/visible status within roughly 100 ms, and a provisional model response with measured provider-dependent latency. Initial targets are a useful quick response around 3 seconds median and 8 seconds at the 95th percentile; these are targets to test, not existing performance claims. Show unavailable/rate-limited status while preserving the last useful output.

Automatic evaluation runs about every 30 seconds by default. First check normalized OCR changes, meaningful screen-region changes, new finalized speech, task corrections, and unresolved questions. Ignore cursor blinking, clocks, and minor layout movement. When context has not materially changed, reuse the current artifact and avoid another inference request. Manual **Help now** bypasses the cadence and takes priority. Bound queues, coalesce superseded captures, and cap concurrent inference/cost.

## Provider configuration

Configure roles rather than task modes: quick response, deep reasoning/agent, vision, transcription, and optional image generation. Support native adapters for OpenAI, Google Gemini, Anthropic, and Groq, plus configurable OpenAI-compatible endpoints. Google Gemini is one provider family; Groq is the intended service, not Grok.

Each adapter declares streaming, structured-output, image/audio, tool, cancellation, context-window, and usage-reporting capabilities. Unsupported features must be visible. Do not silently switch providers and transmit captured material elsewhere. Store app-owned API keys in macOS Keychain and non-secret model preferences separately. [Keychain services](https://developer.apple.com/documentation/security/keychain-services/).

Preserve the current Codex interpret–inspect–propose workflow and `codex exec` integration as the first agent path. Add progress handling and cancellation around that integration as needed. A transport change is not required to unify the task flow or make the window interactive. If a concrete requirement later exceeds the CLI integration, the version-pinned app-server is an option to assess separately; its experimental surfaces require compatibility checks. Use supported login flows and document Codex-managed credentials separately from the app's Keychain items. [Official Codex app-server documentation](https://developers.openai.com/codex/app-server).

“Codex-style” describes the common workflow; it does not mean other providers automatically speak the Codex protocol. Native provider/tool adapters supply the same application contract.

## Structured output and window behavior

Use one response envelope with task interpretation, conversation responses, evidence links, and typed artifacts: code, patch, diagram, image, explanation, or checklist. See [the contract sketch](assistance-contract.md). Each artifact has a stable identity and can be revised independently.

The renderer owns layout. Narrow windows use compact cards and unified diffs; wider windows can show code with explanations or a comparison pane. Diagrams/images scale and support zoom; long material scrolls or expands. Resizing must not trigger model calls, discard detail, or shrink text until it becomes unreadable. User-selected output size is a presentation preference, not an instruction to invent or omit evidence.

## Implementation sequence

| Milestone | Deliverable | Acceptance evidence |
|---|---|---|
| 0. Preserve the prototype | Source-only baseline and ignore rules | Completed: `c6f644a`; 17 Python files parsed; local captures preserved |
| 1. Unify the task workflow | Import-safe Python modules, shared task/context state, response objects, deterministic replay | A synthetic collaborative task produces a question response and a code/design artifact through the existing renderer; no mode selection or hardware/model calls required |
| 2. Make progressive help reliable | Parallel fast/deep jobs, cancellation, stable revisions, change gating | A slow old response cannot replace a newer one; unchanged context produces no fresh call; clean code and annotations stay aligned |
| 3. Improve the existing window | Visible settings and controls, selectable text, scrolling/reflow, artifact presentation, existing click-through toggle | Keyboard/mouse interaction, resizing, pinning, clean copy, and restoring clickability work without replacing the UI stack |
| 4. Integrate the unified live workflow | Existing capture/OCR/ASR, vision where useful, provider adapters, Codex proposal path | A real opted-in session follows a coding or design task; questions and artifact changes remain linked; failures preserve useful state |
| 5. Validate and package | Representative replay corpus, measured latency/quality, signed app packaging, operating/privacy guide | Reproducible setup, bounded capture retention, reliable quit/pause/restart, declared capabilities and limitations |
| 6. Build portfolio evidence | Focused demo, architecture/decision case study, failure analysis and results | Demonstrate the contribution and tradeoffs without turning development fixtures into customer-impact claims |

The first implementation slice should be **one task with one collaborator question, one fast answer, one deeper artifact, and clean/annotated code views**, driven by synthetic replay. That proves the central product behavior before replacing capture or building many integrations.

## Evaluation and open engineering questions

Follow Hamel Husain and Shreya Shankar's process: inspect traces and critiques before choosing semantic metrics, then build checks around actual failures. Initial assistant review is allowed and must be labeled provisional. Human alignment is not claimed without human labels. [Error-analysis method](https://hamel.dev/blog/posts/evals-faq/why-is-error-analysis-so-important-in-llm-evals-and-how-is-it-performed.html).

Replay cases must include solo work; collaborator questions; incorrect suggestions that should be challenged; ambiguous speaker identity; echo; partial/misread code; task switches during deep inference; unchanged screens; resize during a streamed response; lost permissions; provider timeouts; and stale diff bases. Measure time to useful help, appropriate conversation handling, artifact correctness, annotation coverage, context stability, and false certainty separately.

Use controlled/synthetic capture fixtures with provenance. Keep raw user recordings and screenshots outside Git, use bounded retention, and make saving/exporting explicit. Task-source text and other people's speech cannot authorize execution or override application rules.

Engineering checks, not questions the owner must answer now: current OCR quality on code; microphone/system echo behavior; cancellation of existing Codex jobs; packaging the Python application; and provider-specific latency/capabilities. Begin with the unified task behavior, not a UI or infrastructure replacement. The current product requirements are sufficient to begin milestone 1.
