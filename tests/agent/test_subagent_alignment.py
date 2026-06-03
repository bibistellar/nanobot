"""Subagent ↔ main agent alignment tests.

User-stated goal (paraphrased): "subagents are the main agent's
*subconscious* — they exist for parallel execution; their permissions
and visible knowledge should match the main agent except for the few
hard isolation boundaries (no cron, no nested spawn, no main-agent
conversation history, read-only ``my``)."

These tests pin that contract so the alignment doesn't silently drift
the next time someone touches ContextBuilder or the tool registry. The
specific cluster-ops incident that triggered this work is captured in
``test_always_skill_full_text_visible_to_subagent``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from nanobot.agent.context import ContextBuilder
from nanobot.agent.subagent import SubagentManager
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider


def _make_subagent_with_context(
    workspace: Path,
    *,
    runtime_state: object | None = None,
) -> SubagentManager:
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test-model"
    ctx = ContextBuilder(workspace=workspace)
    return SubagentManager(
        provider=provider,
        workspace=workspace,
        bus=MessageBus(),
        model="test-model",
        max_tool_result_chars=16_000,
        context_builder=ctx,
        runtime_state=runtime_state,
    )


def _seed_always_skill(workspace: Path, *, name: str, body: str) -> None:
    """Drop a SKILL.md with ``always: true`` frontmatter into the workspace."""
    skill_dir = workspace / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: integration-test skill\nalways: true\n---\n\n{body}",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# System prompt content alignment
# ---------------------------------------------------------------------------


def test_always_skill_full_text_visible_to_subagent(tmp_path: Path) -> None:
    """The exact incident that motivated this work: the cluster-ops skill is
    marked ``always: true`` so the main agent always sees the full SKILL.md
    text (including the ``bibistellar/_k3s`` repo path). The subagent used
    to see only a skill index, invented ``nanobot-chat/nanobot-ops``, and
    crashed with 404. After alignment the subagent must see the same full
    text."""
    secret_marker = "bibistellar/_k3s_marker"
    _seed_always_skill(
        tmp_path,
        name="cluster-ops",
        body=f"Use repo {secret_marker} for workflow dispatch.",
    )
    sm = _make_subagent_with_context(tmp_path)
    prompt = sm._build_subagent_prompt(workspace=tmp_path)
    assert secret_marker in prompt, (
        "always-skill full text must reach the subagent — main agent gets it "
        "via ContextBuilder.build_static_prompt_parts, subagent must too"
    )


def test_bootstrap_files_visible_to_subagent(tmp_path: Path) -> None:
    """SOUL.md / USER.md / AGENTS.md define the bot's persona, the user, and
    the project. Subagents must share that identity layer — otherwise their
    output drifts toward generic LLM defaults instead of the configured bot."""
    (tmp_path / "SOUL.md").write_text("BOT_PERSONA_TOKEN", encoding="utf-8")
    (tmp_path / "USER.md").write_text("USER_PROFILE_TOKEN", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("PROJECT_TOKEN", encoding="utf-8")

    sm = _make_subagent_with_context(tmp_path)
    prompt = sm._build_subagent_prompt(workspace=tmp_path)
    assert "BOT_PERSONA_TOKEN" in prompt
    assert "USER_PROFILE_TOKEN" in prompt
    assert "PROJECT_TOKEN" in prompt


def test_subagent_prompt_contains_preamble_caveats(tmp_path: Path) -> None:
    """The preamble must spell out the four isolation contracts: scope, no
    cron, no nested spawn, no main-agent conversation history, read-only my.
    These aren't just docs — they're contractual guidance to the LLM so it
    doesn't try to escape the sandbox in subtle ways."""
    sm = _make_subagent_with_context(tmp_path)
    prompt = sm._build_subagent_prompt(workspace=tmp_path)
    lowered = prompt.lower()
    assert "subagent" in lowered
    # Scope: result goes back to main agent, not the user
    assert "reported back" in lowered or "main agent" in lowered
    # Recursion guards
    assert "cron" in lowered and "spawn" in lowered
    # Read-only my caveat
    assert "read-only" in lowered


def test_subagent_fallback_prompt_when_no_context_builder(tmp_path: Path) -> None:
    """Bare unit tests that don't bring a ContextBuilder should still work —
    SubagentManager keeps a legacy lite-template fallback so the matrix of
    existing tests in tests/agent/test_subagent*.py keeps passing."""
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test-model"
    sm = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        model="test-model",
        max_tool_result_chars=16_000,
    )
    # Should not raise; should produce something subagent-shaped.
    prompt = sm._build_subagent_prompt(workspace=tmp_path)
    assert prompt
    assert "subagent" in prompt.lower()


# ---------------------------------------------------------------------------
# Tool roster alignment
# ---------------------------------------------------------------------------


