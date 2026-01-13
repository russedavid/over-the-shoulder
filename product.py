import os, time, threading, json, mido, anthropic, base64
from typing import List, Tuple, Optional
import pyautogui
from pynput import keyboard
import pytesseract
from PIL import Image
from pydantic import BaseModel
from AppKit import NSApplication, NSWindow, NSColor, NSFloatingWindowLevel, NSTextField, NSFont, NSLineBreakByClipping, NSImageView, NSImage, NSImageScaleProportionallyUpOrDown
from Foundation import NSObject
from PyObjCTools import AppHelper
from google import genai

SCREENSHOT_REGION, OVERLAY_ORIGIN = (90, 120, 2550, 1480), (965, 265)
COLUMNS, CHARS_PER_LINE, LINE_HEIGHT, TITLE_HEIGHT = 2, 90, 15, 22
LEFT_MARGIN, RIGHT_MARGIN, TOP_MARGIN, BOTTOM_MARGIN, GUTTER, EXTRA_COL_PADDING_PX = 0, 0, 50, 10, 10, 0
window_width, window_height = 1600, 1150
_is_running, _awaiting_second, _interactive_mode = threading.Event(), False, False
_status_label = None

# Mode management: "product" or "system"
_current_mode = "product"

# Product mode state
_product_view_mode = 0
_product_results_shown = False
_product_full_sections = None
_product_diagram_path = None

# System mode state
_system_view_mode = 0
_system_results_shown = False
_system_full_sections = None
_system_diagram_path = None

_PRODUCT_PAGES = [
    ["Problem Statement", "Clarifying Questions", "User Personas", "Product Vision"],
    ["Success Metrics", "Feature Prioritization", "MVP Scope", "User Journey"],
    ["Strategic Thinking", "Technical Considerations", "Risks & Mitigations", "Collaboration Prompts"],
]
_SYSTEM_PAGES = [
    ["Problem Restatement", "Functional Requirements", "Non-Functional Requirements", "Capacity Estimation"],
    ["High-Level Design", "Components", "Data Model"],
    ["API Design", "Scaling Strategy", "Reliability", "Tradeoffs"],
]
ANTHROPIC_MODEL = "claude-opus-4-5-20251101"

class UserPersona(BaseModel):
    persona: str
    needs: List[str]
    pain_points: List[str]

class SuccessMetric(BaseModel):
    metric: str
    rationale: str
    target: str

class Feature(BaseModel):
    feature: str
    user_value: str
    priority: str
    technical_complexity: str

class StrategicConsideration(BaseModel):
    area: str
    insight: str
    recommendation: str
    tradeoffs: List[str]

class TechnicalConsideration(BaseModel):
    area: str
    challenge: str
    approach: str
    tradeoffs: List[str]

class ProductAssistantResponse(BaseModel):
    problem_statement: str
    clarifying_questions: List[str]
    user_personas: List[UserPersona]
    product_vision: str
    success_metrics: List[SuccessMetric]
    feature_prioritization: List[Feature]
    mvp_scope: List[str]
    user_journey: List[str]
    strategic_considerations: List[StrategicConsideration]
    technical_considerations: List[TechnicalConsideration]
    risks_and_mitigations: List[str]
    collaboration_prompts: List[str]

# System Design Models
class SystemComponent(BaseModel):
    name: str
    purpose: str
    technologies: List[str]
    scaling_notes: str

class DataEntity(BaseModel):
    entity: str
    attributes: List[str]
    relationships: List[str]

class APIEndpoint(BaseModel):
    endpoint: str
    method: str
    purpose: str
    notes: str

class SystemDesignResponse(BaseModel):
    problem_restatement: str
    functional_requirements: List[str]
    non_functional_requirements: List[str]
    capacity_estimation: List[str]
    high_level_design: str
    components: List[SystemComponent]
    data_model: List[DataEntity]
    api_design: List[APIEndpoint]
    scaling_strategy: List[str]
    reliability_considerations: List[str]
    tradeoffs: List[str]
    follow_up_questions: List[str]

def take_screenshot() -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    fn = f"screenshot_{ts}.png"
    pyautogui.screenshot(region=SCREENSHOT_REGION).save(fn)
    return fn

def ocr_with_tesseract(image_path: str) -> str:
    try: return pytesseract.image_to_string(Image.open(image_path)).strip()
    except Exception as e: return f"(OCR error: {e})"

def wrap_lines(s: str, max_len: int = CHARS_PER_LINE) -> List[str]:
    out = []
    for line in (s.splitlines() or [""]):
        while len(line) > max_len: out.append(line[:max_len]); line = line[max_len:]
        out.append(line)
    return out

def _mk_color(r, g, b, a=0.9): return NSColor.colorWithCalibratedRed_green_blue_alpha_(r/255.0, g/255.0, b/255.0, a)

