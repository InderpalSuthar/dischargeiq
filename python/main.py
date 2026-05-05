from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from mcp_instance import mcp


class AcceptHeaderMiddleware(BaseHTTPMiddleware):
    """Ensures the Accept header includes text/event-stream for MCP compatibility.
    Some MCP clients (like Prompt Opinion) may not send the required Accept header."""

    async def dispatch(self, request: Request, call_next):
        accept = request.headers.get("accept", "")
        if "text/event-stream" not in accept:
            # Mutate the scope headers to include the required Accept
            headers = dict(request.scope["headers"])
            new_accept = f"{accept}, text/event-stream" if accept else "application/json, text/event-stream"
            # Headers in ASGI scope are list of tuples of bytes
            raw_headers = [
                (k, v) for k, v in request.scope["headers"] if k != b"accept"
            ]
            raw_headers.append((b"accept", new_accept.encode()))
            request.scope["headers"] = raw_headers
        response = await call_next(request)
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield


app = FastAPI(lifespan=lifespan)

app.add_middleware(AcceptHeaderMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/", mcp.streamable_http_app())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
