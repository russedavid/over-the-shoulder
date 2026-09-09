"use strict";
const state = {
  index: null,
  data: null,
  session: null,
  stage: "summary",
  audio: 0,
  image: null,
  point: null,
  review: null,
  dirty: false,
  loadSerial: 0,
};
const $ = (id) => document.getElementById(id);
const time = (t) =>
  `${Math.floor((t || 0) / 60)}:${String(Math.floor((t || 0) % 60)).padStart(2, "0")}`;
function el(tag, text = "", className = "") {
  const n = document.createElement(tag);
  if (text !== null) n.textContent = text;
  if (className) n.className = className;
  return n;
}
function button(text, action, className = "") {
  const n = el("button", text, className);
  n.addEventListener("click", action);
  return n;
}
function append(parent, ...children) {
  children
    .flat()
    .filter(Boolean)
    .forEach((child) => parent.append(child));
  return parent;
}
function card(title, ...children) {
  return append(el("div", null, "card"), el("h2", title), ...children);
}
function pre(text) {
  return el("pre", text || "—");
}
function wordEdits(candidate, reference) {
  const tokenize = (t) =>
    t.toLowerCase().match(/\p{L}[\p{L}\p{N}'’-]*|\p{N}+/gu) || [];
  const a = tokenize(reference),
    b = tokenize(candidate);
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const next = [i];
    for (let j = 1; j <= b.length; j++)
      next[j] = Math.min(
        next[j - 1] + 1,
        prev[j] + 1,
        prev[j - 1] + Number(a[i - 1] !== b[j - 1]),
      );
    prev = next;
  }
  return { edits: prev[b.length], reference: a.length, candidate: b.length };
}
function audioComparison(id, row, text) {
  const review = currentReview(id, "transcript");
  if (review?.status === "approved") {
    if (/\[(unclear|inaudible)\]/i.test(text))
      return "Your reference marks unclear speech; no word error rate is calculated.";
    const d = wordEdits(row.candidate?.text || "", text);
    return d.reference
      ? `${d.edits} word edits / ${d.reference} words in your approved reference (${((100 * d.edits) / d.reference).toFixed(1)}% word error rate).`
      : `Your approved reference contains no speech; the new system transcribed ${d.candidate} words.`;
  }
  return row.comparison
    ? `${row.comparison.word_edits} word edits against ${row.comparison.reference_words} provisional reference words. This is a disagreement signal, not verified transcription accuracy.`
    : "The independent transcription comparison is processing.";
}
function readingText(data) {
  return data
    ? [
        "## Inferred task",
        data.inferred_task,
        "## Visible facts",
        ...(data.facts || []).map(
          (f) =>
            f.description +
            "\nEvidence: " +
            f.evidence +
            "\nCertainty: " +
            f.certainty,
        ),
        "## Uncertainty",
        ...(data.uncertainties || []),
      ].join("\n\n")
    : "";
}

function notice(text) {
  return el("div", text, "notice");
}
function toast(text) {
  $("toast").textContent = text;
  $("toast").style.display = "block";
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => ($("toast").style.display = "none"), 4500);
}
async function get(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error("This recorded item is unavailable.");
  return r.json();
}
function md(text) {
  const root = el("div", null, "markdown");
  let code = false,
    lines = [];
  const flush = () => {
    if (lines.length) {
      root.append(code ? pre(lines.join("\n")) : el("p", lines.join("\n")));
      lines = [];
    }
  };
  for (const line of (text || "").split("\n")) {
    if (line.startsWith("```")) {
      flush();
      code = !code;
      continue;
    }
    if (!code && /^#{1,4} /.test(line)) {
      flush();
      root.append(el("h3", line.replace(/^#+ /, "")));
    } else if (!code && !line.trim()) {
      flush();
    } else lines.push(line);
  }
  flush();
  return root;
}
function details(title, ...body) {
  return append(el("details"), el("summary", title), ...body);
}
function sourceLinks(ids = []) {
  const root = el("div", null, "source-links");
  for (const id of [...new Set(ids)]) {
    const a = state.index.assets[id];
    if (a)
      root.append(
        button(
          `${a.kind === "audio" ? (a.channel === "mic" ? "Mic" : "System") : "Screen"} · ${time(a.end ?? a.at)}`,
          () => jumpSource(id),
        ),
      );
    else root.append(el("span", "Unresolved citation: " + id, "small"));
  }
  return root;
}
function currentReview(id, component) {
  return (state.data?.reviews || state.index.reviews || {})[
    id + ":" + component
  ];
}
function corrected(id, component, fallback) {
  const r = currentReview(id, component);
  return r?.reference_edited ? r.corrected_text : fallback || "";
}
function referenceLabel(id, component) {
  const r = currentReview(id, component);
  return r?.status === "approved"
    ? "Your approved reference"
    : r?.reference_edited
      ? "Your edited reference draft"
      : "Independent reference draft";
}
function reviewButton(id, component, text, hash) {
  return button(
    "Review this " + (component === "assistance" ? "response" : component),
    () => openReview(id, component, text, hash),
  );
}
function openReview(id, component, text, hash) {
  const r = currentReview(id, component);
  state.review = { id, component, hash: hash || "", baseText: text || "" };
  $("review").hidden = false;
  $("review-label").textContent =
    component === "assistance"
      ? "Judge the new response and refine the reference. Different correct solutions are acceptable."
      : "Judge the new output against the original evidence, then correct or approve its reference.";
  $("verdict").value = r?.candidate_verdict || "unreviewed";
  $("reference-status").value = r?.status || "unreviewed";
  $("correction").value = r?.reference_edited ? r.corrected_text : text || "";
  $("notes").value = r?.notes || "";
  $("save-status").textContent = r ? "Previously saved review loaded." : "";
  state.dirty = false;
  $("review").scrollIntoView({ behavior: "smooth", block: "start" });
}
function canNavigate() {
  if (state.dirty) {
    toast("Save your review before moving to another item.");
    return false;
  }
  return true;
}
function hashLocation() {
  const p = new URLSearchParams({
    session: state.session || "loose",
    stage: state.stage,
  });
  if (state.point) p.set("point", state.point);
  history.replaceState(null, "", "#" + p.toString());
}
function groups() {
  const result = new Map();
  for (const id of state.data.session.audio) {
    const a = state.index.assets[id];
    if (!result.has(a.chunk))
      result.set(a.chunk, {
        chunk: a.chunk,
        at: a.start ?? a.end ?? 0,
        ids: [],
      });
    const g = result.get(a.chunk);
    g.at = Math.min(g.at, a.start ?? 0);
    g.ids.push(id);
  }
  return [...result.values()].sort((a, b) => a.at - b.at);
}
async function refresh() {
  if (!canNavigate()) return;
  const old = state.session;
  state.index = await get("/api/index");
  drawSessions();
  if (old) await loadSession(old, false);
  else if (!state.data) {
    const params = new URLSearchParams(location.hash.slice(1));
    state.stage = params.get("stage") || "summary";
    state.point = params.get("point");
    const id = params.get("session");
    if (id === "loose") await loose();
    else
      await loadSession(
        state.index.sessions.some((s) => s.id === id)
          ? id
          : state.index.sessions.reduce((best, session) =>
              session.duration > best.duration ? session : best,
            ).id,
        false,
      );
  } else await loose();
}
function drawSessions() {
  const nav = $("sessions");
  nav.replaceChildren();
  const reviewed = state.index.sessions.filter((session) => {
    const point = session.checkpoints.find((p) => p.primary);
    const verdict =
      state.index.reviews[point?.id + ":assistance"]?.candidate_verdict;
    return verdict && verdict !== "unreviewed";
  }).length;
  document.querySelector("header span").textContent =
    `${state.index.run_label || "Review"} · ${reviewed}/${state.index.sessions.length} primary responses reviewed`;
  document.title = `${state.index.run_label || "Recorded review"} · OTSC`;
  for (const [i, s] of state.index.sessions.entries()) {
    const n = button(`${i + 1}. ${s.title}`, () => loadSession(s.id));
    n.setAttribute("aria-selected", String(s.id === state.session));
    append(
      n,
      el(
        "span",
        `${s.started.slice(5, 16)} · ${time(s.duration)} · ${s.replayed ? "new replay ready" : "processing"}`,
        "small",
      ),
    );
    nav.append(n);
  }
}
async function loadSession(id, reset = true) {
  if (!canNavigate()) return;
  const serial = ++state.loadSerial;
  const data = await get("/api/session/" + encodeURIComponent(id));
  if (serial !== state.loadSerial) return;
  state.data = data;
  state.session = id;
  if (reset) {
    state.stage = "summary";
    state.audio = 0;
    state.image = null;
    state.point = null;
  }
  state.point =
    state.point ||
    state.data.session.checkpoints.find((p) => p.primary)?.id ||
    state.data.session.checkpoints[0]?.id;
  state.image = state.image || state.data.session.screens[0];
  drawSessions();
  render();
}
async function loose() {
  if (!canNavigate()) return;
  state.session = null;
  state.stage = "screens";
  state.data = {
    session: {
      screens: state.index.unassigned_screens,
      audio: [],
      checkpoints: [],
      legacy: [],
    },
    images: {},
    reviews: state.index.reviews,
  };
  state.image = state.index.unassigned_screens[0];
  for (const id of state.index.unassigned_screens)
    state.data.images[id] = await get("/api/image/" + id);
  drawSessions();
  render();
}
async function jumpSource(id) {
  if (!canNavigate()) return;
  const a = state.index.assets[id];
  if (!a) return;
  if (a.session_id && a.session_id !== state.session)
    await loadSession(a.session_id);
  if (!a.session_id && state.session) await loose();
  if (a.kind === "audio") {
    state.stage = "audio";
    state.audio = Math.max(
      0,
      groups().findIndex((g) => g.ids.includes(id)),
    );
  } else {
    state.stage = "screens";
    state.image = id;
  }
  render();
  $("content").scrollIntoView({ behavior: "smooth", block: "start" });
}
function render() {
  document.querySelectorAll("audio").forEach((a) => a.pause());
  $("review").hidden = true;
  state.review = null;
  state.dirty = false;
  $("content").replaceChildren();
  $("heading").replaceChildren();
  $("tabs").replaceChildren();
  const s = state.data.session;
  const meta = state.index.sessions.find((x) => x.id === state.session);
  append(
    $("heading"),
    el(
      "div",
      state.session
        ? "Recorded session · " + s.started
        : "Unassigned source images",
      "eyebrow",
    ),
    el("h1", meta?.title || "Other screenshots"),
    el(
      "p",
      state.session
        ? `${time(s.duration)} recorded · ${s.audio.length} audio files · ${s.screens.length} original screenshots · ${s.checkpoints.length} replay checkpoints`
        : "These older images have no supported link to a recorded audio session. Their OCR and vision outputs are evaluated separately.",
      "muted",
    ),
  );
  const stages = state.session
    ? ["summary", "audio", "screens", "responses"]
    : ["screens"];
  for (const tab of stages) {
    const b = button(
      {
        summary: "Start here",
        audio: "Audio",
        screens: "Screens & vision",
        responses: "New responses",
      }[tab],
      () => {
        if (canNavigate()) {
          state.stage = tab;
          render();
        }
      },
    );
    b.setAttribute("aria-selected", String(state.stage === tab));
    $("tabs").append(b);
  }
  if (!stages.includes(state.stage)) state.stage = stages[0];
  hashLocation();
  ({
    summary: drawSummary,
    audio: drawAudio,
    screens: drawScreens,
    responses: drawResponses,
  })[state.stage]();
}
function drawSummary() {
  const root = $("content"),
    s = state.data.session;
  const primary = state.data.references?.[state.point]?.reference;
  root.append(
    notice(
      "The candidate outputs below were produced by the current OTSC code. Historical responses were excluded from both the new replay and the reference drafts.",
    ),
  );
  if (primary)
    root.append(
      card(
        "What is happening at the selected checkpoint",
        el("p", primary.evidence_summary),
        button(
          "Review the new response",
          () => {
            state.stage = "responses";
            render();
          },
          "primary",
        ),
      ),
    );
  const kpis = el("div", null, "kpis");
  for (const [number, label] of [
    [s.audio.length, "new audio transcriptions"],
    [s.screens.length, "source screenshots"],
    [state.data.replay.checkpoints?.length || 0, "new-system checkpoints"],
  ])
    kpis.append(
      append(
        el("div", null, "card"),
        el("strong", String(number)),
        el("span", label, "small"),
      ),
    );
  root.append(kpis);
  const list = card("Choose a point in the trace");
  for (const p of s.checkpoints) {
    const r = state.data.references[p.id]?.reference;
    const a = state.data.assessments[p.id]?.assessment;
    const row = el("div", null, "result-row");
    append(
      row,
      button(
        `${time(p.at)} · ${r?.title || p.reason}${p.primary ? " · start here" : ""}`,
        () => {
          state.point = p.id;
          state.stage = "responses";
          render();
        },
      ),
      el(
        "p",
        a
          ? `${a.passed === true ? "Provisional pass" : a.passed === false ? "Provisional fail" : "Needs review"}${a.first_failure ? " · " + a.first_failure : ""}`
          : "Reference or assessment is processing.",
        "small",
      ),
    );
    list.append(row);
  }
  root.append(list);
  root.append(
    card(
      "How to review",
      el(
        "p",
        "Start with the new response and its draft reference. Follow a source link when something looks wrong. In Audio, listen to either channel or both and compare the two recognizers. In Screens & vision, inspect the original pixels alongside OCR and image interpretation.",
      ),
      el(
        "p",
        "Your judgments and edited references are saved separately from model output. Approving a reference never rewrites the original recording or the new system’s response.",
      ),
      details(
        "Timing and evaluation limits",
        el("p", s.timing),
        el("p", state.data.replay.mode || "Manual-help checkpoint replay"),
        el(
          "p",
          "All eight sessions are discovery and review data. The current context limits and model choices are retained; this is not a hardware-capture or real-time latency benchmark.",
        ),
      ),
    ),
  );
}
function drawAudio() {
  const root = $("content"),
    items = groups();
  if (!items.length) {
    root.append(el("p", "No audio was recorded in this session.", "empty"));
    return;
  }
  state.audio = Math.min(state.audio, items.length - 1);
  const selected = items[state.audio];
  const toolbar = el("div", null, "toolbar");
  const search = el("input");
  search.type = "search";
  search.placeholder = "Search both transcripts";
  search.setAttribute("aria-label", "Search transcripts");
  const flag = el("input");
  flag.type = "checkbox";
  const label = append(
    el("label"),
    flag,
    document.createTextNode("Disagreements only"),
  );
  append(
    toolbar,
    button("Previous clip", () => moveAudio(-1)),
    button("Next clip", () => moveAudio(1)),
    search,
    label,
  );
  root.append(toolbar);
  const playerCard = card(
    `Audio at ${time(selected.at)} · clip ${state.audio + 1} of ${items.length}`,
  );
  playerCard.append(
    el(
      "p",
      "Microphone and system are capture routes. They are useful speaker hints, not verified identities.",
      "caption",
    ),
  );
  const players = el("div", null, "columns");
  for (const id of selected.ids) {
    const a = state.index.assets[id],
      p = el("audio");
    p.controls = true;
    p.preload = "metadata";
    p.src = "/media/" + id;
    p.dataset.asset = id;
    players.append(
      append(
        el("div"),
        el(
          "h3",
          a.channel === "mic"
            ? "Microphone · usually you"
            : "System audio · usually others",
        ),
        p,
      ),
    );
  }
  playerCard.append(players);
  const speed = el("select");
  for (const rate of [1, 1.25, 1.5, 2]) {
    const o = el("option", rate + "×");
    o.value = rate;
    speed.append(o);
  }
  speed.setAttribute("aria-label", "Playback speed");
  speed.onchange = () =>
    players
      .querySelectorAll("audio")
      .forEach((p) => (p.playbackRate = Number(speed.value)));
  playerCard.append(
    append(
      el("div", null, "actions"),
      button("Play both", () =>
        Promise.all(
          [...players.querySelectorAll("audio")].map((p) => p.play()),
        ).catch(() =>
          toast("Use either player’s play button to start playback."),
        ),
      ),
      button("Pause", () =>
        players.querySelectorAll("audio").forEach((p) => p.pause()),
      ),
      speed,
      el("span", "J / K: previous / next clip", "small"),
    ),
  );
  root.append(playerCard);
  for (const id of selected.ids) {
    const a = state.index.assets[id],
      r = state.data.audio[id] || {},
      draft = corrected(id, "transcript", r.reference?.text),
      c = card(
        a.channel === "mic" ? "Microphone transcript" : "System transcript",
      );
    const cols = el("div", null, "columns");
    cols.append(
      append(
        el("div"),
        el("h3", "New system · Parakeet"),
        el(
          "div",
          r.candidate?.error || r.candidate?.text || "[No speech transcribed]",
          "transcript",
        ),
      ),
    );
    cols.append(
      append(
        el("div"),
        el("h3", referenceLabel(id, "transcript") + " · Whisper"),
        el(
          "div",
          r.reference?.error ||
            draft ||
            (r.reference
              ? "[No speech transcribed]"
              : "Reference is processing…"),
          "transcript",
        ),
      ),
    );
    c.append(cols);
    c.append(el("p", audioComparison(id, r, draft), "caption"));
    const wordRows = (r.reference?.segments || []).flatMap(
      (seg) => seg.words || [],
    );
    if (wordRows.length) {
      const words = el("div", null, "source-links");
      for (const w of wordRows)
        words.append(
          button(w.word, () => {
            const player = players.querySelector(
              'audio[data-asset="' + id + '"]',
            );
            if (player) {
              player.currentTime = Math.max(0, w.start - 0.2);
              player.play().catch(() => {});
            }
          }),
        );
      c.append(details("Jump to a word in the reference audio", words));
    }
    c.append(
      reviewButton(id, "transcript", r.reference?.text || "", r.reference_hash),
    );
    root.append(c);
  }
  const list = el("div", null, "event-list");
  function drawList() {
    list.replaceChildren();
    const query = search.value.toLowerCase();
    items.forEach((g, i) => {
      const rows = g.ids.map((id) => state.data.audio[id] || {});
      const text = rows
        .map((r) => (r.candidate?.text || "") + " " + (r.reference?.text || ""))
        .join(" ");
      const disagreement = rows.some(
        (r) =>
          (r.comparison?.word_disagreement || 0) > 0.15 ||
          r.candidate?.error ||
          r.reference?.error,
      );
      if (
        (query && !text.toLowerCase().includes(query)) ||
        (flag.checked && !disagreement)
      )
        return;
      const b = button("", () => {
        if (canNavigate()) {
          state.audio = i;
          render();
        }
      });
      b.className = i === state.audio ? "active" : "";
      append(
        b,
        el("span", time(g.at)),
        el("span", text.slice(0, 180) || "No speech transcribed"),
        el("span", disagreement ? "Compare" : "", "small"),
      );
      list.append(b);
    });
  }
  search.oninput = drawList;
  flag.onchange = drawList;
  drawList();
  root.append(card("Browse the audio trace", list));
}
function moveAudio(delta) {
  if (!canNavigate()) return;
  state.audio = Math.max(0, Math.min(groups().length - 1, state.audio + delta));
  render();
}
function drawScreens() {
  const root = $("content"),
    ids = state.data.session.screens;
  if (!ids.length) {
    root.append(
      el("p", "No screenshots were recorded in this session.", "empty"),
    );
    return;
  }
  if (!ids.includes(state.image)) state.image = ids[0];
  const id = state.image,
    a = state.index.assets[id],
    r = state.data.images[id] || {};
  const thumbs = el("div", null, "thumbs");
  for (const source of ids) {
    const asset = state.index.assets[source],
      b = button(
        asset.at === null ? "Unassigned image" : time(asset.at),
        () => {
          if (canNavigate()) {
            state.image = source;
            render();
          }
        },
      );
    const img = el("img");
    img.src = "/media/" + source;
    img.alt = "Recorded screenshot";
    b.prepend(img);
    b.setAttribute("aria-selected", String(source === id));
    thumbs.append(b);
  }
  root.append(thumbs);
  const frame = el("div", null, "frame"),
    img = el("img");
  img.src = "/media/" + id;
  img.alt = "Original recorded screenshot; click to zoom";
  img.onclick = () => frame.classList.toggle("zoom");
  frame.append(img);
  root.append(
    frame,
    el(
      "p",
      "Original image · click to inspect at full size. The new system receives its normal resized, redacted frame.",
      "caption",
    ),
  );
  const columns = el("div", null, "columns");
  columns.append(
    card(
      "New OCR · current Tesseract path",
      pre(r.ocr?.error || r.ocr?.text || "Processing…"),
      reviewButton(
        id,
        "ocr",
        r.reference?.reading?.visible_text || "",
        r.reference_hash,
      ),
    ),
  );
  columns.append(
    card(
      referenceLabel(id, "ocr") + " · visible text",
      pre(
        corrected(id, "ocr", r.reference?.reading?.visible_text) ||
          r.reference?.error ||
          "Processing…",
      ),
    ),
  );
  root.append(columns);
  const visuals = el("div", null, "columns");
  for (const [role, title] of [
    ["candidate", "New vision interpretation"],
    ["reference", referenceLabel(id, "vlm")],
  ]) {
    const data = r[role]?.reading,
      c = card(title);
    if (role === "reference" && currentReview(id, "vlm")?.reference_edited) {
      c.append(md(currentReview(id, "vlm").corrected_text));
    } else if (data) {
      append(
        c,
        el("h3", "Inferred task"),
        el("p", data.inferred_task),
        el("h3", "Visible facts"),
      );
      for (const fact of data.facts || [])
        c.append(
          details(
            fact.description,
            el("p", fact.evidence),
            el("p", fact.certainty, "small"),
          ),
        );
      if (data.uncertainties?.length)
        c.append(
          el("h3", "Uncertainty"),
          ...data.uncertainties.map((x) => el("p", x, "small")),
        );
    } else c.append(el("p", r[role]?.error || "Processing…"));
    visuals.append(c);
  }
  root.append(
    visuals,
    reviewButton(
      id,
      "vlm",
      readingText(r.reference?.reading),
      r.reference_hash,
    ),
  );
  if (r.assessment?.assessment) {
    const assessment = r.assessment.assessment;
    root.append(
      card(
        "Provisional component assessment",
        ...["ocr", "vlm"].map((k) => {
          const a = assessment[k];
          return append(
            el("div", null, "criterion"),
            el(
              "h3",
              (k === "ocr" ? "OCR" : "Vision") +
                " · " +
                (a.passed === true
                  ? "Provisional pass"
                  : a.passed === false
                    ? "Provisional fail"
                    : "Needs review"),
            ),
            el("p", a.summary),
            ...(a.consequential_errors || []).map((e) => el("p", e, "small")),
          );
        }),
        details(
          "Reference concerns",
          ...(assessment.reference_concerns || []).map((e) => el("p", e)),
        ),
      ),
    );
  }
  const stem = a.path.replace(/\.[^.]+$/, "");
  const derived = Object.values(state.index.assets).filter(
    (x) => x.kind === "debug_image" && x.path.startsWith(stem + "_boxes."),
  );
  if (derived.length)
    root.append(
      details(
        "Original OCR debug image",
        ...derived.map((x) => {
          const im = el("img", null, "diagram");
          im.src = "/media/" + x.id;
          im.alt = "Historical OCR detection boxes";
          return im;
        }),
      ),
    );
}
function showResponse(value, lane, point) {
  if (!value)
    return el("p", "No new " + lane + " response at this checkpoint.", "empty");
  const response = value.response,
    root = el("div");
  append(
    root,
    el(
      "p",
      `${lane === "quick" ? "Quick help" : "Deep response"} · ${value.seconds.toFixed(1)} s`,
      "caption",
    ),
    el("p", response.summary),
  );
  if (value.delivery_notes?.length) {
    const labels = {
      file_observation_quarantined:
        "Unconfirmed file-cache update excluded; the answer remains available.",
      annotations_repaired:
        "Line explanations repaired against the unchanged code.",
      unconfirmed_fragment_path_omitted:
        "Unconfirmed file path omitted from a partial code proposal.",
      spoken_proposal_presented_as_example:
        "Speech-based code retained as an example, not an observed file.",
    };
    root.append(
      details(
        "Delivery adjustments",
        ...value.delivery_notes.map((note) =>
          el(
            "p",
            note.message ||
              labels[note.reason] ||
              note.reason.replaceAll("_", " "),
          ),
        ),
      ),
    );
  }
  for (const reply of response.conversation || [])
    root.append(
      card(
        reply.action.charAt(0).toUpperCase() + reply.action.slice(1),
        el("p", reply.text),
        sourceLinks([reply.source_id]),
      ),
    );
  for (const [i, artifact] of (response.artifacts || []).entries()) {
    const c = card(
      artifact.title,
      el("span", artifact.basis.replaceAll("_", " "), "pill"),
    );
    if (artifact.kind === "diagram") {
      const im = el("img", null, "diagram");
      im.src = "/diagram/" + point.id + "/" + lane + "/" + i;
      im.alt = artifact.title;
      c.append(im);
    }
    c.append(
      ["code", "patch"].includes(artifact.kind)
        ? pre(artifact.content)
        : md(artifact.content),
    );
    if (artifact.annotations?.length)
      c.append(
        details(
          "Line-by-line teaching notes",
          ...artifact.annotations.map((n) =>
            el("p", `${n.line}. ${n.explanation}`),
          ),
        ),
      );
    c.append(sourceLinks(artifact.source_ids));
    root.append(c);
  }
  if (response.open_questions?.length)
    root.append(
      details(
        "Open questions",
        ...response.open_questions.map((q) => el("p", q)),
      ),
    );
  return root;
}
function drawResponses() {
  const root = $("content"),
    s = state.data.session;
  const selector = el("select");
  selector.setAttribute("aria-label", "Replay checkpoint");
  for (const p of s.checkpoints) {
    const o = el(
      "option",
      `${time(p.at)} · ${p.reason}${p.primary ? " · primary review" : ""}`,
    );
    o.value = p.id;
    selector.append(o);
  }
  selector.value = state.point;
  selector.onchange = () => {
    if (canNavigate()) {
      state.point = selector.value;
      render();
    }
  };
  root.append(append(el("div", null, "toolbar"), selector));
  const point = (state.data.replay.checkpoints || []).find(
      (p) => p.id === state.point,
    ),
    reference = state.data.references[state.point] || {},
    draft = reference.reference,
    assessment = state.data.assessments[state.point]?.assessment;
  root.append(
    notice(
      "Compare the new outputs with the independent reference draft. Only prior recording context was supplied. Historical saved answers were excluded.",
    ),
  );
  if (!point) {
    root.append(
      el(
        "p",
        "The current-system replay is processing. Refresh results shortly.",
        "empty",
      ),
    );
    return;
  }
  if (assessment) {
    const title =
      assessment.passed === true
        ? "Provisional pass"
        : assessment.passed === false
          ? "Provisional fail"
          : "Needs review";
    const c = card(
      title,
      el(
        "p",
        assessment.first_failure ||
          "Review the criteria and trace before accepting this machine judgment.",
      ),
    );
    for (const v of assessment.results || []) {
      const criterion = draft?.criteria.find((x) => x.id === v.criterion_id);
      c.append(
        append(
          el("div", null, "criterion"),
          el(
            "strong",
            (v.passed === true
              ? "Pass · "
              : v.passed === false
                ? "Fail · "
                : "Uncertain · ") + (criterion?.requirement || v.criterion_id),
          ),
          el("p", v.explanation),
          sourceLinks(criterion?.source_ids),
        ),
      );
    }
    if (assessment.reference_concerns?.length)
      c.append(
        details(
          "Concerns about the reference",
          ...assessment.reference_concerns.map((x) => el("p", x)),
        ),
      );
    root.append(
      details("Automated assessment · open after your own review", c),
    );
  }
  if (point.errors?.length)
    root.append(
      card(
        "Recorded runtime errors",
        ...point.errors.map((e) => el("p", e.lane + ": " + e.message)),
      ),
    );
  if (!point.deep && point.raw_proposals?.deep) {
    const raw = point.raw_proposals.deep;
    const draft = raw.structured;
    const view = el("div");
    if (draft) {
      view.append(el("p", draft.summary || ""));
      for (const a of Array.isArray(draft.artifacts) ? draft.artifacts : [])
        view.append(
          card(
            a.title || "Rejected artifact",
            pre(a.content || ""),
            sourceLinks(a.source_ids),
          ),
        );
    } else view.append(pre(raw.text || "No response text was returned."));
    root.append(
      details(
        "Inspect the rejected draft · not displayed by the app",
        notice(
          "The new model produced this draft, but the current app rejected it. Review it alongside the validation error; it is not a delivered response.",
        ),
        view,
      ),
    );
  }
  root.append(
    card(
      "New system: immediate help",
      showResponse(point.quick, "quick", point),
    ),
  );
  const columns = el("div", null, "columns");
  columns.append(
    card(
      "New system: deeper assistance",
      showResponse(point.deep, "deep", point),
    ),
  );
  const refcard = card(referenceLabel(state.point, "assistance"));
  if (draft) {
    append(
      refcard,
      el("h3", draft.current_task),
      md(corrected(state.point, "assistance", draft.ideal_response)),
      details(
        "Acceptance criteria",
        ...draft.criteria.map((c) =>
          append(
            el("div", null, "criterion"),
            el("p", c.requirement),
            sourceLinks(c.source_ids),
          ),
        ),
      ),
      details(
        "Reference uncertainty",
        ...draft.uncertainties.map((u) => el("p", u)),
      ),
      reviewButton(
        state.point,
        "assistance",
        draft.ideal_response,
        reference.reference_hash,
      ),
    );
  } else
    refcard.append(
      el("p", reference.error || "Draft reference is processing…"),
    );
  columns.append(refcard);
  root.append(columns);
  const observations = point.input?.observations || [];
  root.append(
    details(
      `Input trace · ${observations.length} observations`,
      ...observations.map((o) =>
        append(
          el("div", null, "result-row"),
          sourceLinks([o.id]),
          el("span", o.channel + " · " + o.speaker, "small"),
          el("div", o.text, "transcript"),
        ),
      ),
    ),
  );
  if (s.legacy.length) {
    const history = details("Historical responses · comparison only");
    history.classList.add("history");
    history.append(
      el(
        "p",
        "These answers were not supplied to the new system or the reference writer. They are not ground truth.",
      ),
    );
    for (const id of s.legacy) {
      const target = el("div");
      const b = button(
        "Load " + state.index.assets[id].path.split("/").pop(),
        async () => {
          const old = await get("/media/" + id);
          target.replaceChildren(
            el(
              "h3",
              old.likely_request || old.summary || "Historical response",
            ),
            md(old.conversational_response || old.summary || ""),
            details("Historical diff", pre(old.suggested_diff || "")),
          );
          b.disabled = true;
        },
      );
      history.append(b, target);
    }
    root.append(history);
  }
}
$("refresh").onclick = () => refresh().catch((e) => toast(e.message));
$("loose").onclick = () => loose().catch((e) => toast(e.message));
for (const id of ["verdict", "reference-status", "correction", "notes"])
  $(id).addEventListener("input", () => {
    state.dirty = true;
    $("save-status").textContent = "Unsaved changes";
  });
$("save-review").onclick = async () => {
  if (!state.review) return;
  const target = state.review;
  const payload = {
    target_id: target.id,
    component: target.component,
    status: $("reference-status").value,
    candidate_verdict: $("verdict").value,
    corrected_text: $("correction").value,
    notes: $("notes").value,
    reference_hash: target.hash,
    reference_edited: $("correction").value !== target.baseText,
  };
  try {
    const submit = () =>
      fetch("/api/reviews", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Review-Token": state.index.csrf_token,
        },
        body: JSON.stringify(payload),
      });
    let response = await submit();
    if (response.status === 403) {
      state.index.csrf_token = (await get("/api/index")).csrf_token;
      response = await submit();
    }
    if (!response.ok)
      throw Error("Review could not be saved. Your text is still here.");
    const row = await response.json();
    state.data.reviews[row.target_id + ":" + row.component] = row;
    state.index.reviews[row.target_id + ":" + row.component] = row;
    drawSessions();
    state.dirty = false;
    $("save-status").textContent =
      "Saved locally at " + new Date(row.at * 1000).toLocaleTimeString();
    toast("Review saved. Original model outputs are preserved.");
  } catch (e) {
    toast(e.message);
  }
};
document.addEventListener("keydown", (event) => {
  if (
    ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName) ||
    state.stage !== "audio"
  )
    return;
  if (event.key.toLowerCase() === "j") moveAudio(-1);
  if (event.key.toLowerCase() === "k") moveAudio(1);
});
window.addEventListener("beforeunload", (e) => {
  if (state.dirty) {
    e.preventDefault();
    e.returnValue = "";
  }
});
refresh().catch((e) => {
  $("heading").replaceChildren(
    el("h1", "Review unavailable"),
    el("p", e.message),
  );
});
