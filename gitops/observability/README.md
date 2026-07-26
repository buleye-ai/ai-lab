# k3d 可观测性平台部署文档

本文记录在本地 k3d 集群中部署 Kubernetes 监控和日志平台的过程，适用于后续环境重建、配置维护和问题排查。

## 1. 架构

```text
浏览器
  │
  │ http://grafana.localhost:8080
  ▼
k3d server load balancer
  │
  ▼
Traefik Ingress
  │
  ▼
Grafana
  ├── Prometheus ── kube-state-metrics / node-exporter / ServiceMonitor
  │       └── PrometheusRule → Alertmanager → Webhook / 真实通知渠道
  └── Loki ◀──────── Grafana Alloy ◀── Kubernetes Pod logs
```

组件说明：

| 组件 | Helm Chart | 用途 |
| --- | --- | --- |
| Prometheus/Grafana | `kube-prometheus-stack` `87.19.1` | 指标采集、存储、查询和展示 |
| Alertmanager | 包含在 `kube-prometheus-stack` | 告警分组、抑制、路由和通知 |
| Loki | `loki` `7.1.0` | Kubernetes 日志存储和查询 |
| Alloy | `alloy` `1.11.0` | 通过 Kubernetes API 采集 Pod 日志 |
| Alert Webhook | 原生 Kubernetes 资源 | 本地接收通知并在日志中保留测试证据 |

所有业务 Service 均使用 `ClusterIP`。只有现有 Traefik 负责集群入口，避免多个 `LoadBalancer` 抢占 k3d 节点的 `80/443` 端口。

## 2. 当前环境

- 集群：k3d，名称为 `ai-lab`
- 节点：1 个 server、2 个 agent
- IngressClass：`traefik`
- StorageClass：`local-path`
- Grafana 地址：`http://grafana.localhost:8080`
- Alertmanager 地址：`http://alertmanager.localhost:8080`
- Prometheus指标保留时间：7 天
- Prometheus PVC：10 GiB
- Alertmanager PVC：2 GiB
- Loki PVC：10 GiB
- Grafana PVC：5 GiB
- Alertmanager：单副本，已启用

## 3. 前置检查

确认集群和节点正常：

```bash
kubectl cluster-info
kubectl get nodes
kubectl get storageclass
kubectl get ingressclass
```

预期存在：

```text
local-path   (default)
traefik
```

确认 k3d 已将 Traefik 入口映射至 Mac：

```bash
docker port k3d-ai-lab-serverlb
```

预期至少包含：

```text
80/tcp  -> 0.0.0.0:8080
443/tcp -> 0.0.0.0:8443
```

如果没有 HTTP 映射：

```bash
k3d cluster edit ai-lab --port-add "8080:80@loadbalancer"
```

## 4. 添加 Helm 仓库

```bash
helm repo add prometheus-community \
  https://prometheus-community.github.io/helm-charts

helm repo add grafana \
  https://grafana.github.io/helm-charts

helm repo update
```

## 5. 部署方式

### 5.1 推荐：由 Argo CD 持续管理

首次引导：

```bash
kubectl apply -f ../bootstrap/root-application.yaml
kubectl get applications -n argocd
```

此后配置变更流程为：

```text
修改 YAML → 本地渲染校验 → git commit → git push
→ Argo CD 比较 desired/live state → 自动同步 → 健康检查
```

不要再对已经由 Argo CD 管理的 release 手工执行 `helm upgrade`，否则会制造
绕过 Git 审计的配置漂移。下面的 Helm 命令仅用于理解安装过程、首次迁移或故障
恢复参考。

### 5.2 Prometheus 和 Grafana

```bash
helm upgrade --install monitoring \
  prometheus-community/kube-prometheus-stack \
  --version 87.19.1 \
  --namespace observability \
  --create-namespace \
  --values kube-prometheus-stack-values.yaml \
  --wait \
  --timeout 10m
```

配置重点：

