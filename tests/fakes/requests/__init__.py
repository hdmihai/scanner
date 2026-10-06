"""requests FALS pentru testele offline: orice cerere esueaza ca o retea cazuta.

Garanteaza ca testele nu ating internetul si ca fiecare cod care foloseste reteaua
isi exercita ramura de esec (pastreaza datele existente, nu inventeaza)."""


class RequestException(Exception):
    pass


class ConnectionError(RequestException):  # noqa: A001 - aceeasi denumire ca in requests
    pass


class Timeout(RequestException):
    pass


class HTTPError(RequestException):
    pass


exceptions = type("exceptions", (), {"RequestException": RequestException, "ConnectionError": ConnectionError,
                                     "Timeout": Timeout, "HTTPError": HTTPError})


def _offline(*_a, **_k):
    raise ConnectionError("retea dezactivata in testele offline")


get = post = put = delete = head = request = _offline


class Session:
    def __init__(self, *a, **k):
        self.headers = {}

    get = post = put = delete = head = request = staticmethod(_offline)

    def mount(self, *a, **k):
        pass

    def close(self):
        pass