SECTION_COLOR = {
    # Product sections
    "Problem Statement": _mk_color(25, 60, 140), "Clarifying Questions": _mk_color(90, 0, 140),
    "User Personas": _mk_color(0, 85, 150), "Product Vision": _mk_color(0, 110, 90),
    "Success Metrics": _mk_color(140, 50, 0), "Feature Prioritization": _mk_color(45, 45, 45),
    "MVP Scope": _mk_color(0, 110, 140), "User Journey": _mk_color(140, 0, 60),
    "Strategic Thinking": _mk_color(100, 60, 0), "Technical Considerations": _mk_color(0, 80, 120),
    "Risks & Mitigations": _mk_color(120, 0, 80),
    "Collaboration Prompts": _mk_color(0, 100, 60), "Tradeoffs & Risks": _mk_color(80, 40, 100),
    "Kickoff Questions": _mk_color(25, 60, 140), "Framework Reminder": _mk_color(0, 110, 90),
    "Framework (SWE Product)": _mk_color(0, 110, 90), "Technical Angles": _mk_color(0, 80, 120),
    "Collaboration Phrases": _mk_color(0, 100, 60), "Show Your Thinking": _mk_color(100, 60, 0),
    "Diagram": _mk_color(60, 60, 140), "Status": _mk_color(200, 120, 0), "status:": _mk_color(45, 45, 45),
    # System design sections
    "Problem Restatement": _mk_color(25, 60, 140), "Functional Requirements": _mk_color(0, 100, 80),
    "Non-Functional Requirements": _mk_color(90, 60, 0), "Capacity Estimation": _mk_color(120, 40, 80),
    "High-Level Design": _mk_color(0, 85, 150), "Components": _mk_color(60, 60, 140),
    "Data Model": _mk_color(140, 50, 0), "API Design": _mk_color(0, 110, 90),
    "Scaling Strategy": _mk_color(100, 60, 0), "Reliability": _mk_color(120, 0, 80),
    "Tradeoffs": _mk_color(80, 40, 100), "Follow-Up Questions": _mk_color(0, 100, 60),
    # System cheat sheet sections
    "Framework (System Design)": _mk_color(0, 110, 90), "Key Components": _mk_color(60, 60, 140),
    "Scaling Patterns": _mk_color(100, 60, 0),
}
TITLE_FONT = NSFont.boldSystemFontOfSize_(13)
LINE_FONT = NSFont.userFixedPitchFontOfSize_(12) or NSFont.systemFontOfSize_(12)

def _estimate_char_px(font=LINE_FONT) -> float: return 7.5

def _ensure_window_width_for_columns():
    global window_width
    col_width_needed = int(CHARS_PER_LINE * _estimate_char_px(LINE_FONT)) + EXTRA_COL_PADDING_PX
    needed_width = LEFT_MARGIN + RIGHT_MARGIN + (COLUMNS * col_width_needed) + ((COLUMNS - 1) * GUTTER)
    if needed_width > window_width:
        window_width = needed_width
        frame = window.frame()
        window.setFrame_display_(((frame.origin.x, frame.origin.y), (window_width, frame.size.height)), True)

def _place_label(text, x, y_val, width, height, font, color):
    field = NSTextField.alloc().initWithFrame_(((x, y_val), (width, height)))
    field.setStringValue_(text); field.setTextColor_(color); field.setDrawsBackground_(False)
    field.setBackgroundColor_(NSColor.clearColor()); field.setBordered_(False); field.setSelectable_(False)
    field.setEditable_(False); field.setFont_(font); field.cell().setLineBreakMode_(NSLineBreakByClipping)
    field.setUsesSingleLineMode_(True); window.contentView().addSubview_(field)

def format_structured_for_overlay_sections(data: ProductAssistantResponse) -> List[Tuple[str, List[str]]]:
    w, sections = CHARS_PER_LINE, []
    sections.append(("Problem Statement", wrap_lines(data.problem_statement, w)))
    sections.append(("Clarifying Questions", [*sum((wrap_lines(f"• {q}", w) for q in data.clarifying_questions), [])]))
    persona_lines = []
    for p in data.user_personas:
        persona_lines += wrap_lines(f"[{p.persona}]", w)
        persona_lines += [*sum((wrap_lines(f"  Need: {n}", w) for n in p.needs), [])]
        persona_lines += [*sum((wrap_lines(f"  Pain: {pp}", w) for pp in p.pain_points), [])]
        persona_lines += [""]
    sections.append(("User Personas", persona_lines))
    sections.append(("Product Vision", wrap_lines(data.product_vision, w)))
    metric_lines = []
    for m in data.success_metrics:
        metric_lines += wrap_lines(f"[{m.metric}]", w)
        metric_lines += wrap_lines(f"  Why: {m.rationale}", w)
        metric_lines += wrap_lines(f"  Target: {m.target}", w)
        metric_lines += [""]
    sections.append(("Success Metrics", metric_lines))
    feature_lines = []
    for f in data.feature_prioritization:
        feature_lines += wrap_lines(f"[{f.priority}] {f.feature}", w)
        feature_lines += wrap_lines(f"  Value: {f.user_value}", w)
        feature_lines += wrap_lines(f"  Complexity: {f.technical_complexity}", w)
        feature_lines += [""]
    sections.append(("Feature Prioritization", feature_lines))
    sections.append(("MVP Scope", [*sum((wrap_lines(f"• {item}", w) for item in data.mvp_scope), [])]))
    sections.append(("User Journey", [*sum((wrap_lines(f"{i+1}. {step}", w) for i, step in enumerate(data.user_journey)), [])]))
    strat_lines = []
    for s in data.strategic_considerations:
        strat_lines += wrap_lines(f"[{s.area}]", w)
        strat_lines += wrap_lines(f"  Insight: {s.insight}", w)
        strat_lines += wrap_lines(f"  Rec: {s.recommendation}", w)
        strat_lines += ["  Tradeoffs:"]
        strat_lines += [*sum((wrap_lines(f"    • {t}", w) for t in s.tradeoffs), [])]
        strat_lines += [""]
    sections.append(("Strategic Thinking", strat_lines))
    tech_lines = []
    for t in data.technical_considerations:
        tech_lines += wrap_lines(f"[{t.area}]", w)
        tech_lines += wrap_lines(f"  Challenge: {t.challenge}", w)
        tech_lines += wrap_lines(f"  Approach: {t.approach}", w)
        tech_lines += ["  Tradeoffs:"]
        tech_lines += [*sum((wrap_lines(f"    • {tr}", w) for tr in t.tradeoffs), [])]
        tech_lines += [""]
    sections.append(("Technical Considerations", tech_lines))
    sections.append(("Risks & Mitigations", [*sum((wrap_lines(f"• {r}", w) for r in data.risks_and_mitigations), [])]))
    sections.append(("Collaboration Prompts", [*sum((wrap_lines(f"→ {c}", w) for c in data.collaboration_prompts), [])]))
    return sections

