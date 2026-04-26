package main

import rego.v1

import data.exceptions
import data.lib.k8s

# HelmRelease が参照する source は同一リポ内に定義されているものだけ。
# typo & 出所不明 chart 防止。
#  - HelmRepository style: spec.chart.spec.sourceRef
#  - OCIRepository style:  spec.chartRef
# sourceRef.namespace 省略時は HelmRelease 自身の namespace で resolve。
deny contains msg if {
	some hr in k8s.of_kind("HelmRelease")
	not k8s.ref(hr) in exceptions.helmrelease_source_defined
	src := _source(hr)
	src.kind in {"HelmRepository", "OCIRepository"}
	not _source_exists(hr, src)
	msg := sprintf(
		"%s: source %s/%s/%s is not defined in this repo",
		[k8s.ref(hr), src.kind, _src_ns(hr, src), src.name],
	)
}

_source(hr) := hr.spec.chart.spec.sourceRef

_source(hr) := hr.spec.chartRef if not hr.spec.chart

_source_exists(hr, src) if {
	some s in k8s.of_kind(src.kind)
	s.metadata.name == src.name
	s.metadata.namespace == _src_ns(hr, src)
}

_src_ns(_, src) := src.namespace

_src_ns(hr, src) := hr.metadata.namespace if not src.namespace