- Grafana 使用 `ClusterIP`，通过 Traefik Ingress 暴露。
- Grafana 已预配置 Loki 数据源。
- Prometheus 指标保留 7 天，最大约 8 GB。
- 默认 k3s 未暴露部分控制平面指标端点，因此禁用了对应 ServiceMonitor。

### 5.3 Loki

```bash
helm upgrade --install loki \
  grafana/loki \
  --version 7.1.0 \
  --namespace observability \
  --values loki-values.yaml \
  --wait \
  --timeout 10m
```

本地环境使用单体模式和文件系统存储：

- `deploymentMode: SingleBinary`
- 单副本
- 10 GiB `local-path` PVC
- Loki Gateway 使用 `ClusterIP`
- 未启用分布式读写组件及缓存组件

该配置用于开发和实验环境，不适合作为生产高可用配置。

### 5.4 Alloy

```bash
helm upgrade --install alloy \
  grafana/alloy \
  --version 1.11.0 \
  --namespace observability \
  --values alloy-values.yaml \
  --wait \
  --timeout 10m
```

Alloy 使用单副本 Deployment。`loki.source.kubernetes` 通过 Kubernetes API 获取全集群 Pod 日志，因此不需要在每个节点各运行一份，避免重复采集和重复写入。

## 6. 部署验证

检查 Helm releases：

```bash
helm list -n observability
```

检查所有 Pod：

```bash
kubectl get pods -n observability -o wide
```

检查持久卷：

```bash
kubectl get pvc -n observability
```

预期存在四个 Bound PVC：

- `monitoring-grafana`
- Prometheus 数据卷
- Alertmanager 数据卷
- `storage-loki-0`

检查 Ingress：

```bash
kubectl get ingress -n observability
```

检查 Prometheus：

```bash
kubectl get prometheus -n observability
```

预期 `READY=1`、`AVAILABLE=True`。

检查 Alloy：

```bash
kubectl get pods -n observability \
  -l app.kubernetes.io/name=alloy

kubectl logs -n observability \
  -l app.kubernetes.io/name=alloy \
  -c alloy \
  --tail=100
```

检查 GitOps 状态：

```bash
kubectl get applications -n argocd
```

`ai-lab`、`monitoring`、`loki`、`alloy`、`alerting` 应全部为
`Synced / Healthy`。

## 7. 访问 Grafana

浏览器打开：

```text
http://grafana.localhost:8080
```

默认用户名：

```text
admin
```

获取自动生成的管理员密码：

```bash
kubectl get secret monitoring-grafana \
  --namespace observability \
  --output jsonpath='{.data.admin-password}' |
  base64 --decode
echo
```

如果 Ingress 暂时不可用，可以使用端口转发：

```bash
kubectl port-forward \
  --namespace observability \
  service/monitoring-grafana \
  3000:80
```

然后访问 `http://localhost:3000`。

## 8. 告警链路

### 8.1 控制流与数据流

```text
Git 中的 PrometheusRule
  ↓ Argo CD 同步
Prometheus Operator 校验并生成 Prometheus 规则配置
  ↓ 周期评估 PromQL
Prometheus 产生 pending / firing
  ↓ 发送告警实例
Alertmanager 分组、抑制、静默、路由
  ↓ webhook_configs
alert-webhook 接收 firing / resolved
```

Prometheus 决定“何时告警”，Alertmanager 决定“告警发给谁、何时合并发送”。
二者职责不同。

### 8.2 访问和检查

浏览器：

```text
http://alertmanager.localhost:8080
```

资源状态：

```bash
kubectl get alertmanager,pod,pvc,ingress -n observability
kubectl get prometheusrule ai-lab-alert-pipeline-test -n observability
kubectl logs deployment/alert-webhook -n observability
```

### 8.3 端到端测试

仓库中的测试规则默认不触发：

```promql
vector(0) == 1
```

测试时将表达式改为：

```promql
vector(1)
```

提交并推送，等待 `for: 30s` 和 Alertmanager 的 `group_wait`，然后检查 Webhook
日志，应出现 `status: firing`。测试完成后把表达式改回
`vector(0) == 1`，再次提交并推送，应出现 `status: resolved`。

