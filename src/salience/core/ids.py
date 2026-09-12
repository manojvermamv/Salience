from typing import TypeAlias
from uuid import UUID, uuid4


WorkspaceId: TypeAlias = UUID
ContentProgramId: TypeAlias = UUID
JobId: TypeAlias = UUID
RunId: TypeAlias = UUID
TraceId: TypeAlias = UUID


def new_id() -> UUID:
    return uuid4()
