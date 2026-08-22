# checkout-demo Trace 受控 503 与 Git revert 恢复记录

- 日期：2026-08-22
- 范围：`checkout-demo` 单服务 Trace Lab；仅验证受控业务故障与恢复。
- 证据保护：本文不保存原始 Trace payload、完整 Trace ID、request ID、Header、body 或凭据。

## 变更与回滚

| 阶段 | Git 提交 | GitOps 变更 | 观察到的 Argo 状态 |
|---|---|---|---|
| 健康基线 | `6f51fad` | `LAB_MODE=healthy`、`REVISION=baseline` | Synced / Healthy |
| 受控故障 | `5f48b05` | `LAB_MODE=unhealthy`、`REVISION=trace-incident-2026-08-22-01` | Synced / Healthy |
| 恢复 | `9f872eb` | `git revert 5f48b05`，恢复 `healthy` / `baseline` | Synced / Healthy |

没有对 Deployment 做命令式 patch；故障与恢复均经 Git push → Argo CD 同步完成。

## 已观察事实

### 受控故障阶段

1. GitOps revision 已对齐到 `5f48b05`；应用环境变量为 `unhealthy` 和指定 incident revision。
2. 传入合法 W3C `traceparent` 的 `/checkout` 请求返回 **HTTP 503**。
3. Pod 保持 `3/3 Available`，EndpointSlice 三个 endpoint 保持 Ready：这证明 `/healthz` 和平台就绪不等于业务 `/checkout` 可用。
4. Tempo 返回对应 Trace，包含 `GET /checkout`、HTTP 状态码 `503` 和错误描述 `lab_mode_not_healthy`。
5. Loki 按同一 trace_id 找到 `checkout_failed` JSON 日志，且日志同时带有 trace_id、span_id、`lab_mode=unhealthy` 与 incident revision。
6. Synthetic Probe 为 `0`，失败计数由故障前的 161 增至 216。
7. Prometheus 同时显示：
   - `CheckoutAvailabilitySLOViolation`：critical、firing；
   - `CheckoutSyntheticProbeFailed`：warning、firing。
8. Alertmanager 路由结果符合设计：critical 活跃并路由至 `lab-critical-webhook`；warning 被该 critical inhibition 抑制，目标 receiver 为 `lab-warning-webhook`，未作为独立通知升级。
9. Collector 接收与导出计数在故障期间持续增长，未出现 `otelcol_exporter_send_failed_spans` 样本；计数短暂差一属于持续 Synthetic 流量下的 batch 异步窗口，不能据此推断丢失。

### Git revert 恢复阶段

1. `git revert` 提交 `9f872eb` 推送后，Argo revision 对齐到该提交并为 Synced / Healthy。
2. Live Deployment 回到 `LAB_MODE=healthy`、`REVISION=baseline`，`3/3 Available`、三个 endpoint Ready。
3. 新的 `/checkout` 请求返回 **HTTP 200**。
4. Tempo 返回新的健康 Trace（`GET /checkout`、200、baseline）；Loki 按新的 trace_id 找到 `checkout_succeeded` 日志和 span_id。
5. Synthetic Probe 回到 `1`；Prometheus `ALERTS{alertname=~"Checkout.*",alertstate="firing"}` 为空；Alertmanager active Checkout alerts 为 `0`。
6. Collector 仍持续接收并向 Tempo 导出 spans，未观察到 exporter failure 样本。由于 checker 持续每 5 秒发送请求，采样瞬间的 accepted/sent 可能相差一个 batch，不作为数据丢失结论。

## 机制结论与边界

- **已证实机制：**应用 Span 错误状态、结构化日志的 trace_id/span_id、Synthetic SLI、Prometheus/Alertmanager、Argo revision 可在同一受控故障时间窗建立可审计的关联。
- **时间相关性而非根因自动证明：**本实验中 Git revision 明确切换 `LAB_MODE`，所以 revision 与 503 有直接设计因果；在生产事故中，revision 同步只是一项假设线索，仍要检查差异、事件、依赖与回滚结果。
- **告警边界：**Alertmanager 证明路由和 inhibition，不定义 SLI、不做根因诊断，也不独立证明恢复。
- **未验证：**跨服务传播、采样策略、Tempo HA/对象存储、多租户/访问审计、生产容量与成本。

## 最小恢复准入（本次均满足）

- [x] Git revert 已推送，Argo revision 对齐。
- [x] `/checkout` 新请求 200。
- [x] 新健康 Trace 和 Loki trace_id 日志可查询。
- [x] Synthetic SLI 恢复为 1。
- [x] Checkout firing alerts 清空。
- [x] Deployment Available、Pod Ready、EndpointSlice Ready。
- [x] Collector 未观察到导出失败样本。
