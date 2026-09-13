"""Deterministic native creative-specialist runtimes for Phase 7-8."""

from __future__ import annotations

from typing import Any

from salience.agents.execution import AgentExecutionContext, AgentInvocation, AgentRuntime
from salience.creative.contracts import CREATIVE_CAPABILITIES


class WriterAgentRuntime:
    async def invoke(
        self, invocation: AgentInvocation, context: AgentExecutionContext
    ) -> dict[str, Any]:
        brief_id = invocation.input["brief_id"]
        duration = invocation.input["target_duration_seconds"]
        hook_duration = max(1, duration // 4)
        cta_duration = max(1, duration // 4)
        body_duration = duration - hook_duration - cta_duration
        return {
            "contract_version": "ScriptVersion@v1",
            "brief_id": brief_id,
            "content_program_id": invocation.input["content_program_id"],
            "status": "draft",
            "target_format": invocation.input["target_format"],
            "target_duration_seconds": duration,
            "claim_ids": list(invocation.input["claim_ids"]),
            "evidence_ids": list(invocation.input["evidence_ids"]),
            "sections": [
                {
                    "kind": "hook",
                    "text": "Start with the evidence-linked practical question.",
                    "duration_seconds": hook_duration,
                },
                {
                    "kind": "body",
                    "text": "Explain the approved claim with its linked evidence.",
                    "duration_seconds": body_duration,
                },
                {
                    "kind": "cta",
                    "text": "Save the evidence-linked checklist for later.",
                    "duration_seconds": cta_duration,
                },
            ],
        }


class CreativeDirectorAgentRuntime:
    async def invoke(
        self, invocation: AgentInvocation, context: AgentExecutionContext
    ) -> dict[str, Any]:
        return {
            "contract_version": "CreativePlan@v1",
            "brief_id": invocation.input["brief_id"],
            "script_id": invocation.input["script_id"],
            "provider_id": None,
            "shots": [
                {
                    "sequence_no": 1,
                    "narration_reference": "script.section.1",
                    "visual_direction": "clear illustrative visual",
                    "camera_direction": "steady medium shot",
                }
            ],
            "capability_requests": [
                {
                    "capability": "text_to_video",
                    "expected_modality": "video",
                    "selection_criteria": ["policy", "rights", "budget", "technical_validation"],
                }
            ],
        }


class ProductionAgentRuntime:
    _maximum_variants = 3

    async def invoke(
        self, invocation: AgentInvocation, context: AgentExecutionContext
    ) -> dict[str, Any]:
        capability = invocation.input["capability"]
        if capability not in CREATIVE_CAPABILITIES:
            raise ValueError(f"unsupported creative capability: {capability}")
        requested_variants = min(invocation.input["max_variants"], self._maximum_variants)
        return {
            "contract_version": "ProductionPlan@v1",
            "brief_id": invocation.input["brief_id"],
            "script_id": invocation.input["script_id"],
            "capability": capability,
            "requested_variants": requested_variants,
            "selection_criteria": [
                "rights_authorized",
                "policy_authorized",
                "budget_reserved",
                "technical_validation",
            ],
            "external_effect": "workflow_activity_required",
        }


class VerifierAgentRuntime:
    async def invoke(
        self, invocation: AgentInvocation, context: AgentExecutionContext
    ) -> dict[str, Any]:
        return {
            "contract_version": "CreativeVerificationAdvisory@v1",
            "subject_id": invocation.input["subject_id"],
            "subject_type": invocation.input["subject_type"],
            "advisory_only": True,
            "findings": [],
            "deterministic_gate_required": True,
        }


def fixture_creative_runtimes() -> dict[str, AgentRuntime]:
    return {
        "writer_agent": WriterAgentRuntime(),
        "creative_director_agent": CreativeDirectorAgentRuntime(),
        "production_agent": ProductionAgentRuntime(),
        "verifier_agent": VerifierAgentRuntime(),
    }
