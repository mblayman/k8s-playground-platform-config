# Kubernetes Playground Platform Plan

## Goal

Build a serious local Kubernetes playground that uses mature, well-tested Istio sidecar mode for service mesh capabilities, while avoiding Cilium for now to keep the first platform iteration focused.

## Target Stack

- Cluster: multi-node kind
- CNI: kindnet, the default kind CNI
- Service mesh: Istio sidecar mode
- Ingress: Istio ingress gateway with Gateway API
- LoadBalancer support: MetalLB
- TLS: cert-manager
- GitOps: Argo CD
- Observability: Prometheus, Grafana, Kiali
- Policy: Istio mTLS and AuthorizationPolicy first; Kubernetes NetworkPolicy later

## Current Progress

- Decided to keep kind bootstrap configuration in this repo for now instead of creating a separate infrastructure repo.
- Decided that a future infrastructure repo should be reserved for Terraform/OpenTofu and cloud-provider resources such as IAM, managed Kubernetes clusters, DNS zones, and cloud networking.
- Created `clusters/kind/cluster.yaml` for a multi-node kind cluster with 1 control-plane node and 2 worker nodes.
- Created top-level `mise.toml` as the command home for local workflows.
- Added `mise` tasks for kind cluster create, delete, node listing, and status checks.
- Removed the kind README command file in favor of `mise` tasks.
- Decided to use `k8s-playground-service` as an early tracer bullet before installing Istio.
- Decided the first externally reachable app version may use a direct `Service` of type `LoadBalancer` with no TLS, then evolve to Istio ingress and Gateway API later.

Current local cluster tasks:

```sh
mise run kind:create
mise run kind:delete
mise run kind:nodes
mise run kind:status
```

The tasks use `mise` task arguments with defaults so the rendered commands show concrete values, for example:

```sh
kind delete cluster --name k8s-playground
kubectl --context kind-k8s-playground get nodes -o wide
```

## Explicit Non-Goals For First Iteration

- No Cilium
- No service mesh ambient mode
- No Istio before the first app tracer bullet is running
- No production-grade external DNS automation
- No TLS for the first app tracer bullet
- No distributed tracing on day one
- No advanced Envoy customization
- No complex egress gateway design yet

## Rationale

Istio alone is a substantial platform layer. Starting with sidecar mode gives the most mature and well-documented Istio path, including strong L7 policy, stable observability integrations, and a large body of operational guidance.

Skipping Cilium keeps the first platform iteration focused on learning and operating Istio without introducing a second major networking system at the same time.

## What We Give Up By Skipping Cilium

- Stronger NetworkPolicy implementation
- Hubble network observability
- Advanced eBPF networking features
- Optional transparent network encryption
- More production-like CNI behavior

## What We Keep With Istio

- Workload-to-workload mTLS
- Workload identity based on service accounts
- L7 authorization policy
- Traffic routing through Gateway API and Istio proxies
- Ingress gateway control
- Service telemetry
- Kiali mesh graph support
- Prometheus metrics

## Platform Architecture

### Cluster

Use a new multi-node kind cluster instead of continuing to invest in the current single-node cluster.

Initial shape:

```text
1 control-plane node
2 worker nodes
```

Current config path:

```text
clusters/kind/cluster.yaml
```

The control-plane node runs the Kubernetes control-plane components, such as the API server, scheduler, controller manager, and etcd. Worker nodes are where ordinary workloads should generally run.

The control-plane node is still a Kubernetes `Node`, but it normally has a `NoSchedule` taint so normal application pods avoid it unless they explicitly tolerate that taint. This gives us a more realistic cluster shape without requiring every workload to specify node placement manually.

This allows testing of:

- Cross-node pod traffic
- Gateway behavior
- Workload rescheduling
- Multi-replica services
- Istio sidecar behavior across nodes

### LoadBalancer Support

Use MetalLB for LoadBalancer support.

We are intentionally not using cloud-provider-kind. The playground should model the more general Kubernetes `Service` type `LoadBalancer` behavior that applies outside of kind-specific tooling.

LoadBalancer support should be installed before Istio because it gives us a simple external-access validator. The first tracer-bullet version of `k8s-playground-service` can be exposed directly with a temporary `Service` of type `LoadBalancer`.

This direct LoadBalancer exposure is not the final north/south architecture. It exists to prove that the cluster, MetalLB, service selection, and app are working before adding Istio.

### TLS

Install cert-manager early so TLS is part of the platform model from the start.

For local development, start with:

- Self-signed root
- ClusterIssuer
- Certificate for a local app domain pattern such as `*.apps.k8s-playground.local`

### Istio

Install Istio in sidecar mode.

Istio should be added after the first app tracer bullet is running. The app gives us a concrete validator for each platform layer added after that point.

Required components:

- `istio-base`
- `istiod`
- `istio-ingressgateway`

Recommended component:

- `istio-cni`

Istio CNI is separate from the cluster CNI. It does not replace kindnet. It helps Istio configure pod traffic redirection without requiring privileged init containers in every workload pod.

### Sidecar Injection

Use revision-based injection instead of plain `istio-injection=enabled`.

Preferred namespace label pattern:

```yaml
istio.io/rev: stable
```

This gives a cleaner future upgrade path because multiple Istio control plane revisions can be installed and workloads can be moved intentionally.

### mTLS

Use strict mTLS for app namespaces.

Start per namespace rather than mesh-wide:

```yaml
apiVersion: security.istio.io/v1
kind: PeerAuthentication
metadata:
  name: default
  namespace: app-namespace
spec:
  mtls:
    mode: STRICT
```

### Authorization

Use Istio AuthorizationPolicy as the first policy layer.

Baseline model:

