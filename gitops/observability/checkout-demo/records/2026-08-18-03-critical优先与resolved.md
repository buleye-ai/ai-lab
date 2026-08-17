# checkout-demo 告警演练记录：2026-08-18-03 critical 优先、抑制与 resolved

> 时间窗：2026-08-18 00:50–01:00 CST（UTC+08:00）。本记录只保存 webhook 的脱敏摘要，不提交完整 payload、请求头或原始 JSONL。

## 1. 验证目标

验证上轮时序修正（`CheckoutSyntheticProbeFailed for: 2m`）后：

```text
critical 先发往 /alerts/critical
warning 在 Alertmanager 内作为症状 firing，但被 inhibition 抑制
warning 不投递 /alerts/warning
Git revert 后 critical webhook 收到 resolved
```

## 2. GitOps / Argo 链

| 节点 | Commit SHA | 结果 |
| --- | --- | --- |
| 时序修正已生效 | `958701dde8a53fb81143a5b9a08537d64a239576` | 集群 PrometheusRule 已验证 `CheckoutSyntheticProbeFailed for=2m` |
| 临时证据接收端 | `cca4406` | lab receiver 暂时改至独立本机 inspector `host.docker.internal:18082`，用于安全记录脱敏 webhook 摘要 |
| 故障触发 | `a47e49aa0ac4dc857d6df72b42535d6a872c909c` | GitOps 设置 `LAB_MODE=unhealthy`、`REVISION=incident-2026-08-18-03` |
| 恢复 | `763866d4949533f4a3a712f1b3fe3dba91186bfe` | Git revert，服务回到 `healthy/baseline`；相关 Argo Applications `Synced / Healthy` |

## 3. 通知、分级与抑制证据

独立 inspector 捕获到以下脱敏摘要：

| UTC 接收时间 | Path | receiver | 批次状态 | Alert | severity | alertCount |
| --- | --- | --- | --- | --- | --- | --- |
| `16:52:10Z` | `/alerts/critical` | `lab-critical-webhook` | `firing` | `CheckoutAvailabilitySLOViolation` | `critical` | 1 |
| `16:54:40Z` | `/alerts/critical` | `lab-critical-webhook` | `firing` | `CheckoutAvailabilitySLOViolation` | `critical` | 1 |
| `16:57:10Z` | `/alerts/critical` | `lab-critical-webhook` | `firing` | `CheckoutAvailabilitySLOViolation` | `critical` | 1 |
| `16:59:40Z` | `/alerts/critical` | `lab-critical-webhook` | `resolved` | `CheckoutAvailabilitySLOViolation` | `critical` | 1 |

所有批次的 group labels 均为：

```text
alertname=CheckoutAvailabilitySLOViolation
cluster=k3d-ai-lab
environment=lab
namespace=demo
service=checkout-demo
```

断言结果：

```text
critical firing: captured
critical resolved: captured
warning path delivery: not captured
```

故障期间 Prometheus 中 `CheckoutSyntheticProbeFailed [warning]` 与 `CheckoutAvailabilitySLOViolation [critical]` 均 firing；Alertmanager 的 strict inhibition 使 warning 不投递。critical 的后两次 firing 是 lab route `repeat_interval=2m` 的预期重复通知，不是新的事故。

## 4. 恢复关闭证据

- `ai-lab`、`checkout-observability-lab`、`alerting` 均为 `Synced / Healthy`，revision `763866d…`。
- `checkout-demo` 为 `3/3 Available`；`checkout-synthetic-checker` 为 `1/1 Available`。
- Git 回滚后 Prometheus 的 checkout firing alerts 为空。
- webhook 明确收到 `status=resolved`，receiver 和 group labels 与 firing critical 一致。

## 5. 结论

本轮闭环已真实证明：独立业务路径失败 → critical route → warning inhibit → repeat interval → Git revert → resolved webhook。上轮“warning 可能先通知”的时序缺口已通过 `warning for=2m` 的实际回归验证修复。

临时独立 inspector 完成取证后，应将 lab receiver 恢复为常规本机 `18080` endpoint；该恢复本身不改变 Rule、severity、route、group 或 inhibition 语义。
