# 本地 K3s GitOps 实验环境

本文记录在 macOS 上使用 k3d 运行 K3s，并部署 Argo CD、Traefik 入口和可观测性套件的完整过程。

## 1. 整体架构

```text
macOS
└── OrbStack / Docker
    └── k3d cluster: ai-lab
        ├── K3s server × 1
        ├── K3s agent × 2
        ├── k3d server load balancer
        ├── Traefik
        ├── Argo CD
        └── Observability
            ├── Prometheus
            ├── Alertmanager
            ├── Grafana
            ├── Loki
            ├── Alloy
            └── Alert Webhook（本地链路验证）
```

本地访问入口：

| 服务 | 地址 |
| --- | --- |
| Argo CD | `http://argocd.localhost:8080` |
| Grafana | `http://grafana.localhost:8080` |
| Alertmanager | `http://alertmanager.localhost:8080` |

## 2. 为什么使用 k3d

K3s 的节点组件运行在 Linux 上，不能直接作为原生进程安装到 macOS。k3d 将 K3s 节点运行在 Docker 兼容的 Linux 容器中，同时负责：

- 创建 K3s server 和 agent 节点；
- 建立集群 Docker 网络；
- 生成和合并 kubeconfig；
- 提供集群前置负载均衡容器；
- 将容器端口映射到 macOS。

本文中的 Kubernetes 实际发行版仍然是 K3s，k3d 是它在本地容器环境中的生命周期管理工具。

## 3. 安装本地工具

需要准备 Docker Desktop 或 OrbStack。确认 Docker API 可用：

```bash
docker version
```

使用 Homebrew 安装命令行工具：

```bash
brew install k3d kubectl helm
```

检查版本：

```bash
k3d version
kubectl version --client
helm version
```

## 4. 创建 K3s 集群

当前环境使用：

- 集群名：`ai-lab`
- K3s：`v1.35.5-k3s1`
- server：1
- agent：2
- HTTP：Mac `8080` → 集群入口 `80`
- HTTPS：Mac `8443` → 集群入口 `443`

创建集群：

```bash
k3d cluster create ai-lab \
  --image rancher/k3s:v1.35.5-k3s1 \
  --servers 1 \
  --agents 2 \
  --port "8080:80@loadbalancer" \
  --port "8443:443@loadbalancer" \
  --wait
```

如果不需要固定 K3s 版本，可以去掉 `--image` 参数。

验证集群：

```bash
k3d cluster list
kubectl cluster-info
kubectl get nodes -o wide
kubectl get pods -A
```

预期节点：

```text
k3d-ai-lab-server-0
k3d-ai-lab-agent-0
k3d-ai-lab-agent-1
```

检查存储类和 IngressClass：

```bash
kubectl get storageclass
kubectl get ingressclass
```

默认应存在：

```text
local-path
traefik
```

检查宿主机端口映射：

```bash
docker port k3d-ai-lab-serverlb
```

预期至少包含：

```text
80/tcp  -> 0.0.0.0:8080
443/tcp -> 0.0.0.0:8443
```

## 5. K3s ServiceLB 和 Traefik

K3s 默认包含 Traefik 和 ServiceLB。ServiceLB 会为 `LoadBalancer` Service 创建 `svclb-*` Pod，并通过节点 `hostPort` 暴露端口。

检查：

```bash
kubectl get pods -n kube-system
kubectl get pods -n kube-system | grep svclb
kubectl get service traefik -n kube-system
```

本环境只允许 Traefik 使用 `LoadBalancer`。Argo CD、Grafana、Loki 等服务必须保持 `ClusterIP`，由 Traefik 统一转发。

如果多个 Service 同时申请 `80/443`，对应 `svclb-*` Pod 会因为节点端口冲突而一直 `Pending`。排查命令：

```bash
kubectl describe pod -n kube-system <pending-svclb-pod>
```

## 6. 安装 Argo CD

### 6.1 添加 Helm 仓库

```bash
helm repo add argo https://argoproj.github.io/argo-helm
helm repo update
```

### 6.2 安装

当前固定版本：

| 项目 | 版本 |
| --- | --- |
| Helm Chart | `argo-cd-10.2.1` |
| Argo CD | `v3.4.5` |

执行：

