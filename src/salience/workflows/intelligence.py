"""Durable, source-grounded Phase 5 intelligence orchestration.

The workflow owns retries and checkpoints; activities own all I/O and canonical
PostgreSQL writes.  Activities only return IDs and compact normalized data, so
replay never depends on an SDK, provider object, or in-memory research result.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

from salience.agents.execution import AgentInvocation, AgentService
from salience.intelligence.contracts import FetchInput, OpportunityInput, SignalInput, SourceInput
from salience.intelligence.claims import ClaimEvidence, ClaimVerifier
from salience.intelligence.briefs import ContentBriefAssembler
from salience.intelligence.contracts import ClaimInput, PackageEvaluationInput, PackageInput
from salience.intelligence.packages import PackageEvaluator, StrategicPackageGenerator
from salience.intelligence.repository import IntelligenceRepository
from salience.intelligence.scoring import OpportunityRanker
from salience.intelligence.signals import CanonicalSignal, SignalDeduplicator, SignalSupport, normalize_fetched_source
from salience.research.contracts import FetchedSource
from salience.workflows.persistence import CanonicalJobStore, CanonicalRun


INTELLIGENCE_WORKFLOW_TYPE = "IntelligenceLoopWorkflow"
MAX_INTELLIGENCE_ATTEMPTS = 3


@dataclass(frozen=True)
class IntelligenceLoopRequest:
    workspace_id: str
    content_program_id: str
    niche: str
    idempotency_key: str
    dry_run: bool = True
    selected_opportunity_id: str | None = None
    contract_version: str = "IntelligenceRunRequest@v1"

    def __post_init__(self) -> None:
        if self.contract_version != "IntelligenceRunRequest@v1":
            raise ValueError("unsupported intelligence request contract")
        if not self.workspace_id or not self.content_program_id or not self.niche.strip():
            raise ValueError("workspace, content program, and niche are required")
        if not self.idempotency_key:
            raise ValueError("idempotency_key is required")


@dataclass(frozen=True)
class IntelligenceLoopResult:
    state: str
    job_id: str
    trace_id: str
    lead_agent_run_id: str | None = None
    research_agent_run_id: str | None = None
    strategy_agent_run_id: str | None = None
    strategy_version_id: str | None = None
    opportunity_ids: tuple[str, ...] = ()
    queue_entry_id: str | None = None
    selected_package_id: str | None = None
    content_brief_id: str | None = None


@dataclass
class IntelligenceWorkflowState:
    store: CanonicalJobStore
    repository: IntelligenceRepository
    agents: AgentService
    crash_at: str | None = None
    crash_reached: asyncio.Event = field(default_factory=asyncio.Event)
    crashed: bool = False


class IntelligenceActivities:
    def __init__(self, state: IntelligenceWorkflowState) -> None:
        self._state = state

    async def _run(self) -> CanonicalRun:
        return await self._state.store.run_for_workflow(activity.info().workflow_id)

    async def _checkpoint(self, checkpoint: str) -> None:
        await self._state.store.checkpoint(await self._run(), checkpoint)

    @activity.defn(name="salience.intelligence.fetch")
    async def fetch(self, request: IntelligenceLoopRequest) -> dict[str, Any]:
        """Call the native Research specialist then persist one idempotent source chain."""

        run = await self._run()
        lead_run = await self._state.agents.invoke(
            AgentInvocation(agent_id="lead_content_agent", input={"niche": request.niche})
        )
        research_run = await self._state.agents.invoke_from_parent(
            "lead_content_agent",
            AgentInvocation(agent_id="research_agent", input={"niche": request.niche}),
        )
        lead_id = _stage_run_id(run, "lead")
        research_id = _stage_run_id(run, "research")
        await self._state.repository.record_agent_run(
            run_id=lead_id,
            workspace_id=request.workspace_id,
            program_id=request.content_program_id,
            job_id=str(run.job_id),
            manifest=self._state.agents.describe_agent("lead_content_agent"),
            input_payload={"niche": request.niche},
            output_payload=lead_run.output,
            trace_id=run.trace_context.trace_id,
            span_id=run.trace_context.span_id,
        )
        await self._state.repository.record_agent_run(
            run_id=research_id,
            workspace_id=request.workspace_id,
            program_id=request.content_program_id,
            job_id=str(run.job_id),
            manifest=self._state.agents.describe_agent("research_agent"),
            input_payload={"niche": request.niche},
            output_payload=research_run.output,
            trace_id=run.trace_context.trace_id,
            span_id=run.trace_context.span_id,
            parent_run_id=lead_id,
        )
        await self._state.repository.record_delegation(
            parent_run_id=lead_id,
            child_run_id=research_id,
            trace_id=run.trace_context.trace_id,
        )

        observations: list[dict[str, Any]] = []
        for finding in research_run.output["findings"]:
            source = _source_from_finding(finding, request.niche)
            source_id = await self._state.repository.record_source(
                workspace_id=request.workspace_id,
                program_id=request.content_program_id,
                source=SourceInput(
                    source_key=source.source_id,
                    version=source.source_version,
                    connector=source.source_type,
                    trust_level="untrusted_external",
                    configuration={"effect_classification": "read_only"},
                    network_scope=[],
                    protocol_metadata={"agent_run_id": research_id, **source.provenance},
                ),
                trace_id=run.trace_context.trace_id,
            )
            raw_hash = source.raw_hash
            fetch_id = await self._state.repository.record_fetch(
                workspace_id=request.workspace_id,
                program_id=request.content_program_id,
                fetch=FetchInput(
                    source_id=source_id,
                    resource_identity=source.resource_identity,
                    window_key=source.fetched_at.date().isoformat(),
                    request_fingerprint=_hash({"url": source.canonical_url, "niche": request.niche}),
                    canonical_url=source.canonical_url,
                    raw_hash=raw_hash,
                    provenance={
                        "agent_run_id": research_id,
                        "source_identity": source.resource_identity,
                        "trust_level": "untrusted_external",
                    },
                ),
                trace_id=run.trace_context.trace_id,
            )
            evidence_id = await self._state.repository.record_evidence(
                workspace_id=request.workspace_id,
                program_id=request.content_program_id,
                source_id=source_id,
                fetch_id=fetch_id,
                source_uri=source.canonical_url,
                content=source.content,
                content_hash=raw_hash,
                idempotency_key=f"{run.job_id}:evidence:{source.resource_identity}",
                trace_id=run.trace_context.trace_id,
            )
            observations.append(
                {
                    "source_id": source_id,
                    "fetch_id": fetch_id,
                    "evidence_id": evidence_id,
                    "resource_identity": source.resource_identity,
                    "canonical_url": source.canonical_url,
                    "raw_hash": raw_hash,
                    "title": source.title,
                    "content": source.content,
                    "features": source.features,
                }
            )
        await self._checkpoint("research.fetch.persisted")
        if self._state.crash_at == "research.fetch.persisted" and not self._state.crashed:
            self._state.crashed = True
            self._state.crash_reached.set()
            await asyncio.Event().wait()
        return {
            "niche": request.niche,
            "selected_opportunity_id": request.selected_opportunity_id,
            "lead_agent_run_id": lead_id,
            "research_agent_run_id": research_id,
            "observations": observations,
        }

    @activity.defn(name="salience.intelligence.normalize")
    async def normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        run = await self._run()
        signals = [
            normalize_fetched_source(_source_from_observation(observation))
            for observation in payload["observations"]
        ]
        merged = SignalDeduplicator().merge(signals)
        persisted: list[dict[str, Any]] = []
        for signal in merged:
            signal_id = await self._state.repository.record_signal(
                workspace_id=str(run.workspace_id),
                program_id=str(run.content_program_id),
                signal=SignalInput(
                    fingerprint=signal.fingerprint,
                    topic=signal.topic,
                    features=signal.features,
                    availability=signal.feature_availability,
                    provenance={"canonical_url": signal.canonical_url},
                ),
                trace_id=run.trace_context.trace_id,
            )
            for support in signal.supports:
                matching = next(
                    observation
                    for observation in payload["observations"]
                    if observation["resource_identity"] == support.resource_identity
                )
                await self._state.repository.link_signal_support(
                    signal_id=signal_id,
                    evidence_id=matching["evidence_id"],
                    fetch_id=matching["fetch_id"],
                    source_id=matching["source_id"],
                    trace_id=run.trace_context.trace_id,
                )
            persisted.append(
                {
                    "id": signal_id,
                    "fingerprint": signal.fingerprint,
                    "topic": signal.topic,
                    "features": signal.features,
                    "availability": signal.feature_availability,
                }
            )
        await self._checkpoint("signals.normalized")
        return {**payload, "signals": persisted}

    @activity.defn(name="salience.intelligence.rank")
    async def rank(self, payload: dict[str, Any]) -> dict[str, Any]:
        run = await self._run()
        opportunities = OpportunityRanker().rank(
            [
                CanonicalSignal(
                    fingerprint=signal["fingerprint"],
                    topic=signal["topic"],
                    canonical_url="",
                    features=signal["features"],
                    feature_availability=signal["availability"],
                    supports=[SignalSupport(source_id="canonical", resource_identity=signal["id"])],
                )
                for signal in payload["signals"]
            ],
            model_judgements=[],
        )
        signal_ids = {signal["fingerprint"]: signal["id"] for signal in payload["signals"]}
        persisted: list[dict[str, Any]] = []
        for opportunity in opportunities:
            opportunity_id = await self._state.repository.record_opportunity(
                workspace_id=str(run.workspace_id),
                program_id=str(run.content_program_id),
                opportunity=OpportunityInput(
                    fingerprint=opportunity.fingerprint,
                    topic=opportunity.topic,
                    score=opportunity.score,
                    base_score=opportunity.base_score,
                    semantic_adjustment=opportunity.semantic_adjustment,
                    signal_ids=[signal_ids[fingerprint] for fingerprint in opportunity.signal_ids],
                    explanation=opportunity.explanation,
                    risks=opportunity.risks,
                    availability=opportunity.feature_availability,
                    provenance={"ranker": "deterministic-v1"},
                ),
                trace_id=run.trace_context.trace_id,
            )
            persisted.append({"id": opportunity_id, "topic": opportunity.topic, "score": opportunity.score})
        await self._checkpoint("opportunities.ranked")
        return {**payload, "opportunities": persisted}

    @activity.defn(name="salience.intelligence.strategy")
    async def strategy(self, payload: dict[str, Any]) -> dict[str, Any]:
        run = await self._run()
        strategy_run = await self._state.agents.invoke_from_parent(
            "lead_content_agent",
            AgentInvocation(
                agent_id="strategy_agent",
                input={"niche": payload["niche"]},
            ),
        )
        strategy_id = _stage_run_id(run, "strategy")
        await self._state.repository.record_agent_run(
            run_id=strategy_id,
            workspace_id=str(run.workspace_id),
            program_id=str(run.content_program_id),
            job_id=str(run.job_id),
            manifest=self._state.agents.describe_agent("strategy_agent"),
            input_payload={"niche": payload["niche"]},
            output_payload=strategy_run.output,
            trace_id=run.trace_context.trace_id,
            span_id=run.trace_context.span_id,
            parent_run_id=payload["lead_agent_run_id"],
        )
        await self._state.repository.record_delegation(
            parent_run_id=payload["lead_agent_run_id"],
            child_run_id=strategy_id,
            trace_id=run.trace_context.trace_id,
        )
        strategy_version_id = await self._state.repository.record_strategy(
            workspace_id=str(run.workspace_id),
            program_id=str(run.content_program_id),
            strategy=strategy_run.output,
            evidence_ids=[observation["evidence_id"] for observation in payload["observations"]],
            idempotency_key=f"{run.job_id}:strategy",
            trace_id=run.trace_context.trace_id,
        )
        await self._checkpoint("strategy.proposed")
        return {
            **payload,
            "strategy_agent_run_id": strategy_id,
            "strategy_version_id": strategy_version_id,
        }

    @activity.defn(name="salience.intelligence.queue")
    async def queue(self, payload: dict[str, Any]) -> dict[str, Any]:
        run = await self._run()
        if not payload["opportunities"]:
            raise RuntimeError("research produced no ranked opportunity")
        selected = payload["opportunities"][0]
        queue_id = await self._state.repository.record_queue_entry(
            workspace_id=str(run.workspace_id),
            program_id=str(run.content_program_id),
            opportunity_id=selected["id"],
            strategy_version_id=payload["strategy_version_id"],
            priority=1,
            trace_id=run.trace_context.trace_id,
        )
        await self._checkpoint("content_queue.updated")
        return {**payload, "queue_entry_id": queue_id}

    @activity.defn(name="salience.intelligence.packages")
    async def packages(self, payload: dict[str, Any]) -> dict[str, Any]:
        run = await self._run()
        selected = next(
            (
                item
                for item in payload["opportunities"]
                if item["id"] == payload.get("selected_opportunity_id")
            ),
            payload["opportunities"][0],
        )
        if payload.get("selected_opportunity_id") and selected["id"] != payload["selected_opportunity_id"]:
            selected = await self._state.repository.opportunity_details(
                opportunity_id=payload["selected_opportunity_id"],
                program_id=str(run.content_program_id),
            )
        opportunity = OpportunityInput(
            fingerprint=selected.get("fingerprint", _hash({"opportunity_id": selected["id"]})),
            topic=selected["topic"],
            score=selected["score"],
            signal_ids=selected.get("signal_ids", [signal["id"] for signal in payload["signals"]]),
            explanation="Selected from the deterministic source-grounded opportunity ranking.",
            risks=["untrusted_external_support"],
        )
        candidates = StrategicPackageGenerator().generate(
            opportunity,
            {"positioning": "evidence-grounded guidance"},
            count=3,
        )
        evaluator = PackageEvaluator()
        evidence = [{"verification_status": "unverified"} for _ in payload["observations"]]
        evaluated: list[tuple[str, PackageEvaluationInput]] = []
        for candidate in candidates:
            package_id = await self._state.repository.record_package(
                workspace_id=str(run.workspace_id),
                program_id=str(run.content_program_id),
                opportunity_id=selected["id"],
                package=candidate,
                trace_id=run.trace_context.trace_id,
            )
            evaluation = evaluator.evaluate(
                candidate,
                evidence,
                {"positioning": "evidence-grounded guidance"},
            )
            await self._state.repository.record_evaluation(
                package_id=package_id,
                evaluation=evaluation,
                trace_id=run.trace_context.trace_id,
            )
            evaluated.append((package_id, evaluation))
        selection = evaluator.select(evaluated)
        await self._state.repository.select_package(
            package_id=selection.package_id,
            reason=selection.reason,
            trace_id=run.trace_context.trace_id,
        )
        selected_evaluation = next(
            evaluation for package_id, evaluation in evaluated if package_id == selection.package_id
        )
        await self._state.repository.record_evaluation(
            package_id=selection.package_id,
            evaluation=PackageEvaluationInput(
                evaluation_key="selection-v1",
                score=selected_evaluation.score,
                semantic_score=selected_evaluation.semantic_score,
                reason=selection.reason,
                status="selected",
                provenance={"selector": "deterministic-packages-v1"},
            ),
            trace_id=run.trace_context.trace_id,
        )
        selected_package = next(
            candidate
            for (package_id, _), candidate in zip(evaluated, candidates, strict=True)
            if package_id == selection.package_id
        )
        await self._checkpoint("strategic_packages.evaluated")
        return {
            **payload,
            "selected_opportunity": selected,
            "selected_package_id": selection.package_id,
            "selected_package": {
                "diversity_fingerprint": selected_package.diversity_fingerprint,
                "content": selected_package.content,
            },
            "package_selection_reason": selection.reason,
        }

    @activity.defn(name="salience.intelligence.claims")
    async def claims(self, payload: dict[str, Any]) -> dict[str, Any]:
        run = await self._run()
        claim = ClaimVerifier().link(
            ClaimInput(
                fingerprint=_hash({"topic": payload["selected_opportunity"]["topic"], "kind": "research"}),
                text=(
                    f"External evidence may support the selected topic "
                    f"{payload['selected_opportunity']['topic']}."
                ),
            ),
            [
                ClaimEvidence(
                    evidence_id=observation["evidence_id"],
                    relation="supporting",
                    verification_status="unverified",
                    source_trust="untrusted_external",
                )
                for observation in payload["observations"]
            ],
        )
        claim_id = await self._state.repository.record_claim(
            workspace_id=str(run.workspace_id),
            program_id=str(run.content_program_id),
            claim=claim.claim,
            trace_id=run.trace_context.trace_id,
        )
        for evidence_id, relation in claim.links:
            await self._state.repository.link_claim_evidence(
                claim_id=claim_id,
                evidence_id=evidence_id,
                relation=relation,
                trace_id=run.trace_context.trace_id,
            )
        await self._checkpoint("claims.linked")
        return {
            **payload,
            "claims": [
                {
                    "id": claim_id,
                    "fingerprint": claim.claim.fingerprint,
                    "text": claim.claim.text,
                    "verification_status": claim.claim.verification_status,
                    "confidence": claim.claim.confidence,
                }
            ],
        }

    @activity.defn(name="salience.intelligence.brief")
    async def brief(self, payload: dict[str, Any]) -> dict[str, Any]:
        run = await self._run()
        selected = payload["selected_opportunity"]
        opportunity = OpportunityInput(
            fingerprint=selected.get("fingerprint", _hash({"opportunity_id": selected["id"]})),
            topic=selected["topic"],
            score=selected["score"],
            signal_ids=selected.get("signal_ids", [signal["id"] for signal in payload["signals"]]),
            explanation="Selected from the deterministic source-grounded opportunity ranking.",
            risks=["untrusted_external_support"],
        )
        package = payload["selected_package"]
        claims = [
            (
                item["id"],
                ClaimInput(
                    fingerprint=item["fingerprint"],
                    text=item["text"],
                    verification_status=item["verification_status"],
                    confidence=item["confidence"],
                ),
            )
            for item in payload["claims"]
        ]
        brief = ContentBriefAssembler().create(
            program_id=str(run.content_program_id),
            opportunity_id=selected["id"],
            opportunity=opportunity,
            package_id=payload["selected_package_id"],
            package=PackageInput(
                diversity_fingerprint=package["diversity_fingerprint"],
                content=package["content"],
                status="selected",
            ),
            strategy_version_id=payload["strategy_version_id"],
            claims=claims,
        )
        brief_id = await self._state.repository.record_content_brief(
            workspace_id=str(run.workspace_id),
            program_id=str(run.content_program_id),
            brief=brief,
            trace_id=run.trace_context.trace_id,
        )
        await self._checkpoint("content_brief.created")
        return {**payload, "content_brief_id": brief_id}

    @activity.defn(name="salience.intelligence.complete")
    async def complete(self, payload: dict[str, Any]) -> IntelligenceLoopResult:
        run = await self._run()
        result = IntelligenceLoopResult(
            state="completed",
            job_id=str(run.job_id),
            trace_id=run.trace_context.trace_id,
            lead_agent_run_id=payload["lead_agent_run_id"],
            research_agent_run_id=payload["research_agent_run_id"],
            strategy_agent_run_id=payload["strategy_agent_run_id"],
            strategy_version_id=payload["strategy_version_id"],
            opportunity_ids=tuple(item["id"] for item in payload["opportunities"]),
            queue_entry_id=payload["queue_entry_id"],
            selected_package_id=payload.get("selected_package_id"),
            content_brief_id=payload.get("content_brief_id"),
        )
        await self._state.store.complete_with_output(
            run, status="succeeded", output=_result_payload(result)
        )
        return result

    @activity.defn(name="salience.intelligence.cancel")
    async def cancel(self) -> IntelligenceLoopResult:
        run = await self._run()
        await self._checkpoint("intelligence.cancelled")
        await self._state.store.terminal(run, "cancelled")
        return IntelligenceLoopResult(
            state="cancelled", job_id=str(run.job_id), trace_id=run.trace_context.trace_id
        )


@workflow.defn(name=INTELLIGENCE_WORKFLOW_TYPE)
class IntelligenceLoopWorkflow:
    def __init__(self) -> None:
        self._cancellation_requested = False

    @workflow.signal
    def request_cancellation(self) -> None:
        self._cancellation_requested = True

    @workflow.run
    async def run(self, request: IntelligenceLoopRequest) -> IntelligenceLoopResult:
        payload: dict[str, Any] = {"niche": request.niche}
        for activity_name, argument in (
            ("salience.intelligence.fetch", request),
            ("salience.intelligence.normalize", payload),
            ("salience.intelligence.rank", payload),
            ("salience.intelligence.strategy", payload),
            ("salience.intelligence.queue", payload),
            ("salience.intelligence.packages", payload),
            ("salience.intelligence.claims", payload),
            ("salience.intelligence.brief", payload),
        ):
            if self._cancellation_requested:
                return await workflow.execute_activity(
                    "salience.intelligence.cancel",
                    start_to_close_timeout=timedelta(seconds=5),
                )
            if activity_name == "salience.intelligence.fetch":
                payload = await _execute_stage(activity_name, argument)
            else:
                payload = await _execute_stage(activity_name, payload)
        if self._cancellation_requested:
            return await workflow.execute_activity(
                "salience.intelligence.cancel",
                start_to_close_timeout=timedelta(seconds=5),
            )
        return await workflow.execute_activity(
            "salience.intelligence.complete",
            payload,
            start_to_close_timeout=timedelta(seconds=10),
        )


async def _execute_stage(name: str, argument: Any) -> dict[str, Any]:
    return await workflow.execute_activity(
        name,
        argument,
        start_to_close_timeout=timedelta(seconds=10),
        retry_policy=RetryPolicy(
            initial_interval=timedelta(milliseconds=100),
            maximum_interval=timedelta(seconds=1),
            maximum_attempts=MAX_INTELLIGENCE_ATTEMPTS,
        ),
    )


def build_intelligence_worker(
    client: Any, *, task_queue: str, state: IntelligenceWorkflowState
) -> Worker:
    activities = IntelligenceActivities(state)
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[IntelligenceLoopWorkflow],
        activities=[
            activities.fetch,
            activities.normalize,
            activities.rank,
            activities.strategy,
            activities.queue,
            activities.complete,
            activities.cancel,
            activities.packages,
            activities.claims,
            activities.brief,
        ],
    )


def _source_from_finding(finding: dict[str, Any], niche: str) -> FetchedSource:
    content = dict(finding["content"])
    source_uri = str(finding["source_uri"])
    provenance = dict(finding.get("provenance", {}))
    raw_payload = json.dumps(content, sort_keys=True).encode()
    return FetchedSource(
        source_id=str(provenance.get("source_id", "fixture_research")),
        source_version=str(provenance.get("source_version", "1.0.0")),
        source_type=str(provenance.get("connector", "fixture")),
        resource_identity=str(finding.get("source_identity") or source_uri),
        canonical_url=source_uri,
        raw_identity=str(finding.get("source_identity") or source_uri),
        raw_hash=hashlib.sha256(raw_payload).hexdigest(),
        fetched_at=datetime.now(UTC),
        published_at=None,
        title=str(content.get("title") or f"{niche} audience research"),
        content=content,
        raw_payload=raw_payload,
        features={
            "freshness": 0.8,
            "velocity": 0.4,
            "niche_relevance": 0.8,
            "audience_relevance": 0.7,
            "saturation": 0.2,
            "novelty": 0.4,
            "confidence": 0.6,
            "risk": 0.1,
            **{
                name: value
                for name, value in dict(content.get("features", {})).items()
                if isinstance(value, int | float)
            },
        },
        rate_limit={},
        trust_level="untrusted_external",
        provenance={
            "source_uri": source_uri,
            "agent_result": "ResearchResult@v1",
            **provenance,
        },
    )


def _source_from_observation(observation: dict[str, Any]) -> FetchedSource:
    raw_payload = json.dumps(observation["content"], sort_keys=True).encode()
    return FetchedSource(
        source_id=observation["source_id"],
        source_version="1.0.0",
        source_type="canonical",
        resource_identity=observation["resource_identity"],
        canonical_url=observation["canonical_url"],
        raw_identity=observation["resource_identity"],
        raw_hash=observation["raw_hash"],
        fetched_at=datetime.now(UTC),
        published_at=None,
        title=observation.get("title"),
        content=observation["content"],
        raw_payload=raw_payload,
        features=observation["features"],
        rate_limit={},
        trust_level="untrusted_external",
        provenance={},
    )


def _stage_run_id(run: CanonicalRun, stage: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"salience:intelligence:{run.job_id}:{stage}"))


def _hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _result_payload(result: IntelligenceLoopResult) -> dict[str, object]:
    return {
        "state": result.state,
        "lead_agent_run_id": result.lead_agent_run_id,
        "research_agent_run_id": result.research_agent_run_id,
        "strategy_agent_run_id": result.strategy_agent_run_id,
        "strategy_version_id": result.strategy_version_id,
        "opportunity_ids": list(result.opportunity_ids),
        "queue_entry_id": result.queue_entry_id,
        "selected_package_id": result.selected_package_id,
        "content_brief_id": result.content_brief_id,
    }
