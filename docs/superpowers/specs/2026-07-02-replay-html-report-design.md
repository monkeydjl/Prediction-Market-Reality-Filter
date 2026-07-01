# Replay HTML Report Design

**文档版本**: v1.0
**创建日期**: 2026-07-02
**状态**: Spec §4.5 P1 最后一项补全 — 给 replay harness 补 HTML 报告渲染
**关联 spec**: `2026-06-30-production-readiness-gaps.md` §4.5

---

## 背景与目标

Spec §4.5 要求 `replay_decision_pipeline.py` 输出 **HTML/Markdown/JSON** 三种格式的报告。当前实现只有 Markdown + JSON + cases.jsonl,HTML 缺失。本设计补齐 HTML 格式,使 spec §4.5 达到 DONE。

**目标**: 在不改 Markdown/JSON 输出、不引入外部依赖的前提下,新增一个自包含、可交互的 HTML 报告渲染器,与现有 `render_markdown` / `render_json` 并列。

**非目标**: 不重做 Markdown/JSON 渲染;不改 metrics 字段;不改 replay 主流程。

---

## 架构

### 文件改动

只改两个文件,不新增文件:

1. [backend/app/replay/report.py](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/app/replay/report.py)
   - 新增 `render_html(metrics: dict[str, Any]) -> str` 纯函数
   - 扩展 `write_report`:在写 `report.md` + `metrics.json` + `cases.jsonl` 之后,再写 `report.html`
   - `write_report` 返回值不变(仍返回 `report.md` 路径,保持向后兼容)

2. [backend/tests/test_replay_report.py](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/tests/test_replay_report.py)
   - 新增 `TestRenderHtml` 测试类

### 数据流

```
ReplayMetrics.to_dict()  →  metrics dict
                          ↓
              render_markdown  →  report.md
              render_json      →  metrics.json
              render_html      →  report.html   (新增)
              cases (list)     →  cases.jsonl
```

`render_html` 消费与 `render_markdown` 完全相同的 `metrics` dict,不重新计算任何指标。

---

## HTML 结构

单文件自包含,所有 CSS + JS inline,无外部依赖。`<head>` 内 inline `<style>`,`<body>` 末尾 inline `<script>`。

### 7 个 Section(镜像 Markdown 报告)

| # | Section | 渲染方式 |
|---|---|---|
| 1 | Summary | 3 张指标卡片:Total events / Direction changed (with %) / Resolved (with outcome) |
| 2 | Direction Matrix | 4×4 热力图表格(行=原方向 YES/NO/WAIT/AVOID,列=重放方向),对角线单元格绿色背景(未变),非对角红色渐变(变化),颜色强度 = `count / max_count`(max_count = 矩阵所有单元格中的最大值;max_count=0 时所有单元格无背景色) |
| 3 | Brier | 大数值显示 + `brier_frozen` 说明 callout(与 Markdown 同文案) |
| 4 | Direction Accuracy | 横向条形对比(Original correct % vs Replayed correct %)+ delta 徽章(improved=绿/regressed=红/unchanged=灰),resolved=0 时显示 "No resolved samples." |
| 5 | LLM vs Fallback | 表格(Quality / N / Brier mean),空时显示 "No analysis_quality data." |
| 6 | Per-Phase Marginal | 表格(Phase / Downgrades caused / Directions changed / Conflicts),downgrades_caused 列加内联条形指示(`width: pct%`,pct = `downgrades_caused / max_downgrades_across_phases`;max=0 时条形宽度 0%),空时显示 "No per-phase replay run." |
| 7 | Conflict Cases | **可排序表格**(点 `<th>` 切换升/降序,排序图标用 ▲▼ 字符)+ **phase 筛选下拉框**(含默认 "All" 选项 + 从 conflict_cases 提取的唯一 phase 值),空时显示 "No conflict cases." |

### 文档骨架

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>Replay Report</title>
  <style>/* inline CSS */</style>
</head>
<body>
  <h1>Replay Report</h1>
  <p class="generated">_Generated: {timestamp}_</p>

  <section id="summary"><h2>Summary</h2>...</section>
  <section id="direction-matrix"><h2>Direction Matrix</h2>...</section>
  <section id="brier"><h2>Brier</h2>...</section>
  <section id="direction-accuracy"><h2>Direction Accuracy</h2>...</section>
  <section id="llm-vs-fallback"><h2>LLM vs Fallback</h2>...</section>
  <section id="per-phase-marginal"><h2>Per-Phase Marginal Contribution</h2>...</section>
  <section id="conflict-cases"><h2>Conflict Cases</h2>...</section>

  <script>/* inline JS: sortTable + filterTable */</script>
