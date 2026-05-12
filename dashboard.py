"""
dashboard.py

Live dashboard showing plan (as interactive step tracker), KB, and world state.
The plan panel lets observers click "Mark Done" / "Undo" on individual steps;
each click updates the WorldStateTracker and the world-state display.

Started automatically by main.py in a background thread.
Visit: http://localhost:5050
"""

import copy
import threading
import logging
from flask import Flask, jsonify, render_template_string, request

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

_lock  = threading.Lock()
_state = {
    "plan_rows":        [],   # [{"actor": "Robot"|"You", "description": "..."}]
    "plan_actions":     [],   # [["move","p2","kitchen","pantry"], ...]  — PDDL tokens
    "step_statuses":    [],   # ["pending"|"done", ...]
    "base_world_state": None, # world state snapshot taken when plan was set (for undo)
    "kb": {
        "human_preference":      [],
        "human_limitations":     [],
        "robot_limitations":     [],
        "environmental_factors": [],
    },
    "status":      "Starting up...",
    "world_state": {"item_room": {}, "agent_holding": {}, "agent_room": {}},
    "phase":       "PRE_TASK",
    "stop_requested": False,
}

# Reference to the live WorldStateTracker in the main process.
# Set once via set_world_state_tracker(); Flask endpoints mutate it directly.
_world_state_tracker = None

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)
log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)


@app.route("/")
def index():
    return render_template_string(_DASHBOARD_HTML)


@app.route("/state")
def state():
    with _lock:
        return jsonify(_state)


@app.route("/stop", methods=["POST"])
def stop():
    with _lock:
        _state["stop_requested"] = True
    return jsonify({"ok": True})


@app.route("/complete-step", methods=["POST"])
def complete_step():
    """Mark a plan step as done and recompute world state."""
    data = request.get_json() or {}
    idx  = data.get("step")
    with _lock:
        if idx is None or idx >= len(_state["step_statuses"]):
            return jsonify({"ok": False, "error": "invalid step index"}), 400
        _state["step_statuses"][idx] = "done"
        _recompute_world_state_inplace()
    return jsonify({"ok": True})


@app.route("/undo-step", methods=["POST"])
def undo_step():
    """Un-mark a plan step and recompute world state.

    Only the most recently completed step (highest done index) may be undone.
    Undoing an earlier step while a later step is done would leave the world
    in a logically inconsistent state.
    """
    data = request.get_json() or {}
    idx  = data.get("step")
    with _lock:
        if idx is None or idx >= len(_state["step_statuses"]):
            return jsonify({"ok": False, "error": "invalid step index"}), 400
        # Find the last done step — only that one may be undone
        last_done = max(
            (i for i, s in enumerate(_state["step_statuses"]) if s == "done"),
            default=None,
        )
        if idx != last_done:
            return jsonify({
                "ok": False,
                "error": "Can only undo the most recently completed step. "
                         "Undo later steps first.",
            }), 400
        _state["step_statuses"][idx] = "pending"
        _recompute_world_state_inplace()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# World-state helpers (called inside _lock)
# ---------------------------------------------------------------------------

def _apply_tokens_to_tracker(tokens: list, tracker) -> None:
    """Apply one PDDL action token list to a WorldStateTracker."""
    if len(tokens) < 2:
        return
    action, agent = tokens[0], tokens[1]
    if action == "move" and len(tokens) >= 4:
        tracker.agent_moved(agent, tokens[3])
    elif action == "take" and len(tokens) >= 3:
        tracker.agent_took(agent, tokens[2])
    elif action == "drop" and len(tokens) >= 4:
        tracker.agent_dropped(agent, tokens[2], tokens[3])


def _recompute_world_state_inplace() -> None:
    """
    Recompute world state from scratch:
      1. Restore tracker to base_world_state snapshot (taken when plan was last set).
      2. Apply every "done" step's tokens in plan order.
    This makes "Undo" trivially correct — just un-mark and recompute.
    Must be called while _lock is held.
    """
    if _world_state_tracker is None:
        return
    base = _state.get("base_world_state")
    if base:
        _world_state_tracker.from_dict(copy.deepcopy(base))
    else:
        _world_state_tracker.reset()
    for i, status in enumerate(_state["step_statuses"]):
        if status == "done" and i < len(_state["plan_actions"]):
            _apply_tokens_to_tracker(_state["plan_actions"][i], _world_state_tracker)
    _state["world_state"] = _world_state_tracker.to_dict()


# ---------------------------------------------------------------------------
# Public Python API
# ---------------------------------------------------------------------------

