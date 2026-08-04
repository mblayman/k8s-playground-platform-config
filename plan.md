# Kubernetes Playground Platform Plan

## Goal

Build a serious local Kubernetes playground that uses mature, well-tested Istio sidecar mode for service mesh capabilities, while avoiding Cilium for now to keep the first platform iteration focused.

## Target Stack

- Cluster: multi-node kind
- CNI: kindnet, the default kind CNI
- Service mesh: Istio sidecar mode
- Ingress: Istio ingress gateway with Gateway API
- LoadBalancer support: MetalLB for kind; cloud-native load balancing for future GCP clusters
- TLS: cert-manager
- GitOps: Argo CD
- Observability: Grafana stack with Alloy, Mimir, Loki, Tempo, Pyroscope, Beyla, and kind-local MinIO object storage
- Policy: Istio mTLS and AuthorizationPolicy first; Kubernetes NetworkPolicy later
- Protocols: HTTP/1.1 baseline now; HTTP/2 support planned after observability gives enough mesh visibility

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
- Decided to introduce Argo CD immediately after the first LoadBalancer tracer bullet is working, then have Argo adopt/manage the tracer app before adding cert-manager, Istio, and observability. MetalLB remains outside Argo for kind because its IP pool is generated from the local Docker `kind` network at bootstrap time.
- Decided to use MetalLB layer 2 mode for kind. BGP mode is out of scope because this playground does not need to model bare-metal router peering.
- Decided the MetalLB address pool should be derived from the actual Docker `kind` network during bring-up instead of hard-coding a subnet. Repeated cluster rebuilds and different host machines should remain automatable.
- Decided to defer local domain name setup. The first tracer bullet should validate direct access by MetalLB-assigned IP.
- Created kind-only MetalLB layer 2 config template at `clusters/kind/metallb/l2-config.yaml.tpl`.
- Created `scripts/render-metallb-kind-config.sh` to inspect the Docker `kind` network, derive a safe MetalLB IP range, render the MetalLB config, and optionally apply it.
- Added `mise` tasks for MetalLB install, render-config, configure, bootstrap, and status.
- Initially added tracer-bullet app manifests under `apps/k8s-playground-service/tracer-bullet/`, then moved the app source of truth to `../k8s-playground-argocd-apps/components/apps/k8s-playground-service/` after Argo CD was introduced.
- Pinned the app image to `mblayman/k8s-playground-service:0.1.0` because `latest` is not published on Docker Hub.
- Set the app greeting to `Howdy` so the response is visibly non-default.
- Added `mise` tasks for app status and gateway smoke testing.
- Added `mise run cluster:create` as the current full bring-up task. It assumes no existing cluster and:
  - Creates the kind cluster.
  - Bootstraps MetalLB.
  - Installs Argo CD.
  - Bootstraps the kind root app.
  - Waits for expected Argo child apps.
  - Verifies Istio and Gateway API readiness.
  - Smoke tests the gateway app path.
  - Shows platform status.
- Validated the tracer app through MetalLB at `http://172.21.255.200/`, returning `Howdy from k8s-playground-service`.
- Created Argo CD empty-cluster bootstrap namespace configuration under `bootstrap/argocd/kind/`.
- Added `mise` tasks for Argo CD install, status, initial admin password retrieval, and port-forwarding.
- Installed Argo CD `v3.4.4` into the current kind cluster.
- Validated that Argo CD pods are healthy and the `Application`, `ApplicationSet`, and `AppProject` CRDs are registered.
- Confirmed the tracer app still works after Argo CD installation.
- Refactored `argocd:install` so Argo CD manifests are applied first, then independent component rollout checks run in parallel.
- Added `scripts/wait-for-rollout.sh` so rollout waits poll frequently while printing periodic workload and pod status, then wired Argo CD waits and MetalLB controller/speaker waits through it. MetalLB now waits for the controller before the speaker because the speaker depends on controller-created startup state such as the `memberlist` Secret.
- Populated `../k8s-playground-argocd-apps` with the initial `clusters/kind` root app-of-apps structure and the first child `Application` for `k8s-playground-service`.
- Added `mise run argocd:bootstrap-root` to apply the pushed kind root app manifest from GitHub and wait for expected child apps to become synced and healthy:
  - `gateway-api-crds`
  - `cert-manager`
  - `cert-manager-config`
  - `istio-base`
  - `istiod`
  - `istio-cni`
  - `istio-ingressgateway`
  - `gateway-api-config`
  - `k8s-playground-service`