</body>
</html>
```

---

## JS 交互(inline,无第三方库)

两个函数,纯 vanilla JS:

```javascript
// 点表头排序,切换升降序
function sortTable(tableId, colIdx) {
  const table = document.getElementById(tableId);
  const tbody = table.querySelector('tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  const isNumeric = rows[0]?.cells[colIdx]?.dataset.numeric === 'true';
  const direction = table.dataset.sortDir === 'asc' ? 'desc' : 'asc';
  table.dataset.sortDir = direction;
  rows.sort((a, b) => {
    const av = a.cells[colIdx].textContent.trim();
    const bv = b.cells[colIdx].textContent.trim();
    const cmp = isNumeric
      ? parseFloat(av) - parseFloat(bv)
      : av.localeCompare(bv);
    return direction === 'asc' ? cmp : -cmp;
  });
  rows.forEach(r => tbody.appendChild(r));
}

// 按 phase 筛选冲突案例
function filterTable(tableId, selectId) {
  const filter = document.getElementById(selectId).value;
  const rows = document.querySelectorAll(`#${tableId} tbody tr`);
  rows.forEach(r => {
    r.style.display = (!filter || r.dataset.phase === filter) ? '' : 'none';
  });
}
```

- 冲突案例表的每个 `<th>` 带 `onclick="sortTable('conflict-table', <colIdx>)"` 和排序图标
- 每行 `<tr>` 带 `data-phase="<phase>"` 属性
- 筛选下拉 `<select id="phase-filter" onchange="filterTable('conflict-table', 'phase-filter')">`

### 不引入 JS 的部分

- 方向矩阵热力图:纯 CSS(`background-color` 按强度计算后内联到 `<td style="...">`)
- Per-Phase downgrades 内联条形:纯 CSS(`width` 百分比内联到 `<div style="...">`)

---

## 约束与不变量

### 词汇锁(Hard Constraint)

HTML 报告内**不得出现**以下术语(与 Markdown 报告一致,见 project_memory):

`long` / `short` / `buy` / `sell` / `position` / `kelly` / `order`

方向词汇锁定为 `YES` / `NO` / `WAIT` / `AVOID`。`order` 仅作为 HTML 属性值或 JS 变量名(如 `sortDir`)不出现 — 所有 JS 命名避开 `order` 字样(用 `direction` / `sortDir`)。

### 自包含(Hard Constraint)

- 无 `src=` 指向外部资源
- 无 `href=` 指向外部资源(`<link rel="stylesheet">` 禁用)
- 无 `@import`、无 `url()` 引用外部 URL
- 无 CDN、无 http/https/protocol-relative URL
- 所有 CSS 在 `<style>` 内,所有 JS 在 `<script>` 内
- 产物 `report.html` 可直接双击在浏览器打开,无需网络

### 纯函数约束

- `render_html(metrics: dict) -> str`:无 IO、无副作用、确定性
- 与 `render_markdown` / `render_json` 同级,共用同一 `metrics` 输入

### 编码

- UTF-8(`<meta charset="UTF-8">` + `write_text(encoding="utf-8")`)

### 向后兼容

- `render_markdown` / `render_json` 签名与输出不变
- `write_report` 返回值不变(仍返回 `report.md` 路径)
- `cases` 参数行为不变

### 空状态文案一致性

HTML 空状态文案必须与 Markdown 报告**文本内容一致**(去掉 Markdown 的 `_italic_` 标记,保留纯文本):

| Section | 空状态文案 |
|---|---|
| Direction Matrix | `No direction changes recorded.` |
| Brier | `No resolved samples to compute Brier.` |
| Direction Accuracy | `No resolved samples.` |
| LLM vs Fallback | `No analysis_quality data.` |
| Per-Phase Marginal | `No per-phase replay run.` |
| Conflict Cases | `No conflict cases.` |

---

## `render_html` 函数签名

```python
def render_html(metrics: dict[str, Any]) -> str:
    """Render metrics dict to a self-contained HTML report string.

    Pure function: no IO, no side effects. Output is a single HTML
    document with inline CSS + JS, no external resources — can be
    opened directly in a browser without a network connection.

    Mirrors the 7 sections of render_markdown: Summary / Direction
    Matrix / Brier / Direction Accuracy / LLM vs Fallback /
    Per-Phase Marginal / Conflict Cases. Conflict cases table is
    sortable (click headers) and filterable (by phase dropdown).
    """
```

### 辅助函数(模块内私有)

- `_html_escape(text: str) -> str` — 转义 `<`/`>`/`&`/`"`/`'`,防 XSS(虽然数据来自内部 metrics,防御性转义)
- `_heatmap_color(count: int, max_count: int, is_diagonal: bool) -> str` — 返回 `background-color: rgba(...)` 字符串,对角线绿色、非对角红色按强度渐变
- `_format_pct(num: int, denom: int) -> str` — 格式化百分比,denom=0 时返回 "N/A"
- `_direction_accuracy_bar(label: str, pct: float) -> str` — 渲染横向条形对比的 HTML 片段
- `_delta_badge(delta: float | None) -> str` — 渲染 improved/regressed/unchanged 徽章

---

## `write_report` 扩展

```python
def write_report(
    metrics: dict[str, Any],
    output_dir: Path,
    cases: list[dict[str, Any]] | None = None,
) -> Path:
    """Write report.md + metrics.json + cases.jsonl + report.html to
    ``output_dir``.

    Returns the path to ``report.md`` (unchanged for backward compat).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / "report.md"
    md_path.write_text(render_markdown(metrics), encoding="utf-8")
    (output_dir / "metrics.json").write_text(
        render_json(metrics), encoding="utf-8"
    )
    # 新增:HTML 报告(spec §4.5 三格式补齐)
    (output_dir / "report.html").write_text(
        render_html(metrics), encoding="utf-8"
    )
    if cases is not None:
        cases_path = output_dir / "cases.jsonl"
        with cases_path.open("w", encoding="utf-8") as f:
            for c in cases:
                f.write(json.dumps(c, default=str) + "\n")
    return md_path
```

---

## 测试计划

加到 [backend/tests/test_replay_report.py](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/tests/test_replay_report.py) 的 `TestRenderHtml` 类:

| 测试方法 | 断言 |
|---|---|
| `test_render_html_includes_all_sections` | 7 个 section header(`id="summary"` 等)都存在 |
| `test_render_html_returns_non_empty_string` | 非空且以 `<!DOCTYPE html>` 开头 |
| `test_render_html_contains_no_banned_terms` | 输出 lower 后不含 `long`/`short`/`buy`/`sell`/`position`/`kelly`/`order` |
| `test_render_html_is_self_contained` | 无 `src=`、无 `href=`、无 `@import`、无 `url(` 指向外部 |
| `test_render_html_includes_sortable_conflict_table` | 冲突表有 `id="conflict-table"` + `onclick="sortTable"` |
| `test_render_html_includes_direction_matrix_heatmap` | 方向矩阵表格存在 + 至少一个单元格有 `background-color` 内联样式 |
| `test_render_html_includes_phase_filter` | 存在 `id="phase-filter"` 的 `<select>` |
| `test_render_html_handles_empty_conflict_cases` | `conflict_cases=[]` 时不崩溃,显示 "No conflict cases." |
| `test_render_html_handles_no_resolved_samples` | `resolved_count=0` 时不崩溃,Direction Accuracy 显示 "No resolved samples." |
| `test_render_html_escapes_event_ids` | event_id 含 `<script>` 时被转义为 `&lt;script&gt;` |
| `test_write_report_creates_html_file` | `write_report` 后 `report.html` 存在且非空 |

复用现有 `_sample_metrics()` fixture(已包含所有字段)。空数据用 `_sample_metrics()` 的拷贝 + 清零对应字段。

---

## 验收标准

- [ ] `render_html(metrics)` 返回自包含 HTML 字符串,7 个 section 齐全
- [ ] 冲突案例表可排序、可按 phase 筛选
- [ ] 方向矩阵有颜色编码(对角线绿、非对角红渐变)
- [ ] 词汇锁通过(无 long/short/buy/sell/position/kelly/order)
- [ ] 自包含通过(无外部 src/href)
- [ ] `write_report` 产出 `report.html` 文件,且 `report.md`/`metrics.json`/`cases.jsonl` 行为不变
- [ ] 全部新增测试通过
- [ ] 现有 `test_replay_report.py` 测试不回归
- [ ] 全量后端测试套件不回归

---

## 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-07-02 | 初始设计 — replay HTML 报告渲染器 |
