# Argo CD 掌握教程：从会部署到理解 GitOps 控制循环

本文面向已经熟悉 Linux、Kubernetes、Helm 和 Git 的工程师。目标不是记住
Argo CD 命令，而是建立一套能设计、实验、排障和用于生产决策的知识体系。

本文实验基于：

- macOS + k3d；
- K3s `v1.35.5+k3s1`；
- Argo CD `v3.4.5`；
- 一个 server、两个 agent；
- Traefik 统一提供 Ingress。

项目中的安装和环境重建步骤见 [K3s GitOps 实验环境](README.md)。

## 1. 学完后的掌握标准

你应该能够独立回答：

1. Argo CD 解决了什么问题？
2. Git、Argo CD 和 Kubernetes 分别保存什么状态？
3. repo-server、application-controller、argocd-server 各自负责什么？
4. `Synced` 与 `Healthy` 有什么区别？
5. 自动同步、self-heal 和 prune 分别在什么情况下触发？
6. Helm Chart 和自有 values 如何组合？
7. App of Apps 为什么需要一次 bootstrap？
8. 已有 Helm release 如何安全交给 Argo CD？
9. Application 卡在 `OutOfSync`、`Progressing` 或 `Degraded` 时怎么查？
10. 为什么 GitOps 适合作为未来 Agent 的受控执行面？

真正掌握的证据不是 UI 上出现绿色，而是你能预测系统行为、制造故障、找到证据，
并解释为什么恢复。

## 2. 先理解 GitOps

### 2.1 传统发布的问题

传统流程通常是：

```text
工程师或 CI
  ↓
helm upgrade / kubectl apply
  ↓
Kubernetes
```

它可以自动化，但仍存在几个问题：

- 集群里的修改不一定进入 Git；
- 人可以绕过 CI 直接修改资源；
- 很难确认 Git 和集群是否一致；
- 发布工具执行完成，不代表应用健康；
- 配置漂移发生后不会自动纠正。

### 2.2 GitOps 的模型

Argo CD 将流程改为：

```text
工程师
  ↓ commit / pull request
Git：保存期望状态
  ↓ 持续观察
Argo CD：比较并收敛
  ↓ Kubernetes API
集群：保存实际状态
```

这里有两个重要概念：

- `desired state`：Git 经过 Helm、Kustomize 或 YAML 渲染后得到的期望资源；
- `live state`：Kubernetes API 当前保存的实际资源。

Argo CD 持续执行：

```text
读取 desired state
→ 读取 live state
→ 计算 Diff
→ 同步差异
→ 检查健康状态
→ 再次比较
```

这和 Kubernetes Controller 的思想一致：

```text
observe → compare → act → observe
```

所以 Argo CD 本质上是运行在 Kubernetes 上的另一组控制器。

## 3. Argo CD 核心组件

### 3.1 repo-server：生成期望状态

repo-server 负责：

- 拉取 Git；
- 下载 Helm Chart；
- 执行 `helm template`；
- 执行 Kustomize；
- 读取普通 YAML；
- 将最终 manifests 返回给 application-controller。

它负责“算出应该部署什么”，但不直接修改业务集群。

### 3.2 application-controller：比较和收敛

application-controller 负责：

- 监听 Application；
- 请求 repo-server 生成期望资源；
- 查询目标 Kubernetes 集群；
- 比较 desired state 和 live state；
- 执行同步、self-heal 和 prune；
- 维护 Application 的 Sync 与 Health 状态；
- 记录同步历史和操作结果。

它是 Argo CD 的核心控制循环。

### 3.3 argocd-server：提供操作入口

argocd-server 提供：

- Web UI；
- API；
- CLI 登录；
- SSO 和身份认证；
- RBAC 检查；
- Application 查询和操作入口。

argocd-server 不可访问时，后台 application-controller 仍可能继续执行同步。
“管理入口不可用”和“GitOps 控制循环停止”是两个不同故障。

### 3.4 其他常见组件

- Redis：缓存仓库、应用和集群相关数据；
- Dex：可选的 OIDC 身份代理；
- ApplicationSet Controller：批量生成 Application；
- Notifications Controller：发送同步、健康和变更通知。

