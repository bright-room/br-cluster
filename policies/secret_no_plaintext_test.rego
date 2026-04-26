package main

import rego.v1

test_secret_with_data_fails if {
	s := {
		"apiVersion": "v1",
		"kind": "Secret",
		"metadata": {"name": "leak", "namespace": "default"},
		"data": {"token": "aGVsbG8="},
	}
	count(deny) == 1 with input as _wrap([s])
}

test_secret_with_string_data_fails if {
	s := {
		"apiVersion": "v1",
		"kind": "Secret",
		"metadata": {"name": "leak", "namespace": "default"},
		"stringData": {"token": "hello"},
	}
	count(deny) == 1 with input as _wrap([s])
}

test_secret_without_payload_passes if {
	s := {
		"apiVersion": "v1",
		"kind": "Secret",
		"metadata": {"name": "shell", "namespace": "default"},
	}
	count(deny) == 0 with input as _wrap([s])
}

test_non_secret_ignored if {
	cm := {
		"apiVersion": "v1",
		"kind": "ConfigMap",
		"metadata": {"name": "x", "namespace": "default"},
		"data": {"k": "v"},
	}
	count(deny) == 0 with input as _wrap([cm])
}

test_exception_skips_check if {
	s := {
		"apiVersion": "v1",
		"kind": "Secret",
		"metadata": {"name": "leak", "namespace": "default"},
		"stringData": {"token": "hello"},
	}
	count(deny) == 0 with input as _wrap([s])
		with data.exceptions.secret_no_plaintext as {"Secret/default/leak"}
}

_wrap(items) := [{"path": "test.yaml", "contents": item} | some item in items]
