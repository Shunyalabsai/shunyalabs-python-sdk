"""Token state is shared per credential, not per service instance.

A voice pipeline builds one service object per stream. Before 1.1.0 each held its
own token, so N concurrent calls meant 2N mint requests on the critical path of
call setup.
"""

import asyncio

import pytest

from shunyalabs._core._auth import TokenAuth, reset_token_cache


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_token_cache()
    yield
    reset_token_cache()


def _counting_mint(counter):
    async def _mint(self):
        counter["n"] += 1
        self._store_token({"token": f"jwt-{counter['n']}", "expires_in": 900})

    return _mint


class TestSharedTokenCache:
    def test_many_instances_mint_once(self, monkeypatch):
        counter = {"n": 0}
        monkeypatch.setattr(TokenAuth, "_mint", _counting_mint(counter))

        async def main():
            # 50 calls x (one STT + one TTS) = 100 auth objects.
            auths = [TokenAuth("key-abc") for _ in range(100)]
            headers = await asyncio.gather(*(a.aget_auth_headers() for a in auths))
            return headers

        headers = asyncio.run(main())
        assert counter["n"] == 1
        assert len({h["Authorization"] for h in headers}) == 1

    def test_different_credentials_do_not_share(self, monkeypatch):
        counter = {"n": 0}
        monkeypatch.setattr(TokenAuth, "_mint", _counting_mint(counter))

        async def main():
            await TokenAuth("key-a").aget_auth_headers()
            await TokenAuth("key-b").aget_auth_headers()

        asyncio.run(main())
        assert counter["n"] == 2

    def test_different_ttl_does_not_share(self, monkeypatch):
        counter = {"n": 0}
        monkeypatch.setattr(TokenAuth, "_mint", _counting_mint(counter))

        async def main():
            await TokenAuth("key-a", ttl_seconds=900).aget_auth_headers()
            await TokenAuth("key-a", ttl_seconds=60).aget_auth_headers()

        asyncio.run(main())
        assert counter["n"] == 2

    def test_survives_a_new_event_loop(self, monkeypatch):
        # An asyncio.Lock binds to the first loop that touches it. A process-wide
        # cache outlives any one loop, so the lock has to be rebuilt rather than
        # reused across them.
        counter = {"n": 0}
        monkeypatch.setattr(TokenAuth, "_mint", _counting_mint(counter))

        async def once():
            return await TokenAuth("key-abc").aget_auth_headers()

        first = asyncio.run(once())
        second = asyncio.run(once())

        assert counter["n"] == 1, "second loop should reuse the cached token"
        assert first == second

    def test_legacy_private_attributes_still_readable(self, monkeypatch):
        counter = {"n": 0}
        monkeypatch.setattr(TokenAuth, "_mint", _counting_mint(counter))

        auth = TokenAuth("key-abc")
        asyncio.run(auth.aget_auth_headers())

        assert auth._token == "jwt-1"
        assert auth._expires_at > 0
        assert auth._endpoints == {}

    def test_reset_clears_state(self, monkeypatch):
        counter = {"n": 0}
        monkeypatch.setattr(TokenAuth, "_mint", _counting_mint(counter))

        asyncio.run(TokenAuth("key-abc").aget_auth_headers())
        reset_token_cache()
        asyncio.run(TokenAuth("key-abc").aget_auth_headers())

        assert counter["n"] == 2
