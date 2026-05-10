from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from mcp_instance import mcp

VERSION = "2.0.0"
TOOLS = [
    "GeneratePersonalizedDischargePlan",
    "IdentifyReadmissionRiskGaps",
    "DraftTransitionCareSummary",
    "CheckDrugInteractions",
    "CalculateLACEScore",
    "GenerateMedicationCard",
    "GetDischargeReadinessDashboard",
]


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


app = FastAPI(lifespan=lifespan, title="DischargeIQ MCP Server", version=VERSION)

app.add_middleware(AcceptHeaderMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    return JSONResponse({
        "status": "healthy",
        "service": "DischargeIQ",
        "version": VERSION,
        "tool_count": len(TOOLS),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.get("/tools")
async def list_tools():
    return JSONResponse({
        "service": "DischargeIQ",
        "version": VERSION,
        "tools": TOOLS,
        "description": "AI-powered hospital discharge workflow tools for the PO Community MCP.",
    })


app.mount("/", mcp.streamable_http_app())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
