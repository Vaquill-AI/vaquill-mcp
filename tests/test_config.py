"""Tests for vaquill_mcp.config module."""

import pytest

from vaquill_mcp.config import get_api_key, get_base_url, get_timeout


class TestGetApiKey:
    """Tests for API key reading from environment."""

    def test_returns_key_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VAQUILL_API_KEY", "vq_key_test123")
        assert get_api_key() == "vq_key_test123"

    def test_strips_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VAQUILL_API_KEY", "  vq_key_test123  ")
        assert get_api_key() == "vq_key_test123"

    def test_raises_when_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VAQUILL_API_KEY", raising=False)
        with pytest.raises(ValueError, match="VAQUILL_API_KEY"):
            get_api_key()

    def test_raises_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VAQUILL_API_KEY", "")
        with pytest.raises(ValueError, match="VAQUILL_API_KEY"):
            get_api_key()

    def test_raises_when_whitespace_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VAQUILL_API_KEY", "   ")
        with pytest.raises(ValueError, match="VAQUILL_API_KEY"):
            get_api_key()


class TestGetBaseUrl:
    """Tests for base URL reading from environment."""

    def test_returns_default_when_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VAQUILL_BASE_URL", raising=False)
        assert get_base_url() == "https://api.vaquill.ai"

    def test_returns_custom_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VAQUILL_BASE_URL", "http://localhost:8000")
        assert get_base_url() == "http://localhost:8000"

    def test_strips_trailing_slash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VAQUILL_BASE_URL", "http://localhost:8000/")
        assert get_base_url() == "http://localhost:8000"

    def test_rejects_non_http_scheme(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VAQUILL_BASE_URL", "ftp://evil.com")
        with pytest.raises(ValueError, match="http:// or https://"):
            get_base_url()

    def test_rejects_file_scheme(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VAQUILL_BASE_URL", "file:///etc/passwd")
        with pytest.raises(ValueError, match="http:// or https://"):
            get_base_url()

    def test_rejects_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VAQUILL_BASE_URL", "")
        with pytest.raises(ValueError, match="http:// or https://"):
            get_base_url()


class TestGetTimeout:
    """Tests for timeout reading from environment."""

    def test_returns_default_when_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VAQUILL_TIMEOUT", raising=False)
        assert get_timeout() == 120.0

    def test_returns_custom_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VAQUILL_TIMEOUT", "60")
        assert get_timeout() == 60.0

    def test_handles_float_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VAQUILL_TIMEOUT", "30.5")
        assert get_timeout() == 30.5

    def test_rejects_non_numeric(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VAQUILL_TIMEOUT", "not-a-number")
        with pytest.raises(ValueError, match="must be a number"):
            get_timeout()

    def test_rejects_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VAQUILL_TIMEOUT", "0")
        with pytest.raises(ValueError, match="must be positive"):
            get_timeout()

    def test_rejects_negative(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VAQUILL_TIMEOUT", "-5")
        with pytest.raises(ValueError, match="must be positive"):
            get_timeout()
