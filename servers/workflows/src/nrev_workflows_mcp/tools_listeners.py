"""Listener (webhook/trigger) test-lifecycle tools.

A listener node (form submission, CRM event, incoming webhook, email reply…)
only fires when something arrives from the outside world. These tools drive
the same arm → poll → disarm loop the web app's "Test trigger" button uses, so
a listener can be exercised from chat: arm it, have the user fire the real
event (submit the form, send the email), poll for the captured payload, then
build the rest of the workflow against the REAL event shape instead of a
guessed one.

Endpoint paths and parameter names verified against the FE ListenersApiService.
"""
from __future__ import annotations

from . import api
from .app import mcp

_EXECUTION_MODES = ("semi_workflow", "full_workflow")


@mcp.tool()
def activate_listener_test(workflow_id: str, node_id: str, execution_mode: str = "semi_workflow") -> dict:
    """Arm a listener node to capture its next incoming event — step 1 of
    testing any listener-triggered workflow. After arming, tell the user to
    fire the real event (submit the form, reply to the email, trigger the
    webhook), then poll get_listener_event for the captured payload.

    `execution_mode`: "semi_workflow" (default — capture the event only, so
    you can inspect its shape without spending credits downstream) or
    "full_workflow" (the captured event also runs the workflow). Disarm with
    deactivate_listener when done — an armed listener keeps listening.
    """
    if execution_mode not in _EXECUTION_MODES:
        raise ValueError(f"execution_mode must be one of {', '.join(_EXECUTION_MODES)}")
    result = api.activate_listener_test(workflow_id, node_id, execution_mode)
    return {
        "armed": True,
        "node_id": node_id,
        "execution_mode": execution_mode,
        "next_step": (
            "have the user fire the real event, then poll get_listener_event "
            "(status stays 'running' until an event arrives)"
        ),
        "result": result,
    }


@mcp.tool()
def get_listener_event(workflow_id: str, node_id: str, historical: bool = False) -> dict:
    """Poll an armed listener for its captured event — step 2 after
    activate_listener_test. Returns {status, data, ...}: status "running"
    means nothing has arrived yet (poll again after the user fires the event),
    "completed" carries the captured payload(s) in `data` — the REAL event
    shape to configure downstream nodes against — and "failed"/"timeout"
    carry the error.

    `historical=true` returns the latest event captured up to now (useful when
    the event fired before you started polling); the default false only
    returns events that arrived after the listener last listened.
    """
    return api.get_listener_latest_event(workflow_id, node_id, historical=historical)


@mcp.tool()
def deactivate_listener(workflow_id: str, node_id: str) -> dict:
    """Disarm a listener armed with activate_listener_test — step 3, once the
    test event is captured. Always disarm when the test is done; an armed
    listener keeps capturing events."""
    result = api.deactivate_listener(workflow_id, node_id)
    return {"armed": False, "node_id": node_id, "result": result}