- Removed the direct `k8s-playground-service` manifests and `app:deploy` task from this repo after Argo CD adopted the service.
- Installed cert-manager through Argo CD using the Jetstack Helm chart with values kept under `components/platform/cert-manager/` in the Argo apps repo.
- Added and synced Argo-managed local cert-manager config: self-signed bootstrap issuer, local root CA certificate, local CA `ClusterIssuer`, and test certificate request.
- Added Argo app definitions and Helm validation tasks for Istio `base` and `istiod` in `../k8s-playground-argocd-apps`, pinned to Istio `1.30.2`.
- Synced Istio `base` and `istiod` through Argo CD. The Kubernetes control plane resources are healthy, with `deployment/istiod-stable` ready.
- Declared Istio validating webhooks fail-closed with `base.validationFailurePolicy: Fail`, documented sync wave guardrails, and confirmed a full `mise run cluster:create` rebuild comes up successfully with Argo apps synced and healthy.
- Installed Istio CNI as an Argo-managed child app at sync wave `45`, using the same Istio `1.30.2` version and `stable` revision as `istiod`. `istiod` values now set `cni.enabled: true`, and `daemonset/istio-cni-node` is healthy on all three kind nodes.
- Installed Istio ingress gateway as an Argo-managed child app at sync wave `50`, using the Istio `gateway` Helm chart version `1.30.2`, release name `istio-ingressgateway`, and revision `stable`. The gateway Service is `type: LoadBalancer`, and MetalLB assigned external IP `172.21.255.201`.
- Installed Gateway API CRDs as an independent Argo child app at sync wave `0`, using Gateway API `v1.6.0` standard CRDs from the upstream `kubernetes-sigs/gateway-api` repo.
- Configured platform Gateway API resources at sync wave `60`: `GatewayClass/istio` and `Gateway/k8s-playground-gateway` in `istio-system`, manually linked to `Service/istio-ingressgateway` by hostname.
- Added app-owned `HTTPRoute/k8s-playground-service` inside the existing `k8s-playground-service` app component. It uses a resource-level Argo sync wave so it stays close to the app manifests while applying after the app Service.
- Validated the Gateway API route through Istio ingress gateway: `http://172.21.255.201/` returned `Howdy from k8s-playground-service`.
- Removed the temporary direct `LoadBalancer` exposure from `k8s-playground-service`; the app Service now uses the default `ClusterIP` type and external traffic goes through Istio ingress gateway. Verified `http://172.21.255.201/` still returns `Howdy from k8s-playground-service`.
- Moved kind-only MetalLB configuration under `clusters/kind/metallb/` and moved Argo CD empty-cluster bootstrap configuration under `bootstrap/argocd/` so the future `platform/` tree is reserved for steady-state Kubernetes platform components.
- Moved the first Argo-managed desired-state component, `cert-manager-config`, from `../k8s-playground-argocd-apps/components/platform/cert-manager-config/` into this repo at `platform/cert-manager-config/`. The Argo apps repo now keeps only the `Application` wiring for that component.
- Added local validation tasks for this split: `mise run validate:cert-manager-config` in this repo renders the Kustomize component, and the same task in `../k8s-playground-argocd-apps` verifies the `Application` source wiring and renders the referenced sibling path.
- Added `mise run argocd:sync-app --app <name>` to force a child app sync after moving an existing Argo CD `Application` to a different source repo. This refreshes Argo CD operation metadata so the UI does not mix an old repo URL with a new commit SHA.
- Moved the `cert-manager` Helm values from `../k8s-playground-argocd-apps/components/platform/cert-manager/` into this repo at `platform/cert-manager/`. The Argo apps repo keeps the Helm `Application` wiring and references the values through a `$values` source pointed at this repo.
- Moved `gateway-api-config` from `../k8s-playground-argocd-apps/components/platform/gateway-api-config/` into this repo at `platform/gateway-api-config/`. The Argo apps repo now keeps only the `Application` wiring for the platform Gateway API objects.
- Moved `k8s-playground-service` from `../k8s-playground-argocd-apps/components/apps/k8s-playground-service/` into this repo at `apps/k8s-playground-service/`. The Argo apps repo now keeps only the `Application` wiring for the app component.
- Moved the `istio-base` Helm values from `../k8s-playground-argocd-apps/components/platform/istio/base/` into this repo at `platform/istio/base/`. The Argo apps repo keeps the Helm `Application` wiring and references the values through a `$values` source pointed at this repo.
- Moved the `istiod` Helm values from `../k8s-playground-argocd-apps/components/platform/istio/istiod/` into this repo at `platform/istio/istiod/`. The Argo apps repo keeps the Helm `Application` wiring and references the values through a `$values` source pointed at this repo.
- Moved the `istio-cni` Helm values from `../k8s-playground-argocd-apps/components/platform/istio/cni/` into this repo at `platform/istio/cni/`. The Argo apps repo keeps the Helm `Application` wiring and references the values through a `$values` source pointed at this repo.
- Moved the `istio-ingressgateway` Helm values from `../k8s-playground-argocd-apps/components/platform/istio/ingressgateway/` into this repo at `platform/istio/ingressgateway/`. The Argo apps repo keeps the Helm `Application` wiring and references the values through a `$values` source pointed at this repo.
- Converted Argo-managed Helm components in this repo into lightweight wrapper charts. Each wrapper owns its upstream chart repository, chart name, chart version, and values, so the Argo apps repo can point each Helm component at a single `platform-config` source path instead of combining an upstream chart source with a separate `$values` repo source.
- Committed wrapper chart `Chart.lock` files for reproducible dependency digests while ignoring generated `charts/` archives so upstream Helm chart packages are not vendored into source control.
- Added Argo-managed Helm repository registration under `platform/argocd/repositories/`, wired by the `argocd-repositories` child app, so public Helm dependency URLs used by wrapper charts are registered as Helm repos instead of being misclassified as Git repos by Argo CD metadata lookups.
- Enabled revision-based Istio sidecar injection for the `k8s-playground-service` namespace with `istio.io/rev: stable`, restarted the app Deployment, and verified the new pods are connected to `istiod` as `proxy_type: sidecar` while the Gateway API smoke test still returns `Howdy from k8s-playground-service`.
- Added and live-verified strict mTLS for the `k8s-playground-service` namespace with `PeerAuthentication/default` and `mtls.mode: STRICT`. The Istio ingress gateway path still works through mTLS, and a non-meshed plaintext curl pod was rejected with `Connection reset by peer`.
- Added a dedicated Kubernetes `ServiceAccount/k8s-playground-service` and configured the app Deployment to use it so Istio authorization can target the app with a specific workload identity instead of the namespace default service account.
- Added and live-verified Istio `AuthorizationPolicy/allow-ingress-gateway` for `k8s-playground-service`. The policy selects the app workload and allows only the `cluster.local/ns/istio-system/sa/istio-ingressgateway` principal to reach port `8080`; the gateway path still works, non-meshed plaintext traffic is rejected by mTLS, and a separate meshed curl client receives `RBAC: access denied` with HTTP `403`.
- Decided to support HTTP/2 as a future protocol-realism track, but not before the current security baseline and observability are in place. The preferred sequence is observability first, then mesh-only HTTP/2 upgrade experiments, then optional end-to-end h2c support in the Go app.
- Decided the observability track should use a robust Grafana stack rather than Prometheus as the primary collector. The preferred local kind stack is Grafana Alloy for collection, Mimir for metrics, Loki for logs, Tempo for traces, Pyroscope for profiling, Beyla for eBPF-derived telemetry, and MinIO as kind-local object storage backing services that would use real object storage in cloud clusters.
- Decided observability foundations should be ordered before application workloads in Argo sync waves. The current live cluster is adding observability after the app because the app already exists, but a scratch rebuild should bring MinIO, Mimir, Alloy, Grafana, and core collectors online before app wave `70`.
- Started the object-storage foundation by adding an Argo-managed MinIO wrapper chart at `platform/minio`, using the official MinIO chart in kind-friendly standalone mode with a local bootstrap-created root credential Secret. MinIO is generic platform object storage; observability is the first expected consumer but not part of MinIO's component identity.
- Added desired-state observability bucket bootstrap config under `platform/observability/object-storage-config`. It is a separate wave `20` component that creates the `mimir-blocks`, `mimir-ruler`, `mimir-alertmanager`, `loki`, `tempo`, and `pyroscope` buckets in MinIO without making those buckets part of the generic MinIO chart values.
- Decided to start Mimir with an explicit single tenant, `k8s-playground`, so Alloy writes and Grafana queries model Mimir's real `X-Scope-OrgID` tenancy without creating unnecessary team/app tenant complexity.
- Decided the first Mimir deployment may use single-binary mode for kind. This keeps the local metrics backend understandable while still using Mimir's real write, ingest, query, ruler, and object-storage concepts. Revisit a split microservices-style Mimir deployment later after the metrics path is working.
- Added desired-state Mimir single-binary manifests under `platform/observability/mimir`, using `grafana/mimir:3.1.2`, local PVC working storage, separate MinIO buckets for blocks, ruler, and Alertmanager storage, and dedicated local bootstrap-created `mimir-object-storage-credentials`. The observability object-storage config creates a MinIO user and policy scoped to those three Mimir buckets instead of giving Mimir MinIO root credentials.
- Decided alerting should use Mimir ruler plus Alertmanager as the primary learning and production-aligned path, not Grafana Alerting as the default. Grafana remains the visualization UI and can display alert state, but Alertmanager concepts are the priority for platform-team readiness.
- Decided on a functional observability namespace split: backend/UI components such as Mimir, Loki, Tempo, Pyroscope, Grafana, and Alertmanager-related resources live in `observability`; privileged node-level collectors such as Alloy log/node agents and Beyla live in `observability-collectors`; generic MinIO object storage remains in `minio`.

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
mise run app:status
```

Current local Argo CD tasks:

```sh
mise run argocd:install
mise run argocd:bootstrap-root
mise run argocd:sync-app
mise run argocd:status
mise run argocd:admin-password
mise run argocd:port-forward
```

Current local Istio tasks:

```sh
mise run istio:wait
mise run istio:status
```

Current local Gateway API tasks:

```sh
mise run gateway:wait
mise run gateway:external-ip
mise run gateway:smoke-test
mise run gateway:status
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
- Mesh and workload telemetry export

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

