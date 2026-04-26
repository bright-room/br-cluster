package exceptions

import rego.v1

# Phase 1 では空。例外を追加するときは `<policy>: {"<resource ref>"}` の形で
# ここに集約する。例外の追加 PR にはコミットメッセージで理由を必ず書く
# (CLAUDE.md の "Policy as Code" セクション参照)。
#
# 例:
#   secret_no_plaintext := {"Secret/some-ns/some-name"}
default secret_no_plaintext := set()

default helmrelease_source_defined := set()

default helmrelease_version_pinned := set()

default lb_service_pinned := set()
