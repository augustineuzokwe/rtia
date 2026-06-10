"""Tests for the optional remote-Ollama plumbing (``RTIA_OLLAMA_HOST`` +
``RTIA_OLLAMA_AUTH_TOKEN``).

These two env vars opt a process into hitting a *remote*, reverse-proxy-
fronted Ollama (e.g. a NAS deployment behind Caddy) instead of the
default localhost. The helper :func:`agents.config.ollama_remote_kwargs`
returns kwargs that get spread into every ``ChatOllama(...)`` call site;
``langchain-ollama 1.1.0`` natively accepts ``base_url`` and
``client_kwargs={"headers": ...}`` (verified against the installed
package's ``chat_models.py:693, 715``).

The contract these tests defend:

1. Both unset → kwargs ``{}`` → localhost behaviour preserved
   byte-identically (no behavioural change for existing users).
2. Host set, token unset → ``{"base_url": <host>}`` (proxy without auth -
   uncommon but valid for a private LAN).
3. Token set, host unset → ``{}`` (no-op; the token must opt into the
   remote path explicitly so it can't leak to a localhost request).
4. Both set → ``{"base_url": <host>, "client_kwargs": {"headers":
   {"Authorization": "Bearer <token>"}}}`` (the canonical NAS-via-Caddy
   shape).
5. Whitespace in env values is stripped (operators copy-paste tokens with
   trailing newlines; this is a sharp edge worth defending).
"""

from __future__ import annotations

import pytest

from agents.config import (
    OLLAMA_AUTH_TOKEN_ENV_VAR,
    OLLAMA_HOST_ENV_VAR,
    ollama_remote_kwargs,
)


class TestOllamaRemoteKwargs:
    def test_both_unset_returns_empty_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Preserves byte-identical localhost behaviour for existing users.
        # Spreading {} into ChatOllama(...) is a no-op.
        monkeypatch.delenv(OLLAMA_HOST_ENV_VAR, raising=False)
        monkeypatch.delenv(OLLAMA_AUTH_TOKEN_ENV_VAR, raising=False)
        assert ollama_remote_kwargs() == {}

    def test_host_only_returns_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(OLLAMA_HOST_ENV_VAR, "http://nas.local:11435")
        monkeypatch.delenv(OLLAMA_AUTH_TOKEN_ENV_VAR, raising=False)
        assert ollama_remote_kwargs() == {"base_url": "http://nas.local:11435"}

    def test_token_without_host_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The token must opt into the remote path explicitly. Otherwise a
        # forgotten RTIA_OLLAMA_AUTH_TOKEN in the env would silently add a
        # Bearer header to localhost:11434 requests, surprising the operator.
        monkeypatch.delenv(OLLAMA_HOST_ENV_VAR, raising=False)
        monkeypatch.setenv(OLLAMA_AUTH_TOKEN_ENV_VAR, "secret-token-value")
        assert ollama_remote_kwargs() == {}

    def test_both_set_returns_base_url_and_bearer_header(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(OLLAMA_HOST_ENV_VAR, "http://nas.local:11435")
        monkeypatch.setenv(OLLAMA_AUTH_TOKEN_ENV_VAR, "secret-token-value")
        assert ollama_remote_kwargs() == {
            "base_url": "http://nas.local:11435",
            "client_kwargs": {
                "headers": {"Authorization": "Bearer secret-token-value"},
            },
        }

    @pytest.mark.parametrize(
        ("host_value", "token_value"),
        [
            ("  http://nas.local:11435  ", "  secret  "),
            ("\thttp://nas.local:11435\n", "secret\n"),
        ],
    )
    def test_whitespace_in_env_values_is_stripped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        host_value: str,
        token_value: str,
    ) -> None:
        # Operators copy-paste tokens from secret managers / docs / chat
        # windows; trailing whitespace and newlines are routine. Strip
        # them defensively so the constructed Authorization header doesn't
        # carry a stray \n that some HTTP stacks reject.
        monkeypatch.setenv(OLLAMA_HOST_ENV_VAR, host_value)
        monkeypatch.setenv(OLLAMA_AUTH_TOKEN_ENV_VAR, token_value)
        kwargs = ollama_remote_kwargs()
        assert kwargs["base_url"] == "http://nas.local:11435"
        assert kwargs["client_kwargs"]["headers"]["Authorization"] == "Bearer secret"

    def test_empty_string_host_treated_as_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # An exported-but-empty env var (`export RTIA_OLLAMA_HOST=""`) is
        # ambiguous - the operator clearly didn't mean "use localhost AND
        # send Bearer to it". Treat as unset, same as the token-without-
        # host case.
        monkeypatch.setenv(OLLAMA_HOST_ENV_VAR, "")
        monkeypatch.setenv(OLLAMA_AUTH_TOKEN_ENV_VAR, "secret-token-value")
        assert ollama_remote_kwargs() == {}


class TestChatOllamaReceivesRemoteKwargs:
    """End-to-end: when the env vars are set, a ``ChatOllama`` instance
    constructed via the helper has the expected base_url + client_kwargs
    on it. Mocked - no real network call. This is the contract the agent
    files rely on by spreading ``**ollama_remote_kwargs()`` into their
    ``ChatOllama(...)`` calls.
    """

    def test_chat_ollama_built_with_both_kwargs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(OLLAMA_HOST_ENV_VAR, "http://nas.local:11435")
        monkeypatch.setenv(OLLAMA_AUTH_TOKEN_ENV_VAR, "secret-token-value")

        from langchain_ollama import ChatOllama

        # Construct ChatOllama with the same spread the agents do.
        llm = ChatOllama(
            model="llama3.1:8b",
            temperature=0,
            num_predict=4000,
            format="json",
            **ollama_remote_kwargs(),
        )

        assert llm.base_url == "http://nas.local:11435"
        assert llm.client_kwargs == {
            "headers": {"Authorization": "Bearer secret-token-value"},
        }

    def test_chat_ollama_unchanged_when_env_vars_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The contract the no-change-for-existing-users argument rests on:
        # constructing ChatOllama with **{} is byte-identical to not
        # passing the extra kwargs at all.
        monkeypatch.delenv(OLLAMA_HOST_ENV_VAR, raising=False)
        monkeypatch.delenv(OLLAMA_AUTH_TOKEN_ENV_VAR, raising=False)

        from langchain_ollama import ChatOllama

        llm = ChatOllama(
            model="llama3.1:8b",
            temperature=0,
            num_predict=4000,
            format="json",
            **ollama_remote_kwargs(),
        )

        # base_url stays at the langchain-ollama default (None or library
        # default URL). client_kwargs stays at the default ({}).
        assert llm.client_kwargs == {}