MetalLB should remain local kind bootstrap infrastructure rather than an Argo-managed component. Argo CD cannot declaratively discover the host Docker network subnet during sync, and committing rendered machine-specific IP pool config would make the GitOps repo less portable. Future cloud playground clusters should use cloud-appropriate LoadBalancer or gateway infrastructure instead of reusing the kind MetalLB setup.

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
clusters/kind/metallb/l2-config.yaml.tpl
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

### Secrets Management

Defer dedicated secrets-management hardening until the playground needs application credentials, private repo credentials, cloud-provider secrets, or other non-TLS sensitive data. For now, the standard Kubernetes `Secret` interface is sufficient for cert-manager-generated TLS material in the local kind cluster.

Short-term rules:

- Do not commit plaintext Kubernetes `Secret` manifests.
- Treat Kubernetes Secrets as sensitive but not sufficient by themselves; base64 encoding is not encryption.
- Use cert-manager-generated TLS Secrets for certificate lifecycle learning, but protect access to those Secrets with RBAC.

Future options to evaluate:

- SOPS with `age` for encrypted GitOps-managed Secret manifests.
- External Secrets Operator with a backend such as Vault, 1Password, AWS Secrets Manager, or GCP Secret Manager.
- Kubernetes secret encryption at rest for any future non-kind cluster.

Keep the SOPS-versus-ESO decision open until the playground needs app credentials, private repo credentials, or cloud-provider secrets.