def build_diagram_prompt(data: ProductAssistantResponse) -> str:
    features = ', '.join(f.feature for f in data.feature_prioritization[:4])
    personas = ', '.join(p.persona for p in data.user_personas[:2])
    tech_areas = ', '.join(t.area for t in data.technical_considerations[:3]) if data.technical_considerations else ''
    return f"Create a clean system architecture or product flow diagram for: {data.product_vision}. Target users: {personas}. Key features: {features}. Technical areas: {tech_areas}. Style: professional software architecture diagram, showing components, data flow, and user interactions. Simple shapes, clear labels, easy to understand."

def format_system_for_overlay_sections(data: SystemDesignResponse) -> List[Tuple[str, List[str]]]:
    w, sections = CHARS_PER_LINE, []
    sections.append(("Problem Restatement", wrap_lines(data.problem_restatement, w)))
    sections.append(("Functional Requirements", [*sum((wrap_lines(f"• {r}", w) for r in data.functional_requirements), [])]))
    sections.append(("Non-Functional Requirements", [*sum((wrap_lines(f"• {r}", w) for r in data.non_functional_requirements), [])]))
    sections.append(("Capacity Estimation", [*sum((wrap_lines(f"• {c}", w) for c in data.capacity_estimation), [])]))
    sections.append(("High-Level Design", wrap_lines(data.high_level_design, w)))
    comp_lines = []
    for c in data.components:
        comp_lines += wrap_lines(f"[{c.name}]", w)
        comp_lines += wrap_lines(f"  Purpose: {c.purpose}", w)
        comp_lines += wrap_lines(f"  Tech: {', '.join(c.technologies)}", w)
        comp_lines += wrap_lines(f"  Scale: {c.scaling_notes}", w)
        comp_lines += [""]
    sections.append(("Components", comp_lines))
    data_lines = []
    for d in data.data_model:
        data_lines += wrap_lines(f"[{d.entity}]", w)
        data_lines += wrap_lines(f"  Attrs: {', '.join(d.attributes)}", w)
        data_lines += wrap_lines(f"  Rels: {', '.join(d.relationships)}", w)
        data_lines += [""]
    sections.append(("Data Model", data_lines))
    api_lines = []
    for a in data.api_design:
        api_lines += wrap_lines(f"[{a.method}] {a.endpoint}", w)
        api_lines += wrap_lines(f"  {a.purpose}", w)
        api_lines += wrap_lines(f"  Note: {a.notes}", w)
        api_lines += [""]
    sections.append(("API Design", api_lines))
    sections.append(("Scaling Strategy", [*sum((wrap_lines(f"• {s}", w) for s in data.scaling_strategy), [])]))
    sections.append(("Reliability", [*sum((wrap_lines(f"• {r}", w) for r in data.reliability_considerations), [])]))
    sections.append(("Tradeoffs", [*sum((wrap_lines(f"• {t}", w) for t in data.tradeoffs), [])]))
    sections.append(("Follow-Up Questions", [*sum((wrap_lines(f"→ {q}", w) for q in data.follow_up_questions), [])]))
    return sections

def build_system_diagram_prompt(data: SystemDesignResponse) -> str:
    components = ', '.join(c.name for c in data.components[:6])
    entities = ', '.join(d.entity for d in data.data_model[:4])
    return f"Create a clean system architecture diagram for: {data.problem_restatement}. Key components: {components}. Data entities: {entities}. Style: professional distributed systems architecture diagram showing components, databases, caches, queues, data flow arrows, and client interactions. Use cloud/infrastructure icons, clear labels, show read/write paths."

