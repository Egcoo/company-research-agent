"""Keep SEC identity and extracted-document contracts within the adapter layer."""

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    """Reject unexpected fields at model and tool boundaries."""

    model_config = ConfigDict(extra="forbid")


class CompanyIdentity(Contract):
    """Identify a filing entity using the SEC ticker mapping."""

    name: str = Field(min_length=1, max_length=200)
    ticker: str = Field(pattern=r"^[A-Z0-9.-]{1,12}$")
    cik: str = Field(pattern=r"^\d{10}$")


class TextSnippet(Contract):
    """Preserve a bounded excerpt and its extracted-content position."""

    snippet_id: str
    locator: str
    content: str = Field(min_length=1, max_length=6000)
