# checkout-demo Trace Tail Sampling 实验：错误优先保留 + 10% 正常

- 日期：2026-08-22
- 范围：仅修改 Collector 的 traces pipeline；应用代码、Tempo、RBAC、Alloy 均不修改
- Git revert 回到当前无采样教学基线

## GitOps 变更

| 阶段 | 提交 | 变更 | 状态 |
|---|---|---|---|
| Collector 启用 Tail Sampling | `28bdd96` | 加 `tail_sampling` processor：`decision_wait=5s`、`num_traces=100`、错误 100% 保留、正常 10% | Synced / Healthy |
| inventory 受控 503 | `2ea0680` | `LAB_MODE=unhealthy` | Synced / Healthy |
| 恢复（双向） | `de253e2` + `bff746b` | git revert 回到健康/未采样基线 | Synced / Healthy |

## 已验证行为

### 1. 错误 Trace 100% 保留（Tail Sampling 核心能力）

发送 40 个 503 请求后 Tail Sampling metrics 变化：

```text
New traces received:         +47
└─ retain-error-traces sampled (true):    +47  ✓  (100% error retention)
│  retain-error-traces sampled (false):   +0   ✓  (no ERROR traces misclassified)
├─ sample-normal-traces sampled (false): +43  (10% probabilistic drop continued)
├─ sample-normal-traces sampled (true):  +4   (≈10% of previous non-ERRORs)
└─ sampling_trace_dropped_too_early:      0   ✓  (no buffer overflow drops)
```

### 2. Tempo 中所有错误 Trace 均可查

5 个被采样的 503 Trace 全部在 Tempo 中可查，且均包含：

```text
checkout-demo / GET /checkout  http=503
inventory-demo / GET /reserve  http=503
```

缺省 status message 是由于 SDK 设置方式未在 span 上游记录；HTTP 503 属性直接证明 Tail Sampling 保留了错误路径。

### 3. 正常流量仍受 10% 概率控制

健康请求期间 `sample-normal-traces` 按预期保留约 10%，其余丢弃。

### 4. Collector 无异常

```text
sampling_policy_evaluation_error: 0
sampling_trace_dropped_too_early: 0
exporter_send_failed_spans: 0
```

### 5. Git revert 后完全恢复

```text
inventory-demo: healthy / baseline
checkout-demo: 3/3 available
Collector: tail_sampling ABSENT (live ConfigMap)
new /checkout → HTTP 200
```

## 边界

- `decision_wait=5s`：错误 Trace 需等待最长 5 秒才出现在 Tempo，满足大多数人为调查场景，但不适用于低延迟自动反馈回路。
- `num_traces=100`：仅适合低流量 Lab。超过 100 条进行中 Trace 后，新 Trace 的等待 buffer 会被压缩，可能先确保等待窗口足够。
- 单 Collector 副本：Pod 重启时所有待决策的 Trace 丢失。生产必须 HA。
- 未涉及采样后 TraceQL 指标、Service Graph 等 Tempo metrics-generator 能力。
- 不能在禁止实验后保证不同类型错误（如 panic、panic 死循环、内存不足）的完美捕获；本实验只验证了带 SDK StatusCode.ERROR 的受控 503。