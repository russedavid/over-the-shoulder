import os

SCREENSHOT_REGION = (0, 80, 2850, 1720)
OVERLAY_ORIGIN = (965, 265)

COLUMNS = 2
CHARS_PER_LINE = 90
LINE_HEIGHT = 15
TITLE_HEIGHT = 22

LEFT_MARGIN = 0
RIGHT_MARGIN = 0
TOP_MARGIN = 50
BOTTOM_MARGIN = 10
GUTTER = 10
EXTRA_COL_PADDING_PX = 0

BASE_WINDOW_WIDTH = 1600
BASE_WINDOW_HEIGHT = 1150
PROJECT_IMAGE_WINDOW_WIDTH = 1900
PROJECT_IMAGE_WINDOW_HEIGHT = 1300

_SAMPLE_RATE = 16000
_PAIR_AUDIO_CHUNK_SEC = int(os.getenv("PAIR_AUDIO_CHUNK_SEC", "20"))
_PAIR_CAPTURE_WAIT_SEC = float(os.getenv("PAIR_CAPTURE_WAIT_SEC", "8"))
_OPENAI_TIMEOUT_SEC = float(os.getenv("OPENAI_TIMEOUT_SEC", "240"))
_CODEX_TIMEOUT_SEC = int(os.getenv("CODEX_TIMEOUT_SEC", "240"))
PAIR_PROCESS_BACKEND = os.getenv("PAIR_PROCESS_BACKEND", "codex").strip().lower()
PAIR_REPO_ROOT = os.getenv("PAIR_REPO_ROOT", "").strip()
PAIR_CODEX_MODEL = os.getenv("PAIR_CODEX_MODEL", "").strip()
_PARAKEET_MODEL = os.getenv("PARAKEET_MODEL", "mlx-community/parakeet-tdt-0.6b-v3")

_PROJECT_MD_PATH = os.path.abspath("zpds_system_design.md")
_PROJECT_IMAGE_PATH = os.path.abspath("zpds_system_design.png")

_MODE_A_PAGES = [["Mode", "Optimal Solution", "Clarifying Questions"], ["Edge Cases", "Test Cases", "Limitations"]]
_SYSTEM_PAGES = [["Mode", "Functional Requirements", "Non-Functional Reqs", "Capacity Estimation", "Core Entities"], ["API Design", "Data Flow", "High-Level Design"], ["Data Models", "Component Descriptions"], ["Deep Dives"]]
_PAIR_PAGES = [
    ["Task Focus", "Repo Setup", "Install", "Database Setup", "Run API Server", "Run Scripts", "Validate Scripts", "Other Useful Commands"],
    ["Pair Response", "Changed Files", "Implementation Plan", "Code Suggestions"],
    ["Suggested Diff", "Code Suggestions Detail"],
    ["Codex Notes", "Pair Conversation"],
    ["Pair Metadata"],
]
_BEHAVIORAL_PAIR_PAGES = [
    ["Behavioral Response"],
    ["Behavioral Transcript"],
    ["Behavioral Metadata"],
    ["Mode", "Headline and Key Ideas"],
]

OPENAI_REASONING_MODEL = "gpt-5.5"
OPENAI_FAST_VOICE_MODEL = "gpt-5.4-mini"
OPENAI_PAIR_CONTEXT_MODEL = OPENAI_FAST_VOICE_MODEL
OPENAI_PAIR_PROCESS_MODEL = OPENAI_REASONING_MODEL
OPENAI_BEHAVIORAL_PAIR_MODEL = OPENAI_FAST_VOICE_MODEL
OPENAI_IMAGE_MODEL = "gpt-image-2"
OPENAI_XHIGH_REASONING = {"effort": "xhigh"}
OPENAI_HIGH_REASONING = {"effort": "high"}
OPENAI_FAST_REASONING = {"effort": "none"}

_MIDI_HELP_DSA = "47=run 48=mode 49=flip 50=cont 51=simp 43=hide/show"
_MIDI_HELP_SYSTEM = "47=run 48=mode 49=flip 43=hide/show"
_MIDI_HELP_PAIR = "47=process 48=mode 49=flip 50=capture 51=clear 43=hide/show"
_MIDI_HELP_BEHAVIORAL_PAIR = "47=process 48=mode 49=flip 51=clear 43=hide/show"
_MIDI_HELP_PROJECT = "47=top 48=mode 49=flip 43=hide/show"

