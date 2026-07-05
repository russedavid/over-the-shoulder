The best integration is **Codex as a disposable patch generator**, not Codex as a direct editor of your live repo.

Your current pair-mode pipeline already has the hard parts: it records mic/system audio, transcribes chunks, accumulates transcript text, then on MIDI 47 flushes pending audio/screenshots and calls `call_openai_pair_process()` before rendering the response in the floating window.   It also already has an overlay renderer that can show multiple wrapped sections like Python, SQL, conversational response, and metadata.  So I would keep that event loop and replace the final “ask OpenAI pair process model” step with “run Codex against a scratch copy of the repo and display the resulting diff.”

## Recommended architecture

Use this flow:

```text
Audio transcript
    ↓
pair_process_context()
    ↓
flush latest audio + build transcript
    ↓
copy repo to temp scratch directory
    ↓
run codex exec in scratch repo only
    ↓
extract git diff from scratch repo
    ↓
delete scratch repo
    ↓
render suggested diff in overlay
```

This gives you what you want: Codex can reason over the local repo and produce real file edits, but those edits happen only in a temporary copy/worktree. Your actual repo is never modified.

I would **not** make strict `read-only` Codex the primary path. OpenAI’s Codex docs describe `read-only` as a mode where Codex can inspect files but cannot edit files or run commands without approval, while `workspace-write` lets Codex read, edit inside the workspace, and run routine local commands inside that boundary. ([OpenAI Developers][1]) For “suggested edits,” Codex tends to do better when it can actually make the changes somewhere and then you show the generated diff. The safe way to do that is **workspace-write inside a scratch directory**, never inside the real repo. OpenAI’s docs also list `read-only --ask-for-approval never` as a non-interactive mode where Codex can only read files, and `workspace-write` as the mode for non-interactive runs that need to operate in a workspace. ([OpenAI Developers][2])

## Where to plug it into your code

The key replacement point is `call_openai_pair_process()`. Right now it does:

```python
transcript_text = _pair_transcript_text()
prompt = build_pair_process_prompt(_pair_context_json(), transcript_text)
response = client.responses.parse(...)
```

That means the current pair mode depends on OCR-derived context plus the audio transcript.  With Codex, the local repo itself becomes the code context, so the new function should become something like:

```python
transcript_text = _pair_transcript_text()
response = call_codex_pair_process(REPO_ROOT, transcript_text, _pair_context_json())
```

You can keep `_pair_context_json()` as supplemental context for visible terminal output, UI notes, or screenshots, but Codex should inspect the repo directly rather than relying on OCR for code. Your prompt file currently tells the pair assistant to solve independently in Python and SQL, which is ideal for data-transformation interview problems but not ideal for repo-level patch suggestions.  I would add a new Codex-specific prompt instead of mutating the old one.

## Use `codex exec`, not the interactive TUI

Codex CLI has a non-interactive `codex exec` command designed for scripted/CI-style runs. It supports setting the workspace with `--cd`, choosing the sandbox with `--sandbox`, reading the prompt from stdin with `-`, emitting JSON events with `--json`, writing the final assistant message to a file with `--output-last-message`, and validating structured final output with `--output-schema`. ([OpenAI Developers][3]) That is a clean fit for your threaded `pair_process_context()` flow.

The command I’d run from Python is:

```bash
codex exec \
  --cd "$SCRATCH_REPO" \
  --sandbox workspace-write \
  --ask-for-approval never \
  --output-last-message "$SCRATCH_REPO/.codex_pair_final.md" \
  -
```

Important: do **not** use `--dangerously-bypass-approvals-and-sandbox`. The CLI docs label that as bypassing approval prompts and sandboxing, and only appropriate inside an externally hardened environment. ([OpenAI Developers][3])

## Concrete implementation sketch

Add a new response model:

```python
class CodexPairResponse(BaseModel):
    summary: str
    likely_request: str
    changed_files: List[str]
    suggested_diff: str
    test_notes: List[str]
    conversational_response: str
```

