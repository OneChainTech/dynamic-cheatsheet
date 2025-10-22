"""
MCP SSE 服务：提供动态 Cheatsheet 的查询与更新工具。

工具概览
--------
1. `prepare_solve_context`：返回求解阶段所需的 cheatsheet 与 generator prompt。
2. `update_cheatsheet`：结合策展 prompt 与业务模型输出生成新的 cheatsheet 并持久化。
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

from mcp.server.fastmcp import FastMCP
from dynamic_cheatsheet.language_model import LanguageModel
from dynamic_cheatsheet.utils.extractor import extract_cheatsheet
from functools import lru_cache


# --------------------------- 常量与路径 --------------------------- #
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "cheatsheets.db"
PROMPTS_DIR = BASE_DIR / "prompts"
GENERATOR_PROMPT_FILE = PROMPTS_DIR / "generator_prompt.txt"
CURATOR_PROMPT_FILE = PROMPTS_DIR / "curator_prompt_for_dc_cumulative.txt"

DEFAULT_CHEATSHEET = "(empty)"

CURATOR_TEMPERATURE_ENV = "CURATOR_TEMPERATURE"
CURATOR_MAX_TOKENS_ENV = "CURATOR_MAX_TOKENS"

def _parse_float(value: str, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _parse_int(value: str, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


DEFAULT_CURATOR_TEMPERATURE = _parse_float(os.getenv(CURATOR_TEMPERATURE_ENV), 0.0)
DEFAULT_CURATOR_MAX_TOKENS = _parse_int(os.getenv(CURATOR_MAX_TOKENS_ENV), 4096)


# --------------------------- 工具函数 --------------------------- #
def _get_db_connection() -> sqlite3.Connection:
    """获取 SQLite 连接。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    """初始化 SQLite 表结构。"""
    with _get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cheatsheets (
                session_id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


def _get_cheatsheet(session_id: str) -> str:
    """按 session 读取 cheatsheet，若不存在则返回默认值。"""
    if not session_id:
        raise ValueError("session_id 不能为空。")
    with _get_db_connection() as conn:
        row = conn.execute(
            "SELECT content FROM cheatsheets WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    if row is None:
        return DEFAULT_CHEATSHEET
    return row["content"]


def _set_cheatsheet(session_id: str, content: str, previous_content: Optional[str] = None) -> None:
    """按 session 写入 cheatsheet。"""
    if not session_id:
        raise ValueError("session_id 不能为空。")
    normalized = content.strip() or DEFAULT_CHEATSHEET
    if previous_content is not None and normalized == previous_content:
        return
    with _get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO cheatsheets(session_id, content, updated_at)
            VALUES(?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(session_id) DO UPDATE
            SET content = excluded.content,
                updated_at = CURRENT_TIMESTAMP
            """,
            (session_id, normalized),
        )
        conn.commit()


@lru_cache(maxsize=None)
def _read_prompt(path: Path) -> str:
    """读取指定 prompt 文件内容。"""
    if not path.exists():
        raise FileNotFoundError(f"Prompt 文件不存在: {path}")
    return path.read_text(encoding="utf-8")


def _get_curator_temperature() -> float:
    """返回策展温度配置。"""
    return DEFAULT_CURATOR_TEMPERATURE


def _get_curator_max_tokens() -> int:
    """返回策展最大 token 配置。"""
    return DEFAULT_CURATOR_MAX_TOKENS


# --------------------------- MCP 服务定义 --------------------------- #
server = FastMCP(
    name="dynamic-cheatsheet-service",
    instructions=(
        "提供 Dynamic Cheatsheet 所需的查询与更新工具："
        "1) prepare_solve_context -> 基于 session 返回当前 cheatsheet 与 generator prompt；"
        "2) update_cheatsheet -> 基于 session 与模型输出生成并持久化新的 cheatsheet。"
    ),
)

_init_db()


@server.tool(
    name="prepare_solve_context",
    description=(
        "获取 Solve 模块执行前所需的上下文信息。"
        "需提供 session_id，返回对应 cheatsheet 与 generator prompt。"
    ),
)
def prepare_solve_context(session_id: str) -> Dict[str, Any]:
    """
    读取求解阶段所需的核心上下文。
    """
    cheatsheet = _get_cheatsheet(session_id=session_id)
    generator_prompt = _read_prompt(GENERATOR_PROMPT_FILE)
    return {
        "session_id": session_id,
        "cheatsheet": cheatsheet,
        "generator_prompt": generator_prompt,
    }


@server.tool(
    name="update_cheatsheet",
    description=(
        "根据业务模型输出和策展模板生成新的 cheatsheet，并自动持久化。"
        "需提供 session_id。"
    ),
)
def update_cheatsheet(
    session_id: str,
    question: str,
    model_output: str,
) -> Dict[str, Any]:
    """
    利用策展 prompt 更新 cheatsheet 文件。
    """
    current_cheatsheet = _get_cheatsheet(session_id=session_id)
    curator_template = _read_prompt(CURATOR_PROMPT_FILE)
    curator_prompt = (
        curator_template.replace("[[PREVIOUS_CHEATSHEET]]", current_cheatsheet)
        .replace("[[QUESTION]]", question)
        .replace("[[MODEL_ANSWER]]", model_output)
    )

    model_id = os.getenv("MODEL_ID", "deepseek-ai/DeepSeek-V3.2-Exp")
    language_model = LanguageModel(model_name=model_id)

    curator_history = [{"role": "user", "content": curator_prompt}]
    curator_output = language_model.generate(
        history=curator_history,
        temperature=_get_curator_temperature(),
        max_tokens=_get_curator_max_tokens(),
        allow_code_execution=False,
    )

    new_cheatsheet = (
        extract_cheatsheet(curator_output, current_cheatsheet) or current_cheatsheet
    )
    if not new_cheatsheet.strip():
        new_cheatsheet = DEFAULT_CHEATSHEET

    _set_cheatsheet(
        session_id=session_id,
        content=new_cheatsheet,
        previous_content=current_cheatsheet,
    )

    return {
        "status": "ok",
        "session_id": session_id,
        # "cheatsheet_before": current_cheatsheet,
        # "cheatsheet_after": new_cheatsheet,
        # "curator_output": curator_output,
        # "curator_prompt": curator_prompt,
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