```bash
helm upgrade --install argocd \
  argo/argo-cd \
  --version 10.2.1 \
  --namespace argocd \
  --create-namespace \
  --set server.service.type=ClusterIP \
  --set-string 'configs.params.server\.insecure=true' \
  --set server.ingress.enabled=true \
  --set server.ingress.ingressClassName=traefik \
  --set server.ingress.hostname=argocd.localhost \
  --wait \
  --timeout 10m
```

配置说明：

- `server.service.type=ClusterIP`：避免 Argo CD 与 Traefik 抢占节点 `80/443`。
- `server.insecure=true`：Traefik 到 Argo CD Server 使用 HTTP，适用于本地开发环境。
- `server.ingress.*`：由 Argo CD Helm Chart 直接创建 Traefik Ingress，不再维护独立的 Ingress YAML。

### 6.3 验证

```bash
helm list -n argocd
kubectl get pods -n argocd
kubectl get service argocd-server -n argocd
kubectl get ingress -n argocd
```

`argocd-server` 应为：

```text
TYPE: ClusterIP
EXTERNAL-IP: <none>
```

Ingress Host 应为：

```text
argocd.localhost
```

### 6.4 登录

浏览器打开：

```text
http://argocd.localhost:8080
```

用户名：

```text
admin
```

获取初始密码：

```bash
kubectl get secret argocd-initial-admin-secret \
  --namespace argocd \
  --output jsonpath='{.data.password}' |
  base64 --decode
echo
```

安装 Argo CD CLI 后也可以执行：

```bash
argocd admin initial-password -n argocd
```

该命令不是登录 Argo CD Server，而是使用当前 kubeconfig 直接读取
`argocd/argocd-initial-admin-secret`，因此不需要提供 Argo CD 地址，也不要求
提前建立 Argo CD CLI 登录会话。执行前只需确保当前 Kubernetes context
能够访问目标集群：

```bash
kubectl config current-context
kubectl get namespace argocd
```

它基本等价于：

```bash
kubectl get secret argocd-initial-admin-secret \
  --namespace argocd \
  --output jsonpath='{.data.password}' |
  base64 --decode
echo
```

真正使用 Argo CD CLI 管理 Application 时，需要连接 Argo CD Server 并登录。
当前环境通过 Traefik 提供 HTTP 入口，因此执行：

```bash
ARGOCD_PASSWORD="$(argocd admin initial-password -n argocd)"

argocd login argocd.localhost:8080 \
  --username admin \
  --password "$ARGOCD_PASSWORD" \
  --plaintext \
  --insecure \
  --grpc-web
```

参数说明：

- `--plaintext`：当前入口使用 HTTP，不启用客户端 TLS。
- `--insecure`：跳过当前本地实验入口的证书校验。
- `--grpc-web`：通过普通 Traefik Ingress 使用 Argo CD 的 gRPC-Web 接口。

验证 CLI 登录状态：

```bash
argocd account get-user-info
argocd app list
```

`argocd admin initial-password` 只能读取“安装时生成的初始密码”，不能读取后来
修改过的密码。即使初始 Secret 仍存在，管理员密码一旦被修改，读取到的初始密码
也不能再用于登录。初始 Secret 被删除后，该命令则会直接失败。

如果只需要从本机管理 Application，可以绕过 Argo CD Server 的登录认证，使用
Argo CD CLI 的 core 模式。core 模式通过 kubeconfig 直接操作 Kubernetes 中的
Argo CD CRD：

```bash
argocd app list --core
argocd app get monitoring --core
argocd app sync monitoring --core
```

core 模式要求当前 kubeconfig 有权限访问集群，默认命名空间还应指向 `argocd`。
为了不修改日常 context，可以创建临时 kubeconfig：

```bash
ARGOCD_CORE_KUBECONFIG="$(mktemp /tmp/ai-lab-argocd-kubeconfig.XXXXXX)"
kubectl config view --raw --flatten >"$ARGOCD_CORE_KUBECONFIG"
kubectl config set-context --current \
  --namespace=argocd \
  --kubeconfig "$ARGOCD_CORE_KUBECONFIG"

KUBECONFIG="$ARGOCD_CORE_KUBECONFIG" argocd app list --core
rm -f "$ARGOCD_CORE_KUBECONFIG"
```

