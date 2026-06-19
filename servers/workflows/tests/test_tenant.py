"""Tests for tenant awareness: active-tenant read, pin/drift, and the
transport-level access-failure diagnosis. No network — UM is stubbed."""
import pytest

from nrev_workflows_mcp import config, tenant, tools_tenant, transport, um_api
from nrev_workflows_mcp.transport import TenantChangedError


def _tenants(*active_then_others):
    """Build a /user/tenants payload. First arg is the active tenant id; the
    rest are dormant. Each arg is an int id."""
    active_id, *rest = active_then_others
    out = [{"tenant_id": active_id, "tenant_name": f"T{active_id}", "tenant_domain": f"t{active_id}.com", "is_enabled": True}]
    out += [{"tenant_id": i, "tenant_name": f"T{i}", "tenant_domain": f"t{i}.com", "is_enabled": False} for i in rest]
    return {"tenants": out}


@pytest.fixture
def um(monkeypatch):
    """Stub um_api.get_user_tenants with a settable payload + call counter."""
    state = {"payload": _tenants(1, 2), "calls": 0}

    def fake():
        state["calls"] += 1
        return state["payload"]

    monkeypatch.setattr(um_api, "get_user_tenants", fake)
    tenant.reset()
    yield state
    tenant.reset()


# ── active_tenant read + cache ───────────────────────────────────────────────


def test_active_tenant_picks_enabled_and_lists_available(um):
    info = tenant.active_tenant(force=True)
    assert info["active"] == {"tenant_id": 1, "tenant_name": "T1", "tenant_domain": "t1.com"}
    assert len(info["available"]) == 2
    assert [t["is_active"] for t in info["available"]] == [True, False]


def test_active_tenant_caches_until_forced(um):
    tenant.active_tenant(force=True)
    tenant.active_tenant()  # cache hit
    assert um["calls"] == 1
    tenant.active_tenant(force=True)  # bypasses cache
    assert um["calls"] == 2


# ── pin / drift ──────────────────────────────────────────────────────────────


def test_no_drift_without_pin(um):
    assert tenant.check_drift() is None


def test_no_drift_when_active_matches_pin(um):
    tenant.pin(tenant.active_tenant(force=True)["active"])
    assert tenant.check_drift() is None


def test_drift_detected_when_active_changes(um):
    tenant.pin(tenant.active_tenant(force=True)["active"])  # pinned to T1
    um["payload"] = _tenants(2, 1)  # user switched to T2 in the web app
    drift = tenant.check_drift()
    assert drift is not None
    assert drift["from"]["tenant_id"] == 1
    assert drift["to"]["tenant_id"] == 2


def test_assert_pinned_active_raises_on_drift(um):
    tenant.pin(tenant.active_tenant(force=True)["active"])
    um["payload"] = _tenants(2, 1)
    with pytest.raises(TenantChangedError) as exc:
        tenant.assert_pinned_active("creating a workflow")
    assert "creating a workflow" in str(exc.value)
    assert exc.value.from_tenant["tenant_id"] == 1
    assert exc.value.to_tenant["tenant_id"] == 2


def test_assert_pinned_active_noop_without_pin(um):
    tenant.assert_pinned_active("creating a workflow")  # no pin -> must not raise


# ── get_active_tenant tool ───────────────────────────────────────────────────


def test_tool_first_call_pins(um):
    out = tools_tenant.get_active_tenant()
    assert out["active"]["tenant_id"] == 1
    assert out["pinned"]["tenant_id"] == 1
    assert out["changed_since_pin"] is False
    assert out["can_switch"] is True


def test_tool_reports_change_and_keeps_pin(um):
    tools_tenant.get_active_tenant()  # pin T1
    um["payload"] = _tenants(2, 1)  # switched out of band
    out = tools_tenant.get_active_tenant()
    assert out["changed_since_pin"] is True
    assert out["pinned"]["tenant_id"] == 1  # pin NOT silently moved
    assert out["active"]["tenant_id"] == 2


def test_tool_repin_reanchors(um):
    tools_tenant.get_active_tenant()  # pin T1
    um["payload"] = _tenants(2, 1)
    out = tools_tenant.get_active_tenant(repin=True)
    assert out["changed_since_pin"] is False
    assert out["pinned"]["tenant_id"] == 2


def test_tool_single_tenant_cannot_switch(um):
    um["payload"] = _tenants(1)
    out = tools_tenant.get_active_tenant()
    assert out["can_switch"] is False


# ── transport access-failure diagnosis ───────────────────────────────────────


def test_diagnosis_raises_on_resource_host_403_when_drifted(um, monkeypatch):
    monkeypatch.setattr(config, "um_url", lambda: "https://um.example")
    monkeypatch.setattr(tenant, "check_drift", lambda force=True: {"from": {"tenant_id": 1, "tenant_name": "T1"}, "to": {"tenant_id": 2, "tenant_name": "T2"}})
    with pytest.raises(TenantChangedError):
        transport._maybe_raise_tenant_changed("https://workflow.example", 403)


def test_diagnosis_skips_um_host(um, monkeypatch):
    monkeypatch.setattr(config, "um_url", lambda: "https://um.example")
    monkeypatch.setattr(tenant, "check_drift", lambda force=True: {"from": {}, "to": {}})
    # The drift check itself hits UM; a UM failure must never be reframed.
    transport._maybe_raise_tenant_changed("https://um.example", 403)  # no raise


def test_diagnosis_ignores_non_access_status(um, monkeypatch):
    monkeypatch.setattr(config, "um_url", lambda: "https://um.example")
    monkeypatch.setattr(tenant, "check_drift", lambda force=True: {"from": {}, "to": {}})
    transport._maybe_raise_tenant_changed("https://workflow.example", 500)  # no raise


def test_diagnosis_silent_when_no_drift(um, monkeypatch):
    monkeypatch.setattr(config, "um_url", lambda: "https://um.example")
    monkeypatch.setattr(tenant, "check_drift", lambda force=True: None)
    transport._maybe_raise_tenant_changed("https://workflow.example", 404)  # no raise
