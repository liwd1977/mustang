"""环节树拓扑推断：由 DAG / 编译结果驱动，不依赖 catalog flow_kind。"""

from __future__ import annotations

from typing import Any

from core.workflow.complex_flow_builder import count_task_types


def is_recruitment_task_tree_source(source: str) -> bool:
    return source == "guide_dag_compiler:recruitment"


def is_expense_task_tree_source(source: str) -> bool:
    return source in {"guide_dag_compiler:expense", "guide_dag_compiler:foreign_trade_expense"}


def is_foreign_trade_expense_task_tree_source(source: str) -> bool:
    return source == "guide_dag_compiler:foreign_trade_expense"


def task_tree_has_routes(task_root: dict | None) -> bool:
    return int(count_task_types(task_root).get("ROUTE") or 0) > 0


def task_tree_degraded(platform: dict | None, local: dict | None) -> bool:
    """平台环节树相对本地 payload 是否塌陷（空壳 / 环节数锐减 / 并行 ROUTE 丢失）。"""
    if not isinstance(platform, dict) or not platform.get("type"):
        return True
    if not isinstance(local, dict) or not local.get("type"):
        return False

    pt = count_task_types(platform)
    lt = count_task_types(local)
    local_ut = int(lt.get("USERTASK") or 0)
    plat_ut = int(pt.get("USERTASK") or 0)
    if local_ut >= 1 and plat_ut < max(1, local_ut // 2):
        return True

    local_routes = int(lt.get("ROUTE") or 0)
    plat_routes = int(pt.get("ROUTE") or 0)
    if local_routes > 0 and plat_routes < local_routes:
        return True

    name = str(platform.get("taskName") or "").replace(" ", "")
    if (name in {"办理start", "办理"} or "办理start" in name) and local_ut >= 2:
        return True
    return False


def min_route_branches_for_local(local_tree: dict | None) -> int:
    """供 save 响应空壳检测：按本地并行规模设定 ROUTE 分支下限；线性流程不要求 ROUTE。"""
    if not isinstance(local_tree, dict):
        return 0
    routes = int(count_task_types(local_tree).get("ROUTE") or 0)
    if routes <= 0:
        return 0
    child = local_tree.get("child")
    if isinstance(child, dict) and child.get("type") == "ROUTE":
        n = len(child.get("conditions") or [])
        return max(2, n // 2) if n else 2
    return max(2, routes) if routes >= 3 else 2


def local_task_tree_intact(local_tree: dict | None) -> bool:
    """本地 payload 是否含可用环节树（相对平台空壳判定基准）。"""
    if not isinstance(local_tree, dict) or not local_tree.get("type"):
        return False
    types = count_task_types(local_tree)
    return int(types.get("USERTASK") or 0) >= 1 or int(types.get("ROUTE") or 0) >= 1


def looks_like_recruitment_dag(nodes: list[Any]) -> bool:
    from core.workflow.guide_dag_compiler import GuideGraph, _extract_dept_branch_plans

    return len(_extract_dept_branch_plans(GuideGraph.build(nodes))) >= 4
