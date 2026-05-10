import sys
import os

# Ensure the current directory is in the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp_instance import mcp

if __name__ == "__main__":
    # FastMCP's .run() starts the standard I/O server by default
    mcp.run()
