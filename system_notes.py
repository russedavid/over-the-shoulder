from typing import List, Tuple, Optional

CHARS_PER_LINE = 90

def wrap_lines(s: str, max_len: int = CHARS_PER_LINE) -> List[str]:
    out = []
    for line in (s.splitlines() or [""]):
        while len(line) > max_len:
            out.append(line[:max_len])
            line = line[max_len:]
        out.append(line)
    return out

def format_system_notes(data):
    w = CHARS_PER_LINE
    sections: List[Tuple[str, List[str]]] = []

    # Functional Requirements
    fr = data.get("functional_requirements", [])
    sections.append(("Functional Requirements", [ln for req in fr for ln in wrap_lines(f"- {req}", w)]))

    # Non-Functional Requirements
    nfr = data.get("non_functional_requirements", [])
    sections.append(("Non-Functional Reqs", [ln for req in nfr for ln in wrap_lines(f"- {req}", w)]))

    # Capacity Estimation (optional)
    ce = data.get("capacity_estimation")
    if ce:
        ce_lines: List[str] = []
        for calc in ce.get("calculations", []):
            ce_lines += wrap_lines(f"- {calc}", w)
        impact = ce.get("impact_on_design")
        if impact:
            ce_lines += wrap_lines(f"Impact: {impact}", w)
        sections.append(("Capacity Estimation", ce_lines))

    # Core Entities
    core = data.get("core_entities", [])
    sections.append(("Core Entities", [ln for e in core for ln in wrap_lines(f"- {e}", w)]))

    # API Design
    api = data.get("api_design", {}) or {}
    api_lines: List[str] = []
    if "protocol" in api and api["protocol"]:
        api_lines += wrap_lines(f"Protocol: {api['protocol']}", w)
    api_lines += ["Endpoints:"]
    for ep in api.get("endpoints", []):
        api_lines += wrap_lines(f"  - {ep}", w)
    sections.append(("API Design", api_lines))

    # Data Flow (optional)
    df = data.get("data_flow")
    if df:
        sections.append(("Data Flow", [ln for step in df for ln in wrap_lines(f"- {step}", w)]))

    # High-Level Design
    hld = data.get("high_level_design", {}) or {}
    hld_lines: List[str] = []
    hld_lines += ["Components:"]
    for comp in hld.get("components", []):
        hld_lines += wrap_lines(f"  - {comp}", w)
    hld_lines += [""]
    if hld.get("data_flow_description"):
        hld_lines += wrap_lines(f"Flow: {hld['data_flow_description']}", w)
    sections.append(("High-Level Design", hld_lines))

    # Data Models (list of {entity, key_fields})
    dms = data.get("data_models", []) or []
    dm_lines: List[str] = []
    for dm in dms:
        entity = dm.get("entity", "Entity")
        dm_lines += wrap_lines(f"[{entity}]", w)
        for field in dm.get("key_fields", []):
            dm_lines += wrap_lines(f"  - {field}", w)
        dm_lines += [""]
    sections.append(("Data Models", dm_lines))

    # Component Descriptions (list of {component, role, rationale, implementation_details})
    cds = data.get("component_descriptions", []) or []
    if cds:
        cd_lines: List[str] = []
        for cd in cds:
            cd_lines += wrap_lines(f"[{cd.get('component','Component')}]", w)
            if cd.get("role"):       cd_lines += wrap_lines(f"  Role: {cd['role']}", w)
            if cd.get("rationale"):  cd_lines += wrap_lines(f"  Rationale: {cd['rationale']}", w)
            cd_lines += ["  Implementation Details:"]
            for impl in cd.get("implementation_details", []):
                cd_lines += wrap_lines(f"    • {impl}", w)
            cd_lines += [""]
        sections.append(("Component Descriptions", cd_lines))

    # Deep Dives (list of {area, problem, solution, tradeoffs, implementation_details})
    dds = data.get("deep_dives", []) or []
    dd_lines: List[str] = []
    for dd in dds:
        dd_lines += wrap_lines(f"[{dd.get('area','Area')}]", w)
        if dd.get("problem"):   dd_lines += wrap_lines(f"  Problem: {dd['problem']}", w)
        if dd.get("solution"):  dd_lines += wrap_lines(f"  Solution: {dd['solution']}", w)
        dd_lines += ["  Tradeoffs:"]
        for t in dd.get("tradeoffs", []):
            dd_lines += wrap_lines(f"    • {t}", w)
        dd_lines += ["  Implementation:"]
        for impl in dd.get("implementation_details", []):
            dd_lines += wrap_lines(f"    • {impl}", w)
        dd_lines += [""]
    sections.append(("Deep Dives", dd_lines))

    return sections

