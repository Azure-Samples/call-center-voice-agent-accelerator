import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parents[1]

PROVIDER_KEYS = [
    "ACS_CONNECTION_STRING", "TWILIO_AUTH_TOKEN", "INFOBIP_API_KEY", "GENESYS_API_KEY",
    "SINCH_APPLICATION_KEY", "BANDWIDTH_CLIENT_ID", "VONAGE_API_KEY", "WEB_ACCESS_TOKEN",
]

PROBE = """
import asyncio, json, server
rules = sorted({r.rule for r in server.app.url_map.iter_rules()})
async def main():
    c = server.app.test_client()
    h = await (await c.get('/health')).get_json()
    r = await c.get('/')
    return h, r.status_code, r.headers.get('Location')
h, status, loc = asyncio.run(main())
print(json.dumps({"rules": rules, "health": h, "index": [status, loc]}))
"""


def _probe(env_overrides):
    env = {k: v for k, v in os.environ.items() if k not in PROVIDER_KEYS}
    env.update(env_overrides)
    out = subprocess.run(
        [sys.executable, "-c", PROBE], cwd=SERVER_DIR, env=env, capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("genesys", [False, True])
def test_transcription_mode_routes(genesys):
    env = {"CALL_MODE": "transcription", "TRANSCRIPTION_BACKEND": "fake"}
    if genesys:
        env["GENESYS_API_KEY"] = "g"
    res = _probe(env)
    rules = set(res["rules"])
    assert {"/transcript", "/transcribe-test", "/transcript/active", "/transcript/ws", "/web/transcribe/ws"} <= rules
    assert "/web/ws" not in rules
    assert ("/audiohook/ws" in rules) == genesys
    assert res["health"] == {"status": "healthy", "mode": "transcription"}
    assert res["index"][0] == 302 and res["index"][1].endswith("/transcribe-test")


def test_voice_agent_mode_routes_unchanged():
    res = _probe({
        "CALL_MODE": "voice_agent",
        "AZURE_VOICE_LIVE_ENDPOINT": "https://x/",
        "AZURE_VOICE_LIVE_API_KEY": "k",
        "GENESYS_API_KEY": "g",
    })
    rules = set(res["rules"])
    assert {"/web/ws", "/audiohook/ws", "/genesys"} <= rules
    assert "/transcript/ws" not in rules
    assert res["health"] == {"status": "healthy", "mode": "voice_agent"}
    assert res["index"][0] == 200
