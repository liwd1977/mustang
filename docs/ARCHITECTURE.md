# 野马集团低代码应用智能交付 — 技术方案（V1.0 修订版）

> 修订日期：2026-08-06  
> 状态：待开发确认

## 一、范围调整说明

### 1.1 本期做 / 暂缓

| 能力 | 状态 | 说明 |
|------|------|------|
| Streamlit 轻量工作台 | **本期** | 文件上传、日志、配置预览 |
| Word 流程图提取 | **本期** | `野马集团办事指南*.docx` 仅含流程图 |
| 通义千问 VLM 图元识别 | **本期** | 输出节点/连线/条件等原始识别结果 |
| 外部网址表单抓取 | **本期** | 表单与流程图非 1:1，需语义匹配 |
| 有流程无表单识别 | **本期** | 标记 orphan flow，供人工补全 |
| 流程配置数据组装 | **本期** | 输出神笔可用的**配置数据**（非完整 DSL 构建） |
| Excel 指标 → SQL | **部分本期** | DDL 未到位时仅做 Excel 解析与占位 |
| Guardrail（SQL 审查） | **部分本期** | 与 SQL 生成同步启用 |
| **拓扑重建 / DAG / BPMN** | **暂缓** | 流程构建属神笔核心能力，AI 不代建 |
| **图算法（networkx 等）** | **暂缓** | 同上 |
| **复杂任务编排（LangGraph）** | **暂缓** | V1.0 用简单 Pipeline 函数串联 |
| **神笔 API 刷入 / Adapter** | **暂缓** | 等平台 Schema、Token 到位 |
| **9 大板块 DDL** | **暂缓** | 待放入 `data/ddl/` |

### 1.2 核心原则

1. **AI 只产配置，不建流程**：识别与匹配结果序列化为配置 JSON，由神笔平台或交付人员在画布完成流转构建。
2. **表单与流程图分离**：Word 无表单；表单从指定网址获取，经语义匹配关联。
3. **可观测 + 可兜底**：每步输出 JSON 快照、置信度；低置信度与 orphan flow 高亮告警。

---

## 二、总体架构

```
┌─────────────────────────────────────────────────────────────┐
│              Streamlit 工作台 (AI-00)                        │
│  流程页：Word 上传 | 表单 URL 配置 | 日志 | 配置 JSON 预览    │
│  看板页：Excel 上传（DDL 到位前仅解析预览）                    │
└──────────────────────────┬──────────────────────────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ 流程配置管线  │  │ 表单匹配管线  │  │ 看板配置管线  │
│ AI-01/02     │  │ AI-03        │  │ AI-05/06     │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │
       └────────┬────────┘                 │
                ▼                          ▼
       ┌────────────────┐         ┌────────────────┐
       │ 配置数据组装    │         │ Guardrail      │
       │ (Config Bundle)│         │ AI-07          │
       └────────┬───────┘         └────────────────┘
                │
                ▼
       ┌────────────────┐
       │ output/ 快照   │  ──►  [暂缓] 神笔 Adapter / 刷入
       └────────────────┘
```

---

## 三、技术选型

| 层次 | 选型 | 备注 |
|------|------|------|
| Web | **Streamlit 1.3x** | 已确认 |
| 语言 | Python 3.11+ | |
| Word | python-docx | 提取嵌入流程图 |
| Excel | openpyxl / pandas | 指标表 |
| VLM | **通义千问 qwen-vl-max** | DashScope OpenAI 兼容 API |
| 文本 LLM | qwen-plus | 表单语义匹配 |
| 环境变量 | python-dotenv + pydantic-settings | 参考 Consult_Claude |
| HTTP | httpx | 表单网址抓取 |
| HTML 解析 | beautifulsoup4 / lxml | 表单页解析 |
| SQL 审查 | sqlparse | SELECT-only 等规则 |

**环境变量约定**（与 Consult_Claude 一致）：

- `OPENAI_API_KEY` → DashScope Key  
- `DEFAULT_LM_PROVIDER=qwen`  
- `LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`  
- 启动时 `load_dotenv(项目根/.env, override=False)`

---

## 四、表单与流程图关联设计（新增）

### 4.1 数据来源

| 来源 | 内容 | 说明 |
|------|------|------|
| Word 办事指南 | 流程图图片 + 章节标题/ surrounding 文本 | 无表单字段 |
| 指定网址 | 表单定义列表 | 与流程图**非 1:1** |

### 4.2 语义匹配流程

```
流程图条目 (id, 板块, 标题, 上下文文本, VLM 识别摘要)
        +
表单条目   (id, 名称, 字段列表, 页面上下文)
        │
        ▼
  LLM 语义匹配 (qwen-plus)
        │
        ├── matched   → 写入 config.forms[]
        ├── ambiguous → 标记待人工确认 + 候选列表
        └── no_match  → 标记 orphan_flow: true
```

### 4.3 输出：流程配置包 (Flow Config Bundle)