### Istio

Install Istio in sidecar mode.

Istio should be added after the first app tracer bullet is running. The app gives us a concrete validator for each platform layer added after that point.

Required components:

- `istio-base`
- `istiod`
- `istio-cni`
- `istio-ingressgateway`

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

Current app target: `apps/k8s-playground-service/peerauthentication.yaml` declares namespace-scoped strict mTLS for `k8s-playground-service`. The Gateway API path works because the ingress gateway and app sidecars negotiate Istio mTLS proxy-to-proxy.

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

Use explicit Kubernetes service accounts for workloads before writing authorization policies. Istio derives workload identity from namespace and service account, so `k8s-playground-service` should use `cluster.local/ns/k8s-playground-service/sa/k8s-playground-service` instead of sharing `cluster.local/ns/k8s-playground-service/sa/default` with any future pod that might land in the namespace.

App components that contain their own namespace, workload identity, workload, mesh policy, and route should use resource-level sync waves so scratch rebuilds are deterministic:

| App Resource Wave | Purpose |
| ---: | --- |
| `-20` | Namespace and injection labels, so pods are created in a sidecar-injected namespace. |
| `-10` | Service accounts and other workload identities. |
| `0` | Service and Deployment workloads. Argo should let the Deployment become healthy before later waves. |
| `10` | Destination mesh policy such as `PeerAuthentication` after sidecar-injected pods exist. |
| `20` | Authorization policy, once workload identities and mTLS are active. |
| `30` | App-owned routes such as `HTTPRoute`, after mesh policy and authorization are in place. |

Baseline model:

- Default deny in app namespaces
- Explicitly allow traffic between service accounts
- Explicitly allow ingress gateway to call exposed apps
- Explicitly allow monitoring to call health endpoints

Current app target: `apps/k8s-playground-service/authorizationpolicy.yaml` declares the first allow-list rule for `k8s-playground-service`. Because an `ALLOW` policy selects the workload, non-matching traffic to that workload is denied by default. The external gateway path still works and a separate meshed test client is denied.

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

In the current build, introduce observability after the core platform traffic path is proven. In steady-state Argo ordering, foundational observability should come before application workloads so collectors and metrics storage are already online when apps start.

Use a Grafana-centered stack with Grafana Alloy as the primary collector, not Prometheus as the primary collection path.

- Grafana
- Grafana Alloy
- Mimir
- Loki
- Tempo
- Pyroscope
- Beyla
- MinIO for kind-local object storage

Do not make observability part of the early success criteria. The main learning path is the incremental addition of platform layers, not deep inspection of a frequently changing app.

This means observability is not required for the first manual tracer bullet, but once Argo manages the platform, observability foundations are platform dependencies rather than late add-ons.

Local kind storage model:

- Use MinIO as the local object store backing Mimir, Tempo, Loki, and Pyroscope where the charts support object storage.
- Keep MinIO scoped to the kind/local profile only.
- Use real cloud object storage when this platform moves to cloud clusters.
- Do not treat MinIO as a production storage decision.
- Keep MinIO itself Argo-managed unless it becomes an Argo bootstrap dependency. If chart credentials or bucket credentials cannot be safely generated by the chart, add a local `mise` bootstrap task that creates the required Kubernetes Secrets before the root app syncs observability.

Collection model:

- Prefer OTLP push for application metrics and traces when apps are instrumented.
- Do not send logs through the OTLP endpoint by default.
- Collect logs from Kubernetes container stdout/stderr directly with Alloy, using file-based or Kubernetes log collection paths.
- Collect Kubernetes and node metrics before app-specific telemetry.
- Accept scrape compatibility where Kubernetes, Istio, Envoy, kubelet, or exporter ecosystems still require it. Avoid app-owned `/metrics` scraping as the default application pattern, but do not make zero scraping a hard requirement.

Namespace model:

- Use `observability` for backend and UI workloads: Mimir, Loki, Tempo, Pyroscope, Grafana, and Alertmanager/ruler resources.
- Use `observability-collectors` for node-level or otherwise privileged collectors such as Alloy log/node agents, Beyla, and future host/eBPF collectors.
- Keep MinIO in `minio` because it is generic platform object storage rather than an observability-only component.
- Give each component its own ServiceAccount, Secrets, Services, PVCs, and Argo application even when components share a namespace.
- Keep the `observability` namespace suitable for restricted backend/UI workloads. Do not weaken its Pod Security posture merely to accommodate host mounts, eBPF access, Linux capabilities, `hostPID`, or broad collector RBAC.
- Revisit separate namespaces for Mimir, Loki, Tempo, Grafana, or other backends when independent ownership, ResourceQuotas, Pod Security levels, NetworkPolicy boundaries, secret policies, lifecycle management, or chargeback justify the extra operational cost.
- Reevaluate the namespace model when moving observability to a dedicated cluster or splitting Mimir into a production-style distributed deployment.

Mimir tenancy model:

- Use one explicit initial tenant: `k8s-playground`.
- Configure Alloy writes and Grafana reads to include the tenant header `X-Scope-OrgID: k8s-playground` where required by Mimir.
- Treat a Mimir tenant as an operational ownership and isolation boundary, not as a direct copy of every Kubernetes namespace or Deployment.
- For real platform-team use, prefer a `platform` tenant for Kubernetes, node, ingress, mesh, storage, collector, and control-plane metrics.
- Add app/team tenants only when separate ownership, limits, retention, access control, noisy-neighbor isolation, or compliance boundaries justify the extra complexity.

