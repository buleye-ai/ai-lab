# 使用 Python 查看 Alertmanager Webhook 完整请求

## 1. 启动接收器

在 Mac 上运行：

```bash
python3 scripts/alertmanager_webhook.py
```

默认监听：

```text
0.0.0.0:18080
```

保存每次完整请求：

```bash
python3 scripts/alertmanager_webhook.py \
  --output /tmp/alertmanager-webhooks.jsonl
```

## 2. 本地模拟 Alertmanager

```bash
curl \
  --request POST \
  --header 'Content-Type: application/json' \
  --data @agent/diagnostic-agent/examples/alertmanager-firing.json \
  http://127.0.0.1:18080/alerts
```

终端会打印：

- 请求时间和来源；
- HTTP Headers；
- 原始 Body；
- 格式化 JSON；
- receiver 和 status；
- group/common labels；
- 每条告警的 labels、annotations、时间和 fingerprint。

## 3. 让 k3d 中的 Alertmanager 访问 Mac

Pod 中的 `127.0.0.1` 指向 Pod 自己，不是 Mac。本项目当前使用 OrbStack，
实测可达的宿主机地址是：

```text
host.docker.internal
```

Alertmanager Webhook URL 应配置为：

```text
http://host.docker.internal:18080/alerts
```

配置示例：

```yaml
alertmanager:
  config:
    receivers:
      - name: local-webhook
        webhook_configs:
          - url: http://host.docker.internal:18080/alerts
            send_resolved: true
```

不同容器运行时可能提供不同别名。不要直接假定
`host.k3d.internal`、`host.docker.internal` 或 `host.orb.internal` 一定可用，
应从 Pod 内先请求：

```text
http://<host-alias>:18080/healthz
```

当前环境实测：

```text
host.docker.internal → 可达
host.orb.internal    → 可达
host.k3d.internal    → 不可达
```

本项目由 Argo CD 管理，不能直接在集群中手工修改 Alertmanager Secret。应修改
`gitops/observability/kube-prometheus-stack-values.yaml`，提交 Git 后由 Argo CD
同步。

## 4. firing 和 resolved

`send_resolved: true` 表示同一告警恢复后还会发送一次：

```json
{
  "status": "resolved"
}
```

因此可以在 Python 终端中观察完整生命周期：

```text
Prometheus pending
→ firing
→ Alertmanager webhook status=firing
→ 故障恢复
→ Alertmanager webhook status=resolved
```

## 5. 安全提醒

脚本默认将以下 Header 脱敏：

- `Authorization`
- `Cookie`
- `Proxy-Authorization`
- `X-API-Key`

只有在完全受控的本地环境中，才使用：

```bash
python3 scripts/alertmanager_webhook.py \
  --show-sensitive-headers
```

不要把包含 Token、Cookie 或内部告警数据的 JSONL 文件提交到 Git。
