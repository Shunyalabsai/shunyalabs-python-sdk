"""The TTS init frame carries `speed`, and real customer constructor calls work.

Skipped unless pipecat is installed:

    pip install -e . -e plugins/pipecat && pytest tests/plugins
"""

import asyncio

import pytest

pytest.importorskip("pipecat", reason="pipecat-shunyalabsai plugin not installed")

import pipecat_shunyalabs  # noqa: E402
import pipecat_shunyalabs.stt as stt_mod  # noqa: E402
import pipecat_shunyalabs.tts as tts_mod  # noqa: E402
from pipecat_shunyalabs.stt import ShunyalabsSTTService  # noqa: E402
from pipecat_shunyalabs.tts import ShunyalabsTTSService  # noqa: E402
from shunyalabs.asr._models import StreamingConfig  # noqa: E402


class _ReadyTransport:
    def __init__(self):
        self.sent = []
        self._closed = False

    async def send_message(self, msg):
        self.sent.append(msg)

    async def receive_message(self):
        return {"type": "ready", "sample_rate": 24000}


def _init_frame(**kw):
    svc = ShunyalabsTTSService(api_key="test-key", **kw)
    t = _ReadyTransport()

    async def _open():
        return t

    svc._open_transport = _open
    asyncio.run(svc._ensure_transport())
    return t.sent[0]


def test_speed_is_sent_in_the_init_frame():
    assert _init_frame(speed=1.15)["speed"] == 1.15


def test_natural_speed_is_not_sent():
    # Unset or 1.0 leaves the frame as before, so the server default applies.
    assert "speed" not in _init_frame()
    assert "speed" not in _init_frame(speed=1.0)
    assert "speed" not in _init_frame(speed=None)


def test_enablex_tts_arguments():
    # Exactly as a customer passes them: base_url is the REST base and is not used.
    svc = ShunyalabsTTSService(
        api_key="test-key", language="hinglish",
        base_url="https://ttsv2.shunyalabs.ai/v1", voice="Meera", model="zero-indic",
    )
    assert svc._ws_url == "wss://ttsv2.shunyalabs.ai/v1/realtime"
    init = _init_frame(language="hinglish", base_url="https://ttsv2.shunyalabs.ai/v1",
                       voice="Meera", model="zero-indic")
    assert init == {"voice": "Meera", "language": "hinglish", "model": "zero-indic",
                    "client": f"pipecat-shunyalabsai/{pipecat_shunyalabs.__version__}"}


def test_enablex_stt_codeswitch_string_reaches_the_gateway_as_true():
    svc = ShunyalabsSTTService(api_key="test-key", codeswitch="true")
    payload = StreamingConfig(language=svc._language, codeswitch=svc._codeswitch).to_ws_payload()
    assert payload["codeswitch"] is True
    assert StreamingConfig(codeswitch="false").to_ws_payload().get("codeswitch") is False


CLIENT = f"pipecat-shunyalabsai/{pipecat_shunyalabs.__version__}"


def test_the_client_tag_follows_the_package_version():
    assert pipecat_shunyalabs.__version__ in CLIENT
    assert pipecat_shunyalabs._CLIENT_ID == CLIENT


def test_tts_init_frame_carries_the_client_tag():
    assert _init_frame()["client"] == CLIENT


def test_tts_socket_sends_the_client_as_user_agent(monkeypatch):
    seen = {}

    class _Transport:
        def __init__(self, **kw):
            pass

        async def connect(self, ws_headers=None):
            seen["headers"] = ws_headers

    async def _no_endpoints():
        return {}

    monkeypatch.setattr(tts_mod, "WsTransport", _Transport)
    svc = ShunyalabsTTSService(api_key="test-key")
    monkeypatch.setattr(svc._auth, "aget_endpoints", _no_endpoints)
    asyncio.run(svc._open_transport())
    assert seen["headers"] == {"User-Agent": CLIENT}


def test_stt_init_frame_carries_the_client_tag(monkeypatch):
    seen = {}

    class _Stop(Exception):
        pass

    class _Streaming:
        def __init__(self, **kw):
            pass

        async def stream(self, config):
            seen["payload"] = config.to_ws_payload()
            raise _Stop

    async def _no_endpoints():
        return {}

    monkeypatch.setattr(stt_mod, "AsyncStreamingASR", _Streaming)
    svc = ShunyalabsSTTService(api_key="test-key", language="hi", codeswitch="true")
    monkeypatch.setattr(svc._auth, "aget_endpoints", _no_endpoints)
    with pytest.raises(_Stop):
        asyncio.run(svc._connect())
    assert seen["payload"]["client"] == CLIENT
    assert seen["payload"]["language"] == "hi" and seen["payload"]["codeswitch"] is True
