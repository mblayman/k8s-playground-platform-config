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
- Decided not to use Argo CD for the very first bring-up. The initial kind, MetalLB, and tracer app path should be driven by local `mise` tasks and `kubectl` so early failures are easy to debug.
- Decided to introduce Argo CD immediately after the first LoadBalancer tracer bullet is working, then have Argo adopt/manage MetalLB and the tracer app before adding cert-manager, Istio, and observability.
- Decided to use MetalLB layer 2 mode for kind. BGP mode is out of scope because this playground does not need to model bare-metal router peering.
- Decided the MetalLB address pool should be derived from the actual Docker `kind` network during bring-up instead of hard-coding a subnet. Repeated cluster rebuilds and different host machines should remain automatable.
- Decided to defer local domain name setup. The first tracer bullet should validate direct access by MetalLB-assigned IP.
- Created kind-only MetalLB layer 2 config template at `platform/metallb/kind/l2-config.yaml.tpl`.
- Created `scripts/render-metallb-kind-config.sh` to inspect the Docker `kind` network, derive a safe MetalLB IP range, render the MetalLB config, and optionally apply it.
- Added `mise` tasks for MetalLB install, render-config, configure, bootstrap, and status.
- Added tracer-bullet app manifests under `apps/k8s-playground-service/tracer-bullet/`.
- Pinned the tracer-bullet image to `mblayman/k8s-playground-service:0.1.0` because `latest` is not published on Docker Hub.
- Set the tracer-bullet app greeting to `Howdy` so the response is visibly non-default.
- Added `mise` tasks for tracer-bullet app deploy, status, and smoke testing.
- Added `mise run cluster:create` as the current full bring-up task. It assumes no existing cluster and creates the kind cluster, bootstraps MetalLB, deploys the tracer app, smoke tests it, and shows status.
- Validated the tracer app through MetalLB at `http://172.21.255.200/`, returning `Howdy from k8s-playground-service`.

Current local cluster tasks:

```sh
mise run cluster:create
mise run cluster:delete
mise run kind:create
mise run kind:delete
mise run kind:nodes
mise run kind:status
```

Current local MetalLB tasks:

```sh
mise run metallb:install
mise run metallb:render-config
mise run metallb:configure
mise run metallb:bootstrap
mise run metallb:status
```

Current local app tasks:

```sh
mise run app:deploy
mise run app:smoke-test
mise run app:status
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

Use MetalLB layer 2 mode.

Layer 2 mode is the right fit for kind because it only needs an `IPAddressPool` and an `L2Advertisement`. BGP mode is unnecessary for this playground because it would require router/BGP peer configuration and would distract from the main learning path.

The MetalLB `IPAddressPool` should be generated from the Docker `kind` network at bring-up time rather than hard-coded. On this machine, the current Docker `kind` network is `172.21.0.0/16`, but that should be treated as discovered state, not a portable constant.

The bring-up automation should:

1. Ensure the kind cluster exists.
2. Inspect the Docker `kind` network subnet.
3. Choose a small high address range from that subnet for MetalLB.
4. Render/apply the MetalLB `IPAddressPool` and `L2Advertisement`.

For example, if the Docker `kind` network is `172.21.0.0/16`, a reasonable generated pool would be:

```text
172.21.255.200-172.21.255.250
```

Current implementation files:

```text
platform/metallb/kind/l2-config.yaml.tpl
scripts/render-metallb-kind-config.sh
```

Current operator task:

```sh
mise run metallb:bootstrap
```

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

### Local Domain Names

Defer local domain name setup.

The first tracer bullet should be accessed directly by its MetalLB-assigned IP address. This avoids premature decisions about local wildcard DNS, `/etc/hosts`, or external wildcard DNS helpers.

Before adopting local domains, evaluate whether the DNS solution should live outside the cluster, be handled by the host, or use an external wildcard DNS helper. Avoid making local DNS a prerequisite for the early platform path.

### Observability

Layer observability on at the end, after the core platform path is working.

Start with the mature Istio observability stack when that phase begins:

- Prometheus
- Grafana
- Kiali

Do not make observability part of the early success criteria. The main learning path is the incremental addition of platform layers, not deep inspection of a frequently changing app.

Add tracing after metrics and mesh topology are working.

Healthcare/security caution:

- Do not log PHI in URLs, headers, labels, traces, metrics, or access logs.
- Be careful with high-cardinality labels.
- Review what telemetry leaves the cluster.

### GitOps And Argo CD

Do not use Argo CD for the very first bring-up.

The first working path should be operator-driven from local `mise` tasks:

```text
kind cluster -> MetalLB -> tracer app -> external smoke test
```

This keeps the first debugging loop simple. If the tracer app is not reachable, the problem space is limited to the cluster, MetalLB, Kubernetes Services, and the app manifests.

After the tracer bullet works, introduce Argo CD and have it adopt/manage the resources that were proven manually:

```text
working tracer bullet -> install Argo CD -> Argo manages MetalLB and app -> add platform layers through Argo
```

Argo CD should become the steady-state manager before installing the more complex platform layers:

- cert-manager
- Istio
- observability
- evolved app configuration

This gives us both a simple early bootstrap and a GitOps-managed platform before the configuration graph becomes complicated.

## Suggested Repository Layout

```text
mise.toml