注意：裸 `vector(0)` 仍返回一条样本值为 0 的时间序列。Prometheus 告警判断
结果向量是否为空，因此裸 `vector(0)` 仍会触发；比较表达式失败后返回空向量，
才会真正 resolve。

## 9. 使用 Grafana

### 8.1 查看监控

登录后打开 `Dashboards`。`kube-prometheus-stack` 已提供 Kubernetes、Node、Pod 和 Prometheus 等预置 Dashboard。

### 8.2 查看日志

打开 `Explore`，选择 `Loki` 数据源。

查询 Argo CD 的全部日志：

```logql
{namespace="argocd"}
```

查询 Argo CD Server：

```logql
{namespace="argocd", container="server"}
```

查询 observability 命名空间并过滤错误：

```logql
{namespace="observability"} |= "error"
```

查询指定 Pod：

```logql
{namespace="argocd", pod=~"argocd-server-.*"}
```

Alloy 第一次启动时可能尝试发送容器已有的历史日志。部分过旧或乱序日志可能被 Loki 拒绝，实时日志采集不受影响。

## 10. 配置更新

修改对应 values 或告警清单后，先本地渲染，再提交并推送，由 Argo CD 同步。

建议在更新 Chart 前先渲染检查：

```bash
helm template monitoring \
  prometheus-community/kube-prometheus-stack \
  --version 87.19.1 \
  --namespace observability \
  --values kube-prometheus-stack-values.yaml \
  >/dev/null

kubectl kustomize ../applications >/dev/null
kubectl kustomize alerting >/dev/null
```

升级前查看新版本：

```bash
helm search repo prometheus-community/kube-prometheus-stack --versions
helm search repo grafana/loki --versions
helm search repo grafana/alloy --versions
```

升级 Chart 版本时应同步修改本文和部署命令中的固定版本，并检查官方 release notes。

## 11. 常见问题

### Grafana 无法打开

```bash
kubectl get ingress -n observability
kubectl get pods -n observability
docker port k3d-ai-lab-serverlb
curl -I -H 'Host: grafana.localhost' http://127.0.0.1:8080
```

### Loki 数据源不可用

确认 Gateway：

```bash
kubectl get pod,service -n observability -l app.kubernetes.io/instance=loki
```

Grafana 内 Loki 数据源地址应为：

```text
http://loki-gateway.observability.svc.cluster.local
```

### 没有 Pod 日志

```bash
kubectl get pod -n observability -l app.kubernetes.io/name=alloy
kubectl logs -n observability -l app.kubernetes.io/name=alloy -c alloy
```

检查 Alloy 是否出现权限错误、Loki 连接错误或配置解析错误。

### Pod 长时间处于 ContainerCreating

首次安装需要拉取 Grafana、Prometheus、Loki 和 Alloy 镜像。在网络较慢时可检查：

```bash
kubectl get events -n observability --sort-by=.lastTimestamp
kubectl describe pod -n observability <pod-name>
```

## 12. 卸载

当前资源由 Argo CD 管理。直接执行 `helm uninstall` 后，Argo CD self-heal
可能重新创建资源。需要清理时，应先删除或禁用对应 Application，再处理 release
和 PVC。

如果已停止 Argo CD 管理，再卸载 Helm releases：

```bash
helm uninstall alloy -n observability
helm uninstall loki -n observability
helm uninstall monitoring -n observability
```

Helm 卸载后 PVC 可能保留。确认不再需要历史监控和日志数据后，再单独删除：

```bash
kubectl get pvc -n observability
kubectl delete pvc --all -n observability
```

最后删除命名空间：

```bash
kubectl delete namespace observability
```

删除 PVC 和 namespace 会导致本地监控与日志数据不可恢复，操作前应再次确认。

## 13. GitHub 维护建议

提交前检查：

```bash
git status
git diff --check
```

建议提交以下文件：

```text
gitops/observability/
├── README.md
├── alloy-values.yaml
├── kube-prometheus-stack-values.yaml
└── loki-values.yaml
```

建议提交信息：

```text
docs: add k3d observability deployment guide
```