Then add a Codex wrapper. This version copies the repo, initializes a temporary baseline commit, lets Codex edit only the copy, extracts a diff, and removes the copy afterward.

```python
import shutil
import subprocess
import tempfile
from pathlib import Path


CODEX_TIMEOUT_SEC = int(os.getenv("CODEX_TIMEOUT_SEC", "240"))
PAIR_REPO_ROOT = os.getenv("PAIR_REPO_ROOT", "").strip()


def _copy_repo_to_scratch(repo_root: str) -> tuple[str, str]:
    """
    Copy the current repo into a disposable scratch directory.
    This preserves uncommitted working-tree content without touching the real repo.
    """
    repo_root_path = Path(repo_root).expanduser().resolve()
    if not repo_root_path.exists():
        raise RuntimeError(f"PAIR_REPO_ROOT does not exist: {repo_root_path}")

    scratch_parent = tempfile.mkdtemp(prefix="codex_pair_")
    scratch_repo = os.path.join(scratch_parent, "repo")

    shutil.copytree(
        repo_root_path,
        scratch_repo,
        ignore=shutil.ignore_patterns(
            ".git",
            ".codex_pair_final.md",
            "node_modules",
            ".venv",
            "venv",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            "dist",
            "build",
        ),
    )

    subprocess.run(["git", "init"], cwd=scratch_repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "codex-pair@example.local"], cwd=scratch_repo, check=True)
    subprocess.run(["git", "config", "user.name", "codex-pair"], cwd=scratch_repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=scratch_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "baseline"],
        cwd=scratch_repo,
        check=True,
        capture_output=True,
        text=True,
    )

    return scratch_repo, scratch_parent


def _run_codex_exec(scratch_repo: str, prompt: str) -> tuple[str, str]:
    final_path = os.path.join(scratch_repo, ".codex_pair_final.md")
    cmd = [
        "codex",
        "exec",
        "--cd", scratch_repo,
        "--sandbox", "workspace-write",
        "--ask-for-approval", "never",
        "--output-last-message", final_path,
        "-",
    ]

    proc = subprocess.run(
        cmd,
        input=prompt,
        cwd=scratch_repo,
        text=True,
        capture_output=True,
        timeout=CODEX_TIMEOUT_SEC,
    )

    final_message = ""
    if os.path.exists(final_path):
        with open(final_path, "r", encoding="utf-8") as f:
            final_message = f.read().strip()

    if proc.returncode != 0:
        raise RuntimeError(
            "Codex failed.\n"
            f"stdout:\n{proc.stdout[-4000:]}\n\n"
            f"stderr:\n{proc.stderr[-4000:]}\n\n"
            f"final_message:\n{final_message[-4000:]}"
        )

    return final_message, proc.stdout


def _scratch_diff(scratch_repo: str) -> tuple[list[str], str]:
    # Include untracked files in git diff without staging real content.
    subprocess.run(["git", "add", "-N", "."], cwd=scratch_repo, check=False)

    changed = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=scratch_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()

    diff = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--unified=3", "HEAD"],
        cwd=scratch_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    return changed, diff


def build_codex_pair_prompt(transcript_text: str, pair_context_json: str) -> str:
    return f"""
You are running inside a disposable scratch copy of the user's local repository.

Goal:
- Use the audio transcript to infer the latest concrete coding request.
- Inspect the repository as needed.
- Make suggested edits ONLY in this scratch copy.
- Do not commit.
- Do not access the network.
- Do not modify credentials, secrets, generated lockfiles, or unrelated files.
- Prefer the smallest coherent patch.
- If the request is unclear, do not edit files; explain the blocking question.

The caller will extract and display the git diff from this scratch copy.
Your final message should include:
1. A one-sentence summary.
2. The likely request you addressed.
3. Any tests/checks you ran or could not run.
4. A short spoken explanation suitable for the overlay.

Supplemental screen/OCR context, possibly noisy:
<pair_context_json>
{pair_context_json}
</pair_context_json>

Audio transcript:
<transcript>
{transcript_text}
</transcript>
""".strip()


def call_codex_pair_process(repo_root: str, transcript_text: str, pair_context_json: str) -> Optional[CodexPairResponse]:
    scratch_repo = scratch_parent = None
    try:
        scratch_repo, scratch_parent = _copy_repo_to_scratch(repo_root)
        prompt = build_codex_pair_prompt(transcript_text, pair_context_json)

        final_message, _stdout = _run_codex_exec(scratch_repo, prompt)
        changed_files, diff = _scratch_diff(scratch_repo)

        if not diff.strip():
            diff = "(no file edits suggested)"

        return CodexPairResponse(
            summary=final_message.splitlines()[0][:300] if final_message else "Codex completed.",
            likely_request="Inferred from latest audio transcript",
            changed_files=changed_files,
            suggested_diff=diff,
            test_notes=[],
            conversational_response=final_message or "Codex completed with no final message.",
        )
    except Exception as e:
        _log_runtime_error("codex pair process", e)
        _set_pair_status(f"codex error: {e}")
        return None
    finally:
        if scratch_parent:
            shutil.rmtree(scratch_parent, ignore_errors=True)
```

