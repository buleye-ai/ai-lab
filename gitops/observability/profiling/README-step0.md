---
title: eBPF Continuous Profiling Lab 部署与实验手册
status: deployed-platform-kernel-blocked
scope: k3d-ai-lab
---
# eBPF Continuous Profiling Lab：部署、实验与回滚手册

> **状态：平台已通过 GitOps 部署；eBPF 实际采样在当前 OrbStack/k3d 内核上被阻塞。**
>
> 已部署不等于“CPU Profile 已成功采到”。本文把两者拆开记录，避免把 Pod Running 或 Argo Healthy 误写为 eBPF 机制已验证。

## 1. 已部署拓扑与范围

```text
demo/checkout-demo + demo/inventory-demo
        │  仅作为发现与导出目标
        ▼
alloy-profiles-ebpf DaemonSet
  ├─ 仅 node: k3d-ai-lab-agent-0
  ├─ hostPID + root + privileged（pyroscope.ebpf 的技术前提）
  ├─ Role/RoleBinding：仅 demo namespace 的 Pods get/list/watch
  ├─ /var/lib/alloy：emptyDir runtime state
  └─ /tmp：emptyDir symbol cache
        ▼
pyroscope StatefulSet
  ├─ 单副本、5Gi local-path PVC、无 MinIO/对象存储、无 Ingress、无 HA
  └─ ClusterIP Service :4040
```

| 项目 | 版本 / 设置 | 已核验事实 |
|---|---|---|
| Pyroscope | Helm chart/app `2.2.1` | StatefulSet `1/1`、Pod Running、5Gi PVC Bound |
| Alloy | chart `1.11.0` / image `v1.18.0` | DaemonSet 仅期望 1 个 Pod，限定 agent-0 |
| Argo CD Application | `profiling-lab` | Synced / Healthy（仅声明资源健康） |
| 目标范围 | `demo` + checkout-demo / inventory-demo | Alloy 发现配置有 namespace 与 app relabel 双重限制 |
| eBPF 内核加载 | 当前 k3d on OrbStack | **失败：`tp base not found`，不能产生已验证 Profile** |

## 2. 关键安全边界

`pyroscope.ebpf` 要求 `hostPID: true`、root 与 `privileged: true`。目标 relabel 只能限制**导出的 Profile 目标和标签**，不能剥夺 Agent 对宿主机进程空间的技术可见性。

因此本 Lab 固定采用：

- 仅一个明确节点 `k3d-ai-lab-agent-0`，绝不全节点展开；
- 独立 eBPF Alloy，不改日志/Events Alloy；
- `demo` namespace 内仅 `pods` 的 get/list/watch；无 ClusterRole、Node/Secret/Event/Log 权限、无 Kubernetes 写权限；
- 不保存或公开原始 Profile、符号表、命令行或请求参数；
- Lab 结束应使用 Git revert 移除整套 `profiling-lab` Application。

## 3. 部署路径（已执行）

> **受控变更。** 部署前应确认目标 context=`k3d-ai-lab`、Git revision、停止条件和回滚 revision；禁止 `kubectl edit` 绕过 GitOps。

1. 渲染 Pyroscope chart，执行 server dry-run；
2. 先提交并同步 Pyroscope 后端；验证 Application、PVC、StatefulSet、Pod；
3. 渲染 Alloy eBPF values，使用 Alloy `fmt` 校验 HCL，执行 server dry-run；
4. 仅在后端 Ready 后将 Alloy source 加入 `profiling-lab` Application；
5. 发现两个只读文件系统问题：Alloy runtime `/var/lib/alloy` 与 eBPF symbol cache `/tmp/symb-cache`，均改为明确 `emptyDir` 挂载后再同步；
6. 增加 `discovery.kubernetes.namespaces = ["demo"]`，使 namespace-scoped Role 与 discovery 范围一致；
7. 最终核验 Argo `Synced/Healthy`、Pyroscope `1/1`、Alloy DS `1/1`、PVC Bound 与 Agent 日志。

## 4. 当前阻塞：不是配置权限问题

Alloy 已启动、发现配置已加载，且 Pod 可运行；但 `pyroscope.ebpf` 组件报告：

```text
failed to load eBPF tracer
failed to determine system configs: tp base not found
```

当前节点是 `aarch64`，内核为 OrbStack 提供的 `7.0.14-orbstack...`。预检中的 bpffs 与 `/sys/kernel/btf/vmlinux` 存在，只说明基础路径存在；它们不足以保证该内核满足 Alloy eBPF tracer 所需的 tracepoint/BTF 布局。