Mimir deployment model:

- Start with Mimir single-binary mode for kind/local.
- Use MinIO object storage even in single-binary mode so the local deployment still exercises the object-storage-backed architecture.
- Keep ruler and Alertmanager capabilities available or easy to enable because alerting is part of the learning goal.
- Do not start with the full production-style split deployment of distributors, ingesters, queriers, query-frontends, store-gateways, compactors, rulers, and Alertmanager components unless the chart requires it.
- Revisit split deployment later when there is value in learning component-level scaling, failure modes, ring behavior, or production operations.

Recommended sequence:

- Create the `observability` namespace and Argo wiring.
- Install kind-local MinIO early as foundational platform storage. It does not depend on Istio and should be one of the first Argo-managed platform apps after Argo repository prerequisites; it does not need to be installed before Argo unless a future bootstrap dependency requires that.
- Create object-storage buckets/credentials needed by Grafana backends without committing plaintext Secrets.
- Install Mimir first because metrics are the highest-priority signal.
- Install Alloy for Kubernetes and node metrics collection, starting with a DaemonSet and adding a gateway Deployment only when needed.
- Install Grafana with declarative datasources and dashboards.
- Expose Grafana through a `Service` of type `LoadBalancer` using MetalLB, not port-forwarding.
- Configure Grafana authentication from the start. For local kind, generate or inject credentials as a Kubernetes Secret outside plaintext Git; evaluate SSO/OIDC later for cloud clusters.
- Install Loki and configure Alloy to read Kubernetes stdout/stderr directly.
- Install Pyroscope for profiling data.
- Evaluate Beyla for eBPF-derived service telemetry and profiling-adjacent signals without requiring immediate app code changes.
- Install Tempo after the metrics path is working.
- Wire Istio traces and any app OTLP traces to Alloy or the chosen collector endpoint.
- Add explicit application OTLP metrics/traces only after Kubernetes and node collection is stable. For steady-state rebuilds, Alloy and the core telemetry backends should already be online before application workloads sync.
- Consider Kiali later if it adds useful mesh topology beyond what Grafana, Tempo, Mimir, Loki, Beyla, and Istio telemetry already provide.

Initial dashboards:

- Cluster overview
- Node health and saturation
- Namespace/workload overview
- Istio ingress gateway traffic
- App request rate, errors, and duration
- mTLS and authorization denial visibility
- Logs by namespace and workload
- Traces by service after Tempo is installed
- Profiles after Pyroscope is installed

Alerts and alarms need a dedicated design pass because they are a separate operational discipline, not just dashboards with thresholds.

Use Mimir ruler plus Alertmanager as the primary alerting path:

```text
Mimir ruler evaluates PromQL alert rules
  -> Alertmanager groups, deduplicates, inhibits, silences, and routes notifications
```

Grafana should still show dashboards, annotate/explore alert context, and possibly display alert state, but Grafana Alerting is not the default alert evaluator for this playground phase.

Alerting topics to design:

- Where alert rules live in Git and how they are loaded into Mimir ruler.
- How Alertmanager config is managed declaratively without committing sensitive receiver credentials.
- How notifications are routed locally versus in cloud clusters.
- Which local notification target to use first, if any.
- How alert grouping, deduplication, inhibition, silences, repeat intervals, and escalation should work.
- Baseline alert categories: cluster/node health, workload availability, ingress availability, high error rate, high latency, mTLS/authz denials, storage/backend health, collector health, and missing telemetry.
- HIPAA caution: alert labels and messages must not include PHI or sensitive request data.

Healthcare/security caution:

- Do not log PHI in URLs, headers, labels, traces, metrics, or access logs.
- Be careful with high-cardinality labels.
- Review what telemetry leaves the cluster.

### HTTP/2 Protocol Support

Support HTTP/2 eventually, but do not make it part of the immediate mTLS and authorization baseline.

The current app path may stay HTTP/1.1 while the platform adds observability. That keeps security behavior easy to reason about before changing protocol behavior.

Preferred sequence:

- Install and validate the Grafana stack first, starting with MinIO, Mimir, Alloy, Grafana, and Kubernetes/node metrics.
- Use observability to capture the baseline HTTP/1.1 mesh path.
- Experiment with ingress-gateway-to-app-sidecar HTTP/2 over Istio mTLS using an Istio `DestinationRule`, likely starting with `connectionPool.http.h2UpgradePolicy: UPGRADE` for `k8s-playground-service`.
- Verify protocol behavior through Envoy/Istio telemetry before making the change a default pattern.
- Optionally update the Go app to support h2c so the app sidecar can also use HTTP/2 cleartext to the app container.
- If h2c is adopted, make the Service protocol metadata explicit with `appProtocol: kubernetes.io/h2c`.

Protocol notes:

- ALPN negotiates HTTP/2 during a TLS handshake and is the normal external browser path for HTTP/2.
- h2c is HTTP/2 without TLS and is mainly useful for internal cleartext hops, such as sidecar-to-app when the local application supports it.
- Istio mTLS already protects proxy-to-proxy traffic; cert-manager is not involved in Istio workload mTLS.
- Proxy-to-proxy HTTP/2 can be tested before requiring the app container itself to speak h2c.

### GitOps And Argo CD

Do not use Argo CD for the very first bring-up.

The first working path should be operator-driven from local `mise` tasks:

```text
kind cluster -> MetalLB -> tracer app -> external smoke test
```