```json
{
  "flow_id": "sector_外贸_001",
  "sector": "外贸",
  "title": "客商准入审批",
  "source": {
    "word_image_index": 3,
    "image_path": "output/workflows/.../flow_003.png"
  },
  "vlm_recognition": {
    "nodes": [],
    "edges": [],
    "confidence": 0.85,
    "raw_notes": ""
  },
  "form_binding": {
    "status": "matched | ambiguous | orphan",
    "form_id": null,
    "form_name": null,
    "fields": [],
    "match_score": 0.0,
    "candidates": []
  },
  "metadata": {
    "created_at": "",
    "needs_review": false
  }
}
```

> **注意**：不含 DAG/BPMN/节点流转构建；`vlm_recognition` 为 VLM 原始识别，供神笔侧或人工引用。

---

## 五、目录结构

```
mustang/
├── app/                          # Streamlit
│   ├── main.py
│   └── pages/
│       ├── 01_workflow.py        # 流程 + 表单匹配
│       ├── 02_dashboard.py       # 看板（DDL 到位前受限）
│       └── 03_settings.py        # API / 表单 URL 配置
├── core/
│   ├── config/settings.py        # pydantic-settings + dotenv
│   ├── llm/qwen_client.py        # VLM / 文本 LLM 封装
│   ├── workflow/
│   │   ├── word_parser.py        # AI-01
│   │   ├── vlm_recognizer.py     # AI-02
│   │   └── config_assembler.py   # 配置包组装
│   ├── forms/
│   │   ├── form_fetcher.py       # 网址表单抓取
│   │   └── form_matcher.py       # 语义匹配 + orphan 检测
│   ├── dashboard/                # DDL 到位后扩展
│   │   └── excel_parser.py
│   └── guardrail/
│       └── sql_validator.py
├── schemas/
│   └── flow_config_bundle.py     # Pydantic 模型
├── prompts/
│   ├── flowchart_vlm.yaml
│   └── form_matching.yaml
├── data/
│   ├── ddl/                      # 待放入
│   └── samples/
├── output/                       # 生成快照（gitignore）
├── docs/
│   └── ARCHITECTURE.md           # 本文档
├── .env.example
└── pyproject.toml
```

**暂缓目录**（占位，不实现）：

- `core/workflow/topology_builder.py`
- `adapters/shenbi/`
- `core/agents/`（Master/Workflow Agent 编排）

---

## 六、功能模块（修订任务表）

| ID | 名称 | 输入 | 输出 | 优先级 |
|----|------|------|------|--------|
| AI-00 | Streamlit 工作台 | 用户操作 | GUI | P0 |
| AI-01 | Word 解析与图片提取 | .docx | 图片 + 索引 | P0 |
| AI-02 | VLM 流程图识别 | 流程图 PNG | 节点/边/条件 + 置信度 | P1 |
| **AI-03** | **表单抓取与语义匹配** | 表单 URL + 流程索引 | 匹配结果 + orphan 列表 | P1 |
| **AI-04** | **配置数据组装** | VLM + 表单匹配 | Flow Config Bundle JSON | P1 |
| AI-05 | Excel 指标解析 | .xlsx | 维度/度量结构 | P2（DDL 前仅解析） |
| AI-06 | SQL / 报表配置 | Excel + DDL | SQL + 报表 JSON | **阻塞**（待 DDL + 神笔 Schema） |
| AI-07 | Guardrail | SQL/JSON | 审查报告 | P2 |
| AI-08 | 神笔刷入 | Config Bundle | 平台配置 ID | **阻塞**（待神笔资料） |

~~原 AI-03 拓扑重建~~ → 暂缓  
~~原 AI-04 表单与 RBAC 映射~~ → 拆为 AI-03 表单语义匹配；RBAC 待字典到位

---

## 七、开发排期（修订）

| 阶段 | 时间 | 内容 |
|------|------|------|
| **P0** | 8/6 | Git 初始化、脚手架、Streamlit 空壳、Qwen 客户端、.env |
| **P1** | 8/7–8/10 | AI-01/02：Word 提取 + VLM 识别 + 日志预览 |
| **P1+** | 8/8–8/11 | AI-03：表单 URL 抓取、语义匹配、orphan 报告 |
| **P1 收尾** | 8/9–8/11 | AI-04：Config Bundle 导出与批量索引 |
| **P2** | 8/11–8/14 | AI-05 Excel 解析；AI-07 SQL Guardrail（有 DDL 则接 SQL 生成） |
| **P3** | 资料到位后 | 神笔 Adapter + AI-08 刷入 |
| **P4** | 8/17–8/20 | 163 流程批量 + 验收文档 |

**8/10 决策点**（沿用原方案）：VLM 识别若阻塞，启用「识别结果 + 人工在神笔补全」双轨，不依赖拓扑自动重建。

---

## 八、待用户提供

1. **表单来源网址**及登录/鉴权方式（若有）  
2. **神笔**流程配置 JSON Schema 样例（到位后接 Adapter）  
3. **DDL** 放入 `data/ddl/`  
4. 可选：RBAC 角色矩阵 CSV  

---

## 九、风险与应对

| 风险 | 应对 |
|------|------|
| 流程图与表单无法自动对应 | 语义匹配 + ambiguous 候选；orphan 清单人工处理 |
| 表单网站结构变化 | FormFetcher 抽象 + 选择器配置化 |
| VLM 识别率低 | 置信度阈值告警；不阻塞配置包输出 |
| 神笔 Schema 未到位 | 先交付 Universal Config Bundle，Adapter 后补 |
