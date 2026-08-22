# Profiling Lab — Step 0 draft and security gate

**Status:** rendered and server-dry-run validated; intentionally **not registered** in the Argo CD app-of-apps and therefore not deployed.

## Intended PoC topology

```text
demo/checkout-demo + demo/inventory-demo
        │  (only filtered targets are exported)
        ▼
alloy-profiles-ebpf DaemonSet
  - one selected node: k3d-ai-lab-agent-0
  - hostPID + root + privileged (required by pyroscope.ebpf)
  - RBAC: get/list/watch Pods only, Role/RoleBinding in demo
        ▼
pyroscope StatefulSet
  - one replica, 5Gi local PVC, no MinIO/object storage, no ingress, no HA
        ▼
Grafana Pyroscope datasource (Step 3; not present in this draft)
```

## Explicitly out of scope

- No change to the existing logs/events `alloy` Deployment.
- No bundled Pyroscope Agent/Alloy, no pprof scrape annotations, no application code change.
- No `ClusterRole`, `ClusterRoleBinding`, node/secret/event/log permissions, Kubernetes mutation permission or automatic Argo sync.
- No all-node rollout: the DaemonSet has a hard node selector for `k3d-ai-lab-agent-0`.
- No deployment, commit, push, Argo registration, Grafana datasource or profile collection in Step 0.

## Safety fact that target filtering does **not** remove

`pyroscope.ebpf` requires `hostPID: true`, root and `privileged: true`. The relabel rules limit only which `demo` target profiles are forwarded. They do **not** make the agent unable to observe host-level process information. This is why the PoC is isolated, single-node, GitOps-reviewable and deliberately manually gated.

## Validation performed

```text
helm template pyroscope grafana/pyroscope --version 2.2.1 ...             PASS
helm template alloy-profiles-ebpf grafana/alloy --version 1.11.0 ...      PASS
Grafana Alloy v1.18.0 `fmt` against rendered Alloy config                 PASS
kubectl apply --dry-run=server against k3d-ai-lab (both renders)          PASS
app-of-apps kustomization excludes profiling-lab.yaml                      PASS
```

## Before Step 1 may be proposed

1. Review the generated manifests and accept the privileged/hostPID risk.
2. Confirm a short collection window, owner and stop condition.
3. Add `profiling-lab.yaml` to the app-of-apps only in an explicit, reviewed Git commit.
4. Verify Pyroscope readiness/PVC before allowing the eBPF agent to sync.
5. Keep the agent disabled until the backend and target selector are proven.
6. After any collection, remove/revert the Application and check Pod/SLI/Trace/Logs health.