This keeps the first debugging loop simple. If the tracer app is not reachable, the problem space is limited to the cluster, MetalLB, Kubernetes Services, and the app manifests.

After the tracer bullet works, introduce Argo CD and have it adopt/manage the app resources that were proven manually:

```text
working tracer bullet -> install Argo CD -> Argo manages app -> add declarative platform layers through Argo
```

Argo CD should become the steady-state manager before installing the more complex platform layers:

- cert-manager
- Istio
- observability
- evolved app configuration

This gives us both a simple early bootstrap and a GitOps-managed platform before the configuration graph becomes complicated.

### Argo CD Sync Waves

Use sync waves as coarse dependency bands, not arbitrary ordering values. They are a platform dependency contract for CRDs, admission webhooks, controllers, and resources those controllers reconcile.

Current wave structure:

| Wave | Purpose |
| ---: | --- |
| `0` | Cluster API extensions and CRDs not owned by an in-cluster controller app, such as Gateway API CRDs. |
| `5` | Argo CD repository/config prerequisites needed before Helm-backed wrapper apps, such as public Helm repository Secrets. |
| `10` | Core platform foundations that do not depend on Istio, such as cert-manager and kind-local object storage with MinIO. |
| `20` | Configuration consumed by core foundations, such as cert-manager issuers/certificates and MinIO buckets or backend object-storage credentials. |
| `25` | Core observability metrics storage that should exist before workloads, starting with Mimir. |
| `30` | Istio base APIs, CRDs, and validating webhook bootstrap. |
| `35` | Core observability collection, UI, and datasource wiring, especially Alloy Kubernetes/node metrics collection and Grafana backed by Mimir. |
| `40` | Istio control plane runtime, currently `istiod` with revision `stable`. |
| `45` | Istio CNI node agent, installed after `istiod` and before meshed workloads. |
| `50` | Istio ingress gateway or other mesh data-plane gateway components. |
| `55` | Additional telemetry layers that should be available before app workloads where practical, such as Loki log collection, Pyroscope, Tempo, and Beyla. |
| `60` | Platform-owned mesh, ingress, and telemetry integration configuration, such as `GatewayClass`, shared `Gateway`, namespace-level mesh defaults, and Istio-to-collector settings. |
| `70` | Application components, including workloads, services, and app-owned routes when internal resource ordering is sufficient. |
| `80` | Dashboards, alerting configuration, and other late visualization or operations resources that can reference app-specific signals. |

Guardrails:

- Keep Istio validation fail-closed in steady state with `failurePolicy: Fail`.
- Do not create Istio custom resources before wave `40` has installed a healthy `istiod`.
- Put resources that depend on a CRD in a later wave than the CRD owner.
- Put resources that depend on an admission webhook in a later wave than the controller serving that webhook.
- Treat non-Git Secrets required before a Helm app starts as bootstrap inputs, not ordinary later-wave configuration. For example, a MinIO `existingSecret` must be created by local bootstrap before the MinIO app syncs.
- Keep foundational observability before application wave `70` so metrics/log collectors are already online when workloads start during a scratch rebuild.
- Keep app-specific dashboards and alert rules after application wave `70` when they depend on labels, routes, or service names from app components.
- Prefer resource-level sync waves inside an app component before splitting app-owned resources into separate child apps.
- Avoid adding new sync wave numbers unless the dependency cannot fit an existing band.

Istio's chart defaults validating webhooks to `failurePolicy: Ignore` to avoid bootstrap deadlocks while `istiod` is not yet reachable. This platform declares `failurePolicy: Fail` because the desired steady-state posture is fail-closed validation. The rebuildability guardrail is ordering: Istio resources must not be created until after the Istio control plane wave is healthy.

## Suggested Repository Layout

```text
mise.toml

clusters/
  kind/
    cluster.yaml
    metallb/
      l2-config.yaml.tpl

bootstrap/
  argocd/
    kind/
      kustomization.yaml
      namespace.yaml

apps/
  k8s-playground-service/

platform/
  cert-manager/
  cert-manager-config/
  gateway-api-config/
  istio/
    base/
    istiod/
    cni/
    ingressgateway/
  observability/

scripts/
  render-metallb-kind-config.sh
  wait-for-rollout.sh
```

Argo CD application definitions live in a separate repo named `k8s-playground-argocd-apps`:

```text
k8s-playground-argocd-apps/
  clusters/
    kind/
      application.yaml
      apps/
        cert-manager-config.yaml
        gateway-api-config.yaml
        k8s-playground-service.yaml
  components/
    platform/
```

## Implementation Order