如果 Ingress 不可用，可以临时端口转发：

```bash
kubectl port-forward \
  --namespace argocd \
  service/argocd-server \
  8081:80
```

然后访问 `http://localhost:8081`。

### 6.5 更新

更新配置时继续使用相同的 `helm upgrade --install` 命令。查看当前值：

```bash
helm get values argocd -n argocd
```

查看可用 Chart 版本：

```bash
helm search repo argo/argo-cd --versions
```

升级版本前应查看 Argo CD 和 Chart release notes。

## 7. 使用 Argo CD 接管监控、日志与告警

仓库采用 App of Apps：

```text
gitops/bootstrap/root-application.yaml
└── ai-lab Application
    └── gitops/applications/
        ├── monitoring Application
        ├── loki Application
        ├── alloy Application
        └── alerting Application
```

根 Application 只管理子 Application；子 Application 再分别管理 Helm Chart
或原生 Kubernetes 资源。Git 是期望状态，Kubernetes API 是实时状态，Argo CD
持续比较两者并执行同步。

首次引导只需在集群外执行一次：

```bash
kubectl apply -f gitops/bootstrap/root-application.yaml
```

检查：

```bash
kubectl get applications -n argocd
```

预期所有 Application 都为：

```text
Synced   Healthy
```

Monitoring、Loki 和 Alloy 使用 Argo CD multi-source Application：Chart 来自
上游 Helm 仓库，values 来自本 Git 仓库。修改 values 后应提交并推送 Git，
不再直接执行 `helm upgrade`。Alerting Application 管理本地 Webhook 和测试
`PrometheusRule`。

自动同步配置：

```yaml
automated:
  enabled: true
  prune: true
  selfHeal: true
```

- `enabled`：Git 变化后自动同步；
- `prune`：删除 Git 中已经移除的受管资源；
- `selfHeal`：集群资源被手工修改后，恢复成 Git 声明。

验证 self-heal：

```bash
kubectl scale deployment alloy -n observability --replicas=2
kubectl get deployment alloy -n observability --watch
```

Git 声明为 1 副本，Argo CD 应自动恢复为 1。该实验验证的是控制循环，而不是
`kubectl scale` 命令本身。

可观测性套件的完整安装、验证、访问、升级和卸载说明见：

[Observability 部署文档](observability/README.md)

部署后访问：

```text
http://grafana.localhost:8080
http://alertmanager.localhost:8080
```

## 8. 常用运维命令

查看全部 Helm releases：

```bash
helm list -A
```

查看异常 Pod：

```bash
kubectl get pods -A |
  grep -vE 'Running|Completed'
```

查看最近事件：

```bash
kubectl get events -A --sort-by=.lastTimestamp
```

查看 Traefik 路由：

```bash
kubectl get ingress -A
```

停止和启动集群：

```bash
k3d cluster stop ai-lab
k3d cluster start ai-lab
```

导出 kubeconfig：

```bash
k3d kubeconfig get ai-lab
```

## 9. 环境清理

卸载 Argo CD：

```bash
helm uninstall argocd -n argocd
kubectl delete namespace argocd
```

删除整个 k3d 集群：

```bash
k3d cluster delete ai-lab
```

删除集群会一并删除集群内工作负载和基于 `local-path` 的本地数据，操作不可恢复。

## 10. 仓库结构

```text
gitops/
├── README.md
├── bootstrap/
│   └── root-application.yaml
├── applications/
│   ├── kustomization.yaml
│   ├── monitoring.yaml
│   ├── loki.yaml
│   ├── alloy.yaml
│   └── alerting.yaml
└── observability/
    ├── README.md
    ├── alloy-values.yaml
    ├── kube-prometheus-stack-values.yaml
    ├── loki-values.yaml
    └── alerting/
        ├── kustomization.yaml
        ├── prometheus-rule.yaml
        └── webhook-receiver.yaml
```

Argo CD 使用 Helm 参数直接创建 `ClusterIP` Service 和 Traefik Ingress，因此不再保存自动生成的安装清单或独立 Ingress 文件。

## 11. 提交到 GitHub

检查变更：

```bash
git status
git diff --check
```

暂存：

```bash
git add gitops
```

建议提交信息：

```text
docs: document k3s argocd and observability setup
```
