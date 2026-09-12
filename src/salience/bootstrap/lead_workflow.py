from salience.bootstrap.service import BootstrapResult, ContentProgramService


class LeadBootstrapWorkflow:
    """Owned bootstrap orchestration seam; durable scheduling is added at deployment."""

    def __init__(self, service: ContentProgramService) -> None:
        self._service = service

    async def run(self, niche: str) -> BootstrapResult:
        return await self._service.create_from_niche(niche=niche)