def build_system_prompt(ocr_text: str) -> str:
    return f"""You are coaching a software engineer in a SYSTEM DESIGN interview. Analyze the screenshot and OCR text to provide structured guidance for designing a scalable distributed system.

Respond with ONLY valid JSON (no markdown) matching this schema:
{{
  "problem_restatement": "Clear 1-2 sentence summary of what we're building",
  "functional_requirements": ["Core features the system must support"],
  "non_functional_requirements": ["Scalability, latency, availability, consistency requirements"],
  "capacity_estimation": ["QPS estimates", "Storage estimates", "Bandwidth estimates"],
  "high_level_design": "Brief description of the overall architecture approach",
  "components": [{{"name": "...", "purpose": "...", "technologies": ["..."], "scaling_notes": "..."}}],
  "data_model": [{{"entity": "...", "attributes": ["..."], "relationships": ["..."]}}],
  "api_design": [{{"endpoint": "/api/...", "method": "GET/POST/...", "purpose": "...", "notes": "..."}}],
  "scaling_strategy": ["Horizontal scaling approach", "Caching strategy", "Database scaling"],
  "reliability_considerations": ["Redundancy", "Failover", "Data replication"],
  "tradeoffs": ["Key design tradeoffs and decisions made"],
  "follow_up_questions": ["Questions to discuss with interviewer"]
}}

SYSTEM DESIGN INTERVIEW FRAMEWORK:

1. REQUIREMENTS CLARIFICATION (2-3 min):
- Functional: What features? What can users do?
- Non-functional: Scale? Latency? Availability vs consistency?
- Constraints: Existing systems? Budget? Timeline?

2. CAPACITY ESTIMATION (2-3 min):
- Users: DAU, peak concurrent users
- Traffic: Read/write ratio, QPS
- Storage: Data size, growth rate, retention
- Bandwidth: Request/response sizes

3. HIGH-LEVEL DESIGN (5-10 min):
- Draw the big boxes: clients, load balancers, services, databases
- Identify read vs write paths
- Show data flow

4. COMPONENT DEEP DIVE (10-15 min):
- API Gateway / Load Balancer
- Application servers (stateless)
- Caching layer (Redis, Memcached)
- Database (SQL vs NoSQL, sharding strategy)
- Message queues (async processing)
- CDN (static content)
- Search (Elasticsearch)

5. SCALING & RELIABILITY (5 min):
- Horizontal scaling
- Database replication & sharding
- Caching strategies
- Rate limiting
- Circuit breakers
- Monitoring & alerting

KEY COMPONENTS TO CONSIDER:
- Load Balancer: Round-robin, least connections, consistent hashing
- Cache: Cache-aside, write-through, write-behind, TTL strategy
- Database: Primary-replica, sharding (hash, range, geo), denormalization
- Queue: Kafka, RabbitMQ, SQS for async processing
- Storage: S3/blob storage for media, CDN for distribution

COMMON PATTERNS:
- Read-heavy: Add read replicas, caching layers, CDN
- Write-heavy: Async writes, message queues, eventual consistency
- Global scale: Multi-region, geo-routing, data replication
- Real-time: WebSockets, pub/sub, long polling

<ocr_text>
{ocr_text}
</ocr_text>"""

def call_anthropic_system(ocr_text: str, image_path: Optional[str]) -> Optional[SystemDesignResponse]:
    try: client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    except Exception as e: print(f"Failed to initialize Anthropic client: {e}"); return None
    content = []
    if image_path:
        with open(image_path, "rb") as f: img_data = base64.b64encode(f.read()).decode("utf-8")
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_data}})
    content.append({"type": "text", "text": build_system_prompt(ocr_text)})
    try:
        message = client.messages.create(model=ANTHROPIC_MODEL, max_tokens=8000, messages=[{"role": "user", "content": content}])
        response_text = "".join(block.text for block in message.content if hasattr(block, "type") and block.type == "text").strip()
        if response_text.startswith("```json"): response_text = response_text[7:]
        elif response_text.startswith("```"): response_text = response_text[3:]
        if response_text.endswith("```"): response_text = response_text[:-3]
        return SystemDesignResponse(**json.loads(response_text.strip()))
    except Exception as e: print(f"API/Parse error: {e}"); return None

def generate_diagram_async(data, mode: str):
    global _product_diagram_path, _system_diagram_path
    try:
        client = genai.Client()
        if mode == "product":
            prompt = build_diagram_prompt(data)
        else:
            prompt = build_system_diagram_prompt(data)
        response = client.models.generate_content(model="gemini-3-pro-image-preview", contents=[prompt])
        for part in response.parts:
            if part.inline_data is not None:
                ts = time.strftime("%Y%m%d_%H%M%S")
                path = os.path.abspath(f"diagram_{mode}_{ts}.png")
                part.as_image().save(path)
                if mode == "product":
                    _product_diagram_path = path
                    pages = _PRODUCT_PAGES
                    view_mode = _product_view_mode
                else:
                    _system_diagram_path = path
                    pages = _SYSTEM_PAGES
                    view_mode = _system_view_mode
                if _current_mode == mode and view_mode == len(pages):
                    AppHelper.callAfter(_show_diagram_view)
                return
    except Exception as e:
        print(f"Diagram generation error: {e}")

