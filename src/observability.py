"""
EduTutor — наблюдаемость: единая точка записи шагов графа и действий агента в JSONL.

Каждый запрос трассируется по этапам с уникальным `request_id` (см. JsonlStepLogger,
logging_setup): вход/выход узлов графа (`node:*`) и вызовы инструментов модели
(`agent.action`). Логгер привязан к `deps.step_logger`; при его отсутствии
(тесты/моки/детерминированные прогоны) все функции — безопасные no-op.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("edututor.observability")


def _step_logger(deps: Any) -> Any:
    if deps is None:
        return None
    return getattr(deps, "step_logger", None)


def log_graph_node(
    deps: Any,
    node: str,
    *,
    status: str = "ok",
    duration: Optional[float] = None,
    note: str = "",
) -> None:
    """Запись прохода узла графа (этап трассировки запроса)."""
    sl = _step_logger(deps)
    if sl is None:
        return
    try:
        sl.log_step(
            agent_action=f"node:{node}",
            status=status,
            duration=duration,
            extra={"note": note} if note else None,
        )
    except Exception:
        logger.warning("JSONL: не удалось записать шаг node:%s", node)


def log_agent_tool(
    deps: Any,
    tool: str,
    *,
    ok: bool,
    elapsed_ms: int,
    args: Optional[Dict[str, Any]] = None,
    reason: str = "",
) -> None:
    """Запись вызова инструмента агентом (function calling)."""
    sl = _step_logger(deps)
    if sl is None:
        return
    try:
        sl.log_step(
            agent_action="agent.action",
            tool=tool,
            status="ok" if ok else "error",
            duration=elapsed_ms / 1000.0,
            extra={"args": args, "reason": reason},
        )
    except Exception:
        logger.warning("JSONL: не удалось записать agent.action %s", tool)