## 4. Application 是什么

Application 是一个 Kubernetes CR，描述四件事：

```text
从哪里读取 → 读取哪个版本 → 部署到哪里 → 如何同步
```

最小示例：

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: demo
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/example/demo.git
    targetRevision: main
    path: deploy
  destination:
    server: https://kubernetes.default.svc
    namespace: demo
  syncPolicy:
    syncOptions:
      - CreateNamespace=true
```

逐项理解：

| 字段 | 含义 |
| --- | --- |
| `metadata.namespace` | Application CR 自己保存在哪，通常是 `argocd` |
| `project` | 使用哪个 AppProject 约束仓库、集群、命名空间和资源类型 |
| `repoURL` | Git 或 Helm 仓库 |
| `targetRevision` | Git 分支、标签、提交或 Chart 版本 |
| `path` | Git 仓库中的资源目录 |
| `destination.server` | 目标 Kubernetes API |
| `destination.namespace` | 业务资源默认部署命名空间 |
| `CreateNamespace=true` | 目标命名空间不存在时自动创建 |

注意两个 namespace 不同：

```text
metadata.namespace = argocd
  Application CR 保存的位置

destination.namespace = demo
  业务资源部署的位置
```

## 5. 完成第一个 Application

### 5.1 准备目录

示例：

```text
demo/
├── deployment.yaml
├── service.yaml
└── kustomization.yaml
```

在提交前本地验证：

```bash
kubectl kustomize demo
kubectl apply --dry-run=client -k demo
```

### 5.2 创建 Application

```bash
kubectl apply -f demo-application.yaml
kubectl get application demo -n argocd
```

如果没有开启自动同步，第一次通常会看到：

```text
OutOfSync
```

这不是故障，只表示 Git 中存在资源，而集群尚未与之同步。

### 5.3 手工同步

使用 Argo CD Server：

```bash
argocd app diff demo
argocd app sync demo
argocd app wait demo --health
```

如果不使用 Argo CD Server 登录，可以通过 kubeconfig 使用 core 模式：

```bash
argocd app diff demo --core
argocd app sync demo --core
argocd app get demo --core
```

core 模式直接操作 Argo CD CRD，要求当前 kubeconfig 有集群权限，并指向
`argocd` 命名空间。

## 6. 正确理解 Sync 和 Health

### 6.1 Sync Status

Sync 回答：

> Git 生成的期望资源和集群实际资源是否一致？

常见状态：

- `Synced`：一致；
- `OutOfSync`：存在差异；
- `Unknown`：无法完成比较。

### 6.2 Health Status

Health 回答：

> 资源当前是否正常运行？

常见状态：

- `Healthy`：正常；
- `Progressing`：正在启动或滚动更新；
- `Degraded`：资源存在但运行异常；
- `Missing`：期望资源不存在；
- `Suspended`：资源被暂停；
- `Unknown`：Argo CD 无法判断。

典型组合：

| 状态 | 含义 |
| --- | --- |
| `Synced / Healthy` | 配置一致，运行正常 |
| `OutOfSync / Healthy` | 应用仍正常，但 Git 与集群存在差异 |
| `Synced / Progressing` | 配置已应用，Pod 等资源仍在启动 |
| `Synced / Degraded` | Git 配置已经应用，但配置本身运行失败 |

不要把 `Synced` 当成应用可用性的证明。

## 7. 自动同步、自愈与裁剪

配置：

```yaml
syncPolicy:
  automated:
    enabled: true
    prune: true
    selfHeal: true