def _ensure_status_label():
    global _status_label
    if _status_label is not None and _status_label.superview() is not None: return _status_label
    lbl = NSTextField.alloc().initWithFrame_(((8, 8), (420, 18)))
    lbl.setBordered_(False); lbl.setEditable_(False); lbl.setSelectable_(False); lbl.setDrawsBackground_(True)
    lbl.setBackgroundColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
    lbl.setTextColor_(_mk_color(45, 45, 45)); lbl.setFont_(NSFont.userFixedPitchFontOfSize_(11) or NSFont.systemFontOfSize_(11))
    window.contentView().addSubview_(lbl); _status_label = lbl
    return lbl

def _update_status(text: str): lbl = _ensure_status_label(); lbl.setStringValue_(text); lbl.setFrameOrigin_((8, 8))

def _update_overlay_sections(sections: List[Tuple[str, List[str]]]):
    global _last_sections
    _last_sections = sections
    _ensure_window_width_for_columns()
    for sv in list(window.contentView().subviews()): sv.removeFromSuperview()
    frame = window.frame()
    available_w, available_h = frame.size.width, frame.size.height
    col_width = (available_w - (LEFT_MARGIN + RIGHT_MARGIN + GUTTER)) / COLUMNS
    x_cols = [LEFT_MARGIN, LEFT_MARGIN + col_width + GUTTER]
    y, col = [available_h - TOP_MARGIN for _ in range(COLUMNS)], 0
    for title, lines in sections:
        color = SECTION_COLOR.get(title, _mk_color(45, 45, 45))
        if y[col] - TITLE_HEIGHT < BOTTOM_MARGIN:
            col += 1
            if col >= COLUMNS: _place_label("…", x_cols[-1], BOTTOM_MARGIN, col_width, LINE_HEIGHT, LINE_FONT, _mk_color(45, 45, 45)); break
            y[col] = available_h - TOP_MARGIN
        _place_label(title, x_cols[col], y[col], col_width, TITLE_HEIGHT, TITLE_FONT, color); y[col] -= TITLE_HEIGHT
        for line in lines:
            if y[col] - LINE_HEIGHT < BOTTOM_MARGIN:
                col += 1
                if col >= COLUMNS: _place_label("…", x_cols[-1], BOTTOM_MARGIN, col_width, LINE_HEIGHT, LINE_FONT, _mk_color(45, 45, 45)); return
                y[col] = available_h - TOP_MARGIN
                _place_label(title, x_cols[col], y[col], col_width, TITLE_HEIGHT, TITLE_FONT, color); y[col] -= TITLE_HEIGHT
            _place_label(line, x_cols[col], y[col], col_width, LINE_HEIGHT, LINE_FONT, color); y[col] -= LINE_HEIGHT

def ui_update(lines: List[str]): AppHelper.callAfter(_update_overlay_sections, [('status:', lines)])
def ui_update_sections(sections: List[Tuple[str, List[str]]]): AppHelper.callAfter(_update_overlay_sections, sections)
def _get_page_sections(sections: List[Tuple[str, List[str]]], page: int, mode: str) -> List[Tuple[str, List[str]]]:
    pages = _PRODUCT_PAGES if mode == "product" else _SYSTEM_PAGES
    if page >= len(pages): return []
    page_titles = set(pages[page])
    return [s for s in sections if s[0] in page_titles]

def _show_diagram_view():
    for sv in list(window.contentView().subviews()): sv.removeFromSuperview()
    frame = window.frame()
    diagram_path = _product_diagram_path if _current_mode == "product" else _system_diagram_path
    pages = _PRODUCT_PAGES if _current_mode == "product" else _SYSTEM_PAGES
    page_num = len(pages) + 1
    total_pages = len(pages) + 1
    mode_label = "PRODUCT" if _current_mode == "product" else "SYSTEM"
    if diagram_path and os.path.exists(diagram_path):
        img = NSImage.alloc().initWithContentsOfFile_(diagram_path)
        if img:
            img_view = NSImageView.alloc().initWithFrame_(((10, 10), (frame.size.width - 20, frame.size.height - 60)))
            img_view.setImage_(img)
            img_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
            window.contentView().addSubview_(img_view)
            _update_status(f"[{mode_label}] Diagram ({page_num}/{total_pages})")
            return
    _place_label("Diagram generating...", 20, frame.size.height - 50, 400, TITLE_HEIGHT, TITLE_FONT, _mk_color(60, 60, 140))
    _update_status(f"[{mode_label}] Diagram ({page_num}/{total_pages}) - generating...")