system_data = {
    "functional_requirements": [
        "Define top 3 user/client actions (“Users should be able to…”).",
        "Ask clarifying PM-style questions (e.g., “Does the system need X?”, “What happens if Y?”).",
        "Keep scope small; prioritize essentials over nice-to-have.",
        "Examples: Twitter → post tweets, follow users, view feed.",
        "Examples: Cache → insert items, set expirations, read items.",
    ],
    "non_functional_requirements": [
        "Define system qualities (“The system should be…”).",
        "Pick 3–5 most relevant (not generic).",
        "Quantify when possible (e.g., latency <200ms, scale to 100M DAU).",
        "Probe relevance: availability vs consistency, scalability, latency, durability, security, fault tolerance, compliance.",
        "Examples: Twitter → highly available, <200ms feed latency, 100M+ DAU scale.",
        "Examples: Banking → strict durability, regulatory compliance, high security.",
    ],
    # Include only if needed; otherwise set to None
    "capacity_estimation": {
        "calculations": [
            "Estimate selectively: QPS, DAU, storage size, set size.",
            "Example: trending topics → estimate topic count to choose heap vs sharding."
        ],
        "impact_on_design": "Only perform estimates that directly change a design choice (e.g., algorithm, sharding, cache size)."
    },
    "core_entities": [
        "Identify main nouns/resources; refine later.",
        "Ask: “Who are the actors?” “What data do we store?”",
        "Examples: Twitter → User, Tweet, Follow.",
        "Examples: E-commerce → User, Product, Order.",
    ],
    "api_design": {
        "protocol": "REST by default; GraphQL for diverse clients; RPC for fast internal calls.",
        "endpoints": [
            "POST /v1/tweets → create tweet",
            "GET /v1/feed → list feed tweets"
        ],
    },
    # Optional; include if multi-stage processing is relevant
    "data_flow": [
        "Outline input → output pipeline for multi-stage systems.",
        "Example (Web crawler): fetch → parse → extract → store → repeat."
    ],
    "high_level_design": {
        "components": [
            "API gateway/load balancer",
            "Application servers",
            "Databases and caches",
            "Queues/streaming for async work",
            "Background workers/services"
        ],
        "data_flow_description": "Build step-by-step from endpoints; focus on functional path first, then layer non-functional needs. Narrate state changes and data flow."
    },
    # Leave empty unless you’re documenting specific schemas
    "data_models": [],
    # Leave empty unless you want per-component writeups
    "component_descriptions": [],
    "deep_dives": [
        {
            "area": "Twitter feed delivery",
            "problem": "Balancing write amplification vs read latency for large follower graphs.",
            "solution": "Evaluate fanout-on-read vs fanout-on-write based on traffic patterns and SLAs.",
            "tradeoffs": [
                "Fanout-on-write: faster reads, heavy writes, storage overhead.",
                "Fanout-on-read: cheaper writes, slower reads, hot-key risks.",
                "Hybrid: cache hot users’ timelines; compute cold timelines on read."
            ],
            "implementation_details": [
                "Use write queues and per-user timeline stores for high-fanout accounts.",
                "Apply cache TTLs and background refresh for hot timelines."
            ]
        },
        {
            "area": "Database scaling",
            "problem": "Single-node limits on throughput, latency, and dataset size.",
            "solution": "Introduce caching, read replicas, and horizontal sharding.",
            "tradeoffs": [
                "Sharding adds operational complexity and rebalancing.",
                "Caching risks staleness; needs invalidation strategy.",
                "Strong consistency vs availability choices under failure."
            ],
            "implementation_details": [
                "Key-based sharding with a stable hash; plan for resharding.",
                "Read-through/write-through cache; explicit invalidation on writes."
            ]
        }
    ]
}
