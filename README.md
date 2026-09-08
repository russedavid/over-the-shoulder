# Over The Shoulder Coder

A desktop collaborator that follows a person's task, understands the surrounding conversation, and helps create the artifact they are working on.

The repository contains the Python/PyObjC/AppKit prototype. Its outstanding source changes were preserved in commit `c6f644a`. The planned refactor keeps this application and UI stack, unifies its task workflow, and improves the existing window. The overhaul has not yet been implemented.

- [Product and refactoring plan](docs/over-the-shoulder-coder-plan.md)
- [Current implementation audit](docs/current-system-audit.md)
- [Illustrative response contract](docs/assistance-contract.md)

The product will use one task-centered workflow for coding, design, explanations, and collaborative discussion. Code assistance includes line-by-line teaching annotations and a separate clean copy. Other participants' questions, suggestions, and objections remain part of the task context.

Screenshots, audio recordings, generated sessions, credentials, and unrelated local documents are excluded from source control. Previously tracked runtime artifacts were untracked without deleting local copies. Historical commits were preserved; publishing a clean portfolio history is a later release task.

Legacy entry points can start GUI/capture behavior at module import. Inspect them without importing or running them during planning. There is not yet a reproducible dependency manifest or an automated behavioral test suite for this prototype.
