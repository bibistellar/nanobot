"""Shared default tool registration used by main agent and sub-agents.

The main agent and sub-agents share a common toolset (filesystem, search,
exec, web). Keeping the registration in one place avoids the two call sites
drifting — for example a new tool added to the main agent but forgotten on
the sub-agent path. Caller-specific tools (spawn, message, ask_user, cron,
notebook, my) are registered separately by the caller.
"""

from __future__ import annotations

from pathlib import Path

from nanobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.search import GlobTool, GrepTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.config.schema import ExecToolConfig, WebToolsConfig


def build_default_tools(
    workspace: Path,
    *,
    exec_config: ExecToolConfig,
    web_config: WebToolsConfig,
    restrict_to_workspace: bool = False,
    extra_read_dirs: list[Path] | None = None,
    registry: ToolRegistry | None = None,
) -> ToolRegistry:
    """Register filesystem / search / exec / web tools onto a registry.

    Returns the registry (created if not supplied) so the caller can chain
    additional registrations.
    """
    tools = registry if registry is not None else ToolRegistry()

    allowed_dir = workspace if (restrict_to_workspace or exec_config.sandbox) else None

    tools.register(
        ReadFileTool(
            workspace=workspace,
            allowed_dir=allowed_dir,
            extra_allowed_dirs=extra_read_dirs,
        )
    )
    for cls in (WriteFileTool, EditFileTool, ListDirTool):
        tools.register(cls(workspace=workspace, allowed_dir=allowed_dir))
    for cls in (GlobTool, GrepTool):
        tools.register(cls(workspace=workspace, allowed_dir=allowed_dir))

    if exec_config.enable:
        tools.register(
            ExecTool(
                working_dir=str(workspace),
                timeout=exec_config.timeout,
                restrict_to_workspace=restrict_to_workspace,
                sandbox=exec_config.sandbox,
                path_append=exec_config.path_append,
                allowed_env_keys=exec_config.allowed_env_keys,
            )
        )

    if web_config.enable:
        tools.register(
            WebSearchTool(
                config=web_config.search,
                proxy=web_config.proxy,
                user_agent=web_config.user_agent,
            )
        )
        tools.register(
            WebFetchTool(
                config=web_config.fetch,
                proxy=web_config.proxy,
                user_agent=web_config.user_agent,
            )
        )

    return tools