clusters/
  kind/
    cluster.yaml

platform/
  metallb/
    kind/
      l2-config.yaml.tpl
  cert-manager/
  istio/
    base/
    istiod/
    cni/
    ingressgateway/
  observability/

apps/
  k8s-playground-service/
    tracer-bullet/
      kustomization.yaml
      namespace.yaml
      deployment.yaml
      service.yaml
    future-istio-managed-shape/
      service.yaml
      httproute.yaml
      peerauthentication.yaml
      authorizationpolicy.yaml

scripts/
  render-metallb-kind-config.sh
```

Argo CD application definitions live in a separate repo named `k8s-playground-argocd-apps`:

```text
k8s-playground-argocd-apps/
  applications/
    metallb.yaml
    cert-manager.yaml
    istio.yaml
    observability.yaml
    k8s-playground-service.yaml
```

## Implementation Order

1. Create a new multi-node kind cluster config. Completed: `clusters/kind/cluster.yaml`.
2. Create the new multi-node kind cluster and install MetalLB. Current command: `mise run cluster:create`.
3. Verify MetalLB status. Completed locally.
4. Deploy a rudimentary `k8s-playground-service` tracer bullet. Completed locally.
5. Expose the tracer bullet with a temporary `Service` of type `LoadBalancer`. Completed locally.
6. Validate that the app is reachable externally without TLS. Completed locally: `http://172.21.255.200/` returned `Howdy from k8s-playground-service`.
7. Install Argo CD manually or with a local `mise` task.
8. Create the `k8s-playground-argocd-apps` repo and add Argo app definitions there.
9. Have Argo CD adopt/manage MetalLB.
10. Have Argo CD adopt/manage the tracer app.
11. Validate that the Argo-managed tracer app is still reachable externally.
12. Install cert-manager through Argo CD.
13. Install Istio sidecar mode through Argo CD.
14. Optionally install Istio CNI through Argo CD.
15. Install Istio ingress gateway through Argo CD.
16. Configure Gateway API resources through Argo CD.
17. Move `k8s-playground-service` external traffic from direct LoadBalancer exposure to Istio ingress gateway and HTTPRoute.
18. Create or update the app namespace with revision-based sidecar injection.
19. Add strict mTLS for the app namespace.
20. Add default-deny AuthorizationPolicy for the app namespace.
21. Allow ingress gateway traffic to the app with AuthorizationPolicy.
22. Validate external routing, sidecar injection, mTLS, and authorization.
23. Install Prometheus, Grafana, and Kiali through Argo CD.
24. Validate observability after the platform traffic path is already working.

## Validation Checklist

- The kind cluster has multiple nodes.
- LoadBalancer services receive usable external addresses.
- `mise run metallb:render-config` renders an `IPAddressPool` and `L2Advertisement` from the Docker `kind` network.
- The first tracer-bullet app is reachable through a direct LoadBalancer service before Istio is installed.
- `mise run app:smoke-test` verifies the direct LoadBalancer path and non-default greeting.
- Argo CD is introduced only after the first tracer bullet works.
- Argo CD can manage MetalLB without breaking LoadBalancer assignment.
- Argo CD can manage the tracer app without breaking external reachability.
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
mblayman/k8s-playground-service:0.1.0
```

The tracer-bullet deployment sets:

```text
GREETING=Howdy
```

Expected response:

```text
Howdy from k8s-playground-service
```

The app listens on port `8080` and exposes:

- `/`
- `/healthz`

Initial app resources:

- Namespace
- Deployment
- Temporary LoadBalancer Service for the first tracer bullet
- Non-default `GREETING` environment variable

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

## Closed Decisions

- Do not create a separate infrastructure repo yet for the kind phase.
- Keep local kind cluster configuration in this repo under `clusters/kind/`.
- Keep platform Kubernetes resources such as MetalLB in this repo rather than a future infrastructure repo.
- Use top-level `mise.toml` for local operator tasks instead of command snippets in per-directory READMEs.
- Deploy a rudimentary app before Istio so there is always a simple validator for platform changes.
- Use MetalLB for kind LoadBalancer support and do not use cloud-provider-kind.
- Use MetalLB layer 2 mode and do not use BGP mode.
- Generate the MetalLB IP address pool from the Docker `kind` network during bring-up rather than hard-coding a subnet.
- Defer local domain name and wildcard DNS setup. Use direct MetalLB IPs for the first tracer bullet.
- Do not use Argo CD for the first bring-up. Use local tasks for kind, MetalLB, and the first tracer app.
- Introduce Argo CD immediately after the direct LoadBalancer tracer bullet works.
- Have Argo CD adopt/manage MetalLB and the tracer app before adding cert-manager, Istio, and observability.
- Create `k8s-playground-argocd-apps` when it is time to introduce Argo CD.
- Do not store Argo CD `Application` definitions temporarily in this platform-config repo.
- Install observability at the end, after the core app, MetalLB, Argo CD, Istio, Gateway API, mTLS, and authorization path is working.
- Pin the tracer-bullet app image to `mblayman/k8s-playground-service:0.1.0` instead of `latest`.
