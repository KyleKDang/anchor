"""What Anthropic's structured outputs will accept as an answer schema.

Its own module rather than a corner of a fake, because both fakes need it and neither
owns it: :mod:`fakeanthropic` checks what goes on the wire, and :mod:`fakellm` checks
every operation's schema under the seam. Putting it in either would make one fake import
the other.

Transcribed 2026-09-09 from the supported-keyword tables at
https://platform.claude.com/docs/en/build-with-claude/structured-outputs - the tables are
the authority, and anything not on them answers 400. Two rules are worth reading twice
because they are what #116 broke: ``maxItems`` is not supported at all, and ``minItems``
is supported only as 0 or 1. A bound outside that belongs on the pydantic model, which is
what validates the answer anyway.

Deliberately not enforced, because the documentation says the opposite and a check that
refuses a legal schema is its own outage: a property may be omitted from ``required``.
Optional properties are supported.
"""

from typing import Any

_UNIVERSAL_KEYWORDS = frozenset(
    # The structural keywords are universal rather than object-only on purpose: a node
    # that is only a ``{"$ref": ...}`` or an ``anyOf`` branch carries no ``type`` at all,
    # and filing them under objects would refuse a schema the provider accepts.
    {"type", "enum", "const", "default", "description", "title", "anyOf", "allOf", "$ref", "$defs"}
)

_KEYWORDS_BY_KIND: dict[str, frozenset[str]] = {
    "object": frozenset({"properties", "required", "additionalProperties"}),
    "array": frozenset({"items", "minItems"}),
    "string": frozenset({"format"}),
}

_SUPPORTED_STRING_FORMATS = frozenset(
    {"date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid"}
)


class RejectedSchema(AssertionError):
    """A schema the real API would answer 400 for.

    Deliberately an ``AssertionError`` rather than a 400 response. A 400 is what the
    provider sends, but the adapter turns one into ``ProviderUnavailable``, which is a
    ``Skipped``, which every caller answers by serving what it cached - so the request
    that can never succeed looks exactly like the month the cap ran out. That silence is
    the whole of #116, and a test must not be able to absorb it the way production did.
    """


def assert_schema_is_accepted(schema: Any, *, where: str = "schema") -> None:
    """Refuse what structured outputs refuses, so CI catches it instead of production."""
    if not isinstance(schema, dict):
        raise RejectedSchema(
            f"{where}: a JSON schema must be an object, not {type(schema).__name__}"
        )

    kind = schema.get("type")
    if isinstance(kind, list):
        raise RejectedSchema(
            f"{where}: a union type like {kind!r} is not supported - write it as an 'anyOf'"
        )
    if kind is None and "properties" in schema:
        # A typeless node carrying properties is an object in everything but the word.
        kind = "object"

    allowed = _UNIVERSAL_KEYWORDS | _KEYWORDS_BY_KIND.get(str(kind), frozenset())
    for key in schema:
        if key not in allowed:
            raise RejectedSchema(
                f"{where}: {key!r} is not supported on type {kind!r} - "
                "structured outputs answers 400 for it"
            )

    for value in schema.get("enum") or ():
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise RejectedSchema(f"{where}: only scalars may appear in an enum, not {value!r}")

    if kind == "object":
        _check_object(schema, where)
    elif kind == "array":
        _check_array(schema, where)
    elif kind == "string":
        _check_string(schema, where)

    for keyword in ("anyOf", "allOf"):
        for index, branch in enumerate(schema.get(keyword) or ()):
            assert_schema_is_accepted(branch, where=f"{where}.{keyword}[{index}]")
    for name, defined in (schema.get("$defs") or {}).items():
        assert_schema_is_accepted(defined, where=f"{where}.$defs.{name}")


def _check_object(schema: dict[str, Any], where: str) -> None:
    if schema.get("additionalProperties") is not False:
        raise RejectedSchema(
            f"{where}: 'additionalProperties' must be False on an object, "
            f"not {schema.get('additionalProperties')!r}"
        )
    for name, child in (schema.get("properties") or {}).items():
        assert_schema_is_accepted(child, where=f"{where}.{name}")


def _check_array(schema: dict[str, Any], where: str) -> None:
    # The one bounded exception in the whole set: 0 and 1 are accepted, nothing above.
    if "minItems" in schema and schema["minItems"] not in (0, 1):
        raise RejectedSchema(
            f"{where}: 'minItems' supports only 0 and 1, not {schema['minItems']!r}"
        )
    if "items" in schema:
        assert_schema_is_accepted(schema["items"], where=f"{where}.items")


def _check_string(schema: dict[str, Any], where: str) -> None:
    if schema.get("format") not in (None, *_SUPPORTED_STRING_FORMATS):
        raise RejectedSchema(f"{where}: string format {schema['format']!r} is not supported")
