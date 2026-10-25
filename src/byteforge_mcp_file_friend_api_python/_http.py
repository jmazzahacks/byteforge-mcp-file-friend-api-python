"""Client HTTP identity and redirect policy; never forward credentials through redirects."""

from importlib.metadata import version
from urllib.request import HTTPRedirectHandler, Request

USER_AGENT = f"byteforge-mcp-file-friend-api-python/{version('byteforge-mcp-file-friend-api-python')}"


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: object, code: int, msg: str, headers: object, newurl: str
    ) -> None:
        return None
