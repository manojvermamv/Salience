class ModelGatewayError(RuntimeError):
    pass


class ModelDisabledError(ModelGatewayError):
    pass


class ModelOutputInvalidError(ModelGatewayError):
    pass
