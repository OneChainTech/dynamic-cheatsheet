"""
MCP SSE 服务：提供动态 Cheatsheet 的查询与更新工具。

工具概览
--------
1. `prepare_solve_context`：为业务求解模型准备上下文，仅返回当前 cheatsheet。
2. `update_cheatsheet`：在策展阶段写入新的 cheatsheet 内容，并持久化保存。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

from mcp.server.fastmcp import FastMCP


# --------------------------- 常量与路径 --------------------------- #
BASE_DIR = Path(__file__).resolve().parent
CHEATSHEET_DIR = BASE_DIR / "cheatsheets"
CHEATSHEET_FILE = CHEATSHEET_DIR / "current_cheatsheet.txt"

DEFAULT_CHEATSHEET = "(empty)"


# --------------------------- 工具函数 --------------------------- #
def _ensure_storage_dir() -> None:
    """保证 Cheatsheet 存储目录存在。"""
    CHEATSHEET_DIR.mkdir(parents=True, exist_ok=True)


def _read_cheatsheet() -> str:
    """返回当前持久化的 cheatsheet 内容，不存在时返回默认值。"""
    if not CHEATSHEET_FILE.exists():
        return DEFAULT_CHEATSHEET
    return CHEATSHEET_FILE.read_text(encoding="utf-8")


def _write_cheatsheet(content: str) -> None:
    """写入最新的 cheatsheet 内容到持久化文件。"""
    _ensure_storage_dir()
    CHEATSHEET_FILE.write_text(content, encoding="utf-8")


# --------------------------- MCP 服务定义 --------------------------- #
server = FastMCP(
    name="dynamic-cheatsheet-service",
    instructions=(
        "提供 Dynamic Cheatsheet 所需的查询与更新工具："
        "1) prepare_solve_context -> 返回当前 cheatsheet；"
        "2) update_cheatsheet -> 更新并持久化新的 cheatsheet。"
    ),
)


@server.tool(
    name="prepare_solve_context",
    description=(
        "获取 Solve 模块执行前所需的上下文信息。"
        "仅返回当前 cheatsheet 文本。"
    ),
)
def prepare_solve_context() -> Dict[str, Any]:
    """
    读取持久化的 cheatsheet，供业务求解模型使用。
    """
    cheatsheet = _read_cheatsheet()
    return {
        "cheatsheet": cheatsheet,
    }


@server.tool(
    name="update_cheatsheet",
    description="在策展阶段写入新的 cheatsheet 内容，并持久化保存。",
)
def update_cheatsheet(new_cheatsheet: str) -> Dict[str, Any]:
    """
    更新 cheatsheet 文件。

    参数:
        new_cheatsheet: 最新的 cheatsheet 文本。
    """
    _write_cheatsheet(new_cheatsheet.strip() or DEFAULT_CHEATSHEET)
    return {
        "status": "ok",
        "cheatsheet_path": str(CHEATSHEET_FILE),
        "length": len(new_cheatsheet),
    }


# --------------------------- 入口函数 --------------------------- #
def main() -> None:
    """
    启动 FastMCP SSE 服务。
    默认监听 0.0.0.0:8000，可通过环境变量 MCP_HOST / MCP_PORT 覆盖。
    """
    load_dotenv("config.env")

    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8000"))

    server.settings.host = host  # type: ignore[attr-defined]
    server.settings.port = port  # type: ignore[attr-defined]

    server.run(transport="sse")


if __name__ == "__main__":
    main()
