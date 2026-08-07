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
    """How to locate one field within a single listing element.

    Four ways to say where a value is, and exactly one must be chosen:

      path   JSONPath, for the structured strategies
      sel    CSS selector, for the dom strategy
      label  the text of a label cell; the value is taken from the cell beside it
      const  a fixed value, for a fact implied by the source rather than the page

    `label` exists because portals present listing details as label-and-value
    tables, and a CSS path to the value cell encodes its position. Anchoring on
    the label text instead survives the rows being reordered, which is the most
    common kind of markup change.
    """

    model_config = {"extra": "forbid"}

    path: str | None = None

    # The literal "self" addresses the listing element itself.
    sel: str | None = None

    label: str | None = Field(
        default=None,
        description="matched against the start of a label cell's text, "
        "case-insensitively, so a help tooltip inside the cell does not defeat it",
    )
    label_in: str = Field(default="td,th,dt", description="tag names that may hold a label")
    value_in: str = Field(default="td,th,dd", description="tag names that may hold a value")

    attr: str | None = None

    # Applied to whatever text the locator produced.
    regex: str | None = Field(default=None, description="first capture group is taken")

    # A yes/no field is often an icon rather than text: a tick or a cross in the
    # value cell. These test for its presence and yield True, False, or None for
    # "the listing did not say" — three states, because the data has three.
    true_if: str | None = Field(
        default=None, description="CSS selector; if it matches inside the value, the field is True"
    )
    false_if: str | None = Field(
        default=None, description="CSS selector; if it matches inside the value, the field is False"
    )

    const: str | int | float | bool | None = None

    @model_validator(mode="after")
    def _exactly_one_locator(self) -> FieldRule:
        locators = [
            self.path is not None,
            self.sel is not None,
            self.label is not None,
            self.const is not None,
        ]
        if sum(locators) != 1:
            raise ValueError("provide exactly one of: path, sel, label, const")
        if self.attr is not None and self.sel is None and self.label is None:
            raise ValueError("attr applies to sel or label")
        if (self.true_if or self.false_if) and self.path is not None:
            raise ValueError("true_if and false_if are for the dom strategy")
        return self

    @property
    def is_tri_state(self) -> bool:
        return self.true_if is not None or self.false_if is not None


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