- [x] Create a new multi-node kind cluster config: `clusters/kind/cluster.yaml`.
- [x] Create the new multi-node kind cluster and install MetalLB. Current command: `mise run cluster:create`.
- [x] Verify MetalLB status.
- [x] Deploy a rudimentary `k8s-playground-service` tracer bullet.
- [x] Expose the tracer bullet with a temporary `Service` of type `LoadBalancer`.
- [x] Validate that the app is reachable externally without TLS: `http://172.21.255.200/` returned `Howdy from k8s-playground-service`.
- [x] Install Argo CD manually or with a local `mise` task: `mise run argocd:install` installed Argo CD `v3.4.4`.
- [x] Create the `k8s-playground-argocd-apps` repo and add Argo app definitions there.
- [x] Keep MetalLB outside Argo CD as kind bootstrap infrastructure because its config depends on local Docker network discovery.
- [x] Have Argo CD adopt/manage the tracer app: completed for `k8s-playground-service`.
- [x] Validate that the Argo-managed tracer app is still reachable externally.
- [x] Install cert-manager through Argo CD.
- [x] Add a local issuer/certificate path through cert-manager.
- [x] Prepare Istio `base` and `istiod` Argo apps and local Helm render validations.
- [x] Commit/push the Istio Argo apps changes and let Argo CD sync `istio-base` and `istiod`.
- [x] Validate Istio sidecar-mode control plane health.
- [x] Commit/push the Istio validating webhook `failurePolicy: Fail` values fix and confirm `istio-base` and `istiod` are `Synced` in Argo CD.
- [x] Install Istio CNI through Argo CD.
- [x] Prepare Istio ingress gateway Argo app and local Helm render validation.
- [x] Install Istio ingress gateway through Argo CD.
- [x] Prepare Gateway API CRDs, platform Gateway config, and app HTTPRoute manifests.
- [x] Configure Gateway API resources through Argo CD.
- [x] Validate `k8s-playground-service` external traffic through Istio ingress gateway and HTTPRoute.
- [x] Remove temporary direct LoadBalancer exposure from `k8s-playground-service` after the Gateway API path remains stable.
- [x] Create or update the app namespace with revision-based sidecar injection.
- [x] Push, sync, and live-verify strict mTLS for the app namespace.
- [x] Push, sync, and live-verify the dedicated app service account identity with app-internal sync waves for scratch rebuild ordering.
- [x] Push, sync, and live-verify ingress-gateway-only AuthorizationPolicy for the app workload.
- [x] Validate external routing, sidecar injection, mTLS, and authorization.
- [ ] Prepare Argo-managed observability wiring and wrapper charts for the Grafana stack, using waves below app wave `70` for foundational storage, backends, collectors, and Grafana.
- [x] Add local bootstrap support for MinIO root credentials that cannot be committed to Git.
- [x] Install kind-local MinIO as generic platform object storage for observability and future platform consumers.
- [x] Add Argo-managed observability object-storage config for the `mimir-blocks`, `mimir-ruler`, `mimir-alertmanager`, `loki`, `tempo`, and `pyroscope` buckets.
- [x] Add desired-state Mimir single-binary manifests and Argo wiring for wave `25`.
- [ ] Install Mimir through Argo CD for metrics storage, starting with single-binary mode and explicit tenant `k8s-playground`.
- [ ] Create `observability-collectors` for privileged node/log/eBPF collectors while keeping backends and UI workloads in `observability`.
- [ ] Install Alloy for Kubernetes and node metrics collection in `observability-collectors` before app-specific telemetry.
- [ ] Configure Alloy to remote-write metrics to Mimir with tenant `k8s-playground`.
- [ ] Install Grafana through Argo CD with declarative datasources and dashboards.
- [ ] Configure Grafana's Mimir/Prometheus datasource with tenant `k8s-playground` where required.
- [ ] Expose Grafana through a MetalLB-backed `LoadBalancer` Service and configure authentication without committing plaintext credentials.
- [ ] Install Loki and configure Alloy to collect Kubernetes stdout/stderr logs directly rather than via OTLP logs.
- [ ] Install Pyroscope for profiling.
- [ ] Evaluate and, if useful, install Beyla for eBPF-derived telemetry.
- [ ] Install Tempo after the metrics path is working.
- [ ] Wire Istio and app trace export to Alloy or the chosen collector endpoint.
- [ ] Add app OTLP metrics/traces only after Kubernetes and node collection is stable.
- [ ] Design alerting around Mimir ruler plus Alertmanager before treating dashboards as operational coverage.
- [ ] Add initial Git-managed platform alert rules and Alertmanager routing/silence strategy without committing sensitive receiver credentials.
- [ ] Validate observability after the platform traffic path is already working.
- [ ] Capture baseline HTTP/1.1 mesh behavior through observability before changing protocols.
- [ ] Experiment with ingress-gateway-to-app-sidecar HTTP/2 over Istio mTLS using a targeted `DestinationRule`.
- [ ] Decide whether to keep mesh-only HTTP/2 or add app-container h2c support in `k8s-playground-service`.
- [ ] If app h2c is adopted, rebuild the service image and mark the Service protocol explicitly with `appProtocol: kubernetes.io/h2c`.
- [ ] Revisit secrets-management hardening later when the playground needs non-TLS app credentials, private repo credentials, or cloud-provider secrets. Candidate approaches remain SOPS/age and External Secrets Operator.

## Validation Checklist

