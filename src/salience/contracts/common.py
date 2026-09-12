class ContractError(ValueError):
    pass


class CompatibilityError(ContractError):
    pass


def contract_major(version: str) -> int:
    try:
        return int(version.split(".", maxsplit=1)[0])
    except ValueError as error:
        raise CompatibilityError(f"invalid contract version: {version}") from error


def contract_versions_compatible(*, supported: str, candidate: str) -> bool:
    return contract_major(supported) == contract_major(candidate)

