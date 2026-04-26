package main

import rego.v1

test_lb_with_pinned_ip_passes if {
	s := _svc("envoy", "envoy-gateway", "LoadBalancer", {"lb-ipam.cilium.io/ips": "10.0.0.10"})
	count(deny) == 0 with input as _wrap([s])
}

test_lb_without_annotation_fails if {
	s := _svc("envoy", "envoy-gateway", "LoadBalancer", {})
	count(deny) == 1 with input as _wrap([s])
}

test_lb_with_empty_annotation_fails if {
	s := _svc("envoy", "envoy-gateway", "LoadBalancer", {"lb-ipam.cilium.io/ips": ""})
	count(deny) == 1 with input as _wrap([s])
}

test_clusterip_ignored if {
	s := _svc("svc", "default", "ClusterIP", {})
	count(deny) == 0 with input as _wrap([s])
}

test_nodeport_ignored if {
	s := _svc("svc", "default", "NodePort", {})
	count(deny) == 0 with input as _wrap([s])
}

test_exception_skips_check if {
	s := _svc("envoy", "envoy-gateway", "LoadBalancer", {})
	count(deny) == 0 with input as _wrap([s])
		with data.exceptions.lb_service_pinned as {"Service/envoy-gateway/envoy"}
}

_svc(name, ns, type, annotations) := {
	"apiVersion": "v1",
	"kind": "Service",
	"metadata": {"name": name, "namespace": ns, "annotations": annotations},
	"spec": {"type": type},
}

_wrap(items) := [{"path": "test.yaml", "contents": item} | some item in items]
