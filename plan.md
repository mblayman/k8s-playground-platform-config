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
| `10` | Core platform controllers, such as cert-manager. |
| `20` | Configuration consumed by core controllers, such as cert-manager issuers and certificates. |
| `30` | Istio base APIs, CRDs, and validating webhook bootstrap. |
| `40` | Istio control plane runtime, currently `istiod` with revision `stable`. |
| `45` | Istio CNI node agent, installed after `istiod` and before meshed workloads. |
| `50` | Istio ingress gateway or other mesh data-plane gateway components. |
| `60` | Platform-owned mesh and ingress configuration, such as `GatewayClass`, shared `Gateway`, and namespace-level mesh defaults. |
| `70` | Application components, including workloads, services, and app-owned routes when internal resource ordering is sufficient. |
| `80` | Observability components, dashboards, and late visualization resources. |

Guardrails:

- Keep Istio validation fail-closed in steady state with `failurePolicy: Fail`.
- Do not create Istio custom resources before wave `40` has installed a healthy `istiod`.
- Put resources that depend on a CRD in a later wave than the CRD owner.
- Put resources that depend on an admission webhook in a later wave than the controller serving that webhook.
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

platform/
  cert-manager/
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
        k8s-playground-service.yaml
  components/
    apps/
      k8s-playground-service/
        kustomization.yaml
        namespace.yaml
        deployment.yaml
        service.yaml
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
- [ ] Create or update the app namespace with revision-based sidecar injection.
- [ ] Add strict mTLS for the app namespace.
- [ ] Add default-deny AuthorizationPolicy for the app namespace.
- [ ] Allow ingress gateway traffic to the app with AuthorizationPolicy.
- [ ] Validate external routing, sidecar injection, mTLS, and authorization.
- [ ] Install Prometheus, Grafana, and Kiali through Argo CD.
- [ ] Validate observability after the platform traffic path is already working.
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
- Have Argo CD adopt/manage the tracer app before adding cert-manager, Istio, and observability. Keep MetalLB outside Argo for the kind cluster.
- Create `k8s-playground-argocd-apps` when it is time to introduce Argo CD.
- Do not store Argo CD `Application` definitions temporarily in this platform-config repo.
- Install observability at the end, after the core app, MetalLB, Argo CD, Istio, Gateway API, mTLS, and authorization path is working.
- Pin the tracer-bullet app image to `mblayman/k8s-playground-service:0.1.0` instead of `latest`.
- Include Istio CNI in the sidecar-mode install so application pods do not require the privileged `istio-init` init container path for traffic redirection.
