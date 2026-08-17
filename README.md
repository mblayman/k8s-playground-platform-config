# k8s-playground-platform-config
A place for manifests, charts, etc for my k8s playground project

## Validation

Mise pins uv and exposes both the complete suite and targeted component checks:

```sh
mise install
mise run validate:all
mise run validate:tempo
```

Validation is implemented as the locked PEP 723 script `scripts/validate.py`. It stages wrapper charts under `.validation/`, builds their committed dependency locks without changing source charts, checks rendered resource contracts, and runs native Mimir, Tempo, Alloy, and Envoy configuration validators where applicable. Successful runs remove their workspace; failed runs preserve it for diagnosis.

## Recovering After Laptop Suspension

Long laptop suspends can advance wall-clock time past the short-lived Istio workload certificates and projected service-account tokens inside the paused kind containers. The pods may still report Ready even though mesh mTLS requests fail with HTTP 503 responses.

Run the targeted repair task after a long suspend or when the gateway smoke test reports repeated 503 responses:

```sh
mise run kind:repair-mesh
```

The task inspects every running `istio-proxy`, recycles only controller-managed pods whose workload certificate is expired or expires within 12 hours, waits for each workload to recover, and verifies the external Gateway API path. This recovery is specific to kind running on a suspendable workstation; continuously running Kubernetes nodes should rotate Istio certificates normally.
