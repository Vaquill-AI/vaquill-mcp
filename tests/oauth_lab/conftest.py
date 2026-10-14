import pytest

from tests.oauth_lab.flow import Lab


@pytest.fixture
def lab(monkeypatch: pytest.MonkeyPatch, respx_mock) -> Lab:
    return Lab(monkeypatch, respx_mock)
