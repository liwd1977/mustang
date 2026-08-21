"""解析 CAP4 表单：提取指定区间内的输入项。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import Frame, Page

from core.oa.client_factory import build_oa_client, require_oa_login
from core.oa.navigation import open_collaboration_page
from core.oa.playwright_env import oa_browser_session
from core.oa.playwright_client import Page

CAP4_EXTRACT_JS = """
([startMarker, endMarker]) => {
  window.scrollTo(0, 0);
  window.scrollTo(0, document.body.scrollHeight);

  const body = (document.body?.innerText || '').replace(/\\r/g, '');
  const lines = body.split('\\n').map(s => s.trim()).filter(Boolean);

  const sectionEls = [];
  document.querySelectorAll('.cap4-title, .section-title, .group-title, .formson-title, th, legend, .field-title').forEach(el => {
    const text = (el.innerText || '').trim();
    if (!text || text.length > 40) return;
    const r = el.getBoundingClientRect();
    if (r.height <= 0) return;
    sectionEls.push({ text, y: Math.round(r.top + window.scrollY) });
  });

  const fields = [];
  document.querySelectorAll('.cap4-field, .field-content, .x-field, tr').forEach(wrapper => {
    let label = '';
    const titleEl = wrapper.querySelector('.cap4-title, .field-title, .x-field-label, th, label');
    if (titleEl) {
      label = titleEl.innerText.trim().replace(/[:：\\*\\s]+$/g, '');
    }
    if (!label) {
      const cells = wrapper.querySelectorAll('th, td');
      if (cells.length >= 2) label = cells[0].innerText.trim().replace(/[:：\\*\\s]+$/g, '');
    }
    if (!label || label.length > 30) return;
    const input = wrapper.querySelector('input:not([type=hidden]), textarea, select');
    if (!input) return;
    const r = wrapper.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return;
    fields.push({
      label,
      tag: input.tagName.toLowerCase(),
      type: input.type || (input.tagName.toLowerCase() === 'textarea' ? 'textarea' : 'select'),
      id: input.id || '',
      y: Math.round(r.top + window.scrollY),
      x: Math.round(r.left),
    });
  });

  fields.sort((a, b) => a.y - b.y || a.x - b.x);

  const endMarkerUsed = body.includes(endMarker) ? endMarker : (body.includes('费用事由') ? '费用事由' : (body.includes('事由') ? '事由' : endMarker));
  const startSec = sectionEls.find(s => s.text.includes(startMarker));
  const endSec = sectionEls.find(s => s.text.includes(endMarkerUsed));

  let between = fields;
  if (startSec && endSec && endSec.y > startSec.y) {
    between = fields.filter(f => f.y > startSec.y && f.y < endSec.y);
  }

  const li = lines.findIndex(l => l === startMarker || l.includes(startMarker));
  const ri = lines.findIndex(l => l === endMarkerUsed || l.includes(endMarkerUsed));
  let textBetween = [];
  if (li >= 0 && ri > li) textBetween = lines.slice(li + 1, ri);

  const sectionHeaders = new Set(['基本信息', '支付信息', '费用明细', '审批环节', '元', '费用事由', '事由']);
  const labelLineRe = /^(发起人|发起人时间|发起人部门|金额|大写|是否[\u4e00-\u9fa5]+|[\u4e00-\u9fa5]{2,8}(?:时间|部门|金额|编号|类型|方式|账号|户名|电话|地址|用途|科目|备注|说明)?)$/;
  const textInputLabels = [];
  for (const line of textBetween) {
    if (!line || line.length > 12) continue;
    if (sectionHeaders.has(line)) continue;
    if (/负责|主管|经理|总裁|出纳|审批环节|野马集团|办公室|智科|测试/.test(line)) continue;
    if (/\\d/.test(line)) continue;
    if (!labelLineRe.test(line)) continue;
    textInputLabels.push(line);
  }

  const domLabels = between.map(f => f.label).filter(Boolean);
  const dedup = [];
  const seen = new Set();
  for (const src of textInputLabels.length ? textInputLabels : domLabels) {
    const k = src.trim();
    if (!k || seen.has(k)) continue;
    seen.add(k);
    dedup.push(k);
  }

  return {
    url: location.href,
    title: document.title,
    bodyHasBasic: body.includes(startMarker),
    bodyHasExpense: body.includes(endMarker),
    endMarkerUsed,
    sectionEls,
    fields,
    between,
    textBetween,
    textInputLabels,
    domLabels,
    inputLabels: dedup,
    bodySnippet: body.replace(/\\s+/g, ' ').slice(0, 1500),
  };
}
"""


@dataclass
class FormFieldItem:
    label: str
    input_type: str = ""
    tag: str = ""
    field_id: str = ""


@dataclass
class FormSectionParseResult:
    success: bool
    template_name: str = ""
    template_id: str = ""
    template_url: str = ""
    start_marker: str = "基本信息"
    end_marker: str = "费用事由"
    fields: list[FormFieldItem] = field(default_factory=list)
    error_message: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def _scroll_frame(frame: Frame) -> None:
    for _ in range(8):
        frame.evaluate("window.scrollBy(0, 500)")
        frame.page.wait_for_timeout(300)


def _extract_from_frame(frame: Frame, start: str, end: str) -> dict[str, Any]:
    _scroll_frame(frame)
    return frame.evaluate(CAP4_EXTRACT_JS, [start, end])


def parse_cap4_between(
    page: Page,
    *,
    start_marker: str = "基本信息",
    end_marker: str = "费用事由",
) -> FormSectionParseResult:
    best: dict[str, Any] | None = None
    for frame in page.frames:
        if "cap4/template/display" not in frame.url:
            continue
        data = _extract_from_frame(frame, start_marker, end_marker)
        if not data.get("bodyHasBasic"):
            continue
        if best is None or len(data.get("inputLabels") or []) > len(best.get("inputLabels") or []):
            best = data

    if not best:
        return FormSectionParseResult(success=False, error_message="未找到 CAP4 表单 iframe 或缺少「基本信息」区块")

    end_used = best.get("endMarkerUsed") or end_marker
    labels = list(best.get("inputLabels") or [])
    dom_between = best.get("between") or []
    dom_by_label = {
        (item.get("label") or "").strip(): item
        for item in dom_between
        if (item.get("label") or "").strip()
    }

    fields: list[FormFieldItem] = []
    for label in labels:
        dom = dom_by_label.get(label)
        fields.append(
            FormFieldItem(
                label=label,
                input_type=(dom or {}).get("type", ""),
                tag=(dom or {}).get("tag", ""),
                field_id=(dom or {}).get("id", ""),
            )
        )

    if not fields and dom_between:
        seen: set[str] = set()
        for item in dom_between:
            label = (item.get("label") or "").strip()
            if not label or label in seen:
                continue
            seen.add(label)
            fields.append(
                FormFieldItem(
                    label=label,
                    input_type=item.get("type", ""),
                    tag=item.get("tag", ""),
                    field_id=item.get("id", ""),
                )
            )

    success = bool(fields) and best.get("bodyHasBasic") and (
        best.get("bodyHasExpense") or end_used != end_marker
    )
    return FormSectionParseResult(
        success=success,
        start_marker=start_marker,
        end_marker=end_used,
        fields=fields,
        raw=best,
    )


def parse_cap4_all(page: Page) -> FormSectionParseResult:
    """提取 CAP4 表单全部可见输入项（调试/原始表单）。"""
    best: dict[str, Any] | None = None
    for frame in page.frames:
        if "cap4/template/display" not in frame.url:
            continue
        data = _extract_from_frame(frame, "基本信息", "费用事由")
        if not data.get("fields"):
            continue
        if best is None or len(data.get("fields") or []) > len(best.get("fields") or []):
            best = data

    if not best:
        return FormSectionParseResult(success=False, error_message="未找到 CAP4 表单 iframe")

    seen: set[str] = set()
    fields: list[FormFieldItem] = []
    for item in best.get("fields") or []:
        label = (item.get("label") or "").strip()
        if not label or label in seen:
            continue
        seen.add(label)
        fields.append(
            FormFieldItem(
                label=label,
                input_type=item.get("type", ""),
                tag=item.get("tag", ""),
                field_id=item.get("id", ""),
            )
        )

    return FormSectionParseResult(
        success=bool(fields),
        start_marker="全部字段",
        end_marker="",
        fields=fields,
        raw=best,
    )


ZTREe_SELECT_JS = """
(templateName) => {
  const treeObj = $.fn.zTree.getZTreeObj('tree');
  if (!treeObj) return { ok: false, step: 'no-ztree' };

  function isCategory(n) {
    return !!(n.data && n.data.type === 'category');
  }

  let all = treeObj.transformToArray(treeObj.getNodes());
  const publicNode = all.find(n => (n.name || '').includes('公共模板'));
  if (publicNode) {
    treeObj.expandNode(publicNode, true, true, true);
    if (typeof clk === 'function') clk(null, 'tree', publicNode);
    all = treeObj.transformToArray(treeObj.getNodes());
  }

  for (const node of all) {
    if (!isCategory(node)) continue;
    treeObj.expandNode(node, true, true, true);
  }
  all = treeObj.transformToArray(treeObj.getNodes());

  const exact = all.find(n => !isCategory(n) && (n.name || '') === templateName);
  const norm = (s) => (s || '').replace(/[（(]/g,'(').replace(/[）)]/g,')').replace(/\\s+/g,'');
  const target = norm(templateName);
  const partial = all.find(n => {
    if (isCategory(n)) return false;
    const name = n.name || '';
    return name.includes(templateName) || norm(name).includes(target) || target.includes(norm(name));
  });
  const tpl = exact || partial;
  if (!tpl) {
    return {
      ok: false,
      step: 'template',
      hints: all.filter(n => !isCategory(n)).map(n => n.name).slice(0, 40)
    };
  }
  treeObj.selectNode(tpl);
  if (typeof clk === 'function') clk(null, 'tree', tpl);
  return { ok: true, picked: tpl.name, id: tpl.data?.id || tpl.id, category: tpl.getParentNode()?.name || '' };
}
"""

CLICK_TREE_NODE_JS = """
(name) => {
  const els = Array.from(document.querySelectorAll('.node_name, a, span, li'));
  let node = els.find(n => (n.innerText || '').trim() === name);
  if (!node) node = els.find(n => (n.innerText || '').trim().startsWith(name));
  if (!node) {
    return { ok: false, available: Array.from(document.querySelectorAll('.node_name')).map(n => (n.innerText||'').trim()) };
  }
  (node.closest('a') || node).click();
  return { ok: true, clicked: (node.innerText||'').trim().slice(0,40) };
}
"""

CLICK_TEMPLATE_ROW_JS = """
(templateName) => {
  const candidates = Array.from(document.querySelectorAll('tr, td, span, a'))
    .filter(el => (el.innerText || '').includes(templateName));
  const target = candidates.find(el => {
    const t = (el.innerText || '').trim();
    return t === templateName || t.startsWith(templateName);
  }) || candidates[0];
  if (!target) {
    return { ok: false, hints: Array.from(document.querySelectorAll('tr,td,span')).map(e=>(e.innerText||'').trim()).filter(t=>t.includes('报销')||t.includes('集团')).slice(0,20) };
  }
  target.click();
  return { ok: true, picked: (target.innerText||'').trim().slice(0,80) };
}
"""


def _get_template_frame(page: Page) -> Frame | None:
    for frame in page.frames:
        if "templateChoose" in frame.url:
            return frame
    return None


def _click_in_any_frame(page: Page, text: str, *, exact: bool = False) -> bool:
    """在所有 frame 中尝试点击含指定文本的元素。"""
    for frame in page.frames:
        try:
            loc = frame.get_by_text(text, exact=exact).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=5000)
                return True
        except Exception:
            continue
    try:
        loc = page.get_by_text(text, exact=exact).first
        if loc.count() and loc.is_visible():
            loc.click(timeout=5000)
            return True
    except Exception:
        pass
    return False


def open_template_via_call_dialog(
    page: Page,
    template_url: str,
    *,
    tree_path: list[str] | None = None,
    template_name: str = "集团公司部门费用报销单",
) -> tuple[Page, dict]:
    """
    按用户实际操作路径：打开协同新建 URL → 调用模板 → 在树中选择模板 → 确定。
    """
    open_collaboration_page(page, template_url, timeout=90_000)
    page.wait_for_timeout(1500)

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

    pick = tf.evaluate(ZTREe_SELECT_JS, template_name)
    if not pick.get("ok"):
        hints = pick.get("hints") or []
        hint_text = "、".join(hints[:8]) if hints else "无"
        raise RuntimeError(f"模板选择失败: {pick}。相近模板: {hint_text}")

    page.wait_for_timeout(4000)

    page.wait_for_timeout(1500)
    ok_btn = page.locator("text=确定").first
    if ok_btn.count() and ok_btn.is_visible():
        ok_btn.click()
    else:
        _click_in_any_frame(page, "确定")

    page.wait_for_timeout(8000)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=45_000)
    except Exception:
        pass

    for _ in range(25):
        for frame in page.frames:
            if "cap4/template/display" not in frame.url:
                continue
            try:
                if frame.evaluate("() => (document.body?.innerText||'').includes('基本信息')"):
                    return page, pick
            except Exception:
                pass
        page.wait_for_timeout(1000)

    return page, pick


def run_parse(
    base_url: str,
    username: str,
    password: str,
    *,
    template_url: str,
    template_name: str = "集团公司部门费用报销单",
    tree_path: list[str] | None = None,
    start_marker: str = "基本信息",
    end_marker: str = "费用事由",
) -> FormSectionParseResult:
    client = build_oa_client()
    with oa_browser_session() as (_, __, ___, page):
        require_oa_login(page, client)
        form_page, pick = open_template_via_call_dialog(
            page,
            template_url,
            tree_path=tree_path,
            template_name=template_name,
        )
        result = parse_cap4_between(form_page, start_marker=start_marker, end_marker=end_marker)
        result.template_url = form_page.url
        m = re.search(r"templateId=(\d+)", form_page.url)
        if m:
            result.template_id = m.group(1)
        result.template_name = pick.get("picked") or template_name
        if pick.get("id"):
            result.template_id = str(pick["id"])
        return result


def run_parse_raw(
    base_url: str,
    username: str,
    password: str,
    *,
    template_url: str,
    template_name: str,
    tree_path: list[str] | None = None,
) -> FormSectionParseResult:
    """打开 OA 模板并提取全部可见字段（原始表单）。"""
    client = build_oa_client()
    with oa_browser_session() as (_, __, ___, page):
        require_oa_login(page, client)
        form_page, pick = open_template_via_call_dialog(
            page,
            template_url,
            tree_path=tree_path,
            template_name=template_name,
        )
        result = parse_cap4_all(form_page)
        result.template_url = form_page.url
        m = re.search(r"templateId=(\d+)", form_page.url)
        if m:
            result.template_id = m.group(1)
        result.template_name = pick.get("picked") or template_name
        if pick.get("id"):
            result.template_id = str(pick["id"])
        return result
