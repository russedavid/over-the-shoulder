import os
from typing import List, Optional, Tuple

from constants import *


def _mode_label_and_help(mode: Optional[str] = None) -> Tuple[str, str]:
    if mode == "mode_a": return "DSA", _MIDI_HELP_DSA
    if mode == "product": return "PRODUCT", _MIDI_HELP_PRODUCT
    if mode == "ml_system_design": return "AI SYSTEM", _MIDI_HELP_AI_SYSTEM
    if mode == "pair": return "PAIR", _MIDI_HELP_PAIR
    if mode == "behavioral_pair": return "BEHAVIORAL", _MIDI_HELP_BEHAVIORAL_PAIR
    if mode == "project": return "PROJECT", _MIDI_HELP_PROJECT
    return "SYSTEM", _MIDI_HELP_SYSTEM


def _mode_section(mode: str, description: str) -> Tuple[str, List[str]]:
    label, help_text = _mode_label_and_help(mode)
    return ("Mode", [f"{label} MODE", description, f"Controls: {help_text}", "Keyboard: F10=flip page, Fn+Arrow=move window"])


def wrap_lines(s: str, max_len: int = CHARS_PER_LINE) -> List[str]:
    out = []
    for line in (s.splitlines() or [""]):
        while len(line) > max_len: out.append(line[:max_len]); line = line[max_len:]
        out.append(line)
    return out


def _fmt_list(items, limit=None, sep="; ", fallback="Not specified") -> str:
    vals = [str(x).strip() for x in (items or []) if str(x).strip()]
    if limit is not None: vals = vals[:limit]
    return sep.join(vals) if vals else fallback


def _project_line_budget() -> int:
    lines_per_col = int((BASE_WINDOW_HEIGHT - TOP_MARGIN - BOTTOM_MARGIN) / LINE_HEIGHT) - 4
    return max(36, lines_per_col * COLUMNS)


def _clean_project_markdown_line(line: str) -> str:
    stripped = line.rstrip()
    if not stripped: return ""
    if stripped.startswith(">"): stripped = stripped.lstrip("> ").strip()
    return stripped