def _rerender_current_view():
    if _current_mode == "product":
        pages = _PRODUCT_PAGES
        view_mode = _product_view_mode
        full_sections = _product_full_sections
    else:
        pages = _SYSTEM_PAGES
        view_mode = _system_view_mode
        full_sections = _system_full_sections
    num_pages = len(pages)
    mode_label = "PRODUCT" if _current_mode == "product" else "SYSTEM"
    if view_mode < num_pages:
        if full_sections:
            page_sections = _get_page_sections(full_sections, view_mode, _current_mode)
            AppHelper.callAfter(_update_overlay_sections, page_sections)
            AppHelper.callAfter(_update_status, f"[{mode_label}] Page {view_mode + 1}/{num_pages + 1}")
    else:
        AppHelper.callAfter(_show_diagram_view)

def build_product_prompt(ocr_text: str) -> str:
    return f"""You are coaching a SOFTWARE ENGINEER in a product vision/design interview. The candidate must demonstrate strong product thinking AND show awareness of architectural/engineering implications. Analyze the screenshot and OCR text to provide structured guidance.

Respond with ONLY valid JSON (no markdown) matching this schema:
{{
  "problem_statement": "Crisp 1-2 sentence reframing of the core problem",
  "clarifying_questions": ["Smart questions showing both product AND technical thinking"],
  "user_personas": [{{"persona": "...", "needs": ["..."], "pain_points": ["..."]}}],
  "product_vision": "Inspiring 1-2 sentence north star vision",
  "success_metrics": [{{"metric": "...", "rationale": "why this matters", "target": "specific goal"}}],
  "feature_prioritization": [{{"feature": "...", "user_value": "...", "priority": "P0/P1/P2", "technical_complexity": "Low/Med/High + brief reason"}}],
  "mvp_scope": ["Core features for v1 launch"],
  "user_journey": ["Step 1...", "Step 2..."],
  "strategic_considerations": [{{"area": "...", "insight": "...", "recommendation": "...", "tradeoffs": ["..."]}}],
  "technical_considerations": [{{"area": "...", "challenge": "...", "approach": "...", "tradeoffs": ["..."]}}],
  "risks_and_mitigations": ["Risk: X → Mitigation: Y"],
  "collaboration_prompts": ["Phrases to engage interviewer and think together"]
}}

SOFTWARE ENGINEER PRODUCT INTERVIEW COACHING:

CLARIFYING QUESTIONS (show structured thinking):
- User segments: "Who is our primary user? Power users vs casual?"
- Scale/constraints: "What's our expected scale? Any latency requirements?"
- Platform: "Mobile-first, web, or cross-platform? Offline support needed?"
- Integration: "What existing systems do we need to integrate with?"
- Success criteria: "How will we measure success technically and product-wise?"

USER PERSONAS (demonstrate empathy + technical awareness):
- Create 2-3 distinct personas with real motivations
- Consider technical sophistication levels of users
- Think about edge cases and power user needs
- Show you understand both emotional and functional needs

PRODUCT VISION (inspire alignment):
- Paint a picture of the future state
- Connect to user impact, not just features
- Make it technically achievable yet ambitious

SUCCESS METRICS (show business + engineering acumen):
- Include leading AND lagging indicators
- Balance user metrics, business metrics, and technical health metrics
- Consider: latency, reliability, adoption, engagement, retention
- Be specific with targets when possible

FEATURE PRIORITIZATION (show judgment + technical awareness):
- Use clear framework (impact vs complexity)
- Consider technical dependencies and sequencing
- Note which features unlock others architecturally
- Be willing to say NO to good ideas for great ones

MVP SCOPE (be decisive):
- Be ruthless about what's truly essential
- Consider what's technically feasible for v1
- Identify technical debt you're willing to take on
- Show you can ship and iterate

STRATEGIC CONSIDERATIONS (show product depth):
- Competitive dynamics and market timing
- Platform/ecosystem effects
- Build vs buy vs partner decisions
- Business model implications

TECHNICAL CONSIDERATIONS (show engineering depth):
- Architecture approach (monolith vs microservices, sync vs async)
- Data model and storage considerations
- Scalability and performance implications
- Security and privacy requirements
- API design philosophy
- Third-party dependencies and risks

COLLABORATION PROMPTS (work together smoothly):
- "From an architecture perspective, I'm thinking..." - show technical depth
- "What's the current tech stack like?" - gather context
- "I see a tradeoff between X and Y..." - surface decisions
- "Let me check my understanding..." - align frequently
- "That's a great point, building on that..." - yes-and thinking

Remember: As a software engineer, you bring UNIQUE VALUE by combining product thinking with technical feasibility. Show you can bridge both worlds.

<ocr_text>
{ocr_text}
</ocr_text>"""

def call_anthropic_product(ocr_text: str, image_path: Optional[str]) -> Optional[ProductAssistantResponse]:
    try: client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    except Exception as e: print(f"Failed to initialize Anthropic client: {e}"); return None
    content = []
    if image_path:
        with open(image_path, "rb") as f: img_data = base64.b64encode(f.read()).decode("utf-8")
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_data}})
    content.append({"type": "text", "text": build_product_prompt(ocr_text)})
    try:
        message = client.messages.create(model=ANTHROPIC_MODEL, max_tokens=8000, messages=[{"role": "user", "content": content}])
        response_text = "".join(block.text for block in message.content if hasattr(block, "type") and block.type == "text").strip()
        if response_text.startswith("```json"): response_text = response_text[7:]
        elif response_text.startswith("```"): response_text = response_text[3:]
        if response_text.endswith("```"): response_text = response_text[:-3]
        return ProductAssistantResponse(**json.loads(response_text.strip()))
    except Exception as e: print(f"API/Parse error: {e}"); return None

