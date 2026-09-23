"""Explicitly approved local bundles; no provider execution or fallback."""

from salience.contracts.plugins import PluginManifest
from salience.plugins.registry import PluginRegistry


def fixture_bundle():
    registry=PluginRegistry(supported_contract_version="1.0")
    registry.register(PluginManifest(plugin_id="fixture.dummy",version="1.0.0",contract_version="1.0",capabilities=("fixture_record",),effect_classification="none",provider_metadata={"deployment_mode":"fixture","maximum_cost_micros":0,"fallback":"deny"}))
    manifest=registry.validate("fixture.dummy","fixture_record",version="1.0.0")
    return {"strategy_version":"fixture.baseline@1.0.0","prompt_version":"fixture.noop@1.0.0","capability_manifest":manifest.model_dump(mode="json")}


def resolve_baseline(connection,goal_id,goal_revision,*,approval_id=None):
    approval=connection.execute("SELECT * FROM v4_goal_baselines WHERE goal_id=%s AND goal_revision=%s AND expires_at>clock_timestamp()",(goal_id,goal_revision)).fetchone()
    if not approval or (approval_id is not None and str(approval["approval_id"])!=str(approval_id)):
        return None
    if connection.execute("SELECT 1 FROM v4_baseline_revocations WHERE approval_id=%s",(approval["approval_id"],)).fetchone():
        return None
    if approval["bundle"]!=fixture_bundle():
        return None
    return approval
