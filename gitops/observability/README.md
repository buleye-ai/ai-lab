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
  └── Loki ◀──────── Grafana Alloy ◀── Kubernetes Pod logs
```

组件说明：

| 组件 | Helm Chart | 用途 |
| --- | --- | --- |
| Prometheus/Grafana | `kube-prometheus-stack` `87.19.1` | 指标采集、存储、查询和展示 |
| Loki | `loki` `7.1.0` | Kubernetes 日志存储和查询 |
| Alloy | `alloy` `1.11.0` | 通过 Kubernetes API 采集 Pod 日志 |

所有业务 Service 均使用 `ClusterIP`。只有现有 Traefik 负责集群入口，避免多个 `LoadBalancer` 抢占 k3d 节点的 `80/443` 端口。

## 2. 当前环境

- 集群：k3d，名称为 `ai-lab`
- 节点：1 个 server、2 个 agent
- IngressClass：`traefik`
- StorageClass：`local-path`
- Grafana 地址：`http://grafana.localhost:8080`
- Prometheus指标保留时间：7 天
- Prometheus PVC：10 GiB
- Loki PVC：10 GiB
- Grafana PVC：5 GiB
- Alertmanager：本地环境暂未启用

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

## 5. 部署

在本目录执行以下命令。

### 5.1 Prometheus 和 Grafana

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

### 5.2 Loki

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

### 5.3 Alloy

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

预期存在三个 Bound PVC：

- `monitoring-grafana`
- Prometheus 数据卷
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

## 8. 使用 Grafana

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

## 9. 配置更新

修改对应 values 文件后，重新执行相同的 `helm upgrade --install` 命令。

建议在更新 Chart 前先渲染检查：

```bash
helm template monitoring \
  prometheus-community/kube-prometheus-stack \
  --version 87.19.1 \
  --namespace observability \
  --values kube-prometheus-stack-values.yaml \
  >/dev/null
```

升级前查看新版本：

```bash
helm search repo prometheus-community/kube-prometheus-stack --versions
helm search repo grafana/loki --versions
helm search repo grafana/alloy --versions
```

升级 Chart 版本时应同步修改本文和部署命令中的固定版本，并检查官方 release notes。

## 10. 常见问题

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

## 11. 卸载

卸载 Helm releases：

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

## 12. GitHub 维护建议

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
