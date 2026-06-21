# reaudit-verification-2026-06-20 Codex 核查结论

日期：2026-06-20

核查对象：`docs/Review-doc/3/reaudit-verification-2026-06-20.md`

## 总体结论

这份再审报告的主结论基本成立：

- 已声明完成的核心闭环修复确实存在。
- 安全、运维、部署、CI/CD 仍然不足。
- 当前系统仍不适合 90 天无人值守生产运行。

但报告中有几处事实需要修正，主要集中在测试统计、通过率口径和“新增测试”判断。

## 已核实为正确的结论

| 报告断言 | 核查结果 |
|---|---|
| `resolve_with_calibration` 已改为先 score/void prediction，再写 event outcome | 正确 |
| 新增 `reconcile_predictions()`，并在 `auto_resolve_events()` 开头调用 | 正确 |
| `freeze_prediction()` 首次 freeze 时 seed verified event-contract link | 正确 |
| 新增 `DIAGNOSIS_TRUST_FLOOR` / `qualified_floor` | 正确 |
| 新增 `loop_run_store.py`、`loop_status_service.py`、`/api/events/loop/status` | 正确 |
| `file_store.py` 使用 `threading.RLock`，不是 `fcntl` | 正确 |
| `calibration_feedback_service.py` 核心函数已有 docstring | 正确 |
| 主概率路径 `probability_engine_service.py` 设置 `max_retries=2` | 正确 |
| CORS 仍是 `allow_origins=["*"]` + `allow_credentials=True` | 正确 |
| 无认证、无 rate limiting、无标准 `/api/health` | 正确 |
| 无 `.github/workflows/ci.yml` | 正确 |
| 无自动备份、无 supervisor、无 dead-man switch | 正确 |

## 需要修正的结论

### 1. “测试计数无法复现”已经过期

报告写：

> 声称 503 pass 无法在当前环境复现。

当前核查已复现：

```text
cd backend
python -m unittest discover -s tests
Ran 503 tests in 19.021s
OK (skipped=1)
```

因此该项应改为：

**当前环境可复现 503 tests OK, 1 skipped。**

### 2. “无新增测试文件”不准确

当前工作区存在新增测试文件：

- `backend/tests/test_loop_run_store.py`

同时还修改了：

- `backend/tests/test_scheduler.py`
- `backend/tests/test_events_routes.py`
- `backend/tests/test_event_resolve_service.py`
- `backend/tests/test_prediction_store.py`
- `backend/tests/test_diagnosis_service.py`

因此“测试文件数量仍为 39、无新增”这个判断不应作为质量结论。当前更准确的描述是：

**测试文件总数为 39，且当前工作区包含新增/修改测试；全量 unittest 可通过。**

### 3. 汇总通过率口径内部不一致

报告 TL;DR 写：

- `45 项检查中 8 项通过 / 3 项警告 / 34 项失败`

后文汇总表写：

- `45 项检查中 10 项通过 / 2 项警告 / 33 项失败`

应统一口径。按报告自身分项表统计，更接近后者：

**10 通过 / 2 警告 / 33 失败，约 22%。**

### 4. “P0 全部未修复”需要限定范围

报告说第一轮 5 个 P0 全部未修复，若仅指：

- CORS
- 依赖锁定
- CI/CD
- 进程守护
- 自动备份

则判断成立。

但如果把第一轮报告里的业务闭环 P0 也算入，例如：

- resolve 写入顺序
- orphan repair
- loop status / run ledger

则不能说“全部未修复”。建议改为：

**安全/运维/部署类 P0 仍未修复；业务闭环一致性 P0 已完成核心修复。**

## 当前生产判断

维持再审报告结论：

**NO-GO for unattended 90-day production**

原因不是核心闭环逻辑，而是生产外壳仍缺：

1. CI workflow。
2. CORS 白名单与认证/限流。
3. supervisor / external scheduler owner。
4. 自动备份与 restore 演练。
5. 标准 `/api/health`、告警、dead-man switch。
6. 持久日志。

## 建议更新报告摘要

建议把再审报告 TL;DR 改成：

> 声称完成的 5 项核心闭环修复全部核实通过；当前全量后端测试可复现 `503 OK, 1 skipped`。但安全、运维、部署和 CI/CD 阻塞项仍基本未动，因此系统仍为 NO-GO for unattended 90-day production。