```

### 7.1 enabled

Git revision 或 Application 参数变化，导致 Application `OutOfSync` 时自动同步。

```text
Git replicas: 1 → 2
→ Argo CD 读取新提交
→ 自动把集群改成 2
```

### 7.2 selfHeal

集群被绕过 Git 修改时，恢复 Git 中声明的状态：

```text
Git replicas = 1
kubectl scale replicas = 2
→ Argo CD 检测 live drift
→ 恢复为 1
```

官方默认 self-heal 重试间隔是 5 秒。我们在 Alloy 实验中也观察到了约 5 秒的
恢复时间。

### 7.3 prune

Git 中删除受管资源后，也删除集群里的对应资源：

```text
Git 中删除 Service
→ Service 成为多余受管资源
→ Argo CD 删除 Service
```

prune 具有破坏性。生产环境应配合：

- PR 审批；
- AppProject；
- Sync Window；
- 关键资源删除保护；
- PVC 和 CRD 的单独策略；
- 备份与恢复演练。

## 8. 必做实验：证明 self-heal

### 8.1 预测

仓库中 Alloy 声明一个副本：

```text
desired replicas = 1
```

如果手工把 live state 改成 2，Argo CD 应在发现漂移后恢复为 1。

### 8.2 制造漂移

```bash
kubectl scale deployment alloy \
  --namespace observability \
  --replicas=2
```

观察：

```bash
kubectl get deployment alloy \
  --namespace observability \
  --watch
```

同时检查：

```bash
kubectl get application alloy -n argocd --watch
```

### 8.3 验证结果

当前实验记录：

```text
16:15:48  desired replicas = 2
16:15:53  desired replicas = 1
```

这证明：

1. Kubernetes 接受了手工修改；
2. Argo CD 发现 live state 与 Git 不一致；
3. self-heal 发起同步；
4. Deployment 恢复 Git 声明；
5. GitOps 控制循环真实运行。

## 9. 使用 Helm Chart

单一 Helm source 示例：

```yaml
spec:
  source:
    repoURL: https://charts.example.com
    chart: demo
    targetRevision: 1.2.3
    helm:
      releaseName: demo
      valueFiles:
        - values.yaml
```

Argo CD 不会在集群里执行一个持续运行的 Helm 客户端。流程是：

```text
repo-server 下载 Chart
→ helm template
→ 得到 Kubernetes YAML
→ application-controller 同步 YAML
```

Helm 在这里主要是模板生成器，部署控制权属于 Argo CD。

## 10. Multi-source：官方 Chart + 自有 values

当前 Monitoring Application：

```yaml
sources:
  - repoURL: https://prometheus-community.github.io/helm-charts
    chart: kube-prometheus-stack
    targetRevision: 87.19.1
    helm:
      releaseName: monitoring
      valueFiles:
        - $values/gitops/observability/kube-prometheus-stack-values.yaml

  - repoURL: https://github.com/buleye-ai/ai-lab.git
    targetRevision: main
    ref: values
```

工作方式：

```text
Source 1：上游 Helm Chart
Source 2：自己的 Git 仓库，命名为 values
                    ↓
$values/... 引用 Source 2 中的文件
                    ↓
repo-server 合并输入并渲染 manifests
```

优点：

- 不需要复制上游 Chart；
- values 可以接受 Git 审计；
- Chart 版本明确固定；
- 上游升级与本地配置分离。

不要用 `sources` 把大量无关应用塞进一个 Application。官方建议无关应用使用
ApplicationSet 或 App of Apps。

## 11. App of Apps

当前根 Application：

```yaml
metadata:
  name: ai-lab
spec:
  source:
    repoURL: https://github.com/buleye-ai/ai-lab.git
    targetRevision: main
    path: gitops/applications
  destination:
    server: https://kubernetes.default.svc
    namespace: argocd
```

仓库结构：

```text
gitops/
├── bootstrap/
│   └── root-application.yaml
├── applications/
│   ├── monitoring.yaml
│   ├── loki.yaml
│   ├── alloy.yaml
│   └── alerting.yaml
└── observability/
    ├── *-values.yaml
    └── alerting/
```

控制关系：

```text
ai-lab 根 Application
  ├── monitoring Application → Prometheus / Grafana / Alertmanager
  ├── loki Application       → Loki
  ├── alloy Application      → Alloy
  └── alerting Application   → PrometheusRule / Webhook