_INTERVIEW_DIMENSION_MAPPING_PATH = os.getenv(
    "INTERVIEW_DIMENSION_MAPPING_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "interview_dimension_mapping.md"),
)

_WORK_EXPERIENCE = """Work Experience
Corbalt June 2024 - Present
Lead Software Engineer
• Log-forwarding automation: Built an orchestration, provisioning, and health automation system for Drupal
sites hosted on Acquia, deployed on ECS with TLS (TCP-SSL) and API authentication.
• Deployment reliability: Developed an ECS deployment tool to coordinate multi-service rollouts and enable
safer rollbacks during production changes.
• Platform tooling: Delivered developer experience and platform operations tools in Go on AWS for a CMS
infrastructure program.

ATX LED March 2023 – June 2024
Principal Software Engineer (Contract)
Coordinated work integrations and feature development with software development teams in Turkey,
prototype work with firmware engineers in romania, 
and prototype work with hardware manufacturers / firmware engineers in china

• IoT control plane: Led design and development of a home automation server integrating cloud connectivity
with local device control.
• Hardware integrations: Implemented Python-based I/O integrations using GPIO and UART for physical
device interfaces (e.g., DMX).
• Lighting protocols: Implemented DALI (Digital Addressable Lighting Interface) support and device control
workflows.
Amazon Web Services (AWS) Feb 2021 – March 2023
Software Engineer
• Service onboarding automation: Built a framework enabling customers to define data models and APIs via
an internal federated API platform (SUDS), reducing setup from several days to ~1 hour.
• Extensible permissions model: Developed a permissions framework allowing customers to bring their own
datasets, schemas, APIs, and permission schemas.
• Integration testing enablement: Built an extensible integration testing framework to mock SUDS data
types/features across lower and production environments when direct access wasn’t feasible.
• Conditional authorization: Implemented a contingent authorization framework to support conditional access
decisions while maintaining an authorization trail at scale.
Amazon Web Services (AWS) May 2020 - Aug 2020
Software Engineer Intern
• Data access auditing: Built a platform for auditing access to marketing and sales data to improve
traceability of sensitive data usage.
• Investigator UI: Developed the front-end for querying and reviewing audit records to support faster access
investigations."""

__all__ = [
    "SCREENSHOT_REGION",
    "OVERLAY_ORIGIN",
    "COLUMNS",
    "CHARS_PER_LINE",
    "LINE_HEIGHT",
    "TITLE_HEIGHT",
    "LEFT_MARGIN",
    "RIGHT_MARGIN",
    "TOP_MARGIN",
    "BOTTOM_MARGIN",
    "GUTTER",
    "EXTRA_COL_PADDING_PX",
    "BASE_WINDOW_WIDTH",
    "BASE_WINDOW_HEIGHT",
    "PROJECT_IMAGE_WINDOW_WIDTH",
    "PROJECT_IMAGE_WINDOW_HEIGHT",
    "_SAMPLE_RATE",
    "_PAIR_AUDIO_CHUNK_SEC",
    "_PAIR_CAPTURE_WAIT_SEC",
    "_OPENAI_TIMEOUT_SEC",
    "_CODEX_TIMEOUT_SEC",
    "PAIR_PROCESS_BACKEND",
    "PAIR_REPO_ROOT",
    "PAIR_CODEX_MODEL",
    "_PARAKEET_MODEL",
    "_PROJECT_MD_PATH",
    "_PROJECT_IMAGE_PATH",
    "_MODE_A_PAGES",
    "_SYSTEM_PAGES",
    "_PAIR_PAGES",
    "_BEHAVIORAL_PAIR_PAGES",
    "OPENAI_REASONING_MODEL",
    "OPENAI_FAST_VOICE_MODEL",
    "OPENAI_PAIR_CONTEXT_MODEL",
    "OPENAI_PAIR_PROCESS_MODEL",
    "OPENAI_BEHAVIORAL_PAIR_MODEL",
    "OPENAI_IMAGE_MODEL",
    "OPENAI_XHIGH_REASONING",
    "OPENAI_HIGH_REASONING",
    "OPENAI_FAST_REASONING",
    "_MIDI_HELP_DSA",
    "_MIDI_HELP_SYSTEM",
    "_MIDI_HELP_PAIR",
    "_MIDI_HELP_BEHAVIORAL_PAIR",
    "_MIDI_HELP_PROJECT",
    "_INTERVIEW_DIMENSION_MAPPING_PATH",
    "_WORK_EXPERIENCE",
]
