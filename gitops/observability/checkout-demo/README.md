# checkout-demo：真实告警演练服务

## 目的与边界

`checkout-demo` 是 `k3d-ai-lab` 中一个隔离的、可经 GitOps 回滚的 HTTP 服务。它为后续验证 Service / EndpointSlice / Probe / Metrics / PrometheusRule / Alertmanager 分级路由提供真实运行时信号。

- `service`: `checkout-demo`
- `team`: `commerce`
- `environment`: `lab`
- `cluster`: `k3d-ai-lab`
- `namespace`: `demo`
- 关键用户路径：`GET /checkout`
- 健康检查：`GET /healthz`
- 指标：`GET /metrics`

本目录的第一阶段只建立健康服务和采集基线：**不包含 PrometheusRule，不改变 Alertmanager receiver/route，不发送 webhook 告警。**

## 运行契约

| 路径 | 健康基线 | 后续受控故障版本 | 说明 |
| --- | --- | --- | --- |
| `/healthz` | HTTP 200 | 保持 200 | Kubernetes 存活/就绪，不等同业务成功 |
| `/checkout` | HTTP 200，写 `checkout_succeeded` JSON 日志 | HTTP 503，写 `checkout_failed` JSON 日志 | 用户关键路径与 SLI 候选 |
| `/metrics` | 暴露请求、错误、延迟 Histogram | 继续可抓取，错误计数上升 | Prometheus 检测信号 |

受控事故只允许通过 Git 修改 `LAB_MODE` 与 `REVISION` 后由 Argo CD 同步；禁止 `kubectl edit`、直接 Helm 更新或手工改 Alertmanager Secret。

## 部署和基线验收

提交、推送后由 `checkout-observability-lab` Argo Application 自动同步。每次变更先记录 commit SHA 与 Argo revision，再验证：

```bash
kubectl -n demo get deployment checkout-demo
kubectl -n demo get pods -l app.kubernetes.io/name=checkout-demo
kubectl -n demo get svc checkout-demo
kubectl -n demo get endpointslice -l kubernetes.io/service-name=checkout-demo
kubectl -n demo get servicemonitor checkout-demo
kubectl -n argocd get application checkout-observability-lab
```

在任一 Pod 上验证服务：

```bash
POD=$(kubectl -n demo get pod -l app.kubernetes.io/name=checkout-demo -o jsonpath='{.items[0].metadata.name}')
kubectl -n demo port-forward pod/$POD 18081:8080
curl -fsS http://127.0.0.1:18081/healthz
curl -fsS http://127.0.0.1:18081/checkout
curl -fsS http://127.0.0.1:18081/metrics
```

预期：Deployment 为 `3/3 Available`，EndpointSlice 有 3 个 ready endpoint，`/healthz` 和 `/checkout` 返回 200，Prometheus target 为 UP，且当前本机 Alertmanager webhook 无新 POST。

## 演练记录和回滚

每次演练在 `records/` 新建 `YYYY-MM-DD-编号.md`，同时将**同一份脱敏记录**复制到私有学习库：

```text
/Users/james/Code/cloud-native-ai-growth/notes/learning/cloud-native/07-observability/05-alerts/records/
```

记录必须包含开始、故障和回滚 commit SHA，Argo sync revision、时间线、Prometheus/Alertmanager/webhook 脱敏证据、人工决策与多层恢复验证。不得提交原始 webhook payload、请求头、Token、客户数据或其他敏感信息。

回滚采用 Git revert 到已验证健康版本；关闭告警需要同时证明 SLI、Pod Ready、EndpointSlice、Deployment、日志/Events 和 Argo revision 恢复，而非只看到 Alertmanager `resolved`。
