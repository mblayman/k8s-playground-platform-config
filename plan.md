# Kubernetes Playground Platform Plan

## Goal

Build a serious local Kubernetes playground that uses mature, well-tested Istio sidecar mode for service mesh capabilities, while avoiding Cilium for now to keep the first platform iteration focused.

## Target Stack

- Cluster: multi-node kind
- CNI: kindnet, the default kind CNI
- Service mesh: Istio sidecar mode
- Ingress: Istio ingress gateway with Gateway API
- LoadBalancer support: MetalLB or cloud-provider-kind
- TLS: cert-manager
- GitOps: Argo CD
- Observability: Prometheus, Grafana, Kiali
- Policy: Istio mTLS and AuthorizationPolicy first; Kubernetes NetworkPolicy later

## Explicit Non-Goals For First Iteration

- No Cilium
- No service mesh ambient mode
- No production-grade external DNS automation
- No tracing on day one
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

This allows testing of:

- Cross-node pod traffic
- Gateway behavior
- Workload rescheduling
- Multi-replica services
- Istio sidecar behavior across nodes

### LoadBalancer Support

Choose one:

- MetalLB: more Kubernetes-like and useful for learning real LoadBalancer behavior
- cloud-provider-kind: simpler kind-native LoadBalancer support

Current recommendation: MetalLB, unless simplicity becomes more important than general Kubernetes platform learning.

### TLS

Install cert-manager early so TLS is part of the platform model from the start.

For local development, start with:

- Self-signed root
- ClusterIssuer
- Certificate for a local app domain pattern such as `*.apps.k8s-playground.local`

### Istio

Install Istio in sidecar mode.

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

Use Gateway API rather than NodePort or legacy Ingress.

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

1. Create a new multi-node kind cluster.
2. Install MetalLB or cloud-provider-kind.
3. Install cert-manager.
4. Install Istio sidecar mode.
5. Optionally install Istio CNI.
6. Install Istio ingress gateway.
7. Configure Gateway API resources.
8. Install Prometheus, Grafana, and Kiali.
9. Create the app namespace with revision-based sidecar injection.
10. Add strict mTLS for the app namespace.
11. Add default-deny AuthorizationPolicy for the app namespace.
12. Deploy `k8s-playground-service`.
13. Add Service and HTTPRoute for `k8s-playground-service`.
14. Allow ingress gateway traffic to the app with AuthorizationPolicy.
15. Validate external TLS, sidecar injection, mTLS, authorization, and observability.

## Validation Checklist

- The kind cluster has multiple nodes.
- LoadBalancer services receive usable external addresses.
- cert-manager can issue a local certificate.
- Istio control plane is healthy.
- Istio ingress gateway is healthy.
- App pods receive sidecars through revision-based injection.
- App-to-app traffic uses mTLS.
- Plaintext traffic to strict-mTLS workloads is rejected.
- Default-deny AuthorizationPolicy blocks unexpected traffic.
- Explicit AuthorizationPolicy allows intended ingress traffic.
- The app is reachable through the Gateway API route.
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
- Service
- HTTPRoute
- PeerAuthentication
- AuthorizationPolicy

## Open Decisions

- MetalLB or cloud-provider-kind?
- Should Istio CNI be included in the first Istio install?
- What local domain should be used for apps?
- Should Argo CD manage platform components immediately, or should the first install be manual and then codified?
- Should observability be installed before or after the first app is deployed?
