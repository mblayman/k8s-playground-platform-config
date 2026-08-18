#!/usr/bin/env -S uv run --locked --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "PyYAML==6.0.2",
# ]
# ///

"""Run platform-config validations without modifying source charts."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parent.parent
VALIDATION_ROOT = ROOT / ".validation"

COMPONENTS = (
    "scripts",
    "argocd-kind-bootstrap",
    "argocd-repositories",
    "argocd-config",
    "minio",
    "observability-object-storage-config",
    "mimir",
    "tempo",
    "alloy",
    "grafana",
    "cert-manager",
    "cert-manager-config",
    "gateway-api-config",
    "management-gateway-config",
    "k8s-playground-service",
    "istio-base",
    "istiod",
    "istio-cni",
    "istio-ingressgateway",
    "istio-managementgateway",
)

DESCRIPTIONS = {
    "scripts": "check local support scripts",
    "argocd-kind-bootstrap": "render kind Argo CD bootstrap resources",
    "argocd-repositories": "validate Argo CD repository registrations",
    "argocd-config": "render steady-state Argo CD configuration",
    "minio": "validate the MinIO wrapper chart",
    "observability-object-storage-config": "validate object storage bucket config",
    "mimir": "validate Mimir manifests and native configs",
    "tempo": "validate the Tempo wrapper chart and native configs",
    "alloy": "validate the Alloy wrapper chart and runtime config",
    "grafana": "validate Grafana dashboards and provisioning",
    "cert-manager": "render the cert-manager wrapper chart",
    "cert-manager-config": "render cert-manager issuer and certificate config",
    "gateway-api-config": "render platform Gateway API config",
    "management-gateway-config": "render management Gateway API config",
    "k8s-playground-service": "validate application manifests",
    "istio-base": "render the Istio base wrapper chart",
    "istiod": "render the istiod wrapper chart",
    "istio-cni": "render the Istio CNI wrapper chart",
    "istio-ingressgateway": "render the Istio ingress gateway wrapper chart",
    "istio-managementgateway": "render the Istio management gateway wrapper chart",
}


def dig(value: Any, *keys: str | int) -> Any:
    """Return a nested value, or None if the path does not exist."""
    current = value
    for key in keys:
        if isinstance(key, int) and isinstance(current, Sequence) and not isinstance(current, str):
            if key >= len(current):
                return None
            current = current[key]
        elif isinstance(key, str) and isinstance(current, Mapping):
            current = current.get(key)
        else:
            return None
    return current


class Harness:
    def __init__(self, workdir: Path, verbose: bool) -> None:
        self.workdir = workdir
        self.verbose = verbose
        self.helm_repository_config = workdir / "helm" / "repositories.yaml"
        self.helm_repository_cache = workdir / "helm" / "repository"
        self.helm_repository_cache.mkdir(parents=True)
        self.helm_repositories: set[str] = set()

    def component_dir(self, name: str) -> Path:
        path = self.workdir / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def run(
        self,
        case: unittest.TestCase,
        args: Sequence[str | os.PathLike[str]],
        *,
        env: Mapping[str, str] | None = None,
        cwd: Path = ROOT,
    ) -> str:
        command = [os.fspath(arg) for arg in args]
        if self.verbose:
            print(f"$ {shlex.join(command)}", flush=True)
        command_env = os.environ.copy()
        if env:
            command_env.update(env)
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=command_env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except FileNotFoundError:
            case.fail(f"required executable not found: {command[0]}")
        if self.verbose:
            if completed.stdout:
                print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
            if completed.stderr:
                print(
                    completed.stderr,
                    end="" if completed.stderr.endswith("\n") else "\n",
                    file=sys.stderr,
                )
        if completed.returncode != 0:
            details = [
                f"command failed with exit code {completed.returncode}: {shlex.join(command)}"
            ]
            if completed.stdout.strip():
                details.append(f"stdout:\n{completed.stdout.rstrip()}")
            if completed.stderr.strip():
                details.append(f"stderr:\n{completed.stderr.rstrip()}")
            case.fail("\n".join(details))
        return completed.stdout

    def yaml_documents(
        self, case: unittest.TestCase, content: str, source: str
    ) -> list[dict[str, Any]]:
        try:
            documents = list(yaml.safe_load_all(content))
        except yaml.YAMLError as error:
            case.fail(f"{source} is not valid YAML: {error}")
        invalid = [type(doc).__name__ for doc in documents if doc is not None and not isinstance(doc, dict)]
        case.assertFalse(invalid, f"{source} contains non-mapping YAML documents: {invalid}")
        return [doc for doc in documents if isinstance(doc, dict)]

    def kustomize(self, case: unittest.TestCase, component: str, path: str) -> list[dict[str, Any]]:
        rendered = self.run(case, ["kubectl", "kustomize", ROOT / path])
        output = self.component_dir(component) / "rendered.yaml"
        output.write_text(rendered)
        return self.yaml_documents(case, rendered, path)

    def stage_chart(self, case: unittest.TestCase, component: str, path: str) -> Path:
        source = ROOT / path
        case.assertTrue(source.is_dir(), f"wrapper chart directory is missing: {path}")
        case.assertTrue((source / "Chart.lock").is_file(), f"wrapper chart must commit Chart.lock: {path}")
        destination = self.component_dir(component) / "chart"
        shutil.copytree(source, destination, ignore=shutil.ignore_patterns("charts"))
        try:
            chart = yaml.safe_load((destination / "Chart.yaml").read_text())
        except yaml.YAMLError as error:
            case.fail(f"{path}/Chart.yaml is not valid YAML: {error}")
        case.assertIsInstance(chart, dict, f"{path}/Chart.yaml must be a YAML mapping")
        dependencies = chart.get("dependencies", [])
        case.assertIsInstance(dependencies, list, f"{path}/Chart.yaml dependencies must be a list")
        for dependency in dependencies:
            repository = dependency.get("repository") if isinstance(dependency, dict) else None
            if not isinstance(repository, str) or repository.startswith(("file://", "oci://", "@")):
                continue
            if repository not in self.helm_repositories:
                digest = hashlib.sha256(repository.encode()).hexdigest()[:12]
                self.run(
                    case,
                    [
                        "helm",
                        "repo",
                        "add",
                        f"validation-{digest}",
                        repository,
                        "--repository-config",
                        self.helm_repository_config,
                        "--repository-cache",
                        self.helm_repository_cache,
                    ],
                )
                self.helm_repositories.add(repository)
        self.run(
            case,
            [
                "helm",
                "dependency",
                "build",
                destination,
                "--repository-config",
                self.helm_repository_config,
                "--repository-cache",
                self.helm_repository_cache,
            ],
        )
        return destination

    def helm_render(
        self,
        case: unittest.TestCase,
        component: str,
        path: str,
        release: str,
        namespace: str,
        *,
        lint: bool = False,
        include_crds: bool = False,
    ) -> list[dict[str, Any]]:
        chart = self.stage_chart(case, component, path)
        if lint:
            self.run(case, ["helm", "lint", chart])
        command: list[str | os.PathLike[str]] = [
            "helm",
            "template",
            release,
            chart,
            "--namespace",
            namespace,
        ]
        if include_crds:
            command.append("--include-crds")
        rendered = self.run(case, command)
        (self.component_dir(component) / "rendered.yaml").write_text(rendered)
        return self.yaml_documents(case, rendered, path)


def resource(
    case: unittest.TestCase,
    documents: Iterable[dict[str, Any]],
    kind: str,
    name: str,
    *,
    prefix: bool = False,
) -> dict[str, Any]:
    matches = [
        doc
        for doc in documents
        if doc.get("kind") == kind
        and isinstance(dig(doc, "metadata", "name"), str)
        and (
            dig(doc, "metadata", "name").startswith(name)
            if prefix
            else dig(doc, "metadata", "name") == name
        )
    ]
    label = f"{kind}/{name}{'*' if prefix else ''}"
    case.assertTrue(matches, f"required resource is missing: {label}")
    case.assertEqual(len(matches), 1, f"expected one {label}, found {len(matches)}")
    return matches[0]


def named(
    case: unittest.TestCase,
    values: Any,
    name: str,
    message: str,
) -> dict[str, Any]:
    case.assertIsInstance(values, list, message)
    matches = [value for value in values if isinstance(value, dict) and value.get("name") == name]
    case.assertEqual(len(matches), 1, message)
    return matches[0]


class ValidationCase(unittest.TestCase):
    def __init__(self, component: str, harness: Harness) -> None:
        super().__init__("runTest")
        self.component = component
        self.harness = harness

    def id(self) -> str:
        return f"validate.{self.component}"

    def __str__(self) -> str:
        return self.component

    def shortDescription(self) -> str | None:
        return DESCRIPTIONS[self.component]

    def runTest(self) -> None:
        VALIDATORS[self.component](self, self.harness)


def validate_scripts(case: unittest.TestCase, harness: Harness) -> None:
    scripts = sorted((ROOT / "scripts").glob("*.sh"))
    case.assertTrue(scripts, "no scripts/*.sh files found")
    for script in scripts:
        harness.run(case, ["bash", "-n", script])
    hosts_test = ROOT / "scripts/test_manage_kind_hosts.py"
    case.assertTrue(hosts_test.is_file(), "required test is missing: scripts/test_manage_kind_hosts.py")
    harness.run(case, [sys.executable, hosts_test])


def validate_argocd_kind_bootstrap(case: unittest.TestCase, harness: Harness) -> None:
    harness.kustomize(case, "argocd-kind-bootstrap", "bootstrap/argocd/kind")
    harness.kustomize(case, "argocd-kind-bootstrap", "platform/argocd/config")


def validate_argocd_repositories(case: unittest.TestCase, harness: Harness) -> None:
    docs = harness.kustomize(case, "argocd-repositories", "platform/argocd/repositories")
    repositories = {
        dig(secret, "stringData", "name"): dig(secret, "stringData", "url")
        for secret in docs
        if secret.get("kind") == "Secret"
    }
    case.assertEqual(
        repositories.get("grafana-community"),
        "https://grafana-community.github.io/helm-charts",
        "Grafana Community Helm repository is missing",
    )


def validate_argocd_config(case: unittest.TestCase, harness: Harness) -> None:
    harness.kustomize(case, "argocd-config", "platform/argocd/config")


def validate_minio(case: unittest.TestCase, harness: Harness) -> None:
    docs = harness.helm_render(case, "minio", "platform/minio", "minio", "minio")
    certificate = resource(case, docs, "Certificate", "minio-server")
    service = resource(case, docs, "Service", "minio")
    deployment = resource(case, docs, "Deployment", "minio")
    port = named(case, dig(service, "spec", "ports"), "https", "MinIO HTTPS Service port is missing")
    cert_volume = named(
        case,
        dig(deployment, "spec", "template", "spec", "volumes"),
        "cert-secret-volume",
        "MinIO certificate volume is missing",
    )
    case.assertEqual(dig(certificate, "spec", "secretName"), "minio-tls", "MinIO server Certificate is missing")
    case.assertEqual(
        (port.get("port"), port.get("targetPort")),
        (443, 9000),
        "MinIO Service must expose HTTPS 443 to target 9000",
    )
    case.assertEqual(
        dig(cert_volume, "secret", "secretName"),
        "minio-tls",
        "MinIO Deployment does not mount minio-tls",
    )


def validate_object_storage(case: unittest.TestCase, harness: Harness) -> None:
    docs = harness.kustomize(
        case,
        "observability-object-storage-config",
        "platform/observability/object-storage-config",
    )
    job = resource(case, docs, "Job", "observability-object-storage-config")
    policies = resource(case, docs, "ConfigMap", "observability-object-storage-policies")
    pod_spec = dig(job, "spec", "template", "spec")
    containers = dig(pod_spec, "containers")
    case.assertIsInstance(containers, list, "Provisioning Job containers are missing")
    case.assertTrue(containers, "Provisioning Job has no container")
    container = containers[0]
    command_parts = container.get("command") if isinstance(container, dict) else None
    case.assertIsInstance(command_parts, list, "Provisioning Job command is missing")
    case.assertTrue(command_parts, "Provisioning Job command is empty")
    command = command_parts[-1]
    case.assertIsInstance(command, str, "Provisioning Job shell command is not a string")
    env = {
        entry.get("name"): entry
        for entry in container.get("env", [])
        if isinstance(entry, dict) and entry.get("name")
    }
    ca_volume = named(case, dig(pod_spec, "volumes"), "minio-ca", "Provisioning Job MinIO CA volume is missing")
    policy_json = dig(policies, "data", "tempo.json")
    case.assertIsInstance(policy_json, str, "Tempo policy data is missing")
    try:
        tempo_policy = json.loads(policy_json)
    except json.JSONDecodeError as error:
        case.fail(f"Tempo policy is not valid JSON: {error}")
    statements = tempo_policy.get("Statement", [])
    case.assertIsInstance(statements, list, "Tempo policy Statement must be a list")
    tempo_actions = [action for statement in statements for action in statement.get("Action", [])]
    tempo_resources = [item for statement in statements for item in statement.get("Resource", [])]
    case.assertTrue(
        "https://minio.minio.svc.cluster.local" in command
        and "http://minio" not in command
        and ":9000" not in command,
        "Provisioning Job must use MinIO HTTPS without a nonstandard port",
    )
    case.assertTrue(
        dig(ca_volume, "secret", "secretName") == "minio-tls"
        and any(item.get("key") == "ca.crt" for item in dig(ca_volume, "secret", "items") or []),
        "Provisioning Job does not trust minio-tls ca.crt",
    )
    case.assertTrue(
        dig(env.get("TEMPO_ACCESS_KEY"), "valueFrom", "secretKeyRef", "name")
        == "tempo-object-storage-credentials"
        and dig(env.get("TEMPO_SECRET_KEY"), "valueFrom", "secretKeyRef", "name")
        == "tempo-object-storage-credentials",
        "Tempo object storage credentials are missing",
    )
    case.assertTrue(
        "tempo-bucket-access" in command
        and "/etc/minio-policies/tempo.json" in command
        and '--user "$TEMPO_ACCESS_KEY"' in command,
        "Tempo user provisioning is incomplete",
    )
    required_actions = {
        "s3:AbortMultipartUpload",
        "s3:DeleteObject",
        "s3:GetObject",
        "s3:GetObjectTagging",
        "s3:ListMultipartUploadParts",
        "s3:PutObject",
        "s3:PutObjectTagging",
    }
    case.assertTrue(required_actions.issubset(tempo_actions), "Tempo policy is missing required object permissions")
    case.assertEqual(
        sorted(tempo_resources),
        sorted(["arn:aws:s3:::tempo", "arn:aws:s3:::tempo/*"]),
        "Tempo policy must be scoped only to its bucket",
    )


def write_native_config(directory: Path, name: str, content: str) -> Path:
    path = directory / name
    path.write_text(content)
    return path


def openssl_fixture(
    case: unittest.TestCase, harness: Harness, component: str, common_name: str
) -> Path:
    tls_dir = harness.component_dir(component) / "gateway-tls"
    tls_dir.mkdir(exist_ok=True)
    key = tls_dir / "tls.key"
    certificate = tls_dir / "tls.crt"
    harness.run(
        case,
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            key,
            "-out",
            certificate,
            "-subj",
            f"/CN={common_name}",
            "-days",
            "1",
        ],
    )
    ca = harness.run(case, ["openssl", "x509", "-in", certificate])
    (tls_dir / "ca.crt").write_text(ca)
    key.chmod(0o644)
    return tls_dir


def validate_envoy_structural(
    case: unittest.TestCase,
    envoy: str,
    server_sds: str,
    client_sds: str,
    *,
    component: str,
    backend_name: str,
    backend_address: str,
    backend_port: int,
    expected_routes: list[dict[str, Any]],
) -> None:
    try:
        envoy_config = yaml.safe_load(envoy)
        server_sds_config = yaml.safe_load(server_sds)
        client_sds_config = yaml.safe_load(client_sds)
    except yaml.YAMLError as error:
        case.fail(f"{component} gateway config is not valid YAML: {error}")
    listener = named(
        case,
        dig(envoy_config, "static_resources", "listeners"),
        "ingest_https",
        f"{component} ingest HTTPS listener is missing",
    )
    filter_chains = listener.get("filter_chains")
    case.assertIsInstance(filter_chains, list, f"{component} ingest filter chain is missing")
    case.assertEqual(len(filter_chains), 1, f"{component} ingest listener must have one filter chain")
    filter_chain = filter_chains[0]
    downstream_tls = dig(filter_chain, "transport_socket", "typed_config")
    case.assertIsInstance(downstream_tls, dict, f"{component} downstream TLS config is missing")
    http_manager = named(
        case,
        filter_chain.get("filters"),
        "envoy.filters.network.http_connection_manager",
        f"{component} HTTP connection manager is missing",
    ).get("typed_config")
    case.assertIsInstance(http_manager, dict, f"{component} HTTP connection manager config is missing")
    virtual_hosts = dig(http_manager, "route_config", "virtual_hosts")
    case.assertIsInstance(virtual_hosts, list, f"{component} gateway virtual host is missing")
    case.assertEqual(len(virtual_hosts), 1, f"{component} gateway must have one ingest virtual host")
    virtual_host = virtual_hosts[0]
    backend = named(
        case,
        dig(envoy_config, "static_resources", "clusters"),
        backend_name,
        f"{component} internal backend cluster is missing",
    )
    case.assertTrue(
        downstream_tls.get("require_client_certificate") is True
        and downstream_tls.get("disable_stateless_session_resumption") is True,
        f"{component} gateway does not require client certificates",
    )
    expected_sans = [
        {
            "san_type": "URI",
            "matcher": {"exact": "spiffe://k8s-playground/collectors/alloy"},
        }
    ]
    case.assertEqual(
        dig(client_sds_config, "resources", 0, "validation_context", "match_typed_subject_alt_names"),
        expected_sans,
        f"{component} gateway does not enforce the exact Alloy certificate identity",
    )
    case.assertEqual(
        virtual_host.get("routes"),
        expected_routes,
        f"{component} gateway route allowlist is incorrect",
    )
    expected_tenant_header = [
        {
            "header": {"key": "X-Scope-OrgID", "value": "k8s-playground"},
            "append_action": "OVERWRITE_IF_EXISTS_OR_ADD",
        }
    ]
    case.assertEqual(
        virtual_host.get("request_headers_to_add"),
        expected_tenant_header,
        f"{component} gateway must control the tenant",
    )
    backend_socket = dig(
        backend,
        "load_assignment",
        "endpoints",
        0,
        "lb_endpoints",
        0,
        "endpoint",
        "address",
        "socket_address",
    )
    case.assertEqual(
        backend_socket,
        {"address": backend_address, "port_value": backend_port},
        f"{component} gateway must target only its internal receiver",
    )
    server_sds_path = dig(
        downstream_tls,
        "common_tls_context",
        "tls_certificate_sds_secret_configs",
        0,
        "sds_config",
        "path_config_source",
        "path",
    )
    client_sds_path = dig(
        downstream_tls,
        "common_tls_context",
        "validation_context_sds_secret_config",
        "sds_config",
        "path_config_source",
        "path",
    )
    case.assertTrue(
        server_sds_path == "/etc/envoy/server-certificate-sds.yaml"
        and client_sds_path == "/etc/envoy/client-validation-sds.yaml"
        and dig(server_sds_config, "resources", 0, "tls_certificate", "watched_directory", "path")
        == "/etc/envoy/tls"
        and dig(client_sds_config, "resources", 0, "validation_context", "watched_directory", "path")
        == "/etc/envoy/tls",
        f"{component} gateway does not use filesystem SDS for certificate rotation",
    )
    monitors = dig(envoy_config, "overload_manager", "resource_monitors")
    monitor = named(
        case,
        monitors,
        "envoy.resource_monitors.global_downstream_max_connections",
        f"{component} downstream connection monitor is missing",
    )
    case.assertEqual(
        dig(monitor, "typed_config", "max_active_downstream_connections"),
        1024,
        f"{component} gateway does not cap downstream connections",
    )


def validate_mimir(case: unittest.TestCase, harness: Harness) -> None:
    docs = harness.kustomize(case, "mimir", "platform/observability/mimir")
    cm = resource(case, docs, "ConfigMap", "mimir-config")
    certificate = resource(case, docs, "Certificate", "minio-client-ca")
    deployment = resource(case, docs, "Deployment", "mimir")
    gateway_cm = resource(case, docs, "ConfigMap", "mimir-gateway-", prefix=True)
    gateway_certificate = resource(case, docs, "Certificate", "mimir-gateway")
    gateway_deployment = resource(case, docs, "Deployment", "mimir-gateway")
    gateway_pdb = resource(case, docs, "PodDisruptionBudget", "mimir-gateway")
    gateway_service = resource(case, docs, "Service", "mimir")
    internal_service = resource(case, docs, "Service", "mimir-internal")
    config = dig(cm, "data", "mimir.yaml")
    runtime = dig(cm, "data", "runtime.yaml")
    envoy = dig(gateway_cm, "data", "envoy.yaml")
    server_sds = dig(gateway_cm, "data", "server-certificate-sds.yaml")
    client_sds = dig(gateway_cm, "data", "client-validation-sds.yaml")
    for value, label in (
        (config, "Mimir config"),
        (runtime, "Mimir runtime config"),
        (envoy, "Mimir gateway Envoy config"),
        (server_sds, "Mimir gateway server SDS config"),
        (client_sds, "Mimir gateway client SDS config"),
    ):
        case.assertIsInstance(value, str, f"{label} is missing")
    runtime_config = yaml.safe_load(runtime)
    case.assertEqual(
        dig(runtime_config, "overrides", "k8s-playground", "max_global_exemplars_per_user"),
        1000,
        "Mimir exemplar ingestion must have a bounded tenant limit",
    )
    ca_volume = named(
        case,
        dig(deployment, "spec", "template", "spec", "volumes"),
        "minio-ca",
        "Mimir MinIO CA volume is missing",
    )
    case.assertEqual(dig(certificate, "spec", "secretName"), "minio-client-ca", "Mimir MinIO CA Certificate is missing")
    case.assertTrue(
        config.count("endpoint: minio.minio.svc.cluster.local:443") == 3
        and config.count("tls_ca_path: /etc/mimir/minio-ca/ca.crt") == 3
        and "insecure: true" not in config,
        "Mimir must use verified MinIO HTTPS on port 443",
    )
    case.assertTrue(
        dig(ca_volume, "secret", "secretName") == "minio-client-ca"
        and any(item.get("key") == "ca.crt" for item in dig(ca_volume, "secret", "items") or []),
        "Mimir does not mount the MinIO CA",
    )
    case.assertIn(
        "http://mimir-internal.observability.svc.cluster.local:8080/alertmanager",
        config,
        "Mimir internal clients must bypass the authenticated gateway",
    )
    case.assertTrue(
        dig(gateway_certificate, "spec", "secretName") == "mimir-gateway-tls"
        and dig(gateway_certificate, "spec", "duration") == "2160h"
        and dig(gateway_certificate, "spec", "renewBefore") == "720h"
        and dig(gateway_certificate, "spec", "privateKey", "rotationPolicy") == "Always",
        "Mimir gateway Certificate rotation is not configured",
    )
    case.assertEqual(dig(gateway_deployment, "spec", "replicas"), 2, "Mimir gateway must run two replicas")
    case.assertEqual(dig(gateway_pdb, "spec", "minAvailable"), 1, "Mimir gateway disruption budget must retain one replica")
    case.assertTrue(
        dig(gateway_service, "spec", "selector", "app.kubernetes.io/name") == "mimir-gateway"
        and dig(gateway_service, "spec", "ports")
        == [{"name": "https", "port": 443, "targetPort": "https", "appProtocol": "https"}],
        "Mimir must expose only authenticated HTTPS",
    )
    case.assertTrue(
        dig(internal_service, "spec", "selector", "app.kubernetes.io/name") == "mimir"
        and any(
            port.get("name") == "http" and port.get("port") == 8080
            for port in dig(internal_service, "spec", "ports") or []
        ),
        "Raw Mimir must remain internal on 8080",
    )
    mimir_routes = [
        {"match": {"path": "/api/v1/push"}, "route": {"cluster": "mimir_internal", "timeout": "30s"}},
        {"match": {"path": "/otlp/v1/metrics"}, "route": {"cluster": "mimir_internal", "timeout": "30s"}},
        {"match": {"prefix": "/"}, "direct_response": {"status": 404}},
    ]
    validate_envoy_structural(
        case,
        envoy,
        server_sds,
        client_sds,
        component="Mimir",
        backend_name="mimir_internal",
        backend_address="mimir-internal.observability.svc.cluster.local",
        backend_port=8080,
        expected_routes=mimir_routes,
    )
    directory = harness.component_dir("mimir")
    config_path = write_native_config(directory, "mimir.yaml", config)
    runtime_path = write_native_config(directory, "runtime.yaml", runtime)
    envoy_path = write_native_config(directory, "gateway-envoy.yaml", envoy)
    server_path = write_native_config(directory, "gateway-server-certificate-sds.yaml", server_sds)
    client_path = write_native_config(directory, "gateway-client-validation-sds.yaml", client_sds)
    harness.run(
        case,
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{config_path}:/etc/mimir/mimir.yaml:ro",
            "-v",
            f"{runtime_path}:/etc/mimir/runtime.yaml:ro",
            "-e",
            "AWS_ACCESS_KEY_ID=test",
            "-e",
            "AWS_SECRET_ACCESS_KEY=test",
            "grafana/mimir:3.1.2",
            "-target=all",
            "-config.file=/etc/mimir/mimir.yaml",
            "-config.expand-env=true",
            "-print.config",
        ],
    )
    tls_dir = openssl_fixture(case, harness, "mimir", "mimir.observability.svc.cluster.local")
    harness.run(
        case,
        [
            "docker",
            "run",
            "--rm",
            "--user",
            "0:0",
            "-v",
            f"{envoy_path}:/etc/envoy/envoy.yaml:ro",
            "-v",
            f"{server_path}:/etc/envoy/server-certificate-sds.yaml:ro",
            "-v",
            f"{client_path}:/etc/envoy/client-validation-sds.yaml:ro",
            "-v",
            f"{tls_dir}:/etc/envoy/tls:ro",
            "envoyproxy/envoy:v1.36.4",
            "--mode",
            "validate",
            "-c",
            "/etc/envoy/envoy.yaml",
        ],
    )


def validate_tempo(case: unittest.TestCase, harness: Harness) -> None:
    docs = harness.helm_render(
        case,
        "tempo",
        "platform/observability/tempo",
        "tempo",
        "observability",
        lint=True,
    )
    cm = resource(case, docs, "ConfigMap", "tempo")
    certificate = resource(case, docs, "Certificate", "tempo-minio-client-ca")
    statefulset = resource(case, docs, "StatefulSet", "tempo-internal")
    internal_service = resource(case, docs, "Service", "tempo-internal")
    gateway_cm = resource(case, docs, "ConfigMap", "tempo-gateway")
    gateway_certificate = resource(case, docs, "Certificate", "tempo-gateway")
    gateway_deployment = resource(case, docs, "Deployment", "tempo-gateway")
    gateway_pdb = resource(case, docs, "PodDisruptionBudget", "tempo-gateway")
    gateway_service = resource(case, docs, "Service", "tempo")
    config = dig(cm, "data", "tempo.yaml")
    envoy = dig(gateway_cm, "data", "envoy.yaml")
    server_sds = dig(gateway_cm, "data", "server-certificate-sds.yaml")
    client_sds = dig(gateway_cm, "data", "client-validation-sds.yaml")
    for value, label in (
        (config, "Tempo config"),
        (envoy, "Tempo gateway Envoy config"),
        (server_sds, "Tempo gateway server SDS config"),
        (client_sds, "Tempo gateway client SDS config"),
    ):
        case.assertIsInstance(value, str, f"{label} is missing")
    pod_spec = dig(statefulset, "spec", "template", "spec")
    container = named(case, dig(pod_spec, "containers"), "tempo", "Tempo container is missing")
    env = {
        entry.get("name"): entry
        for entry in container.get("env", [])
        if isinstance(entry, dict) and entry.get("name")
    }
    ca_volume = named(case, dig(pod_spec, "volumes"), "minio-ca", "Tempo MinIO CA volume is missing")
    claims = dig(statefulset, "spec", "volumeClaimTemplates")
    case.assertIsInstance(claims, list, "Tempo volume claim template is missing")
    case.assertTrue(claims, "Tempo volume claim template is missing")
    claim = claims[0]
    case.assertTrue(
        dig(statefulset, "spec", "replicas") == 1
        and container.get("image") == "docker.io/grafana/tempo:2.10.8",
        "Tempo must run one single-binary replica",
    )
    case.assertTrue(
        dig(internal_service, "spec", "type") == "ClusterIP"
        and dig(internal_service, "spec", "selector", "app.kubernetes.io/name") == "tempo",
        "Raw Tempo must remain internal",
    )
    case.assertIn("multitenancy_enabled: true", config, "Tempo multitenancy is not enabled")
    case.assertTrue(
        all(
            value in config
            for value in (
                "backend: s3",
                "bucket: tempo",
                "endpoint: minio.minio.svc.cluster.local:443",
                "insecure: false",
                "tls_ca_path: /etc/tempo/minio-ca/ca.crt",
                "tls_server_name: minio.minio.svc.cluster.local",
            )
        ),
        "Tempo must use its dedicated MinIO bucket over verified HTTPS",
    )
    case.assertTrue(
        dig(env.get("AWS_ACCESS_KEY_ID"), "valueFrom", "secretKeyRef", "name")
        == "tempo-object-storage-credentials"
        and dig(env.get("AWS_SECRET_ACCESS_KEY"), "valueFrom", "secretKeyRef", "name")
        == "tempo-object-storage-credentials"
        and "-config.expand-env=true" in container.get("args", []),
        "Tempo S3 credentials must come from its bootstrap Secret",
    )
    case.assertTrue(
        dig(certificate, "spec", "secretName") == "tempo-minio-client-ca"
        and dig(ca_volume, "secret", "secretName") == "tempo-minio-client-ca"
        and any(item.get("key") == "ca.crt" for item in dig(ca_volume, "secret", "items") or []),
        "Tempo does not mount its MinIO CA",
    )
    case.assertTrue(
        "path: /var/tempo/wal" in config
        and dig(claim, "spec", "storageClassName") == "standard"
        and dig(claim, "spec", "resources", "requests", "storage") == "5Gi",
        "Tempo WAL persistence is not configured",
    )
    case.assertIs(dig(pod_spec, "automountServiceAccountToken"), False, "Tempo must not use the Kubernetes API token")
    case.assertTrue(
        dig(container, "securityContext", "allowPrivilegeEscalation") is False
        and dig(container, "securityContext", "readOnlyRootFilesystem") is True
        and dig(container, "securityContext", "capabilities", "drop") == ["ALL"],
        "Tempo container hardening is incomplete",
    )
    case.assertTrue(
        dig(gateway_certificate, "spec", "secretName") == "tempo-gateway-tls"
        and dig(gateway_certificate, "spec", "duration") == "2160h"
        and dig(gateway_certificate, "spec", "renewBefore") == "720h"
        and dig(gateway_certificate, "spec", "privateKey", "rotationPolicy") == "Always"
        and "tempo.observability.svc.cluster.local" in (dig(gateway_certificate, "spec", "dnsNames") or []),
        "Tempo gateway Certificate rotation is not configured",
    )
    case.assertTrue(
        dig(gateway_deployment, "spec", "replicas") == 2
        and dig(gateway_pdb, "spec", "minAvailable") == 1,
        "Tempo gateway must run two replicas",
    )
    case.assertTrue(
        bool(dig(gateway_deployment, "spec", "template", "metadata", "annotations", "checksum/config")),
        "Tempo gateway config changes must roll its pods",
    )
    case.assertTrue(
        dig(gateway_service, "spec", "selector", "app.kubernetes.io/name") == "tempo-gateway"
        and dig(gateway_service, "spec", "ports")
        == [{"name": "https", "port": 443, "targetPort": "https", "appProtocol": "https"}],
        "Tempo must expose only authenticated HTTPS",
    )
    tempo_routes = [
        {
            "match": {
                "path": "/v1/traces",
                "headers": [{"name": ":method", "string_match": {"exact": "POST"}}],
            },
            "route": {"cluster": "tempo_internal", "timeout": "30s"},
        },
        {"match": {"prefix": "/"}, "direct_response": {"status": 404}},
    ]
    validate_envoy_structural(
        case,
        envoy,
        server_sds,
        client_sds,
        component="Tempo",
        backend_name="tempo_internal",
        backend_address="tempo-internal.observability.svc.cluster.local",
        backend_port=4318,
        expected_routes=tempo_routes,
    )
    directory = harness.component_dir("tempo")
    config_path = write_native_config(directory, "tempo.yaml", config)
    envoy_path = write_native_config(directory, "gateway-envoy.yaml", envoy)
    server_path = write_native_config(directory, "gateway-server-certificate-sds.yaml", server_sds)
    client_path = write_native_config(directory, "gateway-client-validation-sds.yaml", client_sds)
    harness.run(
        case,
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{config_path}:/conf/tempo.yaml:ro",
            "-e",
            "AWS_ACCESS_KEY_ID=test",
            "-e",
            "AWS_SECRET_ACCESS_KEY=test",
            "grafana/tempo:2.10.8",
            "-config.file=/conf/tempo.yaml",
            "-config.expand-env=true",
            "-config.verify=true",
        ],
    )
    tls_dir = openssl_fixture(case, harness, "tempo", "tempo.observability.svc.cluster.local")
    harness.run(
        case,
        [
            "docker",
            "run",
            "--rm",
            "--user",
            "0:0",
            "-v",
            f"{envoy_path}:/etc/envoy/envoy.yaml:ro",
            "-v",
            f"{server_path}:/etc/envoy/server-certificate-sds.yaml:ro",
            "-v",
            f"{client_path}:/etc/envoy/client-validation-sds.yaml:ro",
            "-v",
            f"{tls_dir}:/etc/envoy/tls:ro",
            "envoyproxy/envoy:v1.36.4",
            "--mode",
            "validate",
            "-c",
            "/etc/envoy/envoy.yaml",
        ],
    )


def validate_alloy(case: unittest.TestCase, harness: Harness) -> None:
    docs = harness.helm_render(
        case,
        "alloy",
        "platform/collectors/alloy",
        "alloy",
        "observability-collectors",
    )
    cm = resource(case, docs, "ConfigMap", "alloy")
    daemonset = resource(case, docs, "DaemonSet", "alloy")
    service = resource(case, docs, "Service", "alloy")
    peer_authentication = resource(case, docs, "PeerAuthentication", "alloy-otlp")
    authorization_policy = resource(case, docs, "AuthorizationPolicy", "alloy-otlp")
    mimir_certificate = resource(case, docs, "Certificate", "alloy-mimir-client")
    tempo_certificate = resource(case, docs, "Certificate", "alloy-tempo-client")
    config = dig(cm, "data", "config.alloy")
    case.assertIsInstance(config, str, "Alloy runtime config is missing")
    otlp_port = named(case, dig(service, "spec", "ports"), "otlp-http", "Alloy OTLP/HTTP Service port is missing")
    rules = dig(authorization_policy, "spec", "rules")
    case.assertIsInstance(rules, list, "Alloy OTLP AuthorizationPolicy rules are missing")
    case.assertTrue(rules, "Alloy OTLP AuthorizationPolicy rules are missing")
    rule = rules[0]
    case.assertEqual(
        dig(daemonset, "spec", "template", "metadata", "annotations", "sidecar.istio.io/inject"),
        "true",
        "Alloy pods must explicitly request Istio injection",
    )
    case.assertTrue(
        otlp_port.get("port") == 4318
        and otlp_port.get("targetPort") == 4318
        and otlp_port.get("appProtocol") == "http",
        "Alloy Service must expose OTLP/HTTP on port 4318",
    )
    case.assertEqual(
        dig(peer_authentication, "spec", "portLevelMtls", "4318", "mode"),
        "STRICT",
        "Alloy OTLP port must require strict mTLS",
    )
    case.assertEqual(
        dig(rule, "from", 0, "source", "principals"),
        ["cluster.local/ns/k8s-playground-service/sa/k8s-playground-service"],
        "Alloy OTLP authorization must allow only the application ServiceAccount",
    )
    case.assertEqual(
        dig(rule, "to", 0, "operation"),
        {
            "ports": ["4318"],
            "methods": ["POST"],
            "paths": ["/v1/metrics", "/v1/traces"],
        },
        "Alloy OTLP authorization must allow only metrics and traces from the application",
    )
    case.assertTrue(
        all(
            value in config
            for value in (
                'otelcol.receiver.otlp "application"',
                'endpoint = "0.0.0.0:4318"',
                'otelcol.processor.memory_limiter "application"',
                'otelcol.processor.batch "application"',
                'otelcol.exporter.otlphttp "mimir"',
            )
        ),
        "Alloy OTLP metrics pipeline is incomplete",
    )
    case.assertTrue(
        'endpoint = "https://mimir.observability.svc.cluster.local/otlp"' in config
        and "metrics = [otelcol.exporter.otlphttp.mimir.input]" in config
        and "metrics = [otelcol.exporter.otlphttp.tempo.input]" not in config
        and 'otelcol.exporter.prometheus "application"' not in config,
        "Application metrics must remain OTLP through Mimir ingestion",
    )
    case.assertTrue(
        'otelcol.processor.batch "application_traces"' in config
        and "traces  = [otelcol.processor.memory_limiter.application.input]" in config
        and "traces  = [otelcol.processor.batch.application_traces.input]" in config
        and "traces = [otelcol.exporter.otlphttp.tempo.input]" in config
        and config.count('otelcol.exporter.otlphttp "tempo"') == 1
        and "traces = [otelcol.exporter.otlphttp.mimir.input]" not in config,
        "Application traces must have one dedicated Tempo pipeline",
    )
    for certificate, secret, label in (
        (mimir_certificate, "alloy-mimir-client-tls", "Mimir"),
        (tempo_certificate, "alloy-tempo-client-tls", "Tempo"),
    ):
        case.assertTrue(
            dig(certificate, "spec", "secretName") == secret
            and dig(certificate, "spec", "uris") == ["spiffe://k8s-playground/collectors/alloy"]
            and dig(certificate, "spec", "duration") == "2160h"
            and dig(certificate, "spec", "renewBefore") == "720h"
            and dig(certificate, "spec", "privateKey", "rotationPolicy") == "Always",
            f"Alloy {label} client Certificate rotation is not configured",
        )
    pod_spec = dig(daemonset, "spec", "template", "spec")
    tempo_volume = named(case, dig(pod_spec, "volumes"), "tempo-client-tls", "Alloy Tempo client TLS volume is missing")
    alloy_container = named(case, dig(pod_spec, "containers"), "alloy", "Alloy container is missing")
    tempo_mount = named(case, alloy_container.get("volumeMounts"), "tempo-client-tls", "Alloy Tempo client TLS mount is missing")
    case.assertTrue(
        dig(tempo_volume, "secret", "secretName") == "alloy-tempo-client-tls"
        and tempo_mount.get("mountPath") == "/var/run/tempo-tls"
        and tempo_mount.get("readOnly") is True,
        "Alloy does not mount its Tempo client identity",
    )
    case.assertTrue(
        'url = "https://mimir.observability.svc.cluster.local/api/v1/push"' in config
        and config.count('ca_file     = "/var/run/mimir-tls/ca.crt"') >= 1
        and config.count('cert_file   = "/var/run/mimir-tls/tls.crt"') >= 1
        and config.count('key_file    = "/var/run/mimir-tls/tls.key"') >= 1
        and 'reload_interval = "1m"' in config
        and "http://mimir.observability" not in config,
        "Alloy must use authenticated HTTPS for both Mimir write protocols",
    )
    case.assertTrue(
        'endpoint = "https://tempo.observability.svc.cluster.local"' in config
        and 'timeout  = "10s"' in config
        and 'ca_file         = "/var/run/tempo-tls/ca.crt"' in config
        and 'cert_file       = "/var/run/tempo-tls/tls.crt"' in config
        and 'key_file        = "/var/run/tempo-tls/tls.key"' in config
        and 'server_name     = "tempo.observability.svc.cluster.local"' in config
        and config.count('reload_interval = "1m"') >= 2
        and 'queue_size        = 16777216' in config
        and 'sizer             = "bytes"' in config
        and "http://tempo.observability" not in config,
        "Alloy must use bounded authenticated HTTPS for Tempo traces",
    )
    case.assertNotIn("X-Scope-OrgID", config, "Alloy must not choose backend tenants directly")
    case.assertTrue(
        'remote_timeout = "10s"' in config
        and 'sample_age_limit = "15m"' in config
        and config.count('max_elapsed_time = "5m"') == 2
        and config.count("block_on_overflow = false") == 2
        and config.count("wait_for_result   = false") == 2,
        "Alloy exporters must have bounded non-blocking retries",
    )
    case.assertNotRegex(config, r"(?m)^\s*logs\s*=", "Alloy must not accept unrouted OTLP logs")
    config_path = write_native_config(harness.component_dir("alloy"), "config.alloy", config)
    harness.run(
        case,
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{config_path}:/etc/alloy/config.alloy:ro",
            "grafana/alloy:v1.18.0",
            "validate",
            "/etc/alloy/config.alloy",
        ],
    )


def walk_json(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def strict_json_loads(content: str) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant: {value}")

    return json.loads(content, parse_constant=reject_constant)


def validate_grafana(case: unittest.TestCase, harness: Harness) -> None:
    allowed = {
        "fixed",
        "shades",
        "thresholds",
        "palette-classic",
        "palette-classic-by-name",
        "continuous-GrYlRd",
        "continuous-RdYlGr",
        "continuous-BlYlRd",
        "continuous-YlRd",
        "continuous-BlPu",
        "continuous-YlBl",
        "continuous-blues",
        "continuous-reds",
        "continuous-greens",
        "continuous-purples",
    }
    dashboards = sorted((ROOT / "platform/observability/grafana/dashboards").glob("*.json"))
    case.assertTrue(dashboards, "Grafana dashboard files are missing")
    for path in dashboards:
        try:
            dashboard = strict_json_loads(path.read_text())
        except (json.JSONDecodeError, ValueError) as error:
            case.fail(f"{path.relative_to(ROOT)} is not valid JSON: {error}")
        for value in walk_json(dashboard):
            if isinstance(value, dict) and isinstance(value.get("color"), dict):
                mode = value["color"].get("mode")
                if mode is not None and mode is not False:
                    case.assertIn(
                        mode,
                        allowed,
                        f"{path.relative_to(ROOT)}: unsupported Grafana color mode {mode!r}",
                    )
    docs = harness.helm_render(
        case,
        "grafana",
        "platform/observability/grafana",
        "grafana",
        "observability",
        lint=True,
    )
    service = resource(case, docs, "Service", "grafana")
    grafana_config = resource(case, docs, "ConfigMap", "grafana")
    case.assertEqual(dig(service, "spec", "type"), "ClusterIP", "Grafana Service is not a ClusterIP")
    datasource_content = dig(grafana_config, "data", "datasources.yaml")
    case.assertIsInstance(datasource_content, str, "Grafana datasource provisioning is missing")
    datasource_config = yaml.safe_load(datasource_content)
    datasources = dig(datasource_config, "datasources")
    mimir_datasource = named(case, datasources, "Mimir", "Mimir datasource is missing")
    tempo_datasource = named(case, datasources, "Tempo", "Tempo datasource is missing")
    case.assertEqual(
        dig(mimir_datasource, "jsonData", "exemplarTraceIdDestinations"),
        [{"datasourceUid": "tempo", "name": "trace_id"}],
        "Mimir exemplars must link trace_id to Tempo",
    )
    case.assertTrue(
        tempo_datasource.get("uid") == "tempo"
        and tempo_datasource.get("type") == "tempo"
        and tempo_datasource.get("access") == "proxy"
        and tempo_datasource.get("url") == "http://tempo-internal.observability.svc.cluster.local:3200"
        and tempo_datasource.get("editable") is False,
        "Grafana Tempo datasource must use the internal query endpoint",
    )
    case.assertTrue(
        dig(tempo_datasource, "jsonData", "httpHeaderName1") == "X-Scope-OrgID"
        and dig(tempo_datasource, "secureJsonData", "httpHeaderValue1") == "k8s-playground",
        "Tempo datasource tenant header is missing",
    )
    case.assertTrue(
        dig(tempo_datasource, "jsonData", "nodeGraph", "enabled") is True
        and dig(tempo_datasource, "jsonData", "search", "hide") is False
        and dig(tempo_datasource, "jsonData", "traceQuery", "timeShiftEnabled") is True,
        "Tempo datasource trace exploration is incomplete",
    )
    config = "\n".join(
        str(value)
        for doc in docs
        if doc.get("kind") == "ConfigMap"
        for value in (doc.get("data") or {}).values()
    )
    case.assertIn("https://grafana.k8s-playground.test/", config, "Grafana management URL is missing")
    case.assertIn(
        "http://mimir-internal.observability.svc.cluster.local:8080/prometheus",
        config,
        "Grafana must use the internal Mimir query endpoint",
    )
    case.assertTrue(
        "X-Scope-OrgID" in config and "k8s-playground" in config,
        "Mimir datasource tenant header is missing",
    )
    dashboard_config = resource(case, docs, "ConfigMap", "grafana-platform-dashboards")
    rendered_dashboards = dashboard_config.get("data", {})
    case.assertEqual(len(rendered_dashboards), 2, "Grafana dashboards are missing")
    for name, value in rendered_dashboards.items():
        try:
            strict_json_loads(value)
        except (json.JSONDecodeError, ValueError) as error:
            case.fail(f"rendered Grafana dashboard {name} is not valid JSON: {error}")


def validate_cert_manager(case: unittest.TestCase, harness: Harness) -> None:
    harness.helm_render(
        case,
        "cert-manager",
        "platform/cert-manager",
        "cert-manager",
        "cert-manager",
        include_crds=True,
    )


def validate_k8s_playground_service(case: unittest.TestCase, harness: Harness) -> None:
    docs = harness.kustomize(case, "k8s-playground-service", "apps/k8s-playground-service")
    deployment = resource(case, docs, "Deployment", "k8s-playground-service")
    container = named(
        case,
        dig(deployment, "spec", "template", "spec", "containers"),
        "service",
        "k8s-playground-service container is missing",
    )
    env = {
        entry.get("name"): entry.get("value")
        for entry in container.get("env", [])
        if isinstance(entry, dict) and entry.get("name")
    }
    case.assertEqual(
        container.get("image"),
        "mblayman/k8s-playground-service:0.2.0",
        "k8s-playground-service image is not the OpenTelemetry release",
    )
    case.assertTrue(
        env.get("OTEL_SDK_DISABLED") == "false"
        and env.get("OTEL_METRICS_EXPORTER") == "otlp"
        and env.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        == "http://alloy.observability-collectors.svc.cluster.local:4318"
        and env.get("OTEL_EXPORTER_OTLP_PROTOCOL") == "http/protobuf",
        "application metrics must use OTLP/HTTP through Alloy",
    )
    case.assertEqual(env.get("OTEL_TRACES_EXPORTER"), "otlp", "application traces must use OTLP through Alloy")
    case.assertEqual(env.get("OTEL_SEMCONV_STABILITY_OPT_IN"), "http", "stable HTTP semantic conventions are not enabled")
    case.assertTrue(
        env.get("OTEL_EXPORTER_OTLP_TIMEOUT") == "10000"
        and env.get("OTEL_BSP_MAX_QUEUE_SIZE") == "2048"
        and env.get("OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT") == "32",
        "OpenTelemetry export is not bounded",
    )


def simple_kustomize(component: str, path: str) -> Callable[[unittest.TestCase, Harness], None]:
    def validate(case: unittest.TestCase, harness: Harness) -> None:
        harness.kustomize(case, component, path)

    return validate


def simple_helm(
    component: str, path: str, release: str, namespace: str
) -> Callable[[unittest.TestCase, Harness], None]:
    def validate(case: unittest.TestCase, harness: Harness) -> None:
        harness.helm_render(case, component, path, release, namespace)

    return validate


VALIDATORS: dict[str, Callable[[unittest.TestCase, Harness], None]] = {
    "scripts": validate_scripts,
    "argocd-kind-bootstrap": validate_argocd_kind_bootstrap,
    "argocd-repositories": validate_argocd_repositories,
    "argocd-config": validate_argocd_config,
    "minio": validate_minio,
    "observability-object-storage-config": validate_object_storage,
    "mimir": validate_mimir,
    "tempo": validate_tempo,
    "alloy": validate_alloy,
    "grafana": validate_grafana,
    "cert-manager": validate_cert_manager,
    "cert-manager-config": simple_kustomize("cert-manager-config", "platform/cert-manager-config"),
    "gateway-api-config": simple_kustomize("gateway-api-config", "platform/gateway-api-config"),
    "management-gateway-config": simple_kustomize(
        "management-gateway-config", "platform/management-gateway-config"
    ),
    "k8s-playground-service": validate_k8s_playground_service,
    "istio-base": simple_helm("istio-base", "platform/istio/base", "istio-base", "istio-system"),
    "istiod": simple_helm("istiod", "platform/istio/istiod", "istiod", "istio-system"),
    "istio-cni": simple_helm("istio-cni", "platform/istio/cni", "istio-cni", "istio-system"),
    "istio-ingressgateway": simple_helm(
        "istio-ingressgateway",
        "platform/istio/ingressgateway",
        "istio-ingressgateway",
        "istio-system",
    ),
    "istio-managementgateway": simple_helm(
        "istio-managementgateway",
        "platform/istio/managementgateway",
        "istio-managementgateway",
        "istio-system",
    ),
}


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(
        description="Validate platform configuration components.",
        epilog="Selectors may be exact component names or shell-style wildcards. No selectors runs all components.",
    )
    argument_parser.add_argument("selectors", nargs="*", help="component selectors")
    argument_parser.add_argument("--list", action="store_true", help="list matching validation components and exit")
    argument_parser.add_argument("--verbose", action="store_true", help="show commands and command output")
    argument_parser.add_argument(
        "--keep-workdir",
        action="store_true",
        help="preserve the temporary validation workdir after success",
    )
    return argument_parser


def select_components(argument_parser: argparse.ArgumentParser, selectors: Sequence[str]) -> list[str]:
    if not selectors:
        return list(COMPONENTS)
    selected: set[str] = set()
    unknown: list[str] = []
    for selector in selectors:
        matches = [component for component in COMPONENTS if fnmatch.fnmatchcase(component, selector)]
        if not matches:
            unknown.append(selector)
        selected.update(matches)
    if unknown:
        argument_parser.error(f"unknown selector(s): {', '.join(unknown)}")
    return [component for component in COMPONENTS if component in selected]


def main(argv: Sequence[str] | None = None) -> int:
    argument_parser = parser()
    args = argument_parser.parse_args(argv)
    components = select_components(argument_parser, args.selectors)
    if args.list:
        for component in components:
            print(component)
        return 0

    VALIDATION_ROOT.mkdir(exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="validate-", dir=VALIDATION_ROOT))
    harness = Harness(workdir, args.verbose)
    suite = unittest.TestSuite(ValidationCase(component, harness) for component in components)
    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
    try:
        result = runner.run(suite)
    except BaseException:
        print(f"validation workdir preserved: {workdir}", file=sys.stderr)
        raise
    if result.wasSuccessful() and not args.keep_workdir:
        shutil.rmtree(workdir)
    else:
        print(f"validation workdir preserved: {workdir}", file=sys.stderr)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
