from pydantic import BaseModel, Field


class NewMember(BaseModel):
    """Cadastro/vínculo de usuário em uma arena. Sem 'name' = fluxo legado (vincular conta existente por e-mail)."""
    email: str = Field(max_length=254)
    role: str
    name: str | None = Field(None, max_length=120)
    phone: str | None = Field(None, max_length=30)
    active: bool = True
    authMethod: str = "password"
    linkExisting: bool = False
    sendInvite: bool = True


class MemberChange(BaseModel):
    role: str | None = None
    status: str | None = Field(None, pattern=r"^(active|suspended)$")
    name: str | None = Field(None, max_length=120)
    phone: str | None = Field(None, max_length=30)
    authMethod: str | None = None