def run_pipeline():
    global _product_results_shown, _system_results_shown
    global _product_view_mode, _system_view_mode
    global _product_full_sections, _system_full_sections
    global _product_diagram_path, _system_diagram_path
    mode = _current_mode
    # Reset diagram path and view mode for this mode
    if mode == "product":
        _product_diagram_path = None
        _product_view_mode = 0
        cheat = product_cheat_sheet
    else:
        _system_diagram_path = None
        _system_view_mode = 0
        cheat = system_cheat_sheet
    mode_label = "PRODUCT" if mode == "product" else "SYSTEM"
    try:
        ui_update_sections([("Status", [f"[{mode_label}] Generating response..."])] + cheat)
        try: shot = take_screenshot()
        except Exception as e: ui_update([f"Screenshot error: {e}"]); return
        ocr_text = ocr_with_tesseract(shot)
        if mode == "product":
            data = call_anthropic_product(ocr_text, image_path=shot)
            if data:
                sections = format_structured_for_overlay_sections(data)
                def _render():
                    global _product_full_sections
                    _product_full_sections = sections
                    _update_overlay_sections(sections); _update_status(f"[{mode_label}] All sections")
                AppHelper.callAfter(_render)
                _product_results_shown = True
                threading.Thread(target=generate_diagram_async, args=(data, mode), daemon=True).start()
            else:
                ui_update([f"[{mode_label}] (No structured response)"])
                _product_results_shown = False
        else:
            data = call_anthropic_system(ocr_text, image_path=shot)
            if data:
                sections = format_system_for_overlay_sections(data)
                def _render():
                    global _system_full_sections
                    _system_full_sections = sections
                    _update_overlay_sections(sections); _update_status(f"[{mode_label}] All sections")
                AppHelper.callAfter(_render)
                _system_results_shown = True
                threading.Thread(target=generate_diagram_async, args=(data, mode), daemon=True).start()
            else:
                ui_update([f"[{mode_label}] (No structured response)"])
                _system_results_shown = False
    except Exception as e:
        ui_update([f"Pipeline error: {e}"])
        if mode == "product":
            _product_results_shown = False
        else:
            _system_results_shown = False
    finally: _is_running.clear()

app = NSApplication.sharedApplication()
window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_((OVERLAY_ORIGIN, (window_width, window_height)), 15, 2, False)
window.setTitlebarAppearsTransparent_(True); window.setSharingType_(0)
window.setBackgroundColor_(NSColor.clearColor()); window.setOpaque_(False); window.setHasShadow_(False)
window.setAlphaValue_(1.0); window.setLevel_(NSFloatingWindowLevel); window.setIgnoresMouseEvents_(True); window.makeKeyAndOrderFront_(None)


class WindowDelegate(NSObject):
    def _report(self):
        frame = window.frame(); msg = f"origin=({int(frame.origin.x)}, {int(frame.origin.y)})  size=({int(frame.size.width)} x {int(frame.size.height)})"
        print(msg, flush=True); _update_status(msg)
    def windowDidResize_(self, notification): self._report()
    def windowDidMove_(self, notification): self._report()

delegate = WindowDelegate.alloc().init(); window.setDelegate_(delegate)

def _apply_interactive_mode():
    global _status_label
    if _interactive_mode:
        window.setIgnoresMouseEvents_(False); window.setHasShadow_(True); window.setMovableByWindowBackground_(True); window.setAlphaValue_(0.7)
        _ensure_status_label(); ui_update(["Interactive mode ON. Press F2 to turn OFF."]); delegate._report()
    else:
        window.setIgnoresMouseEvents_(True); window.setHasShadow_(False); window.setAlphaValue_(1.0)
        if _status_label is not None: _status_label.removeFromSuperview(); _status_label = None
        mode_label = "PRODUCT" if _current_mode == "product" else "SYSTEM"
        ui_update([f'[{mode_label}] Interactive OFF. MIDI 49=switch mode, 50=flip page, 63x2=run/clear'])

def toggle_interactive_mode():
    global _interactive_mode
    _interactive_mode = not _interactive_mode; AppHelper.callAfter(_apply_interactive_mode)

