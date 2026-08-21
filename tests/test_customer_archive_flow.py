"""客商档案表：侧向自选抄送 + 主链末尾抄送。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.workflow.complex_flow_builder import count_task_types, iter_all_tasks
from core.workflow.flow_semantics import collect_self_select_cc_specs
from core.workflow.guide_dag_compiler import compile_task_tree_from_guide
from core.workflow.shenbi_builder import _linearize_task_chain
from schemas.workflow import WorkflowNodeItem


def _load_customer_archive_nodes() -> list[WorkflowNodeItem] | None:
    parse_path = Path("output/workflows/guide_2026080301_parse_result.json")
    if not parse_path.is_file():
        return None
    data = json.loads(parse_path.read_text(encoding="utf-8"))
    for sr in data.get("sector_results") or []:
        for flow in sr.get("flows") or []:
            hint = flow.get("image", {}).get("title_hint") or flow.get("title") or ""
            if "客商档案" in hint and flow.get("nodes"):
                return [WorkflowNodeItem.model_validate(n) for n in flow["nodes"]]
    return None


@pytest.fixture(scope="module")
def archive_nodes() -> list[WorkflowNodeItem]:
    nodes = _load_customer_archive_nodes()
    if not nodes:
        pytest.skip("缺少客商档案 parse 缓存")
    return nodes


def test_self_select_cc_spec_for_business_accountant(archive_nodes):
    specs = collect_self_select_cc_specs(archive_nodes)
    assert any(s.field_label == "业务部门会计" for s in specs)
    assert any(s.task_name == "抄送业务部门会计" for s in specs)


def test_customer_archive_serial_dag_with_two_cc_groups(archive_nodes):
    settings = type("S", (), {"shenbi_tenant_id": "t1"})()
    root, source = compile_task_tree_from_guide(
        "野马集团客商档案表",
        archive_nodes,
        proc_id="p-archive",
        settings=settings,
    )
    assert source == "guide_dag_compiler:serial_dag"
    types = count_task_types(root)
    assert types.get("CCTASK", 0) >= 2
    assert types.get("USERTASK", 0) >= 1

    names = [t.get("taskName") or "" for t in iter_all_tasks(root)]
    assert any("部门主管" in n for n in names)
    assert any("业务部门会计" in n for n in names)
    assert any("财务总监" in n or "分管领导" in n for n in names)


def test_customer_archive_main_chain_order(archive_nodes):
    """审批须在抄送之前，否则神笔设计器会丢弃抄送环节。"""
    settings = type("S", (), {"shenbi_tenant_id": "t1"})()
    root, _ = compile_task_tree_from_guide(
        "野马集团客商档案表",
        archive_nodes,
        proc_id="p-archive",
        settings=settings,
    )
    chain = _linearize_task_chain(root)
    types = [t.get("type") for t in chain]
    assert types[0] == "STARTTASK"
    first_usertask = next(i for i, t in enumerate(types) if t == "USERTASK")
    first_cctask = next(i for i, t in enumerate(types) if t == "CCTASK")
    assert first_usertask < first_cctask
