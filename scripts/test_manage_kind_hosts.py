#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("manage-kind-hosts.py")
BEGIN_MARKER = "# BEGIN k8s-playground managed kind hosts"
END_MARKER = "# END k8s-playground managed kind hosts"


class ManageKindHostsTest(unittest.TestCase):
    def run_script(self, mode: str, content: bytes | str, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        data = content.encode() if isinstance(content, str) else content
        with tempfile.NamedTemporaryFile() as hosts_file:
            hosts_file.write(data)
            hosts_file.flush()
            return subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    mode,
                    "--hosts-file",
                    hosts_file.name,
                    "--user-gateway-ip",
                    "172.21.255.200",
                    "--management-gateway-ip",
                    "172.21.255.201",
                    *arguments,
                ],
                capture_output=True,
                check=False,
            )

    def test_render_preserves_unmanaged_content(self) -> None:
        original = b"127.0.0.1 localhost\n192.0.2.10 existing.test # keep this\n"
        result = self.run_script("render", original)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertTrue(result.stdout.startswith(original))
        self.assertIn(b"172.21.255.200 app.k8s-playground.test", result.stdout)
        self.assertIn(
            b"172.21.255.201 argocd.k8s-playground.test grafana.k8s-playground.test",
            result.stdout,
        )

    def test_render_replaces_a_stale_managed_block(self) -> None:
        original = f"""127.0.0.1 localhost

{BEGIN_MARKER}
# Kubernetes context: old-context
192.0.2.20 app.k8s-playground.test
192.0.2.21 argocd.k8s-playground.test grafana.k8s-playground.test
{END_MARKER}
192.0.2.10 existing.test
"""
        result = self.run_script("render", original)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertNotIn(b"old-context", result.stdout)
        self.assertIn(b"192.0.2.10 existing.test", result.stdout)
        self.assertEqual(result.stdout.count(BEGIN_MARKER.encode()), 1)

    def test_render_rejects_conflicting_unmanaged_hostname(self) -> None:
        result = self.run_script(
            "render", "127.0.0.1 localhost\n192.0.2.30 app.k8s-playground.test\n"
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"outside the managed block", result.stderr)

    def test_remove_deletes_only_the_managed_block(self) -> None:
        original = f"""127.0.0.1 localhost
{BEGIN_MARKER}
# Kubernetes context: kind-k8s-playground
172.21.255.200 app.k8s-playground.test
172.21.255.201 argocd.k8s-playground.test grafana.k8s-playground.test
{END_MARKER}
192.0.2.10 existing.test
"""
        result = self.run_script("remove", original)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"127.0.0.1 localhost\n192.0.2.10 existing.test\n")

    def test_check_accepts_current_block(self) -> None:
        rendered = self.run_script("render", "127.0.0.1 localhost\n")
        result = self.run_script("check", rendered.stdout)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b"Managed hosts block is current", result.stdout)

    def test_rejects_unbalanced_markers(self) -> None:
        result = self.run_script("render", f"127.0.0.1 localhost\n{BEGIN_MARKER}\n")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"duplicated or unbalanced", result.stderr)

    def test_rejects_ipv6_gateway(self) -> None:
        result = self.run_script("render", "", "--user-gateway-ip", "2001:db8::1")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"is not an IPv4 address", result.stderr)

    def test_render_preserves_crlf_unmanaged_content(self) -> None:
        original = b"127.0.0.1 localhost\r\n192.0.2.10 existing.test\r\n"
        result = self.run_script("render", original)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertTrue(result.stdout.startswith(original))


if __name__ == "__main__":
    unittest.main()
