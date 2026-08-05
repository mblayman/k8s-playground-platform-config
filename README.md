# k8s-playground-platform-config
A place for manifests, charts, etc for my k8s playground project

## Recovering After Laptop Suspension

Long laptop suspends can advance wall-clock time past the short-lived Istio workload certificates and projected service-account tokens inside the paused kind containers. The pods may still report Ready even though mesh mTLS requests fail with HTTP 503 responses.

Run the targeted repair task after a long suspend or when the gateway smoke test reports repeated 503 responses:

```sh
mise run kind:repair-mesh
```

The task inspects every running `istio-proxy`, recycles only controller-managed pods whose workload certificate is expired or expires within 12 hours, waits for each workload to recover, and verifies the external Gateway API path. This recovery is specific to kind running on a suspendable workstation; continuously running Kubernetes nodes should rotate Istio certificates normally.
