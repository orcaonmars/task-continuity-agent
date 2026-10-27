"""Bounded strategy change after a model makes no production progress."""

import time
from common import atomic_json, read_json


def repair_stalled_state(task):
    state = read_json(task / "agent-state.json", {})
    if not state.get("stall_reason"):
        return False
    meta = read_json(task / "task.json", {})
    attempts = int(meta.get("production_recovery_attempts", 0))
    if attempts >= 2:
        return False
    atomic_json(
        task / "ContextArchive" / ("stalled-" + str(time.time_ns()) + ".json"), state
    )
    # Preserve original goal, files, known tool results, read ledger and any uncertain
    # tool receipt. Replace the stale reasoning loop with one concrete recovery turn.
    recent = state.get("turns", [])[-1:]
    recent.append(
        [
            {
                "role": "user",
                "content": "Recovery: the previous worker repeated reference/setup work without creating output. "
                "Do not repeat that reading loop. The goal, execution_start, checkpoint, actual files "
                "and previously read sections are pinned above. Inspect an existing job if needed; "
                "otherwise perform the first actual import/build action now, then save a checkpoint. "
                "Do not restart the whole plan or reread the entire brief.",
            }
        ]
    )
    state.update(
        turns=recent,
        readonly_steps=0,
        stagnant_steps=0,
        no_action=0,
        stall_reason=None,
        same_action=0,
        last_action=None,
        progress_window_started=time.time(),
        brief_reads_without_action=3,
        phase="recovering_production",
        last_error=None,
    )
    atomic_json(task / "agent-state.json", state)
    return True
