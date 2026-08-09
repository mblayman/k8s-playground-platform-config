#!/usr/bin/env ruby

require "ipaddr"
require "open3"
require "optparse"

BEGIN_MARKER = "# BEGIN k8s-playground managed kind hosts"
END_MARKER = "# END k8s-playground managed kind hosts"
HOSTNAMES = %w[
  app.k8s-playground.test
  argocd.k8s-playground.test
  grafana.k8s-playground.test
].freeze

options = {
  context: "kind-k8s-playground",
  hosts_file: "/etc/hosts"
}

parser = OptionParser.new do |opts|
  opts.banner = "Usage: #{File.basename($PROGRAM_NAME)} MODE [options]"
  opts.on("--context CONTEXT", "Kubernetes context") { |value| options[:context] = value }
  opts.on("--hosts-file PATH", "Hosts file to inspect") { |value| options[:hosts_file] = value }
  opts.on("--user-gateway-ip IP", "Override the discovered user gateway IP") { |value| options[:user_ip] = value }
  opts.on("--management-gateway-ip IP", "Override the discovered management gateway IP") { |value| options[:management_ip] = value }
end

parser.parse!
mode = ARGV.shift
unless %w[check render remove].include?(mode) && ARGV.empty?
  warn parser
  exit 2
end

def gateway_ip(context, namespace, service)
  command = [
    "kubectl", "--context", context, "-n", namespace,
    "get", "service/#{service}",
    "-o", "jsonpath={.status.loadBalancer.ingress[0].ip}"
  ]
  output, error, status = Open3.capture3(*command)
  abort "Could not read #{namespace}/#{service}: #{error.strip}" unless status.success?

  ip = output.strip
  abort "#{namespace}/#{service} does not have a LoadBalancer IP" if ip.empty?
  ip
end

def validate_ipv4!(value, label)
  address = IPAddr.new(value)
  abort "#{label} is not an IPv4 address: #{value}" unless address.ipv4?
rescue IPAddr::InvalidAddressError
  abort "#{label} is not a valid IP address: #{value}"
end

def locate_managed_block(lines)
  starts = lines.each_index.select { |index| lines[index].chomp == BEGIN_MARKER }
  ends = lines.each_index.select { |index| lines[index].chomp == END_MARKER }

  if starts.length > 1 || ends.length > 1 || starts.length != ends.length
    abort "Managed hosts markers are duplicated or unbalanced; refusing to modify the file"
  end
  return nil if starts.empty?
  abort "Managed hosts markers are reversed; refusing to modify the file" if starts.first > ends.first

  starts.first..ends.first
end

def conflicting_hostnames(lines, managed_range)
  lines.each_with_index.flat_map do |line, index|
    next [] if managed_range&.cover?(index)

    fields = line.split("#", 2).first.to_s.split
    next [] if fields.length < 2

    fields.drop(1) & HOSTNAMES
  end.uniq
end

content = File.binread(options[:hosts_file])
lines = content.lines
managed_range = locate_managed_block(lines)
conflicts = conflicting_hostnames(lines, managed_range)
unless conflicts.empty?
  abort "Managed hostnames exist outside the managed block: #{conflicts.join(", ")}"
end

if mode == "remove"
  if managed_range
    $stdout.write(lines[0...managed_range.begin].join)
    $stdout.write(lines[(managed_range.end + 1)..].to_a.join)
  else
    $stdout.write(content)
  end
  exit 0
end

user_ip = options[:user_ip] || gateway_ip(options[:context], "istio-system", "istio-ingressgateway")
management_ip = options[:management_ip] || gateway_ip(options[:context], "istio-system", "istio-managementgateway")
validate_ipv4!(user_ip, "User gateway IP")
validate_ipv4!(management_ip, "Management gateway IP")

expected_block = <<~HOSTS
  #{BEGIN_MARKER}
  # Kubernetes context: #{options[:context]}
  #{user_ip} app.k8s-playground.test
  #{management_ip} argocd.k8s-playground.test grafana.k8s-playground.test
  #{END_MARKER}
HOSTS

if mode == "check"
  abort "Managed hosts block is missing" unless managed_range

  actual_block = lines[managed_range].join
  abort "Managed hosts block is stale" unless actual_block == expected_block

  puts "Managed hosts block is current for #{options[:context]}."
  exit 0
end

if managed_range
  $stdout.write(lines[0...managed_range.begin].join)
  $stdout.write(expected_block)
  $stdout.write(lines[(managed_range.end + 1)..].to_a.join)
else
  $stdout.write(content)
  $stdout.write("\n") unless content.empty? || content.end_with?("\n")
  $stdout.write("\n") unless content.empty? || content.end_with?("\n\n")
  $stdout.write(expected_block)
end