def test_subagent_roster_includes_aligned_tools(tmp_path: Path) -> None:
    """Tools opened up to subagent scope must show up in the subagent registry.
    image_generation / long_task / complete_goal / sessions used to be
    main-only by accident (no _scopes declared → defaulted to {'core'})."""
    sm = _make_subagent_with_context(tmp_path)
    tools = sm._build_tools(workspace=tmp_path)
    for name in ("image_generation", "long_task", "complete_goal", "sessions"):
        assert tools.has(name), f"{name} must be in the subagent registry"


def test_subagent_my_tool_is_read_only(tmp_path: Path) -> None:
    """The subagent gets a ``my`` tool, but it MUST be the read-only variant —
    otherwise a parallel subagent could rewrite the main agent's model /
    iteration limits and the main agent would never know."""
    fake_runtime = MagicMock()  # stands in for AgentLoop
    sm = _make_subagent_with_context(tmp_path, runtime_state=fake_runtime)
    tools = sm._build_tools(workspace=tmp_path)
    my = tools.get("my")
    assert my is not None
    assert my._modify_allowed is False


def test_subagent_my_tool_absent_without_runtime_state(tmp_path: Path) -> None:
    """Without a runtime_state hookup (e.g. bare unit tests), ``my`` simply
    isn't registered. This is graceful degradation — silently giving the
    subagent a None-runtime ``my`` would crash on first inspect."""
    sm = _make_subagent_with_context(tmp_path)  # no runtime_state
    tools = sm._build_tools(workspace=tmp_path)
    assert not tools.has("my")


def test_subagent_recursion_guards_still_in_place(tmp_path: Path) -> None:
    """Alignment does NOT relax the two hard recursion boundaries: a subagent
    must never be able to schedule new cron jobs or spawn nested subagents."""
    fake_runtime = MagicMock()
    sm = _make_subagent_with_context(tmp_path, runtime_state=fake_runtime)
    tools = sm._build_tools(workspace=tmp_path)
    assert not tools.has("cron"), "subagent must not be able to schedule cron"
    assert not tools.has("spawn"), "subagent must not spawn nested subagents"


# ---------------------------------------------------------------------------
# LTM injection alignment
# ---------------------------------------------------------------------------


def test_fetch_ltm_block_returns_empty_without_dashscope(tmp_path: Path) -> None:
    """LTM is a fork feature — when Dashscope isn't configured the helper
    silently returns "", so callers (main + subagent) don't need to guard."""
    ctx = ContextBuilder(workspace=tmp_path)
    assert ctx.fetch_ltm_block("any query") == ""


def test_fetch_ltm_block_wraps_dashscope_hits_in_tags(tmp_path: Path) -> None:
    """When LTM returns text, it's wrapped in ``[Long-term Memory]`` tags —
    that's the exact shape the main agent's build_messages expects, and
    the subagent path now reuses the same helper instead of building its
    own block. Drift here would split main and subagent LTM rendering."""
    dashscope = MagicMock()
    dashscope.search_memory.return_value = "Project context: SSH on port 443."
    ctx = ContextBuilder(workspace=tmp_path)
    ctx.dashscope = dashscope

    block = ctx.fetch_ltm_block("how do we deploy?")
    assert "[Long-term Memory]" in block
    assert "SSH on port 443" in block
    assert "[/Long-term Memory]" in block
    dashscope.search_memory.assert_called_once_with(query="how do we deploy?")


def test_fetch_ltm_block_swallows_dashscope_errors(tmp_path: Path) -> None:
    """Main agent's inline path uses ``except Exception: pass`` to keep a
    flaky memory store from killing every turn. The helper must keep that
    same swallow-and-return-empty behaviour."""
    dashscope = MagicMock()
    dashscope.search_memory.side_effect = RuntimeError("dashscope down")
    ctx = ContextBuilder(workspace=tmp_path)
    ctx.dashscope = dashscope
    assert ctx.fetch_ltm_block("query") == ""


# ---------------------------------------------------------------------------
# ContextBuilder helper invariants
# ---------------------------------------------------------------------------


def test_static_prompt_parts_excludes_session_bits(tmp_path: Path) -> None:
    """build_static_prompt_parts is meant to be reusable across the main
    agent and subagents. Per-session sections (recent history, archived
    summary) must NOT leak in — those belong to a specific conversation."""
    ctx = ContextBuilder(workspace=tmp_path)
    parts = ctx.build_static_prompt_parts(workspace=tmp_path)
    joined = "\n\n".join(parts)
    assert "# Recent History" not in joined
    assert "[Archived Context Summary]" not in joined


def test_main_agent_prompt_still_includes_session_bits(tmp_path: Path) -> None:
    """Regression guard: after carving the helper out, the main agent's
    full build_system_prompt must still add session-scoped sections when
    they're provided. Otherwise we silently regress recent-history
    rendering for active chats."""
    ctx = ContextBuilder(workspace=tmp_path)
    full = ctx.build_system_prompt(session_summary="ARCHIVED_SESSION_TOKEN")
    assert "ARCHIVED_SESSION_TOKEN" in full