def set_world_state_tracker(tracker) -> None:
    """
    Store a reference to the main process's WorldStateTracker.
    Must be called before execution begins so step completions update the
    live object (which is read by ctx.world_state during replanning).
    """
    global _world_state_tracker
    _world_state_tracker = tracker


def update(plan_rows: list, kb, status: str = "", plan_actions: list = None) -> None:
    """
    Push a new plan + KB snapshot to the dashboard.

    plan_rows    : [{"actor": "Robot"|"You", "description": "..."}]
    kb           : KnowledgeBase instance
    status       : short status string (optional)
    plan_actions : [["move","p2",…], …]  — PDDL token lists, one per plan step.
                   When provided, step_statuses reset to all "pending" and the
                   current world state is snapshotted as the base for undo.
    """
    raw_kb = kb.get_state()
    simplified_kb = {
        cat: [f["fact"] if isinstance(f, dict) else f for f in facts]
        for cat, facts in raw_kb.items()
    }
    with _lock:
        _state["plan_rows"] = plan_rows
        _state["kb"]        = simplified_kb
        if status:
            _state["status"] = status
        if plan_actions is not None:
            _state["plan_actions"]  = plan_actions
            _state["step_statuses"] = ["pending"] * len(plan_actions)
            # Snapshot from the live tracker if available, otherwise from cached state
            if _world_state_tracker is not None:
                _state["world_state"]      = _world_state_tracker.to_dict()
            # Deep copy so replaying done steps doesn't corrupt the base snapshot
            _state["base_world_state"] = copy.deepcopy(_state["world_state"])


def set_status(status: str) -> None:
    with _lock:
        _state["status"] = status


def set_phase(phase: str) -> None:
    with _lock:
        _state["phase"] = phase


def update_world_state(world_dict: dict) -> None:
    """Directly push a world state dict (used when tracker updates happen outside the dashboard)."""
    with _lock:
        _state["world_state"] = world_dict


def consume_stop_request() -> bool:
    with _lock:
        flag = _state.get("stop_requested", False)
        _state["stop_requested"] = False
    return flag


def start(port: int = 5050) -> int:
    thread = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port,
                               debug=False, use_reloader=False),
        daemon=True,
    )
    thread.start()
    return port


# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Robot Task Planner</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #f2f2f7;
      color: #1c1c1e;
      min-height: 100vh;
    }

    /* ── Header ── */
    header {
      background: #fff;
      border-bottom: 1px solid #e5e5ea;
      padding: 14px 28px;
      display: flex;
      align-items: center;
      gap: 10px;
      position: sticky;
      top: 0;
      z-index: 10;
    }
    header h1 { font-size: 17px; font-weight: 600; letter-spacing: -0.3px; }
    .live-dot {
      width: 9px; height: 9px; border-radius: 50%;
      background: #30d158; flex-shrink: 0;
      animation: pulse 2.4s ease-in-out infinite;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1;   transform: scale(1); }
      50%       { opacity: 0.4; transform: scale(0.85); }
    }
    .status-pill {
      margin-left: auto;
      font-size: 12px; font-weight: 500; color: #636366;
      background: #f2f2f7; border: 1px solid #e5e5ea;
      border-radius: 20px; padding: 4px 12px;
    }
    .stop-btn {
      margin-left: 8px; font-size: 12px; font-weight: 600;
      color: #fff; background: #ff3b30; border: none;
      border-radius: 8px; padding: 5px 14px; cursor: pointer;
    }
    .stop-btn:hover { opacity: 0.82; }
    .phase-badge {
      font-size: 11px; font-weight: 700; letter-spacing: 0.5px;
      text-transform: uppercase; color: #fff;
      background: #636366; border-radius: 6px; padding: 3px 10px;
    }
    .phase-PRE_TASK  { background: #0071e3; }
    .phase-REPLAN    { background: #ff9f0a; }
    .phase-POST_TASK { background: #30d158; }

    /* ── Layout ── */
    main {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 20px;
      padding: 24px 28px;
      max-width: 1200px;
      margin: 0 auto;
    }
    @media (max-width: 800px) { main { grid-template-columns: 1fr; } }

    /* ── Cards ── */
    .card {
      background: #fff;
      border-radius: 14px;
      box-shadow: 0 1px 4px rgba(0,0,0,0.07), 0 0 0 1px rgba(0,0,0,0.04);
      overflow: hidden;
    }
    .card-header {
      padding: 14px 20px;
      border-bottom: 1px solid #f2f2f7;
      display: flex; align-items: center; gap: 8px;
    }
    .card-header h2 { font-size: 14px; font-weight: 600; letter-spacing: -0.1px; }
    .card-body { padding: 0; }
    .step-check { width: 22px; text-align: center; font-size: 15px; }
    .check-done    { color: #30d158; }
    .check-pending { color: #c7c7cc; }

    /* ── Progress bar ── */
    .progress-wrap {
      padding: 10px 20px 6px;
      display: flex; align-items: center; gap: 10px;
      border-bottom: 1px solid #f2f2f7;
    }
    .progress-label { font-size: 12px; color: #636366; white-space: nowrap; }
    .progress-track {
      flex: 1; height: 5px; background: #f2f2f7;
      border-radius: 3px; overflow: hidden;
    }
    .progress-fill {
      height: 100%; background: #30d158;
      border-radius: 3px; transition: width 0.4s ease;
    }

    /* ── Step table ── */
    table { width: 100%; border-collapse: collapse; font-size: 14px; }
    thead th {
      text-align: left; padding: 8px 16px;
      font-size: 11px; font-weight: 600;
      text-transform: uppercase; letter-spacing: 0.6px; color: #aeaeb2;
    }
    tbody tr { border-top: 1px solid #f2f2f7; transition: background 0.15s; }

    /* Step states */
    tr.step-done    { background: #f6fef9; }
    tr.step-current { background: #f0f7ff; }
    tr.step-pending { background: #fff; }
    tr.step-pending:hover { background: #fafafa; }

    tbody td { padding: 10px 16px; vertical-align: middle; }



    .step-num { color: #c7c7cc; font-size: 12px; font-weight: 500; width: 32px; }

    .actor-badge {
      display: inline-block; font-size: 12px; font-weight: 600;
      border-radius: 6px; padding: 2px 9px; white-space: nowrap;
    }
    .actor-you   { background: #e8f4ff; color: #0071e3; }
    .actor-robot { background: #e8faf0; color: #1a9e4a; }

    .action-text { color: #3a3a3c; }
    .action-done { color: #aeaeb2; text-decoration: line-through; }

    /* Buttons */
    .action-btn {
      font-size: 11px; font-weight: 600; border-radius: 7px;
      padding: 4px 11px; cursor: pointer; white-space: nowrap;
      border: none; transition: opacity 0.15s;
    }
    .action-btn:hover { opacity: 0.8; }
    .btn-done { background: #30d158; color: #fff; }
    .btn-undo { background: #f2f2f7; color: #636366; border: 1px solid #e5e5ea; }
    .btn-undo-locked { background: #f2f2f7; color: #c7c7cc; border: 1px solid #f2f2f7;
                       cursor: not-allowed; opacity: 0.5; }
    .btn-undo-locked:hover { opacity: 0.5; }

    .plan-empty {
      padding: 28px 20px; text-align: center;
      color: #c7c7cc; font-size: 14px; font-style: italic;
    }

    /* ── Knowledge base ── */
    .kb-section { padding: 12px 20px; border-top: 1px solid #f2f2f7; }
    .kb-section:first-child { border-top: none; }
    .kb-label {
      font-size: 11px; font-weight: 600; text-transform: uppercase;
      letter-spacing: 0.6px; color: #aeaeb2; margin-bottom: 7px;
    }
    .kb-fact {
      display: flex; align-items: flex-start; gap: 8px;
      font-size: 14px; color: #3a3a3c; line-height: 1.45; padding: 2px 0;
    }
    .kb-bullet { color: #0071e3; font-weight: 700; flex-shrink: 0; margin-top: 1px; }
    .kb-empty  { font-size: 13px; color: #c7c7cc; font-style: italic; }

    /* ── World state ── */
    .ws-grid {
      display: grid; grid-template-columns: 1fr 1fr;
    }
    .ws-section { padding: 14px 20px; border-top: 1px solid #f2f2f7; }
    .ws-section:nth-child(-n+2) { border-top: none; }
    .ws-label {
      font-size: 11px; font-weight: 600; text-transform: uppercase;
      letter-spacing: 0.6px; color: #aeaeb2; margin-bottom: 8px;
    }
    .ws-row {
      display: flex; align-items: center; gap: 8px;
      font-size: 13px; color: #3a3a3c; padding: 3px 0;
    }
    .ws-name { font-weight: 500; min-width: 68px; color: #636366; }
    .loc-kitchen { color: #0071e3; font-weight: 600; }
    .loc-pantry  { color: #1a9e4a; font-weight: 600; }
    .loc-holding { color: #ff9f0a; font-weight: 600; }
    .ws-empty    { font-size: 13px; color: #c7c7cc; font-style: italic; }

    /* ── Animations ── */
    .fade { animation: fadein 0.3s ease; }
    @keyframes fadein { from { opacity: 0.5; } to { opacity: 1; } }
  </style>
</head>
<body>

<header>
  <div class="live-dot"></div>
  <h1>Robot Task Planner</h1>
  <span class="phase-badge" id="phase-badge">PRE_TASK</span>
  <span class="status-pill" id="status">Connecting…</span>
  <button class="stop-btn" onclick="sendStop()">Stop</button>
</header>

<main>
  <!-- Step Tracker (Plan) -->
  <div class="card">
    <div class="card-header">
      <h2>Step Tracker</h2>
    </div>
    <div class="card-body" id="plan-body">
      <div class="plan-empty">Waiting for plan…</div>
    </div>
  </div>

  <!-- Knowledge Base -->
  <div class="card">
    <div class="card-header">
      <h2>Constraints &amp; Preferences</h2>
    </div>
    <div class="card-body" id="kb-body"></div>
  </div>

  <!-- World State (full width) -->
  <div class="card" style="grid-column: 1 / -1;">
    <div class="card-header">
      <h2>World State</h2>
    </div>
    <div class="card-body" id="ws-body">
      <div class="plan-empty">Waiting for world state…</div>
    </div>
  </div>
</main>

<script>
  const KB_LABELS = {
    human_preference:      "Your Preferences",
    human_limitations:     "Your Limitations",
    robot_limitations:     "Robot Limitations",
    environmental_factors: "Environment Notes",
  };

  let lastPlanHash = "";
  let lastKbHash   = "";
  let lastWsHash   = "";
  let lastPhase    = "";

  function hash(obj) { return JSON.stringify(obj); }

  // ── Step Tracker ───────────────────────────────────────────────────────────

  function renderPlan(rows, statuses) {
    const body = document.getElementById("plan-body");
    if (!rows || rows.length === 0) {
      body.innerHTML = '<div class="plan-empty">No steps yet.</div>';
      return;
    }

    const st = statuses || rows.map(() => "pending");
    const doneCount = st.filter(s => s === "done").length;
    const total     = rows.length;
    const pct       = total > 0 ? Math.round(doneCount / total * 100) : 0;
    const currentIdx = st.findIndex(s => s !== "done");

    let html = `
      <div class="progress-wrap">
        <span class="progress-label">${doneCount} / ${total} done</span>
        <div class="progress-track">
          <div class="progress-fill" style="width:${pct}%"></div>
        </div>
      </div>
      <table>
        <thead><tr>
          <th></th><th>#</th><th>Who</th><th>Action</th><th></th>
        </tr></thead>
        <tbody>`;

    // Only the last done step may be undone (earlier steps have downstream dependents)
    const lastDoneIdx = st.reduce((last, s, i) => s === "done" ? i : last, -1);

    rows.forEach((r, i) => {
      const isDone    = st[i] === "done";
      const isCurrent = i === currentIdx;
      const rowClass  = isDone ? "step-done" : (isCurrent ? "step-current" : "step-pending");
      const badge     = r.actor === "You" ? "actor-you" : "actor-robot";
      const textClass = isDone ? "action-done" : "action-text";
      const checkIcon = isDone
        ? `<span class="step-check check-done">✓</span>`
        : `<span class="step-check check-pending">○</span>`;

      let btn;
      if (isDone) {
        if (i === lastDoneIdx) {
          btn = `<button class="action-btn btn-undo" onclick="undoStep(${i})">Undo</button>`;
        } else {
          btn = `<button class="action-btn btn-undo-locked" disabled
                         title="Undo later steps first">Undo</button>`;
        }
      } else {
        btn = `<button class="action-btn btn-done" onclick="completeStep(${i})">Mark&nbsp;Done</button>`;
      }

      html += `<tr class="${rowClass}">
        <td>${checkIcon}</td>
        <td class="step-num">${i + 1}</td>
        <td><span class="actor-badge ${badge}">${r.actor}</span></td>
        <td class="${textClass}">${r.description}</td>
        <td>${btn}</td>
      </tr>`;
    });

    html += "</tbody></table>";
    body.innerHTML = html;
  }

  async function completeStep(idx) {
    await fetch("/complete-step", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ step: idx }),
    });
    refresh();
  }

  async function undoStep(idx) {
    await fetch("/undo-step", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ step: idx }),
    });
    refresh();
  }

  // ── Knowledge Base ─────────────────────────────────────────────────────────

  function renderKb(kb) {
    const body = document.getElementById("kb-body");
    body.innerHTML = Object.entries(KB_LABELS).map(([key, label]) => {
      const facts = (kb && kb[key]) ? kb[key] : [];
      const content = facts.length > 0
        ? facts.map(f =>
            `<div class="kb-fact">
               <span class="kb-bullet">•</span><span>${f}</span>
             </div>`).join("")
        : `<div class="kb-empty">None recorded</div>`;
      return `<div class="kb-section">
                <div class="kb-label">${label}</div>${content}
              </div>`;
    }).join("");
  }

  // ── World State ────────────────────────────────────────────────────────────

  function renderWorldState(ws) {
    const body = document.getElementById("ws-body");
    if (!ws || !ws.item_room) {
      body.innerHTML = '<div class="plan-empty">No world state yet.</div>';
      return;
    }
    const items  = ["bread", "ham", "cheese", "lettuce"];
    const agents = { p1: "You", p2: "Robot" };

    // Map item → who is holding it (if anyone)
    const heldBy = {};
    for (const [agent, item] of Object.entries(ws.agent_holding || {})) {
      if (item) heldBy[item] = agents[agent] || agent;
    }

    // Items column
    let itemsHtml = items.map(item => {
      if (heldBy[item]) {
        return `<div class="ws-row">
          <span class="ws-name">${item}</span>
          <span class="loc-holding">${heldBy[item]} holding</span>
        </div>`;
      }
      const room = ws.item_room[item];
      const cls  = room === "kitchen" ? "loc-kitchen" : "loc-pantry";
      return `<div class="ws-row">
        <span class="ws-name">${item}</span>
        <span class="${cls}">${room || "?"}</span>
      </div>`;
    }).join("");

    // Agents column
    let agentsHtml = Object.entries(agents).map(([id, label]) => {
      const room = (ws.agent_room || {})[id] || "?";
      const held = (ws.agent_holding || {})[id];
      const cls  = room === "kitchen" ? "loc-kitchen" : "loc-pantry";
      const extra = held ? ` <span style="color:#ff9f0a;font-size:12px">(holding ${held})</span>` : "";
      return `<div class="ws-row">
        <span class="ws-name">${label}</span>
        <span class="${cls}">${room}</span>${extra}
      </div>`;
    }).join("");

    body.innerHTML = `
      <div class="ws-grid">
        <div class="ws-section">
          <div class="ws-label">Items</div>${itemsHtml}
        </div>
        <div class="ws-section">
          <div class="ws-label">Agents</div>${agentsHtml}
        </div>
      </div>`;
  }

  // ── Stop button ────────────────────────────────────────────────────────────

  async function sendStop() {
    await fetch("/stop", { method: "POST" });
    document.getElementById("status").textContent = "Stop requested…";
  }

  // ── Polling ────────────────────────────────────────────────────────────────

  async function refresh() {
    try {
      const res  = await fetch("/state");
      const data = await res.json();

      document.getElementById("status").textContent = data.status || "";

      // Phase badge
      if (data.phase && data.phase !== lastPhase) {
        const badge = document.getElementById("phase-badge");
        badge.textContent = data.phase;
        badge.className   = `phase-badge phase-${data.phase}`;
        lastPhase = data.phase;
      }

      // Plan + step statuses (hash both together so status changes trigger re-render)
      const planHash = hash(data.plan_rows) + "|" + hash(data.step_statuses);
      if (planHash !== lastPlanHash) {
        renderPlan(data.plan_rows, data.step_statuses);
        document.getElementById("plan-body").classList.add("fade");
        setTimeout(() => document.getElementById("plan-body").classList.remove("fade"), 350);
        lastPlanHash = planHash;
      }

      const kbHash = hash(data.kb);
      if (kbHash !== lastKbHash) {
        renderKb(data.kb);
        document.getElementById("kb-body").classList.add("fade");
        setTimeout(() => document.getElementById("kb-body").classList.remove("fade"), 350);
        lastKbHash = kbHash;
      }

      const wsHash = hash(data.world_state);
      if (wsHash !== lastWsHash) {
        renderWorldState(data.world_state);
        document.getElementById("ws-body").classList.add("fade");
        setTimeout(() => document.getElementById("ws-body").classList.remove("fade"), 350);
        lastWsHash = wsHash;
      }
    } catch (_) {
      document.getElementById("status").textContent = "Reconnecting…";
    }
  }

  refresh();
  setInterval(refresh, 1500);
</script>
</body>
</html>"""