def _load_project_text_pages() -> List[List[Tuple[str, List[str]]]]:
    if not os.path.exists(_PROJECT_MD_PATH):
        return [[("Past Project Deep Dive", [f"Missing file: {_PROJECT_MD_PATH}"])]]

    sections: List[Tuple[str, List[str]]] = []
    title: Optional[str] = None
    body: List[str] = []
    with open(_PROJECT_MD_PATH, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if line.startswith("## "):
                if title is not None: sections.append((title, body))
                title, body = line.lstrip("# ").strip(), []
            elif line.startswith("### "):
                if title is not None: sections.append((title, body))
                title, body = line.lstrip("# ").strip(), []
            else:
                body.append(_clean_project_markdown_line(line))
    if title is not None: sections.append((title, body))

    pages: List[List[Tuple[str, List[str]]]] = []
    budget = _project_line_budget()
    for title, raw_lines in sections:
        wrapped: List[str] = []
        previous_blank = False
        for line in raw_lines:
            if not line:
                if not previous_blank:
                    wrapped.append("")
                previous_blank = True
                continue
            previous_blank = False
            wrapped.extend(wrap_lines(line, CHARS_PER_LINE))
        if not wrapped:
            wrapped = [""]
        for start in range(0, len(wrapped), budget):
            chunk = wrapped[start:start + budget]
            page_title = title if start == 0 else f"{title} (continued)"
            pages.append([(page_title, chunk)])
    if not pages:
        pages = [[("Past Project Deep Dive", ["No prepared material found."])]]
    pages[0] = [_mode_section("project", "Prepared past-project deep dive from zpds_system_design.md; final page shows zpds_system_design.png.")] + pages[0]
    return pages


def format_mode_a_for_overlay_sections(data) -> List[Tuple[str, List[str]]]:
    w = CHARS_PER_LINE; sections: List[Tuple[str, List[str]]] = [_mode_section("mode_a", "Coding and DSA interview assistant for problem solving, continuations, and simplification.")]
    opt_lines = wrap_lines(f"Approach: {data.optimal_solution.approach}", w) + wrap_lines(f"Time: {data.optimal_solution.time_complexity}", w)
    opt_lines += wrap_lines(f"Space: {data.optimal_solution.space_complexity}", w) + ["Implementation:"] + wrap_lines(data.optimal_solution.language_specific_implementation, w)
    sections.append(("Optimal Solution", opt_lines))
    sections.append(("Clarifying Questions", [*sum((wrap_lines(f"- {q}", w) for q in data.clarifying_questions), [])]))
    sections.append(("Edge Cases", [*sum((wrap_lines(f"- {ec}", w) for ec in data.edge_cases), [])]))
    sections.append(("Test Cases", [*sum((wrap_lines(f"- {tc}", w) for tc in data.test_cases), [])]))
    sections.append(("Limitations", [*sum((wrap_lines(f"- {lim}", w) for lim in data.limitations), [])]))
    return sections


def format_system_for_overlay_sections(data) -> List[Tuple[str, List[str]]]:
    w = CHARS_PER_LINE; sections: List[Tuple[str, List[str]]] = [_mode_section("system", "Backend/system architecture interview answer with requirements, APIs, data models, and deep dives.")]
    sections.append(("Functional Requirements", [*sum((wrap_lines(f"- {req}", w) for req in data.functional_requirements), [])]))
    sections.append(("Non-Functional Reqs", [*sum((wrap_lines(f"- {req}", w) for req in data.non_functional_requirements), [])]))
    if data.capacity_estimation:
        ce_lines = [*sum((wrap_lines(f"- {calc}", w) for calc in data.capacity_estimation.calculations), [])]
        ce_lines += wrap_lines(f"Impact: {data.capacity_estimation.impact_on_design}", w)
        sections.append(("Capacity Estimation", ce_lines))
    sections.append(("Core Entities", [*sum((wrap_lines(f"- {entity}", w) for entity in data.core_entities), [])]))
    api_lines = wrap_lines(f"Protocol: {data.api_design.protocol}", w) + ["Endpoints:"]
    api_lines += [*sum((wrap_lines(f"  - {endpoint}", w) for endpoint in data.api_design.endpoints), [])]
    sections.append(("API Design", api_lines))
    if data.data_flow: sections.append(("Data Flow", [*sum((wrap_lines(f"- {step}", w) for step in data.data_flow), [])]))
    hld_lines = ["Components:"] + [*sum((wrap_lines(f"  - {comp}", w) for comp in data.high_level_design.components), [])] + [""]
    hld_lines += wrap_lines(f"Flow: {data.high_level_design.data_flow_description}", w)
    sections.append(("High-Level Design", hld_lines))
    dm_lines = []
    for dm in data.data_models: dm_lines += wrap_lines(f"[{dm.entity}]", w) + [*sum((wrap_lines(f"  - {field}", w) for field in dm.key_fields), [])] + [""]
    sections.append(("Data Models", dm_lines))
    if data.component_descriptions:
        cd_lines = []
        for cd in data.component_descriptions:
            cd_lines += wrap_lines(f"[{cd.component}]", w) + wrap_lines(f"  Role: {cd.role}", w) + wrap_lines(f"  Rationale: {cd.rationale}", w)
            cd_lines += ["  Implementation Details:"] + [*sum((wrap_lines(f"    • {impl}", w) for impl in cd.implementation_details), [])] + [""]
        sections.append(("Component Descriptions", cd_lines))
    dd_lines = []
    for dd in data.deep_dives:
        dd_lines += wrap_lines(f"[{dd.area}]", w) + wrap_lines(f"  Problem: {dd.problem}", w) + wrap_lines(f"  Solution: {dd.solution}", w)
        dd_lines += ["  Tradeoffs:"] + [*sum((wrap_lines(f"    • {t}", w) for t in dd.tradeoffs), [])]
        dd_lines += ["  Implementation:"] + [*sum((wrap_lines(f"    • {impl}", w) for impl in dd.implementation_details), [])] + [""]
    sections.append(("Deep Dives", dd_lines))
    return sections


def format_ai_system_for_overlay_sections(data) -> List[Tuple[str, List[str]]]:
    w = CHARS_PER_LINE; sections: List[Tuple[str, List[str]]] = [_mode_section("ml_system_design", "AI product system design answer with a simple core architecture and advanced AI concerns reserved for deep dives.")]
    sections.append(("Functional Requirements", [*sum((wrap_lines(f"- {req}", w) for req in data.functional_requirements), [])]))
    sections.append(("Non-Functional Reqs", [*sum((wrap_lines(f"- {req}", w) for req in data.non_functional_requirements), [])]))
    if data.capacity_estimation:
        ce_lines = [*sum((wrap_lines(f"- {calc}", w) for calc in data.capacity_estimation.calculations), [])]
        ce_lines += wrap_lines(f"Impact: {data.capacity_estimation.impact_on_design}", w)
        sections.append(("Capacity Estimation", ce_lines))
    sections.append(("Core Entities", [*sum((wrap_lines(f"- {entity}", w) for entity in data.core_entities), [])]))
    api_lines = wrap_lines(f"Protocol: {data.api_design.protocol}", w) + ["Endpoints:"]
    api_lines += [*sum((wrap_lines(f"  - {endpoint}", w) for endpoint in data.api_design.endpoints), [])]
    sections.append(("API Design", api_lines))
    if data.data_flow: sections.append(("Data Flow", [*sum((wrap_lines(f"- {step}", w) for step in data.data_flow), [])]))
    hld_lines = ["Components:"] + [*sum((wrap_lines(f"  - {comp}", w) for comp in data.high_level_design.components), [])] + [""]
    hld_lines += wrap_lines(f"Flow: {data.high_level_design.data_flow_description}", w)
    sections.append(("High-Level Design", hld_lines))
    dm_lines = []
    for dm in data.data_models: dm_lines += wrap_lines(f"[{dm.entity}]", w) + [*sum((wrap_lines(f"  - {field}", w) for field in dm.key_fields), [])] + [""]
    sections.append(("Data Models", dm_lines))
    if data.component_descriptions:
        cd_lines = []
        for cd in data.component_descriptions:
            cd_lines += wrap_lines(f"[{cd.component}]", w) + wrap_lines(f"  Role: {cd.role}", w) + wrap_lines(f"  Rationale: {cd.rationale}", w)
            cd_lines += ["  Implementation Details:"] + [*sum((wrap_lines(f"    - {impl}", w) for impl in cd.implementation_details), [])] + [""]
        sections.append(("Component Descriptions", cd_lines))
    dd_lines = []
    for dd in data.deep_dives:
        dd_lines += wrap_lines(f"[{dd.area}]", w) + wrap_lines(f"  Problem: {dd.problem}", w) + wrap_lines(f"  Solution: {dd.solution}", w)
        dd_lines += ["  Tradeoffs:"] + [*sum((wrap_lines(f"    - {t}", w) for t in dd.tradeoffs), [])]
        dd_lines += ["  Implementation:"] + [*sum((wrap_lines(f"    - {impl}", w) for impl in dd.implementation_details), [])] + [""]
    sections.append(("Deep Dives", dd_lines))
    return sections


def format_product_system_for_overlay_sections(data) -> List[Tuple[str, List[str]]]:
    w = CHARS_PER_LINE; sections: List[Tuple[str, List[str]]] = [_mode_section("product", "Product-first system design answer centered on user journey, frontend behavior, tradeoffs, and edge cases.")]
    sections.append(("Functional Requirements", [*sum((wrap_lines(f"- {req}", w) for req in data.functional_requirements), [])]))
    sections.append(("Non-Functional Reqs", [*sum((wrap_lines(f"- {req}", w) for req in data.non_functional_requirements), [])]))
    if data.capacity_estimation:
        ce_lines = [*sum((wrap_lines(f"- {calc}", w) for calc in data.capacity_estimation.calculations), [])]
        ce_lines += wrap_lines(f"Impact: {data.capacity_estimation.impact_on_design}", w)
        sections.append(("Capacity Estimation", ce_lines))
    journey_lines = []
    for s in data.user_journey:
        journey_lines += wrap_lines(f"{s.step}. {s.actor} - {s.action} -> {s.system_component} -> {s.result}", w)
    sections.append(("User Journey", journey_lines or ["No user journey provided."]))
    if data.frontend_design:
        fd = data.frontend_design
        fd_lines = ["Surfaces:"] + [*sum((wrap_lines(f"  - {surface}", w) for surface in fd.surfaces), [])]
        if fd.client_state: fd_lines += wrap_lines(f"Client state: {fd.client_state}", w)
        if fd.realtime_or_offline_behavior: fd_lines += wrap_lines(f"Realtime/offline: {fd.realtime_or_offline_behavior}", w)
        if fd.error_states:
            fd_lines += ["Error states:"] + [*sum((wrap_lines(f"  - {err}", w) for err in fd.error_states), [])]
        sections.append(("Frontend Design", fd_lines))
    sections.append(("Core Entities", [*sum((wrap_lines(f"- {entity}", w) for entity in data.core_entities), [])]))
    api_lines = wrap_lines(f"Protocol: {data.api_design.protocol}", w) + ["Endpoints:"]
    api_lines += [*sum((wrap_lines(f"  - {endpoint}", w) for endpoint in data.api_design.endpoints), [])]
    sections.append(("API Design", api_lines))
    if data.data_flow: sections.append(("Data Flow", [*sum((wrap_lines(f"- {step}", w) for step in data.data_flow), [])]))
    hld_lines = ["Components:"] + [*sum((wrap_lines(f"  - {comp}", w) for comp in data.high_level_design.components), [])] + [""]
    hld_lines += wrap_lines(f"Flow: {data.high_level_design.data_flow_description}", w)
    sections.append(("High-Level Design", hld_lines))
    dm_lines = []
    for dm in data.data_models: dm_lines += wrap_lines(f"[{dm.entity}]", w) + [*sum((wrap_lines(f"  - {field}", w) for field in dm.key_fields), [])] + [""]
    sections.append(("Data Models", dm_lines))
    if data.component_descriptions:
        cd_lines = []
        for cd in data.component_descriptions:
            cd_lines += wrap_lines(f"[{cd.component}]", w) + wrap_lines(f"  Role: {cd.role}", w) + wrap_lines(f"  Rationale: {cd.rationale}", w)
            cd_lines += ["  Implementation Details:"] + [*sum((wrap_lines(f"    - {impl}", w) for impl in cd.implementation_details), [])] + [""]
        sections.append(("Component Descriptions", cd_lines))
    tradeoff_lines = []
    for t in data.tradeoffs:
        tradeoff_lines += wrap_lines(f"[{t.decision}]", w) + wrap_lines(f"  Options: {_fmt_list(t.options_considered, sep=', ')}", w)
        tradeoff_lines += wrap_lines(f"  Chosen: {t.chosen_option}", w) + wrap_lines(f"  Why: {t.rationale}", w) + [""]
    sections.append(("Tradeoffs", tradeoff_lines or ["No tradeoffs provided."]))
    edge_lines = []
    for e in data.edge_cases: edge_lines += wrap_lines(f"- {e.scenario}: {e.handling}", w)
    sections.append(("Edge Cases", edge_lines or ["No edge cases provided."]))
    if data.llm_design:
        llm = data.llm_design
        llm_lines = [f"Needed: {'yes' if llm.needed else 'no'}"]
        if llm.use_cases: llm_lines += ["Use cases:"] + [*sum((wrap_lines(f"  - {uc}", w) for uc in llm.use_cases), [])]
        if llm.components: llm_lines += ["Components:"] + [*sum((wrap_lines(f"  - {comp}", w) for comp in llm.components), [])]
        if llm.risks: llm_lines += ["Risks:"] + [*sum((wrap_lines(f"  - {risk}", w) for risk in llm.risks), [])]
        if llm.mitigations: llm_lines += ["Mitigations:"] + [*sum((wrap_lines(f"  - {m}", w) for m in llm.mitigations), [])]
        sections.append(("LLM Design", llm_lines))
    dd_lines = []
    for dd in data.deep_dives:
        dd_lines += wrap_lines(f"[{dd.area}]", w) + wrap_lines(f"  Problem: {dd.problem}", w) + wrap_lines(f"  Solution: {dd.solution}", w)
        dd_lines += ["  Tradeoffs:"] + [*sum((wrap_lines(f"    - {t}", w) for t in dd.tradeoffs), [])]
        dd_lines += ["  Implementation:"] + [*sum((wrap_lines(f"    - {impl}", w) for impl in dd.implementation_details), [])] + [""]
    sections.append(("Deep Dives", dd_lines))
    return sections


__all__ = [
    "_mode_label_and_help",
    "_mode_section",
    "wrap_lines",
    "_load_project_text_pages",
    "format_mode_a_for_overlay_sections",
    "format_system_for_overlay_sections",
    "format_ai_system_for_overlay_sections",
    "format_product_system_for_overlay_sections",
]
