"""Startup/provider settings using the same native Python/AppKit window stack."""

import AppKit as A
import objc
from Foundation import NSMakeRect, NSObject

from otsc.native import FlippedView, button, color, field, frame, label, popup
from otsc.privacy import redact
from otsc.settings import ModelChoice, save_settings

PROVIDERS = ["disabled", "codex", "openai", "gemini", "anthropic", "groq", "compatible"]
ROLES = ["primary_user", "other_people", "uncertain"]


class Preferences(NSObject):
    def initWithController_(self, controller):
        self = objc.super(Preferences, self).init()
        if self is None:
            return None
        self.controller = controller
        self.fields = {}
        self.window = A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(160, 130, 920, 740),
            A.NSWindowStyleMaskTitled | A.NSWindowStyleMaskClosable,
            A.NSBackingStoreBuffered,
            False,
        )
        self.window.setReleasedWhenClosed_(False)
        self.window.setTitle_("Over The Shoulder Coder · Settings")
        self.window.setBackgroundColor_(color("f8f4ec"))
        root = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, 920, 740))
        self.window.setContentView_(root)
        frame(label(root, "Choose the models that work beside you", size=21, bold=True), 24, 18, 860, 30)
        frame(
            label(
                root,
                "Quick and deep answers run independently. Screen OCR and context building use Astra low / Fast through Codex. API keys stay in Keychain.",
            ),
            24,
            52,
            868,
            36,
        )
        for lane, x in [("quick", 24), ("deep", 474)]:
            choice = getattr(controller.settings, lane)
            frame(label(root, lane.capitalize() + " response", size=16, bold=True), x, 95, 400, 26)
            fields = {}
            fields["provider"] = popup(root, PROVIDERS, choice.provider)
            frame(fields["provider"], x, 125, 420, 28)
            for key, title, value, y in [
                ("model", "Model", choice.model, 160),
                ("base_url", "Base URL", choice.base_url, 198),
                ("key", "API key", "", 236),
                ("max_tokens", "Token limit", str(choice.max_tokens), 274),
                ("reasoning", "Reasoning", choice.reasoning, 312),
            ]:
                frame(label(root, title), x, y + 5, 86, 24)
                fields[key] = field(root, value, "Provider default" if key == "base_url" else "", secure=key == "key")
                frame(fields[key], x + 88, y, 332, 28)
            fields["images"] = button(
                root, "Send screenshot (vision required)", self, "noop:", checkbox=True
            )
            fields["images"].setState_(int(choice.send_images))
            frame(fields["images"], x, 348, 290, 28)
            fields["fast"] = button(root, "Codex Fast", self, "noop:", checkbox=True)
            fields["fast"].setState_(int(choice.fast_mode))
            fields["fast"].setEnabled_(choice.provider == "codex")
            fields["fast"].setToolTip_("Faster processing for compatible Codex models, using the higher Fast credit rate.")
            frame(fields["fast"], x + 296, 348, 124, 28)
            fields["provider"].setTarget_(self)
            fields["provider"].setAction_("providerChanged:")
            self.fields[lane] = fields
        frame(label(root, "Capture and conversation", size=16, bold=True), 24, 394, 860, 26)
        s = controller.settings
        for key, title, x in [
            ("capture_screen", "Screen + OCR", 24),
            ("microphone", "Microphone", 220),
            ("system_audio", "System audio", 450),
        ]:
            item = button(root, title, self, "noop:", checkbox=True)
            item.setState_(int(getattr(s, key)))
            frame(item, x, 425, 180, 26)
            self.fields[key] = item
        frame(label(root, "Mic speaker"), 24, 463, 100, 24)
        self.fields["microphone_role"] = popup(root, ROLES, s.microphone_role)
        frame(self.fields["microphone_role"], 126, 458, 240, 28)
        frame(label(root, "System speaker"), 450, 463, 120, 24)
        self.fields["system_role"] = popup(root, ROLES, s.system_role)
        frame(self.fields["system_role"], 578, 458, 316, 28)
        frame(label(root, "Transcription"), 24, 501, 100, 24)
        self.fields["transcription"] = popup(root, ["disabled", "local", "groq", "openai"], s.transcription)
        frame(self.fields["transcription"], 126, 496, 160, 28)
        self.fields["transcription_model"] = field(
            root, s.transcription_model, "ASR model (blank uses provider default)"
        )
        frame(self.fields["transcription_model"], 294, 496, 290, 28)
        self.fields["transcription_key"] = field(root, "", "Transcription API key", secure=True)
        frame(self.fields["transcription_key"], 594, 496, 300, 28)
        for key, title, value, x, width in [
            ("interval_seconds", "Project poll (s)", str(s.interval_seconds), 24, 210),
            ("hourly_requests", "Requests / hour", str(s.hourly_requests), 270, 255),
            ("audio_chunk_seconds", "Audio chunk (s)", str(s.audio_chunk_seconds), 570, 325),
        ]:
            frame(label(root, title), x, 541, 135, 25)
            self.fields[key] = field(root, value)
            frame(self.fields[key], x + 138, 536, width - 138, 28)
        frame(label(root, "Screen region"), 24, 580, 100, 24)
        self.fields["screen_region"] = field(
            root,
            ",".join(map(str, s.screen_region)) if s.screen_region else "",
            "x,y,width,height — blank captures the primary screen",
        )
        frame(self.fields["screen_region"], 126, 575, 500, 28)
        frame(
            label(
                root,
                "Screen OCR repeats immediately after each result; project polling applies when screen capture is off. Local ASR may download weights. Audio roles are channel hints.",
            ),
            24,
            613,
            868,
            34,
        )
        self.status = label(root, "")
        frame(self.status, 24, 659, 635, 60)
        frame(button(root, "Cancel", self, "cancel:"), 675, 678, 96, 32)
        frame(button(root, "Save settings", self, "save:"), 781, 678, 114, 32)
        self.window.center()
        return self

    def noop_(self, sender):
        pass

    def providerChanged_(self, sender):
        for lane in ("quick", "deep"):
            fields = self.fields[lane]
            enabled = str(fields["provider"].titleOfSelectedItem()) == "codex"
            fields["fast"].setEnabled_(enabled)
            if not enabled:
                fields["fast"].setState_(0)

    def cancel_(self, sender):
        self.window.orderOut_(None)

    def save_(self, sender):
        try:
            settings = self.controller.settings.model_copy(deep=True)
            credentials = []
            for lane in ("quick", "deep"):
                fields = self.fields[lane]
                choice = ModelChoice(
                    provider=str(fields["provider"].titleOfSelectedItem()),
                    model=str(fields["model"].stringValue()).strip(),
                    base_url=str(fields["base_url"].stringValue()).strip(),
                    max_tokens=int(fields["max_tokens"].stringValue()),
                    reasoning=str(fields["reasoning"].stringValue()).strip(),
                    send_images=bool(fields["images"].state()),
                    fast_mode=bool(fields["fast"].state()),
                )
                if choice.provider not in {"disabled", "codex"} and not choice.model:
                    raise ValueError(f"Enter the {lane} model name")
                if choice.provider == "compatible" and not choice.endpoint():
                    raise ValueError("Enter the compatible API base URL")
                setattr(settings, lane, choice)
                secret = str(fields["key"].stringValue()).strip()
                if secret:
                    credentials.append((choice, lane, secret))
            for key in ("capture_screen", "microphone", "system_audio"):
                setattr(settings, key, bool(self.fields[key].state()))
            for key in ("microphone_role", "system_role", "transcription"):
                setattr(settings, key, str(self.fields[key].titleOfSelectedItem()))
            settings.transcription_model = str(self.fields["transcription_model"].stringValue()).strip()
            for key in ("interval_seconds", "hourly_requests", "audio_chunk_seconds"):
                setattr(settings, key, int(self.fields[key].stringValue()))
            region = str(self.fields["screen_region"].stringValue()).strip()
            settings.screen_region = tuple(int(n.strip()) for n in region.split(",")) if region else None
            if settings.screen_region and (len(settings.screen_region) != 4 or min(settings.screen_region[2:]) <= 0):
                raise ValueError("Screen region must be x,y,width,height with positive width and height")
            if (settings.microphone or settings.system_audio) and settings.transcription == "disabled":
                raise ValueError("Choose a transcription provider for audio capture")
            secret = str(self.fields["transcription_key"].stringValue()).strip()
            if secret and settings.transcription in {"openai", "groq"}:
                credentials.append((ModelChoice(provider=settings.transcription), "transcription", secret))
            settings.configured = True
            if settings.quick.provider == settings.deep.provider == "disabled":
                raise ValueError("Enable at least one response provider")
            settings = type(settings).model_validate(settings.model_dump())
            for choice, lane, secret in credentials:
                self.controller.credentials.set(choice, lane, secret)
            save_settings(settings)
            self.controller.apply_settings(settings)
            self.window.orderOut_(None)
        except Exception as error:
            self.status.setStringValue_(redact(str(error))[:320])
