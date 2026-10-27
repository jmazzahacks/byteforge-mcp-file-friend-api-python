import time
from unittest.mock import patch

from byteforge_mcp_file_friend_api_python import Client

FILE_ID = "c" * 32


def test_client_status_can_be_reused_after_idle() -> None:
    client = Client("https://ff.example")
    client.deadline = time.monotonic() - 1

    def status(method: str, url: str, authenticated: bool = False) -> dict:
        client.check_time()
        return {"id": FILE_ID, "state": "uploading"}

    with patch.object(client, "json_request", side_effect=status):
        assert client.info(FILE_ID)["state"] == "uploading"
        client.deadline = time.monotonic() - 1
        client.remove(FILE_ID)
