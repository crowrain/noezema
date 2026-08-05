"""Tests for ToolBroker — sandbox security, tool execution, rate limits."""

from packages.tool_broker.broker import ToolBroker
from packages.tool_broker.tools import CommandTool, PythonTool, WebFetchTool, SearchTool


# ---------------------------------------------------------------------------
# CommandTool security
# ---------------------------------------------------------------------------

def test_bash_simple_command():
    """Basic command execution."""
    tool = CommandTool()
    result = tool.execute("echo hello")
    assert result.success
    assert "hello" in result.output


def test_bash_blocked_sudo():
    """sudo is blocked."""
    tool = CommandTool()
    result = tool.execute("sudo ls /root")
    assert not result.success
    assert "blocked" in result.error.lower()


def test_bash_blocked_rm_rf_root():
    """rm -rf / is blocked."""
    tool = CommandTool()
    result = tool.execute("rm -rf /")
    assert not result.success


def test_bash_timeout():
    """Command respects timeout."""
    tool = CommandTool(timeout=1)
    result = tool.execute("sleep 10")
    assert not result.success
    assert "timeout" in result.error.lower()


def test_bash_list_dir():
    """ls works inside workspace."""
    tool = CommandTool()
    result = tool.execute("ls -la /tmp/noezema-workspace")
    assert result.success


# ---------------------------------------------------------------------------
# PythonTool
# ---------------------------------------------------------------------------

def test_python_simple():
    """Python executes and returns result."""
    tool = PythonTool()
    result = tool.execute("1 + 1")
    assert result.success


def test_python_timeout():
    """Python respects timeout."""
    tool = PythonTool(timeout=1)
    result = tool.execute("import time; time.sleep(10)")
    assert not result.success
    assert "timeout" in result.error.lower()


# ---------------------------------------------------------------------------
# WebFetchTool
# ---------------------------------------------------------------------------

def test_web_fetch_blocked_file():
    """file:// protocol is blocked."""
    tool = WebFetchTool()
    result = tool.fetch("file:///etc/passwd")
    assert not result.success


# ---------------------------------------------------------------------------
# ToolBroker registry
# ---------------------------------------------------------------------------

def test_broker_list_tools():
    """Broker lists available tools."""
    broker = ToolBroker()
    tools = broker.list_tools()
    names = [t["name"] for t in tools]
    assert "bash" in names
    assert "python" in names
    assert "web_fetch" in names
    assert "search" in names


def test_broker_unknown_tool():
    """Unknown tool returns error."""
    broker = ToolBroker()
    result = broker.call("nonexistent")
    assert not result.success
    assert "unknown" in result.error.lower()


def test_broker_audit_log():
    """Tool calls are logged."""
    broker = ToolBroker()
    broker.call("bash", command="echo audit")
    log = broker.get_audit_log()
    assert len(log) == 1
    assert log[0]["tool"] == "bash"


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def test_rate_limit_enforced():
    """Rate limit blocks excessive calls."""
    from packages.tool_broker.broker import ToolConfig
    config = ToolConfig(
        name="bash",
        description="test",
        rate_limit=2,
        rate_window=60,
    )
    broker = ToolBroker(tools={"bash": config})
    
    # Two calls should pass
    assert broker.call("bash", command="echo 1").success
    assert broker.call("bash", command="echo 2").success
    
    # Third should be rate limited
    result = broker.call("bash", command="echo 3")
    assert not result.success
    assert "rate limit" in result.error.lower()
