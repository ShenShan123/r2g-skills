"""User approval endpoints. These are never registered as model tools."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .repairs import RepairController, RepairDenied

router = APIRouter()


def _runtime():
    from .server import STATE
    from .tools import RT
    return STATE, RepairController(STATE["sessions"].path, RT.store, RT.pipeline)


@router.get("/api/sessions/{sid}/repair-proposals/{pid}")
def proposal(sid: str, pid: str):
    _, controller = _runtime()
    try:
        return controller.public(controller.proposal(sid, pid))
    except RepairDenied as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/api/sessions/{sid}/repair-proposals/{pid}/approve")
async def approve(sid: str, pid: str, request: Request):
    state, controller = _runtime()
    body = await request.json()
    try:
        grant = controller.approve(sid, pid, body.get("source_snapshot_id", ""),
                                   max_rounds=body.get("max_rounds", 2),
                                   max_model_calls=body.get("max_model_calls", 6),
                                   wall_seconds=body.get("wall_seconds", 900))
        controller.dispatch_approvals(state["bridge"])
        turn = state["sessions"].turns.by_key("repair-grant:" + grant["id"])
        return {"grant": grant, "turn": turn}
    except (RepairDenied, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/sessions/{sid}/repair-grants/{gid}/revoke")
def revoke(sid: str, gid: str):
    _, controller = _runtime()
    controller.grant(sid, gid)
    with controller._c() as c:
        c.execute("UPDATE repair_grants SET status='revoked',deadline=0 WHERE id=? AND session_id=?", (gid, sid))
    controller.tick()
    return {"status": "revoked"}


def install(state):
    from .tools import RT
    controller = RepairController(state["sessions"].path, RT.store, RT.pipeline)
    controller.recover()
    state["bridge"].add_hook(controller.tick)
    state["bridge"].add_hook(lambda: controller.dispatch_approvals(state["bridge"]))
