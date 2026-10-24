"""Client-owned redirect policy; never forward credentials through redirects."""

from urllib.request import HTTPRedirectHandler, Request


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: object, code: int, msg: str, headers: object, newurl: str
    ) -> None:
        return None
