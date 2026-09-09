"""Provider adapters with cancellable streaming and a common validated response."""

import base64
import json
import re
from pathlib import Path
from urllib.parse import quote

import httpx

from otsc.models import decode_json, response_schema
from otsc.privacy import app_directory, redact
from otsc.prompts import SYSTEM, build_prompt
from otsc.settings import Credentials, ModelChoice
from otsc.telemetry import digest, record_progress
from otsc.workspace import verified_from_snapshot


def latest_image(snapshot, enabled):
    if not enabled:
        return None
    for item in reversed(snapshot.observations):
        if item.image_path:
            path = Path(item.image_path)
            root = (app_directory() / "captures").resolve()
            if (
                not path.is_symlink()
                and path.resolve().is_relative_to(root)
                and path.exists()
                and path.stat().st_size < 8_000_000
            ):
                return base64.b64encode(path.read_bytes()).decode()
            break
    return None


def make_request(choice, prompt, key, image_data=None, *, lane="deep", schema=None, system=SYSTEM):
    schema = response_schema(lane) if schema is None else schema
    provider, root = choice.provider, choice.endpoint()
    if not choice.model.strip():
        raise ValueError(f"Choose a model for {provider} in Settings")
    if not root:
        raise ValueError("Set the API base URL, including /v1 where required")
    headers = {"Content-Type": "application/json"}
    if provider == "openai":
        headers["Authorization"] = "Bearer " + key
        content = [{"type": "input_text", "text": prompt}]
        if image_data:
            content.append({"type": "input_image", "image_url": "data:image/png;base64," + image_data})
        body = {
            "model": choice.model,
            "instructions": system,
            "input": [{"role": "user", "content": content}],
            "stream": True,
            "store": False,
            "max_output_tokens": choice.max_tokens,
            "text": {"format": {"type": "json_schema", "name": "assistance", "strict": True, "schema": schema}},
        }
        if choice.reasoning:
            body["reasoning"] = {"effort": choice.reasoning}
        return root + "/responses", headers, body
    if provider == "gemini":
        headers["x-goog-api-key"] = key
        parts = [{"text": prompt}]
        if image_data:
            parts.append({"inline_data": {"mime_type": "image/png", "data": image_data}})
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "maxOutputTokens": choice.max_tokens,
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
            },
        }
        model = quote(choice.model.removeprefix("models/"), safe="")
        return root + f"/models/{model}:streamGenerateContent?alt=sse", headers, body
    if provider == "anthropic":
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
        content = [{"type": "text", "text": prompt}]
        if image_data:
            content.append(
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_data}}
            )
        body = {
            "model": choice.model,
            "system": system,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": choice.max_tokens,
            "stream": True,
            "tools": [
                {
                    "name": "submit_assistance",
                    "description": "Return the task assistance to the desktop app.",
                    "input_schema": schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": "submit_assistance"},
        }
        return root + "/messages", headers, body
    if key:
        headers["Authorization"] = "Bearer " + key
    content = prompt
    if image_data:
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + image_data}},
        ]
    body = {
        "model": choice.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        "stream": True,
        "max_completion_tokens": choice.max_tokens,
        "response_format": {"type": "json_object"},
    }
    if choice.reasoning:
        body["reasoning_effort"] = choice.reasoning
    return root + "/chat/completions", headers, body


def sse_events(lines):
    data = []
    for line in lines:
        if not line:
            if data:
                yield "\n".join(data)
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield "\n".join(data)


def event_text(provider, event):
    if event.get("error") or event.get("type") in {"error", "response.failed", "response.incomplete"}:
        raise RuntimeError("Model did not complete its response: " + redact(json.dumps(event))[:350])
    if provider == "openai":
        if event.get("type") == "response.output_text.delta":
            return event.get("delta", "")
        if event.get("type") == "response.refusal.delta":
            raise RuntimeError("The model declined this request")
    elif provider == "gemini":
        return "".join(
            p.get("text", "")
            for c in event.get("candidates", [])
            for p in c.get("content", {}).get("parts", [])
            if not p.get("thought")
        )
    elif provider == "anthropic":
        delta = event.get("delta", {})
        if delta.get("type") == "input_json_delta":
            return delta.get("partial_json", "")
        if delta.get("stop_reason") == "max_tokens":
            raise RuntimeError("The response reached its token limit; increase the limit in Settings")
    else:
        for item in event.get("choices", []):
            if item.get("finish_reason") == "length":
                raise RuntimeError("The response reached its token limit; increase the limit in Settings")
            return item.get("delta", {}).get("content") or ""
    return ""