```

根 Application 管理子 Application，子 Application 管理业务资源。

### 为什么需要 bootstrap

Argo CD 尚未知道 Git 仓库时，无法从 Git 创建第一个 Application。因此需要一次：

```bash
kubectl apply -f gitops/bootstrap/root-application.yaml
```

之后新增、修改和删除子 Application 都通过 Git 完成。

生产环境的 bootstrap 可以交给 Terraform、Cluster API 或平台初始化流程。

## 12. 接管已有 Helm Release

不要直接删除旧 release 再让 Argo CD 重建。安全接管步骤：

1. 记录现有 Chart 版本、releaseName、namespace 和 values；
2. 创建对应 Application；
3. 固定相同 Chart 版本；
4. 保持相同 releaseName 和 namespace；
5. 暂时关闭自动同步；
6. 执行本地 `helm template`；
7. 查看 Argo CD Diff；
8. 排除随机值、不可变字段和危险删除；
9. 手工执行第一次同步；
10. 验证 Pod、PVC、Service 和 Ingress；
11. 再开启 automated、selfHeal 和 prune。

接管的本质不是把 Helm release “导入 Argo CD 数据库”，而是让 Argo CD 渲染出
同名资源、接管字段和资源跟踪。

### 随机值陷阱

Grafana Chart 可能生成随机管理员密码：

```text
第一次渲染：password=A
第二次渲染：password=B
```

同样的 Git 输入产生不同输出，会造成永久 Diff。当前环境改为引用已有 Secret：

```yaml
grafana:
  admin:
    existingSecret: monitoring-grafana
```

GitOps 要求渲染尽量确定：

> 相同输入应该得到相同输出。

## 13. 资源跟踪和字段所有权

Argo CD 会跟踪资源属于哪个 Application，例如：

```yaml
argocd.argoproj.io/tracking-id:
  alerting:monitoring.coreos.com/PrometheusRule:observability/ai-lab-alert-pipeline-test
```

它用于判断：

- 资源属于哪个 Application；
- 哪些资源需要更新；
- Git 删除后哪些资源需要 prune；
- 哪些资源可能是 orphan。

当前 Helm Application 还启用了：

```yaml
syncOptions:
  - ServerSideApply=true
```

Server-Side Apply 由 API Server 维护字段所有权，适合大型 CRD 和多控制器共同管理
的资源，但仍可能出现 field manager conflict，需要通过 `managedFields` 判断冲突方。

## 14. 同步顺序：Phase、Hook 和 Wave

### 14.1 Hook Phase

常用 Hook：

| Hook | 执行时机 |
| --- | --- |
| `PreSync` | 主资源同步前，例如数据库迁移 |
| `Sync` | 主同步阶段 |
| `PostSync` | 同步成功且资源健康后 |
| `SyncFail` | 同步失败后 |
| `PreDelete` | 删除整个 Application 资源前 |
| `PostDelete` | 删除完成后 |

示例：

```yaml
metadata:
  annotations:
    argocd.argoproj.io/hook: PreSync
    argocd.argoproj.io/hook-delete-policy: HookSucceeded
```

### 14.2 Sync Wave

同一阶段中通过整数控制顺序：

```yaml
metadata:
  annotations:
    argocd.argoproj.io/sync-wave: "-1"
```

顺序：

```text
phase
→ wave，从小到大
→ kind
→ name
```

不要用大量 wave 掩盖错误的应用边界。跨应用的复杂依赖，更适合通过服务重试、
健康检查和向后兼容设计解决。

## 15. 一次 Git 变更的完整路径

以修改 Alertmanager values 为例：

```text
1. 修改 values
2. 本地 helm template 校验
3. git commit / push
4. application-controller 进入 reconciliation
5. repo-server 获取 Git 和 Helm Chart
6. repo-server 渲染 manifests
7. application-controller 读取 live resources
8. 计算 desired/live Diff
9. Application 变为 OutOfSync
10. automated sync 更新 Kubernetes
11. Prometheus Operator 看到 Alertmanager CR 变化
12. Operator 创建或更新 StatefulSet
13. Pod 启动并通过健康检查
14. Application 变为 Synced / Healthy
```

这里存在两层控制循环：

```text
Argo CD
  → 让 Alertmanager CR 与 Git 一致

Prometheus Operator
  → 让 StatefulSet、Pod 与 Alertmanager CR 一致