- Default deny in app namespaces
- Explicitly allow traffic between service accounts
- Explicitly allow ingress gateway to call exposed apps
- Explicitly allow monitoring to call health endpoints

Example intent:

```text
ingress gateway service account may call k8s-playground-service on GET /
monitoring service account may call k8s-playground-service on GET /healthz
everything else is denied
```

### Kubernetes NetworkPolicy

Do not build the first security model around Kubernetes NetworkPolicy because kindnet is not the long-term desired policy engine.

Phased approach:

- Phase 1: Istio mTLS and AuthorizationPolicy
- Phase 2: revisit CNI and NetworkPolicy after Istio is working

### North/South Traffic

Use a two-stage north/south path.

Stage 1 tracer bullet:

- Expose `k8s-playground-service` directly through a temporary `Service` of type `LoadBalancer`.
- Do not use TLS yet.
- Do not use Istio yet.
- Validate that external traffic reaches the app.

Stage 2 target architecture:

- Move external app access behind Istio ingress gateway.
- Use Gateway API rather than NodePort or legacy Ingress.
- Change app services back to normal in-cluster services where appropriate.

Platform-owned resources:

- GatewayClass
- Gateway
- TLS listener configuration
- Certificates
- External address through LoadBalancer support

App-owned resources:

- Deployment
- Service
- HTTPRoute
- AuthorizationPolicy

The app should not expose itself directly with NodePort.

The temporary direct `LoadBalancer` service is acceptable only as an early tracer bullet. The target state is external traffic through Istio ingress gateway and Gateway API routing.

### Observability

Start with the mature Istio observability stack:

- Prometheus
- Grafana
- Kiali

Add tracing later after the core platform is stable.

Healthcare/security caution:

- Do not log PHI in URLs, headers, labels, traces, metrics, or access logs.
- Be careful with high-cardinality labels.
- Review what telemetry leaves the cluster.

## Suggested Repository Layout

```text
mise.toml

clusters/
  kind/
    cluster.yaml

platform/
  metallb/
  cert-manager/
  istio/
    base/
    istiod/
    cni/
    ingressgateway/
  observability/

apps/
  k8s-playground-service/
    namespace.yaml
    deployment.yaml
    service.yaml
    httproute.yaml
    peerauthentication.yaml
    authorizationpolicy.yaml
```

If managed through Argo CD:

```text
argocd/
  applications/
    metallb.yaml
    cert-manager.yaml
    istio.yaml
    observability.yaml
    k8s-playground-service.yaml
```

## Implementation Order

1. Create a new multi-node kind cluster config. Completed: `clusters/kind/cluster.yaml`.
2. Create the new multi-node kind cluster.
3. Install MetalLB.
4. Deploy a rudimentary `k8s-playground-service` tracer bullet.
5. Expose the tracer bullet with a temporary `Service` of type `LoadBalancer`.
6. Validate that the app is reachable externally without TLS.
7. Install cert-manager.
8. Install Istio sidecar mode.
9. Optionally install Istio CNI.
10. Install Istio ingress gateway.
11. Configure Gateway API resources.
12. Move `k8s-playground-service` external traffic from direct LoadBalancer exposure to Istio ingress gateway and HTTPRoute.
13. Install Prometheus, Grafana, and Kiali.
14. Create or update the app namespace with revision-based sidecar injection.
15. Add strict mTLS for the app namespace.
16. Add default-deny AuthorizationPolicy for the app namespace.
17. Allow ingress gateway traffic to the app with AuthorizationPolicy.
18. Validate external routing, sidecar injection, mTLS, authorization, and observability.

## Validation Checklist

- The kind cluster has multiple nodes.
- LoadBalancer services receive usable external addresses.
- The first tracer-bullet app is reachable through a direct LoadBalancer service before Istio is installed.
- cert-manager can issue a local certificate.
- Istio control plane is healthy.
- Istio ingress gateway is healthy.
- App pods receive sidecars through revision-based injection.
- App-to-app traffic uses mTLS.
- Plaintext traffic to strict-mTLS workloads is rejected.
- Default-deny AuthorizationPolicy blocks unexpected traffic.
- Explicit AuthorizationPolicy allows intended ingress traffic.
- The app is reachable through the Gateway API route after the Istio migration.
- Prometheus receives Istio metrics.
- Kiali shows the app and gateway traffic.

## First App Target

Deploy `k8s-playground-service` using the image:

```text
mblayman/k8s-playground-service:latest
```

The app listens on port `8080` and exposes:

- `/`
- `/healthz`

Initial app resources:

- Namespace
- Deployment
- Temporary LoadBalancer Service for the first tracer bullet

Later app resources after Istio is introduced:

- Namespace with revision-based sidecar injection
- Deployment
- Service
- HTTPRoute
- PeerAuthentication
- AuthorizationPolicy

## Open Decisions

- Should Istio CNI be included in the first Istio install?
- What local domain should be used for apps?
- Should Argo CD manage platform components immediately, or should the first install be manual and then codified?
- Should observability be installed before or after the first app is deployed?
- Should the tracer-bullet app be managed by Argo CD immediately, or should it start as a manually applied manifest that is codified first?

## Closed Decisions

- Do not create a separate infrastructure repo yet for the kind phase.
- Keep local kind cluster configuration in this repo under `clusters/kind/`.
- Keep platform Kubernetes resources such as MetalLB in this repo rather than a future infrastructure repo.
- Use top-level `mise.toml` for local operator tasks instead of command snippets in per-directory READMEs.
- Deploy a rudimentary app before Istio so there is always a simple validator for platform changes.
- Use MetalLB for kind LoadBalancer support and do not use cloud-provider-kind.
