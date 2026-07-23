# Agent Notes

## Helm Wrapper Charts

Argo-managed Helm components in this repo use lightweight wrapper charts under `platform/`.

The wrapper chart owns:

- upstream chart repository URL
- upstream chart name
- upstream chart version
- local values
- `Chart.lock` dependency digest

The Argo apps repo should point at these wrapper chart directories as single-source `Application` paths. Do not reintroduce Argo multi-source apps that combine an upstream Helm chart source with a separate `$values` source for these components.

Current wrapper chart paths:

- `platform/cert-manager`
- `platform/istio/base`
- `platform/istio/istiod`
- `platform/istio/cni`
- `platform/istio/ingressgateway`

Keep wrapper chart `version` aligned with the upstream dependency version to avoid a separate local versioning scheme. For example, Istio wrappers use `version: 1.30.2` when their dependency chart version is `1.30.2`. Cert-manager uses wrapper `version: 1.21.0` while the dependency uses Jetstack's `v1.21.0` chart version.

Commit `Chart.lock` files. They preserve dependency digests for reproducibility.

Do not commit downloaded chart archives. `platform/**/charts/` is ignored intentionally so validation can download dependencies locally without vendoring upstream chart packages into source control.

Argo CD still needs public Helm dependency repositories registered as Helm repositories. Steady-state repository Secrets live in `platform/argocd/repositories/` and are installed by the `argocd-repositories` child app from the Argo apps repo. Without those Secrets, Argo may treat dependency repository URLs such as `https://charts.jetstack.io` as Git repositories during UI/API revision metadata lookups and emit `git fetch` errors.

When changing a wrapper chart:

- Update `Chart.yaml` for upstream chart repo/name/version changes.
- Update `values.yaml` under the dependency chart name because these are dependency values.
- Run `helm dependency update <wrapper-chart-path>` to refresh `Chart.lock`.
- Run `mise run validate:all` from this repo.
- Run `mise run validate:all` from `../k8s-playground-argocd-apps` if Argo wiring is changed.

## Repo Responsibilities

This repo owns Kubernetes desired state and bootstrap/local support configuration.

The `../k8s-playground-argocd-apps` repo owns Argo CD `Application` wiring: which component is installed for a cluster, sync waves, destination namespace, sync policy, and source path.

For public GitHub sources, Argo CD does not need extra repo credentials.
