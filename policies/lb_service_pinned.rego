package main

import rego.v1

import data.exceptions
import data.lib.k8s

# LoadBalancer Service は Cilium LB-IPAM の自動採番に頼らず、
# `lb-ipam.cilium.io/ips` annotation で IP を固定する。
# 自動採番に流すと DNS / nftables / Cilium L2 Announcement と不整合になる
# (docs/network.md "LB IP の払い出し方式" 参照)。
deny contains msg if {
	some s in k8s.of_kind("Service")
	s.spec.type == "LoadBalancer"
	not k8s.ref(s) in exceptions.lb_service_pinned
	not _has_pinned_ip(s)
	msg := sprintf(
		"%s: LoadBalancer Service must pin IP via 'lb-ipam.cilium.io/ips' annotation",
		[k8s.ref(s)],
	)
}

_has_pinned_ip(s) if {
	ips := s.metadata.annotations["lb-ipam.cilium.io/ips"]
	ips != ""
}