**已观察事实：**后端和 Agent 资源已部署并可运行；eBPF tracer 加载失败。
**待验证假设：**换到受支持的原生 Linux 节点/内核后，相同配置可加载 tracer 并导出 Profile。
**禁止结论：**不能因为 Argo Healthy、Pod Ready 或 Pyroscope Ready 就声称当前 Lab 已完成 eBPF CPU profiling。

## 5. 实验方法（迁移到可支持 Linux 节点后执行）

### 5.1 实验目标

验证“在固定负载和固定时间窗内，continuous profile 能找出 CPU 花在哪个运行时/函数路径”，而不是仅验证 Pod 是否存活。

### 5.2 前置准入卡

| 项目 | 本次填写要求 |
|---|---|
| 业务假设 | 例如：checkout 在固定负载下出现 CPU 回归 |
| 对象 | 仅 `demo/checkout-demo`，单节点、单 revision |
| 时间窗 | 10 分钟健康基线 + 最长 10 分钟受控对照 |
| 用户影响判据 | Synthetic SLI、HTTP 成功率、延迟；Profile 不替代这些指标 |
| 采样边界 | 一个 service、一个节点、无全量 node profiling |
| 停止条件 | SLI 降级、Agent CPU/内存超限、意外目标出现、collector error 持续 |
| 回滚 | Git revert profiling Application / Alloy source；确认 Agent 被删除 |

### 5.3 健康基线（只读）

```bash
kubectl --context k3d-ai-lab -n argocd get application profiling-lab
kubectl --context k3d-ai-lab -n observability get sts,ds,pods,pvc \
  -l 'app.kubernetes.io/instance in (pyroscope,alloy-profiles-ebpf)'
kubectl --context k3d-ai-lab -n demo get pods -l app.kubernetes.io/name=checkout-demo -o wide
```

在 Grafana 配置 Pyroscope datasource 后，固定 10 分钟绝对时间窗、`service_name=checkout-demo`，保存 baseline 火焰图的**脱敏摘要**：热点函数类别、CPU 百分比区间、查询标签、时间窗与 Git revision；不要提交原始 Profile。

### 5.4 受控对照

1. 仅通过 checkout GitOps workload 的单一、可 revert 变更制造受控 CPU 工作；不得对未知服务压测；
2. 同时记录 Synthetic SLI、Prometheus CPU/节流、Tempo Trace、Loki 错误日志和 Argo revision；
3. 在相同标签与相同长度时间窗生成 Pyroscope Diff；
4. 比较“新增热点”是否与工作负载变更的调用路径一致；
5. Git revert 后，确认新窗口恢复、Profile 差异收敛、SLI/Trace/Logs/对象状态均正常。

### 5.5 成功判据

```text
Pyroscope 可查询到目标 service 的 CPU profile
+ Diff 显示可解释的新增/减少热点
+ 同时间窗 Metrics/Trace/Logs 支持工作负载假设
+ revert 后 SLI、对象、日志、Trace 和 Profile 对照恢复
= 受控性能回归证据链成立
```

## 6. 回滚与恢复

**高影响受控变更：**移除带 `hostPID + privileged` 的 Agent 必须走 Git revert。

1. `git revert <profiling-commit-range>`，推送；
2. 等 Argo revision 对齐并确认 `profiling-lab` 资源 prune；
3. 确认 `alloy-profiles-ebpf` DaemonSet / Pod / Role / RoleBinding 不再存在；
4. 如后端也不再需要，确认 Pyroscope StatefulSet/PVC 的处置符合保留策略；Lab 默认不把含 Profile 数据的 PVC 当作可公开产物；
5. 再核验 checkout Synthetic SLI、Trace、Loki、EndpointSlice、Pod Ready、Deployment Available、告警与 Git/Argo revision。

## 7. 相关 Git 记录

- `db69b0d`：Pyroscope backend 与初始 Lab 资产；
- `e1b1505`：启用 backend GitOps sync；
- `6585579`：启用单节点 scoped eBPF Alloy；
- `de84367`：为 Alloy runtime state 添加 emptyDir；
- `f05b523`：为 eBPF symbol cache `/tmp` 添加 emptyDir；

这些记录证明的是 Lab 部署与故障修正路径，不是生产平台部署经历。
