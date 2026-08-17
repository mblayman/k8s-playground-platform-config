#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///

import argparse
import ipaddress
import subprocess
import sys
from pathlib import Path


BEGIN_MARKER = "# BEGIN k8s-playground managed kind hosts"
END_MARKER = "# END k8s-playground managed kind hosts"
HOSTNAMES = {
    "app.k8s-playground.test",
    "argocd.k8s-playground.test",
    "grafana.k8s-playground.test",
}


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def gateway_ip(context: str, namespace: str, service: str) -> str:
    command = [
        "kubectl",
        "--context",
        context,
        "-n",
        namespace,
        "get",
        f"service/{service}",
        "-o",
        "jsonpath={.status.loadBalancer.ingress[0].ip}",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        fail(f"Could not read {namespace}/{service}: {result.stderr.strip()}")

    value = result.stdout.strip()
    if not value:
        fail(f"{namespace}/{service} does not have a LoadBalancer IP")
    return value


def validate_ipv4(value: str, label: str) -> None:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        fail(f"{label} is not a valid IP address: {value}")
    if address.version != 4:
        fail(f"{label} is not an IPv4 address: {value}")


def line_without_ending(line: bytes) -> bytes:
    return line.rstrip(b"\r\n")


def locate_managed_block(lines: list[bytes]) -> tuple[int, int] | None:
    starts = [index for index, line in enumerate(lines) if line_without_ending(line) == BEGIN_MARKER.encode()]
    ends = [index for index, line in enumerate(lines) if line_without_ending(line) == END_MARKER.encode()]

    if len(starts) > 1 or len(ends) > 1 or len(starts) != len(ends):
        fail("Managed hosts markers are duplicated or unbalanced; refusing to modify the file")
    if not starts:
        return None
    if starts[0] > ends[0]:
        fail("Managed hosts markers are reversed; refusing to modify the file")
    return starts[0], ends[0]


def conflicting_hostnames(lines: list[bytes], managed_range: tuple[int, int] | None) -> list[str]:
    conflicts: list[str] = []
    for index, line in enumerate(lines):
        if managed_range and managed_range[0] <= index <= managed_range[1]:
            continue
        fields = line.split(b"#", 1)[0].split()
        if len(fields) < 2:
            continue
        for field in fields[1:]:
            hostname = field.decode(errors="surrogateescape")
            if hostname in HOSTNAMES and hostname not in conflicts:
                conflicts.append(hostname)
    return conflicts


def expected_block(context: str, user_ip: str, management_ip: str) -> bytes:
    return (
        f"{BEGIN_MARKER}\n"
        f"# Kubernetes context: {context}\n"
        f"{user_ip} app.k8s-playground.test\n"
        f"{management_ip} argocd.k8s-playground.test grafana.k8s-playground.test\n"
        f"{END_MARKER}\n"
    ).encode()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(usage="%(prog)s MODE [options]")
    parser.add_argument("mode", choices=("check", "render", "remove"))
    parser.add_argument("--context", default="kind-k8s-playground", help="Kubernetes context")
    parser.add_argument("--hosts-file", default="/etc/hosts", help="Hosts file to inspect")
    parser.add_argument("--user-gateway-ip", help="Override the discovered user gateway IP")
    parser.add_argument("--management-gateway-ip", help="Override the discovered management gateway IP")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    content = Path(args.hosts_file).read_bytes()
    lines = content.splitlines(keepends=True)
    managed_range = locate_managed_block(lines)
    conflicts = conflicting_hostnames(lines, managed_range)
    if conflicts:
        fail(f"Managed hostnames exist outside the managed block: {', '.join(conflicts)}")

    if args.mode == "remove":
        if managed_range:
            start, end = managed_range
            output = b"".join(lines[:start] + lines[end + 1 :])
        else:
            output = content
        sys.stdout.buffer.write(output)
        return

    user_ip = args.user_gateway_ip or gateway_ip(args.context, "istio-system", "istio-ingressgateway")
    management_ip = args.management_gateway_ip or gateway_ip(
        args.context, "istio-system", "istio-managementgateway"
    )
    validate_ipv4(user_ip, "User gateway IP")
    validate_ipv4(management_ip, "Management gateway IP")
    block = expected_block(args.context, user_ip, management_ip)

    if args.mode == "check":
        if not managed_range:
            fail("Managed hosts block is missing")
        start, end = managed_range
        if b"".join(lines[start : end + 1]) != block:
            fail("Managed hosts block is stale")
        print(f"Managed hosts block is current for {args.context}.")
        return

    if managed_range:
        start, end = managed_range
        output = b"".join(lines[:start]) + block + b"".join(lines[end + 1 :])
    else:
        separator = b""
        if content and not content.endswith(b"\n"):
            separator += b"\n"
        if content and not content.endswith(b"\n\n"):
            separator += b"\n"
        output = content + separator + block
    sys.stdout.buffer.write(output)


if __name__ == "__main__":
    main()
