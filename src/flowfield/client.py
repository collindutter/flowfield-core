"""HTTP client for local interfaces. Only the service opens the database."""

import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from flowfield.errors import ApplicationError
from flowfield.reads import receipt


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class Client:
    def __init__(self, directory: Path, port: int):
        self.directory = directory
        self.port = port

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None, *, timeout: int = 60
    ) -> Any:
        request = Request(
            f"http://127.0.0.1:{self.port}/api/{path}",
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            with build_opener(ProxyHandler({}), NoRedirect()).open(
                request, timeout=timeout
            ) as response:
                payload = json.load(response)
                return receipt(payload) if method != "GET" else payload
        except HTTPError as error:
            payload = json.loads(error.read())
            detail = payload.get("error", {})
            raise ApplicationError(
                detail.get("code", "invalid_request"),
                detail.get("message", str(payload.get("detail", "Request failed."))),
                error.code,
            ) from error
        except (URLError, TimeoutError) as error:
            raise ApplicationError(
                "service_unavailable",
                f"Cannot reach Flowfield on port {self.port}. "
                "Start flowfield serve with this port.",
            ) from error
