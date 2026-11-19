"""Evaluators config (evaluators.yaml) for `evalgate evaluate`: built-ins only, no code loading.

Unknown keys are rejected so a typo never silently drops a check. Arguments are checked again
by the evaluator factories when the config is loaded (io/evaluators_config.py).
"""

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evalgate.models.mapping import ImportManifest

Name = Annotated[str, Field(min_length=1)]


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: Name


class JsonValidSpec(_Spec):
    type: Literal["json_valid"]


class JsonSchemaSpec(_Spec):
    type: Literal["json_schema"]
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    schema_file: Annotated[str, Field(min_length=1)] | None = None

    @model_validator(mode="after")
    def _one_source(self) -> Self:
        if (self.schema_ is None) == (self.schema_file is None):
            raise ValueError("give exactly one of `schema` (inline) or `schema_file`")
        return self


class RegexSpec(_Spec):
    type: Literal["regex"]
    pattern: str
    must_match: bool = True


class ContainsSpec(_Spec):
    type: Literal["contains", "not_contains"]
    text: str
    ignore_case: bool = False


class ExactMatchSpec(_Spec):
    type: Literal["exact_match"]
    field: Name = "expected"
    normalize: list[Literal["strip", "casefold"]] = []


class LengthSpec(_Spec):
    type: Literal["length"]
    min: int | None = None
    max: int | None = None
    unit: Literal["chars", "words"] = "chars"


class NumericCloseSpec(_Spec):
    type: Literal["numeric_close"]
    field: Name = "expected"
    abs_tol: float = 0.0
    rel_tol: float = 0.0


EvaluatorSpec = Annotated[
    JsonValidSpec
    | JsonSchemaSpec
    | RegexSpec
    | ContainsSpec
    | ExactMatchSpec
    | LengthSpec
    | NumericCloseSpec,
    Field(discriminator="type"),
]


class EvaluatorsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal[1]
    manifest: ImportManifest
    store_output: bool = False
    evaluators: Annotated[list[EvaluatorSpec], Field(min_length=1)]
