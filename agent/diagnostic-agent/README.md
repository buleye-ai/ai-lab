# 告警驱动的只读诊断 Agent

这个项目接收 Alertmanager Webhook，查询 Kubernetes、Prometheus 和 Loki，
最后输出一份“事实与推断分离”的结构化诊断报告。

第一版只做诊断，不修改集群。

## 目标链路

```text
Alertmanager firing / resolved
  ↓
Webhook 输入校验
  ↓
创建 trace_id
  ↓
只读查询 Kubernetes / Prometheus / Loki
  ↓
整理证据
  ↓
生成诊断假设和建议
  ↓
输出 DiagnosticReport
```

## 为什么先定义契约

如果不先确定输入和输出，后面很容易出现：

- Alertmanager 字段变化导致程序崩溃；
- 工具返回内容无限增长；
- LLM 把推断写成事实；
- 无法判断一次诊断是否成功；
- 没有稳定输入，无法做回归评测。

契约是 Agent Harness 的第一层边界。

## 目录

```text
diagnostic-agent/
├── Dockerfile
├── README.md
├── contracts/
│   ├── alertmanager-webhook.schema.json
│   └── diagnostic-report.schema.json
├── examples/
    ├── alertmanager-firing.json
    └── diagnostic-report.json
├── src/diagnostic_agent/
│   ├── config.py
│   ├── harness.py
│   ├── server.py
│   └── tools.py
└── tests/
    └── test_harness.py
```

## 输入契约

`contracts/alertmanager-webhook.schema.json` 只要求诊断必需字段，并允许
Alertmanager 增加额外字段。

第一版处理规则：

- `firing`：执行完整诊断；
- `resolved`：关闭对应事件，不重新执行完整诊断；
- 一次 Webhook 包含多个 alerts 时逐条处理；
- 优先从单条 alert labels 读取 `namespace`、`pod`、`container`；
- 缺少资源定位标签时输出“不足以定位”，不能猜测资源名称。

## 输出契约

诊断报告强制拆分：

- `evidence`：工具返回的事实；
- `hypotheses`：根据事实形成的根因假设；
- `recommended_actions`：建议动作；
- `tool_calls`：查询轨迹；
- `safety`：本次运行的权限和执行边界。

`automatic_action_taken` 第一版必须保持 `false`。

## 安全边界

第一版 Harness 必须保证：

1. Kubernetes 只允许 `get/list/watch`；
2. Prometheus 和 Loki 只允许查询；
3. namespace 必须在允许列表；
4. 日志查询必须限制时间和行数；
5. 每个工具都有超时；
6. 不读取 Secret 内容；
7. 不执行 shell；
8. 不调用 Kubernetes 写 API；
9. 不根据 LLM 文本直接执行操作；
10. 所有工具调用记录在 `tool_calls`。

## 当前阶段完成标准

```text
输入 Schema 可以描述 Alertmanager firing
输出 Schema 能区分证据、假设和建议
样例包含一个可追踪的完整诊断
automatic_action_taken = false
```

## 本地测试

项目运行时只使用 Python 标准库：

```bash
cd agent/diagnostic-agent

PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest discover -s tests -v
```

启动本地服务：

```bash
PYTHONPATH=src python3 -m diagnostic_agent.server
```

健康检查：

```bash
curl http://127.0.0.1:8080/healthz
```

发送样例告警：

```bash
curl \
  --request POST \
  --header 'Content-Type: application/json' \
  --data @examples/alertmanager-firing.json \
  http://127.0.0.1:8080/api/v1/alerts
```

本机进程没有 Pod ServiceAccount，Kubernetes 查询会失败并记录为失败的
`tool_call`；服务仍应返回结构化报告。完整查询需要部署到集群。

## 构建并导入 k3d

本地实验使用 `diagnostic-agent:dev`，不依赖外部镜像仓库：

```bash
docker build --tag diagnostic-agent:dev .
k3d image import diagnostic-agent:dev --cluster ai-lab
```

GitOps 清单位于：

```text
gitops/agent/diagnostic-agent/
```

镜像导入后，提交并推送 Git。根 Application 会创建 `diagnostic-agent`
Application，再由它部署工作负载。

## 运行时配置

| 环境变量 | 默认值 | 作用 |
| --- | --- | --- |
| `ALLOWED_NAMESPACES` | `demo,observability` | 允许诊断的命名空间 |
| `PROMETHEUS_URL` | 集群内 Prometheus Service | Prometheus 查询入口 |
| `LOKI_URL` | 集群内 Loki Gateway | Loki 查询入口 |
| `QUERY_TIMEOUT_SECONDS` | `5` | 单次工具调用超时 |
| `LOG_LOOKBACK_SECONDS` | `600` | 日志回看时间 |
| `LOG_LIMIT` | `50` | 最大日志行数，代码上限 200 |

`ALLOWED_NAMESPACES` 是应用层白名单，不等于 Kubernetes 授权。当前 Agent
Application 只在 `observability` 创建只读 RoleBinding；后续故障实验需要在
`demo` Namespace 中单独创建 RoleBinding。这样删除 Agent Application 时不会
误删或接管 `demo` Namespace。

## 当前诊断策略

第一版故意不调用 LLM，先建立可测试的确定性基线：

1. 校验 Alertmanager 输入；
2. 校验 namespace allowlist 和 Kubernetes 名称；
3. 查询 Pod 状态；
4. 查询最近十分钟重启增量；
5. 查询最近十分钟错误日志；
6. 根据 `CrashLoopBackOff`、`not found` 等信号生成基础假设；
7. 输出证据、假设、工具 Trace 和安全声明。

后续接入 LLM 时，模型只读取这组受限证据并生成解释，不能决定查询任意资源，
也不能执行 Kubernetes 写操作。确定性基线可以作为评测 LLM 是否真正提升诊断
质量的对照组。