def summary_preview(text):
    match = re.search(r'"summary"\s*:\s*("(?:[^"\\]|\\.)*")', text)
    if match:
        try:
            return json.loads(match.group(1))[:1000]
        except ValueError:
            pass
    return ""


class HTTPProvider:
    def __init__(self, choice: ModelChoice, credentials=None, *, transport=None):
        self.choice = choice.model_copy(deep=True)
        self.credentials = credentials or Credentials()
        self.transport = transport
        self.last_raw_response = None
        self.last_raw_text = ""

    def generate(self, snapshot, lane, token, progress):
        files = verified_from_snapshot(snapshot)
        raw = self.generate_json(
            snapshot,
            lane,
            token,
            progress,
            schema=response_schema(lane),
            system=SYSTEM,
            prompt=build_prompt(snapshot, lane, verified_files=files),
        )
        self.last_raw_response = raw
        from otsc.delivery import prepare_response

        return prepare_response(raw, snapshot, lane, token, progress, provider=self)

    def generate_json(self, snapshot, lane, token, progress, *, schema, system, prompt):
        key = self.credentials.get(self.choice, lane)
        if not key and self.choice.provider != "compatible":
            raise ValueError(f"Add the {lane} {self.choice.provider} API key in Settings")
        url, headers, body = make_request(
            self.choice,
            prompt,
            key,
            latest_image(snapshot, self.choice.send_images),
            lane=lane,
            schema=schema,
            system=system,
        )
        text, preview = "", ""
        usage = {}
        record_progress(
            progress,
            "provider_request",
            provider=self.choice.provider,
            model=self.choice.model,
            prompt_hash=digest(system + prompt),
            schema_hash=digest(schema),
        )
        with httpx.Client(
            timeout=httpx.Timeout(90, connect=15), transport=self.transport, follow_redirects=False
        ) as client:
            token.on_cancel(client.close)
            token.check()
            with client.stream("POST", url, headers=headers, json=body) as stream:
                if stream.status_code >= 300:
                    message = stream.read().decode(errors="replace")[:1200]
                    raise RuntimeError(f"{self.choice.provider} HTTP {stream.status_code}: {redact(message)[:500]}")
                progress("Connected; preparing assistance…")
                for raw in sse_events(stream.iter_lines()):
                    token.check()
                    if raw == "[DONE]":
                        break
                    event = json.loads(raw)
                    usage.update(event_usage(self.choice.provider, event))
                    text += event_text(self.choice.provider, event)
                    if len(text) > 200000:
                        raise RuntimeError("Response exceeded the app's size limit")
                    current = summary_preview(text)
                    if current and current != preview:
                        preview = current
                        progress("Draft: " + preview)
        token.check()
        if usage:
            record_progress(progress, "provider_usage", **usage)
        self.last_raw_text = text
        return decode_json(text)


def event_usage(provider, event):
    if provider == "gemini":
        source = event.get("usageMetadata", {})
        mapping = {
            "promptTokenCount": "input_tokens",
            "candidatesTokenCount": "output_tokens",
            "cachedContentTokenCount": "cached_tokens",
        }
    else:
        source = (
            event.get("usage")
            or event.get("response", {}).get("usage")
            or event.get("message", {}).get("usage")
            or event.get("x_groq", {}).get("usage")
            or {}
        )
        mapping = {
            "input_tokens": "input_tokens",
            "prompt_tokens": "input_tokens",
            "output_tokens": "output_tokens",
            "completion_tokens": "output_tokens",
            "cached_input_tokens": "cached_tokens",
        }
    return {
        target: source[key]
        for key, target in mapping.items()
        if isinstance(source.get(key), int) and not isinstance(source[key], bool) and 0 <= source[key] <= 10**12
    }


class DisabledProvider:
    def generate(self, snapshot, lane, token, progress):
        from otsc.scheduler import Cancelled

        raise Cancelled()


def provider_for(choice, credentials):
    if choice.provider == "disabled":
        return DisabledProvider()
    if choice.provider == "codex":
        from otsc.codex import CodexProvider

        return CodexProvider(choice)
    return HTTPProvider(choice, credentials)
