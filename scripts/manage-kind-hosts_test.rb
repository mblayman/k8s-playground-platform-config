#!/usr/bin/env ruby

require "minitest/autorun"
require "open3"
require "rbconfig"
require "tempfile"

class ManageKindHostsTest < Minitest::Test
  SCRIPT = File.expand_path("manage-kind-hosts.rb", __dir__)
  BEGIN_MARKER = "# BEGIN k8s-playground managed kind hosts"
  END_MARKER = "# END k8s-playground managed kind hosts"

  def run_script(mode, content)
    Tempfile.create("k8s-playground-hosts") do |file|
      file.write(content)
      file.flush
      Open3.capture3(
        RbConfig.ruby,
        SCRIPT,
        mode,
        "--hosts-file", file.path,
        "--user-gateway-ip", "172.21.255.200",
        "--management-gateway-ip", "172.21.255.201"
      )
    end
  end

  def test_render_preserves_unmanaged_content
    original = "127.0.0.1 localhost\n192.0.2.10 existing.test # keep this\n"
    output, error, status = run_script("render", original)

    assert status.success?, error
    assert output.start_with?(original)
    assert_includes output, "172.21.255.200 app.k8s-playground.test"
    assert_includes output, "172.21.255.201 argocd.k8s-playground.test grafana.k8s-playground.test"
  end

  def test_render_replaces_a_stale_managed_block
    original = <<~HOSTS
      127.0.0.1 localhost

      #{BEGIN_MARKER}
      # Kubernetes context: old-context
      192.0.2.20 app.k8s-playground.test
      192.0.2.21 argocd.k8s-playground.test grafana.k8s-playground.test
      #{END_MARKER}
      192.0.2.10 existing.test
    HOSTS
    output, error, status = run_script("render", original)

    assert status.success?, error
    refute_includes output, "old-context"
    assert_includes output, "192.0.2.10 existing.test"
    assert_equal 1, output.scan(BEGIN_MARKER).length
  end

  def test_render_rejects_conflicting_unmanaged_hostname
    _output, error, status = run_script(
      "render",
      "127.0.0.1 localhost\n192.0.2.30 app.k8s-playground.test\n"
    )

    refute status.success?
    assert_includes error, "outside the managed block"
  end

  def test_remove_deletes_only_the_managed_block
    original = <<~HOSTS
      127.0.0.1 localhost
      #{BEGIN_MARKER}
      # Kubernetes context: kind-k8s-playground
      172.21.255.200 app.k8s-playground.test
      172.21.255.201 argocd.k8s-playground.test grafana.k8s-playground.test
      #{END_MARKER}
      192.0.2.10 existing.test
    HOSTS
    output, error, status = run_script("remove", original)

    assert status.success?, error
    assert_equal "127.0.0.1 localhost\n192.0.2.10 existing.test\n", output
  end
end
