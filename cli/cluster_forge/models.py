from enum import StrEnum

from pydantic import BaseModel


class ServerType(StrEnum):
    GATEWAY = "gateway"
    NODE = "node"
    EXTERNAL = "external"


class ServerDefinition(BaseModel):
    name: str
    type: ServerType

    @property
    def needs_network_config(self) -> bool:
        return self.type == ServerType.GATEWAY


class Inventory(BaseModel):
    environments: list[str]
    servers: list[ServerDefinition]
