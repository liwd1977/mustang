"""遍历「调用模板」对话框中公共模板目录树。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import Frame, Page

from core.oa.client_factory import build_oa_client, require_oa_login
from core.oa.form_parser import _get_template_frame
from core.oa.playwright_env import oa_browser_session

ZTREe_CATALOG_JS = """
() => {
  const treeObj = $.fn.zTree.getZTreeObj('tree');
  if (!treeObj) return { ok: false, step: 'no-ztree' };

  function isCategory(n) {
    return !!(n.data && n.data.type === 'category');
  }

  function collectTemplates(node) {
    const items = [];
    for (const child of node.children || []) {
      if (isCategory(child)) continue;
      items.push({
        name: child.name || '',
        id: String(child.data?.id || child.id || ''),
        type: child.data?.type || '',
      });
    }
    return items;
  }

  const all = treeObj.transformToArray(treeObj.getNodes());
  const publicNode = all.find(n => n.name === '公共模板' || (n.name || '').includes('公共模板'));
  if (!publicNode) {
    return {
      ok: false,
      step: 'public-root',
      roots: all.filter(n => n.level === 0).map(n => n.name),
    };
  }

  treeObj.expandNode(publicNode, true, true, true);
  if (typeof clk === 'function') clk(null, 'tree', publicNode);

  const pub = treeObj.getNodeByTId(publicNode.tId) || publicNode;
  const catalog = {};
  const subdirs = [];

  for (const child of pub.children || []) {
    if (!isCategory(child)) continue;
    subdirs.push(child.name);
    treeObj.expandNode(child, true, true, true);
    if (typeof clk === 'function') clk(null, 'tree', child);
    const expanded = treeObj.getNodeByTId(child.tId) || child;
    catalog[child.name] = collectTemplates(expanded);
  }

  const finance = catalog['财务审批'] || [];
  return {
    ok: true,
    subdir_count: subdirs.length,
    subdirs,
    catalog,
    finance_first3: finance.slice(0, 3).map(t => t.name),
    finance_total: finance.length,
  };
}
"""


@dataclass
class TemplateCatalogResult:
    success: bool
    subdirs: list[str] = field(default_factory=list)
    catalog: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    finance_first3: list[str] = field(default_factory=list)
    finance_total: int = 0
    error_message: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def open_template_choose_dialog(page: Page, template_url: str) -> Frame:
    """打开协同新建页并弹出「调用模板」对话框，返回 templateChoose iframe。"""
    page.goto(template_url, wait_until="networkidle")
    page.wait_for_timeout(3000)

    clicked = False
    for sel in ["#refresh2_a", "#refresh2", "[title='调用模板']", "text=调用模板"]:
        btn = page.locator(sel).first
        if btn.count():
            try:
                btn.click(timeout=8000)
                clicked = True
                break
            except Exception:
                continue
    if not clicked:
        raise RuntimeError("未找到「调用模板」按钮")

    page.wait_for_timeout(2500)
    tf = _get_template_frame(page)
    if not tf:
        raise RuntimeError("未找到调用模板 iframe（templateChoose）")
    return tf


def list_public_template_catalog(page: Page, template_url: str) -> TemplateCatalogResult:
    tf = open_template_choose_dialog(page, template_url)
    page.wait_for_timeout(1500)
    data = tf.evaluate(ZTREe_CATALOG_JS)
    if not data.get("ok"):
        return TemplateCatalogResult(
            success=False,
            error_message=str(data),
            raw=data,
        )

    catalog = data.get("catalog") or {}
    return TemplateCatalogResult(
        success=True,
        subdirs=list(data.get("subdirs") or []),
        catalog=catalog,
        finance_first3=list(data.get("finance_first3") or []),
        finance_total=int(data.get("finance_total") or 0),
        raw=data,
    )


def run_list_public_templates(
    base_url: str,
    username: str,
    password: str,
    *,
    template_url: str,
) -> TemplateCatalogResult:
    client = build_oa_client()
    with oa_browser_session() as (_, __, ___, page):
        require_oa_login(page, client)
        result = list_public_template_catalog(page, template_url)
        return result