_ensure_window_width_for_columns()
product_cheat_sheet = [
    ("Kickoff Questions", ["Who is the target user?", "What problem are we solving?", "Expected scale/latency requirements?", "Existing systems to integrate?", "Mobile, web, or cross-platform?"]),
    ("Framework (SWE Product)", ["1. Clarify (product + technical)", "2. Users + Personas", "3. Vision + Success Metrics", "4. Features + Technical Complexity", "5. MVP + Architecture Approach"]),
    ("Technical Angles", ["\"What's the current tech stack?\"", "\"From an architecture perspective...\"", "\"I see a tradeoff between X and Y...\"", "\"For scalability, we'd need to consider...\""]),
    ("Show Your Thinking", ["Bridge product + engineering", "State technical assumptions", "Explain WHY not just WHAT", "Be decisive on tradeoffs"]),
]
system_cheat_sheet = [
    ("Framework (System Design)", ["1. Requirements (functional + non-functional)", "2. Capacity Estimation", "3. High-Level Design", "4. Deep Dive Components", "5. Scaling + Reliability"]),
    ("Clarifying Questions", ["What's the expected QPS/DAU?", "Read-heavy or write-heavy?", "Consistency vs availability preference?", "Latency requirements?", "Data retention policy?"]),
    ("Key Components", ["Load Balancer", "API Gateway", "Cache (Redis/Memcached)", "Message Queue", "CDN", "Database (SQL/NoSQL)", "Search (Elasticsearch)"]),
    ("Scaling Patterns", ["Horizontal scaling", "Database sharding", "Read replicas", "Caching layers", "Async processing", "Rate limiting"]),
]
_product_full_sections = product_cheat_sheet
ui_update_sections(product_cheat_sheet)

def _extract_vk(key) -> Optional[int]:
    try:
        if hasattr(key, "vk") and key.vk is not None: return int(key.vk)
        if hasattr(key, "value") and hasattr(key.value, "vk"): return int(key.value.vk)
        if hasattr(key, "vkCode"): return int(key.vkCode)
    except: pass
    return None

def _quit_app():
    def _close(): window.close(); AppHelper.stopEventLoop()
    AppHelper.callAfter(_close)

def on_release(key):
    global _awaiting_second
    global _product_results_shown, _system_results_shown
    global _product_view_mode, _system_view_mode
    try:
        if key == keyboard.Key.f1: _quit_app(); return False
        if key == keyboard.Key.f2: toggle_interactive_mode(); return
        vk = _extract_vk(key)
        if vk is None: return
        if str(vk) == "63":
            if not _awaiting_second: _awaiting_second = True
            else:
                _awaiting_second = False
                # Check if current mode has results shown - if so, clear them
                if _current_mode == "product":
                    if _product_results_shown:
                        _product_results_shown = False
                        _product_view_mode = 0
                        ui_update_sections(product_cheat_sheet)
                        return
                else:
                    if _system_results_shown:
                        _system_results_shown = False
                        _system_view_mode = 0
                        ui_update_sections(system_cheat_sheet)
                        return
                if _is_running.is_set(): ui_update(["Already running…"]); return
                _is_running.set(); threading.Thread(target=run_pipeline, daemon=True).start()
    except Exception as e: ui_update([f"Key handling error: {e}"])

listener = keyboard.Listener(on_release=on_release); listener.start()

_last_note50_ts, _last_note49_ts, _NOTE_DEBOUNCE_SEC = 0.0, 0.0, 0.15

def _switch_mode():
    """Switch between product and system mode, displaying appropriate content."""
    global _current_mode
    _current_mode = "system" if _current_mode == "product" else "product"
    mode_label = "PRODUCT" if _current_mode == "product" else "SYSTEM"
    if _current_mode == "product":
        if _product_results_shown and _product_full_sections:
            # Show saved product results
            _rerender_current_view()
        else:
            # Show product cheat sheet
            ui_update_sections(product_cheat_sheet)
            AppHelper.callAfter(_update_status, f"[{mode_label}] Cheat Sheet")
    else:
        if _system_results_shown and _system_full_sections:
            # Show saved system results
            _rerender_current_view()
        else:
            # Show system cheat sheet
            ui_update_sections(system_cheat_sheet)
            AppHelper.callAfter(_update_status, f"[{mode_label}] Cheat Sheet")

def on_midi(msg):
    try:
        mtype, note, vel = getattr(msg, "type", None), getattr(msg, "note", None), getattr(msg, "velocity", None)
        if mtype != "note_on" or (vel is not None and vel == 0):
            return
        global _last_note50_ts, _last_note49_ts
        now = time.time()
        # Note 49: Switch modes
        if note == 49:
            if now - _last_note49_ts < _NOTE_DEBOUNCE_SEC: return
            _last_note49_ts = now
            AppHelper.callAfter(_switch_mode)
        # Note 50: Flip pages within current mode
        elif note == 50:
            if now - _last_note50_ts < _NOTE_DEBOUNCE_SEC: return
            _last_note50_ts = now
            def _toggle():
                global _product_view_mode, _system_view_mode
                if _current_mode == "product":
                    pages = _PRODUCT_PAGES
                    _product_view_mode = (_product_view_mode + 1) % (len(pages) + 1)
                else:
                    pages = _SYSTEM_PAGES
                    _system_view_mode = (_system_view_mode + 1) % (len(pages) + 1)
                _rerender_current_view()
            AppHelper.callAfter(_toggle)
    except Exception as e: AppHelper.callAfter(ui_update, [f"MIDI error: {e}"])

print("Available MIDI devices:"); [print("  ", name) for name in mido.get_input_names()]
midi_port = mido.open_input(mido.get_input_names()[0], callback=on_midi)
AppHelper.runEventLoop(); midi_port.close()
