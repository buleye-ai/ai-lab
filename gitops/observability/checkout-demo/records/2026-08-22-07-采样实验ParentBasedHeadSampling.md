# checkout-demo Trace 采样实验：10% Parent-based Head Sampling

- 日期：2026-08-22
- 范围：仅修改两个 deployment 的 env，不改 tracing_utils.py（SDK 原生支持 OTEL_TRACES_SAMPLER）
- 脱敏：不保存原始 Trace ID、request body、Header 或凭据

## GitOps 变更

| 阶段 | Git 提交 | 变更 | 状态 |
|---|---|---|---|
| 采样开启 | `2e9b2e1` | 两个 workload 各加 `OTEL_TRACES_SAMPLER=parentbased_traceidratio`、`OTEL_TRACES_SAMPLER_ARG=0.1` | Synced / Healthy |
| 恢复 | `b49a316` | `git revert 2e9b2e1`，完全移除采样 env | Synced / Healthy |

## 已验证行为

### 环境变量生效

两个 Pod 的 env 均存在：

```text
OTEL_TRACES_SAMPLER=parentbased_traceidratio
OTEL_TRACES_SAMPLER_ARG=0.1
```

### 采样比例

用 23 个根（无 W3C 传入）Trace ID 验证：在 Tempo 中找到的 ≈ **2/23 ≈ 9%**，与 10% 目标一致。

### 跨服务完整保留/丢弃验证

采样开启时，2 个被保留的 root Trace 的 Tempo 分析结果：

| Trace ID (部分) | checkout-demo | inventory-demo | 父子关系 |
|---|---|---|---|
| `0a37ed7a6e21...` | `GET /checkout` root | `GET /reserve` child | ✓ |
| `4ad02dd8988a...` | `GET /checkout` root | `GET /reserve` child | ✓ |

ParentBased 策略保证：root 决定采样 ⇒ child inventory Span 也必然被采样并导出；root 被丢弃 ⇒ child 也不导出，不会出现半条 Trace。

### 显式 flags=01 请求不受影响

传入 W3C `traceparent` 且 flags=01 的请求始终被采样，所有此类请求的 Trace 在 Tempo 中可查。这不违反采样策略——ParentBased 认可已采样的远端父 Span。

### 错误保留局限（由 SDK 标准行为证实）

Head Sampling 在请求入口已丢弃的 root Trace 无法在后续看到错误。它不是 Collector 级别的 Tail Sampling，不保证故障/错误 Trace 必留。

### Collector 无 exporter failure

采样开启期间 `otelcol_exporter_send_failed_spans` 始终为零。

### Git revert 后完全恢复

无采样 env，新请求 Trace 再次可查。

## 边界

- 这不是生产级错误必留采样；要验证错误必留需要 Collector Tail Sampling。
- 10% 在 Lab 验证中已得出约 9% 的实际比例，但不能自动证明相同代码能在不同速率或后端容量下保持相同比例。
- Cosmos count 仅为时间窗口内的近似，真实采样基于 trace_id 模 2^128 的算法。