```

Argo CD 管理声明，Operator 实现领域逻辑。

## 16. 标准排障路径

排障时沿控制流从上往下查，不要一开始就随机重启 Pod。

### 16.1 Git revision 是否被读取

```bash
kubectl get application monitoring \
  --namespace argocd \
  --output jsonpath='{.status.sync.revision}'
echo
```

多源 Application 还应确认每个 source 的 revision。

### 16.2 Application 状态和条件

```bash
kubectl get applications -n argocd
kubectl describe application monitoring -n argocd
argocd app get monitoring --core
```

重点看：

- sync status；
- health status；
- conditions；
- operation state；
- sync result；
- resource message。

### 16.3 查看 Diff

```bash
argocd app diff monitoring --core
```

常见原因：

- Chart 随机值；
- admission webhook 增加默认字段；
- Operator 重写字段；
- HPA 修改 replicas；
- 字段顺序或类型变化；
- tracking annotation；
- Server-Side Apply 字段冲突。

### 16.4 repo-server

```bash
kubectl logs deployment/argocd-repo-server \
  --namespace argocd
```

重点查：

- Git 凭据；
- DNS 和网络；
- Chart 仓库；
- targetRevision；
- values 路径；
- Helm 渲染错误；
- Kustomize 构建错误。

### 16.5 application-controller

```bash
kubectl logs statefulset/argocd-application-controller \
  --namespace argocd
```

重点查：

- Kubernetes RBAC；
- apply 冲突；
- webhook 拒绝；
- hook 失败；
- finalizer；
- prune 失败；
- 健康检查。

### 16.6 业务资源

Application 已经 Synced，但 Health 异常时进入 Kubernetes 层：

```bash
kubectl get pods -n observability
kubectl get events -n observability --sort-by=.lastTimestamp
kubectl describe pod <pod-name> -n observability
kubectl logs <pod-name> -n observability
```

### 16.7 状态与排查方向

| 状态 | 优先检查 |
| --- | --- |
| `Unknown` | repo-server、仓库、渲染、集群连接 |
| `OutOfSync` | Diff、自动同步策略、字段漂移 |
| `Progressing` | rollout、probe、镜像拉取、PVC |
| `Degraded` | Pod、Operator、事件、应用日志 |
| Sync Failed | operationState、hook、RBAC、webhook |

## 17. 生产环境设计

本地实验可以使用：

- `default` AppProject；
- HTTP；
- 单个管理员；
- main 分支自动同步；
- 单集群；
- 本地 `local-path`；
- 宽松权限。

生产环境至少应考虑：

### 身份与权限

- OIDC/SSO；
- 禁用或严格管理 admin；
- Argo CD RBAC；
- 每个团队独立 AppProject；
- 仓库、集群、命名空间白名单；
- 控制可部署的资源类型。

### Git 和发布

- 保护主分支；
- Pull Request 审批；
- CODEOWNERS；
- commit 签名；
- 固定 Chart 和镜像版本；
- promotion 分支或环境目录；
- 避免长期跟踪 `latest` 或不受控的 `main`。

### 同步安全

- Sync Window；
- 高风险环境先手工同步；
- prune 删除保护；
- PVC、Namespace 和 CRD 单独控制；
- PreSync 数据库迁移；
- PostSync 冒烟验证；
- 明确回滚和 forward-fix 策略。

### 可观测性和恢复

- 监控 Argo CD 自身；
- 同步失败通知；
- repo-server 与 controller 容量规划；
- Git、集群凭据和配置备份；
- 灾难恢复演练；
- 避免把 Git 中的 Secret 明文作为期望状态。

Secret 可使用 External Secrets、Sealed Secrets、SOPS 或 Vault 等方案管理。

## 18. Argo CD 与 CI 的边界

Argo CD 主要负责 CD，不替代 CI。

合理分工：

```text
CI
├── 单元测试
├── 安全扫描
├── 构建镜像
├── 推送 Registry
└── 更新 GitOps 仓库中的镜像版本