Then in `pair_process_context()`, replace this line:

```python
response = call_openai_pair_process()
```

with:

```python
transcript_text = _pair_transcript_text()
response = call_codex_pair_process(PAIR_REPO_ROOT, transcript_text, _pair_context_json())
```

You would also update `_pair_last_response`’s type and the save/render functions to expect `CodexPairResponse`.

## Overlay changes

Right now the pair overlay is shaped around `python`, `sql`, and `conversational_response`.  For Codex, I’d change those sections to:

```python
def format_codex_pair_for_overlay_sections() -> List[Tuple[str, List[str]]]:
    sections = []
    status_line = f"Status: {_pair_status}"

    if _pair_last_response:
        resp = _model_to_dict(_pair_last_response)
        sections.append(("Pair Response", [
            status_line,
            f"Summary: {resp.get('summary', '')}",
            f"Likely request: {resp.get('likely_request', '')}",
        ]))
        sections.append(("Changed Files", resp.get("changed_files", []) or ["(none)"]))
        sections.append(("Suggested Diff", wrap_lines(resp.get("suggested_diff", "") or "(empty)", CHARS_PER_LINE)))
        sections.append(("Codex Notes", wrap_lines(resp.get("conversational_response", "") or "(empty)", CHARS_PER_LINE)))
    else:
        sections.append(("Pair Response", [
            status_line,
            "No Codex response yet.",
            "Press 47 to process transcript against the repo.",
        ]))
        sections.append(("Changed Files", ["(none)"]))
        sections.append(("Suggested Diff", ["(empty)"]))
        sections.append(("Codex Notes", ["(empty)"]))

    return sections
```

Then update your pair pages in `constants.py` so one page shows the summary and changed files, and another page shows the diff. Since diffs can be long, I’d make “Suggested Diff” its own page and keep `49/F10=Flip Page` as the navigation mechanism.

## Why scratch-copy beats pure read-only

Pure read-only mode can work for “tell me what to change,” but it asks Codex to imagine a patch without applying it. The scratch-copy design lets Codex produce the patch as actual edits, optionally run local checks inside the scratch workspace, and gives your overlay a real `git diff`. OpenAI’s docs say `codex exec` is meant for non-interactive scripted runs, and that `--output-last-message` is useful for downstream scripting, which matches this wrapper pattern. ([OpenAI Developers][3])

The safety boundary is simple:

```text
real repo:     never passed as writable target
scratch repo:  Codex can edit
overlay:       shows diff only
cleanup:       scratch repo deleted
```

That is the cleanest way to get “Codex responses to the audio transcript generated in the window, showing suggested edits, without actually making edits to the repo.”

[1]: https://developers.openai.com/codex/concepts/sandboxing "Sandbox – Codex | OpenAI Developers"
[2]: https://developers.openai.com/codex/agent-approvals-security "Agent approvals & security – Codex | OpenAI Developers"
[3]: https://developers.openai.com/codex/cli/reference "Command line options – Codex CLI | OpenAI Developers"
