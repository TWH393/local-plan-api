class APIError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)

    def body(self):
        return {'error': {'message': self.message, 'type': 'plan_gateway_error', 'code': self.code}}