Argo CD
├── 读取 Git
├── 比较集群状态
├── 发布
├── 自愈
└── 报告同步和健康状态
```

CI 不需要获得生产集群写权限。它只需要提交或创建 PR，Argo CD 在集群侧拉取并
执行发布。

## 19. Argo CD 与 Agent 的结合

未来运维 Agent 的安全闭环可以设计为：

```text
Alertmanager 触发事件
→ Agent 查询 Kubernetes、Prometheus、Loki、Argo CD
→ 形成证据和根因假设
→ 创建 Git 修改或 Pull Request
→ 人工或策略审批
→ Argo CD 同步
→ Agent 验证指标、日志和告警恢复
```

Argo CD 为 Agent 提供：

- 受约束的动作面；
- Git 审计；
- PR 审批；
- 可重复部署；
- 状态收敛；
- 回滚依据；
- 变更后的验证入口。

初期不要给 Agent 无限制的 `kubectl` 写权限。优先让 Agent 提交 Git 变更，
通过 Harness 管理审批、超时、权限、追踪和评测。

## 20. 建议练习路线

按顺序完成：

### 练习一：状态观察

- 修改 Git 中 Deployment 副本数；
- 预测 Application 状态变化；
- 观察 `OutOfSync → Synced`；
- 解释 Git 变化和 live drift 的区别。

### 练习二：self-heal

- 使用 `kubectl scale` 制造漂移；
- 记录发现与恢复时间；
- 暂时关闭 self-heal 再重复；
- 比较两次行为。

### 练习三：prune

- 创建一个无状态 ConfigMap；
- 同步后从 Git 删除；
- 观察 prune；
- 不要使用 PVC、Namespace 或有价值资源。

### 练习四：失败同步

- 故意配置不存在的镜像；
- 观察 `Synced / Degraded` 或 `Progressing`；
- 从 Argo CD 追到 Deployment、Pod 和 Event；
- 修复 Git 并验证恢复。

### 练习五：Hook 和 Wave

- 增加 PreSync Job；
- 增加 PostSync 检查；
- 制造 Hook 失败；
- 观察同步停止和操作记录。

### 练习六：AppProject

- 限制只允许部署到 `observability`；
- 禁止创建 Namespace；
- 尝试越权 Application；
- 从拒绝信息理解平台权限边界。

完成这些实验后，才算从“会操作 Argo CD”进入“能够设计和治理 Argo CD”。

## 21. 面试中的两分钟解释

可以这样表达：

> Argo CD 是 Kubernetes 原生的 GitOps 持续交付控制器。它通过 repo-server
> 将 Git、Helm 或 Kustomize 渲染为期望状态，由 application-controller
> 持续比较期望状态和集群实际状态，并根据同步策略执行自动同步、自愈和裁剪。
> Sync Status 描述 Git 与集群是否一致，Health Status 描述工作负载是否正常。
> 我在本地 k3d 环境中用 App of Apps 管理 Monitoring、Loki、Alloy 和 Alerting，
> 使用 multi-source 组合上游 Helm Chart 与自有 values，并通过手工修改 Alloy
> 副本数验证了约 5 秒的 self-heal。生产设计上，我会用 AppProject、RBAC、
> PR 审批、Sync Window 和删除保护限制自动化风险。

## 22. 官方资料

- [Argo CD 官方文档](https://argo-cd.readthedocs.io/)
- [Automated Sync Policy](https://argo-cd.readthedocs.io/en/latest/user-guide/auto_sync/)
- [Multiple Sources](https://argo-cd.readthedocs.io/en/stable/user-guide/multiple_sources/)
- [Sync Phases and Waves](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-waves/)
- [Application Specification](https://argo-cd.readthedocs.io/en/stable/user-guide/application-specification/)
- [AppProject](https://argo-cd.readthedocs.io/en/stable/user-guide/projects/)

## 23. 最后的心智模型

记住这一条主线即可：

```text
Git 保存“应该是什么”
Kubernetes 保存“现在是什么”
repo-server 负责“算出应该是什么”
application-controller 负责“比较并修正”
argocd-server 负责“让人和系统操作 Argo CD”
Operator 负责“把领域 CR 继续实现成底层资源”
```

掌握 Argo CD 的核心，不是能点 Sync，而是能从 Git revision 一直追踪到最终 Pod，
知道每一层控制器观察什么、修改什么，以及失败证据在哪里。