- The kind cluster has multiple nodes.
- LoadBalancer services receive usable external addresses.
- `mise run metallb:render-config` renders an `IPAddressPool` and `L2Advertisement` from the Docker `kind` network.
- The first tracer-bullet app is reachable through a direct LoadBalancer service before Istio is installed.
- The initial direct LoadBalancer app smoke test was replaced by the Gateway API smoke test after ingress migration.
- Argo CD is introduced only after the first tracer bullet works.
- Argo CD pods are healthy in the `argocd` namespace.
- Argo CD CRDs are registered: `Application`, `ApplicationSet`, and `AppProject`.
- MetalLB remains managed by local kind bootstrap tasks, and Argo-managed apps can still use MetalLB-assigned LoadBalancer IPs.
- Argo CD can manage the tracer app without breaking external reachability.
- `mise run argocd:bootstrap-root` waits for every expected Argo child app to become `Synced` and `Healthy`.
- cert-manager can issue a local certificate.
- No plaintext sensitive Kubernetes Secret manifests are committed.
- Istio control plane is healthy.
- Istio CNI DaemonSet is healthy on every schedulable node before sidecar injection is enabled for app workloads.
- Istio ingress gateway is healthy.
- Istio ingress gateway `Service` receives a MetalLB LoadBalancer IP.
- Gateway API CRDs are established.
- `GatewayClass/istio`, `Gateway/k8s-playground-gateway`, and `HTTPRoute/k8s-playground-service` are accepted.
- `mise run gateway:smoke-test` verifies the app through the Istio ingress gateway path.
- App pods receive sidecars through revision-based injection.
- App-to-app traffic uses mTLS.
- Plaintext traffic to strict-mTLS workloads is rejected.
- Default-deny AuthorizationPolicy blocks unexpected traffic.
- Explicit AuthorizationPolicy allows intended ingress traffic.
- The app is reachable through the Gateway API route after the Istio migration.
- MinIO is available as the kind-local object storage backend for observability services that need object storage.
- Mimir uses explicit tenant `k8s-playground` for the initial local playground metrics path.
- Mimir starts in single-binary mode for kind while still using MinIO object storage.
- Mimir receives Kubernetes and node metrics collected through Alloy for tenant `k8s-playground`.
- Backend/UI workloads remain in `observability`, privileged node/eBPF collectors run in `observability-collectors`, and generic object storage remains in `minio`.
- On a scratch rebuild, Mimir and Alloy are healthy before application wave `70` syncs workloads.
- Grafana is reachable through a MetalLB external IP and requires authentication.
- Grafana datasources are provisioned declaratively for installed backends.
- Loki receives Kubernetes container stdout/stderr logs collected directly by Alloy.
- Pyroscope is available for profiling data.
- Beyla is evaluated for eBPF-derived telemetry and enabled only if it adds useful local signal with acceptable complexity.
- Tempo receives traces after the metrics path is working.
- App-specific OTLP metrics/traces are added only after Kubernetes and node telemetry are stable.
- Mimir ruler and Alertmanager are the primary alerting path for platform alerts.
- Alerting ownership, Git-managed rule location, Alertmanager routing, silence strategy, and notification targets are documented before alerts are considered complete.
- HTTP/1.1 baseline protocol behavior is visible before HTTP/2 changes are introduced.
- If a mesh HTTP/2 experiment is enabled, telemetry confirms ingress-gateway-to-app-sidecar protocol behavior over Istio mTLS.
- If h2c is enabled in the app, the app still serves HTTP/1.1 clients unless there is a deliberate reason to remove that compatibility.

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

- What local domain should be used for apps?
- Should future GitOps secret handling use SOPS/age, External Secrets Operator, or both for different secret classes?

## Closed Decisions

- Do not create a separate infrastructure repo yet for the kind phase.
- Keep local kind cluster configuration in this repo under `clusters/kind/`.
- Keep kind-only Kubernetes support such as MetalLB in this repo under `clusters/kind/` until a future infrastructure repo is needed for cloud substrate resources.
- Keep Argo CD empty-cluster install resources under `bootstrap/` because they start GitOps reconciliation before steady-state platform components exist.
- Use top-level `mise.toml` for local operator tasks instead of command snippets in per-directory READMEs.
- Deploy a rudimentary app before Istio so there is always a simple validator for platform changes.
- Use MetalLB for kind LoadBalancer support and do not use cloud-provider-kind.
- Use MetalLB layer 2 mode and do not use BGP mode.
- Generate the MetalLB IP address pool from the Docker `kind` network during bring-up rather than hard-coding a subnet.
- Defer local domain name and wildcard DNS setup. Use direct MetalLB IPs for the first tracer bullet.
- Do not use Argo CD for the first bring-up. Use local tasks for kind, MetalLB, and the first tracer app.
- Introduce Argo CD immediately after the direct LoadBalancer tracer bullet works.
- For the initial live build, have Argo CD adopt/manage the tracer app before adding cert-manager, Istio, and observability. Keep MetalLB outside Argo for the kind cluster.
- Create `k8s-playground-argocd-apps` when it is time to introduce Argo CD.
- Do not store Argo CD `Application` definitions temporarily in this platform-config repo.
- For the current live cluster, retrofit observability after the core app, MetalLB, Argo CD, Istio, Gateway API, mTLS, and authorization path is working.
- For scratch rebuilds, order foundational observability before application workloads so storage, metrics, collectors, and Grafana are already online before app wave `70`.
- Build observability around Grafana Alloy, Mimir, Loki, Tempo, Pyroscope, Beyla, and Grafana rather than a Prometheus-first collection model.
- Install Mimir and Kubernetes/node metrics before Tempo and app-specific telemetry because metrics are the higher-priority initial signal.
- Collect logs directly from Kubernetes stdout/stderr rather than using OTLP logs by default.
- Use MinIO only as kind-local object storage for observability backends; use real object storage for future cloud clusters.
- Expose Grafana with a LoadBalancer Service and configured auth, not port-forwarding.
- Support HTTP/2 later, after observability is installed, starting with a reversible mesh-only experiment before changing the Go app to support h2c.
- Pin the tracer-bullet app image to `mblayman/k8s-playground-service:0.1.0` instead of `latest`.
- Include Istio CNI in the sidecar-mode install so application pods do not require the privileged `istio-init` init container path for traffic redirection.
