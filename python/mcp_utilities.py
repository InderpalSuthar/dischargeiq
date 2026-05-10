import logging

logger = logging.getLogger(__name__)


def create_text_response(text: str, is_error: bool = False) -> str:
    """Create a text response for MCP tool output.

    In healthcare software, unhandled exceptions are unacceptable.
    Errors are returned as structured messages, not raised as exceptions,
    so the MCP framework can relay them gracefully to the platform.
    """
    if is_error:
        logger.warning("Tool error: %s", text)
        return f"⚠️ **Error:** {text}"
    return text
