from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class ServerType(StrEnum):
    GATEWAY = "gateway"
    NODE = "node"
    EXTERNAL = "external"


class K8sRole(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    WORKER = "worker"


class ServerDefinition(BaseModel):
    name: str
    type: ServerType
    k8s_role: K8sRole | None = None

    @property
    def needs_network_config(self) -> bool:
        return self.type == ServerType.GATEWAY


class Inventory(BaseModel):
    environments: list[str]
    servers: list[ServerDefinition]
