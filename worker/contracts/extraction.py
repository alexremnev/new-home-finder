"""Extraction schema: where values live in a page.

This module defines the only vocabulary an inferred schema may use. A rule can
name a path, a selector, an attribute, a regular expression, or a constant, and
nothing else. There is no expression language and no generated code, so a schema
produced by a model describes *where* a value is, never *what to do* with it.
All interpretation — units, dates, enumerations — happens in the normalisation
layer, which is written by hand and covered by tests.

The same definitions serve two purposes: the storage format for
`parse_schemas.schema`, and the JSON Schema handed to the model as a structured
output format. One contract, so there is no text parsing on the way back.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Strategy = Literal["next_data", "api_json", "jsonld", "dom"]
Verdict = Literal["ok", "degraded", "broken"]


class FieldRule(BaseModel):
    """How to locate one field within a single listing element."""

    model_config = {"extra": "forbid"}

    # Structured strategies (next_data, api_json, jsonld): JSONPath relative to
    # the listing element.
    path: str | None = None

    # The dom strategy: CSS selector relative to the listing element. The
    # literal "self" addresses the element itself.
    sel: str | None = None
    attr: str | None = None

    # Applied to whichever of the above produced a value.
    regex: str | None = Field(default=None, description="first capture group is taken")

    # A fixed value, for facts implied by the source rather than the page.
    const: str | int | float | bool | None = None

    @model_validator(mode="after")
    def _exactly_one_locator(self) -> FieldRule:
        locators = [self.path is not None, self.sel is not None, self.const is not None]
        if sum(locators) != 1:
            raise ValueError("provide exactly one of: path, sel, const")
        if self.attr is not None and self.sel is None:
            raise ValueError("attr applies to sel only")
        return self


class ExtractionSchema(BaseModel):
    """A complete recipe for turning one response body into listing records."""

    model_config = {"extra": "forbid"}

    strategy: Strategy
    root: str | None = Field(
        default=None, description="JSONPath to the array of listings; structured strategies"
    )
    item: str | None = Field(
        default=None, description="CSS selector for one listing element; dom strategy"
    )
    fields: dict[str, FieldRule]

    @model_validator(mode="after")
    def _container_matches_strategy(self) -> ExtractionSchema:
        if self.strategy == "dom":
            if not self.item:
                raise ValueError("dom strategy requires item")
        elif not self.root:
            raise ValueError(f"{self.strategy} strategy requires root")
        if not self.fields:
            raise ValueError("fields must not be empty")
        return self


class Health(BaseModel):
    """Result of validating an extraction against a body.

    A candidate schema is only activated after passing these checks against the
    same body it was inferred from, so a bad inference costs milliseconds rather
    than weeks of silence.
    """

    items_found: int
    fill_rate: dict[str, float] = Field(default_factory=dict)
    implausible: int = 0
    id_duplicates: int = 0
    verdict: Verdict
    notes: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.verdict != "broken"
