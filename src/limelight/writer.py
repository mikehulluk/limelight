from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import re
import shutil
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterable, Mapping, Sequence

from .images import (
    DEFAULT_MAX_WIDTH,
    ConvertedImage,
    ImageConversionError,
    convert_image,
)
from .largeseries import DEFAULT_CHUNK_SIZE
from .signing import DocumentSignature, sign_document
from .story_markdown import (
    ImageWidthError,
    numbered_story_items,
    resolve_cross_references,
    rewrite_image_destinations,
    story_image_references,
    unparsed_attribute_text,
)

logger = logging.getLogger(__name__)

# The Dhall types a manifest is written against. They ship inside the package
# so that an installed limelight can write one, and are copied into every
# package so that a package stands on its own.
LANGUAGE_REFERENCE_DIR = Path(__file__).resolve().parent / "language-reference"


DATA_TYPES = {
    "text",
    "category",
    "integer",
    "natural",
    "double",
    "boolean",
    "date",
    "datetime",
    "calendarPeriod",
}

WORD48_MAX = 2**48
STEP_UNITS = {
    "ns": "ns",
    "us": "us",
    "ms": "ms",
    "s": "s",
    "Gs": "Gs",
    "gs": "Gs",
}


def array(
    values: Sequence[Any],
    *,
    dtype: str | None = None,
    label: str | None = None,
    unit: str | None = None,
    description: str | None = None,
    nullable: bool | None = None,
) -> "ArraySpec":
    return ArraySpec(
        values=list(values),
        dtype=dtype,
        label=label,
        unit=unit,
        description=description,
        nullable=nullable,
    )


def summary_statistics(values: Sequence[float]) -> dict[str, float]:
    """Compute min/max/mean/sd (population standard deviation) for `values`.

    Intended for precomputing small summary tables at example-build time -
    the result is ordinary Python data meant to be written straight into an
    array()/TableViewSpec, not evaluated by any Limelight runtime transform.
    """
    if not values:
        raise ValueError("summary_statistics requires at least one value")
    count = len(values)
    mean = sum(values) / count
    variance = sum((value - mean) ** 2 for value in values) / count
    return {
        "min": min(values),
        "max": max(values),
        "mean": mean,
        "sd": math.sqrt(variance),
    }


# Line separators that str.splitlines honours but json.dumps leaves alone once
# ensure_ascii is off; written raw they would break the manifest's indentation.
_ESCAPED_LINE_SEPARATORS = {ord(c): f"\\u{ord(c):04x}" for c in "\x85\u2028\u2029"}


def _quote(value: str) -> str:
    # Dhall shares JSON's escapes but not its surrogate pairs, so characters
    # outside the basic plane must be written as themselves (the file is
    # UTF-8), and "${" would otherwise start an interpolation.
    quoted = json.dumps(value, ensure_ascii=False).translate(_ESCAPED_LINE_SEPARATORS)
    return quoted.replace("${", "\\${")


def _column_ref(table_id: str, column: str) -> str:
    return f"{table_id}['{column}']"


def _dhall_bool(value: bool) -> str:
    return "True" if value else "False"


def _dhall_float(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError(f"Dhall cannot represent non-finite float {value!r}")

    rendered = format(float(value), ".12g")
    if "e" not in rendered.lower() and "." not in rendered:
        rendered += ".0"
    return rendered


def _indent(text: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line if line else line for line in text.splitlines())


def _field(prefix: str, name: str, value: str, indent: int) -> str:
    if "\n" not in value:
        return f"{' ' * indent}{prefix} {name} = {value}"

    return f"{' ' * indent}{prefix} {name} =\n{_indent(value, indent + 2)}"


def _record(fields: Sequence[tuple[str, str]], indent: int = 0) -> str:
    if not fields:
        return "{}"

    lines = [" " * indent + "{ " + f"{fields[0][0]} = "]
    first_value = fields[0][1]
    if "\n" in first_value:
        lines[0] = " " * indent + "{ " + fields[0][0] + " ="
        lines.append(_indent(first_value, indent + 2))
    else:
        lines[0] += first_value

    for name, value in fields[1:]:
        lines.append(_field(",", name, value, indent))

    lines.append(" " * indent + "}")
    return "\n".join(lines)


def _list(items: Sequence[str], item_type: str, indent: int = 0) -> str:
    if not items:
        return f"[] : List {item_type}"

    lines = [" " * indent + "["]
    for index, item in enumerate(items):
        prefix = "," if index else ""
        if "\n" in item:
            lines.append(" " * indent + f"{prefix} " + item.splitlines()[0])
            for line in item.splitlines()[1:]:
                lines.append(" " * (indent + 2) + line)
        else:
            lines.append(" " * indent + f"{prefix} {item}")
    lines.append(" " * indent + "]")
    return "\n".join(lines)


def _optional_text(value: str | None) -> str:
    return f"Some {_quote(value)}" if value is not None else "None Text"


def _optional_display_text(value: str | None) -> str:
    return f"Some {_quote(value)}" if value is not None else "None Limelight.DisplayText"


def _optional_text_list(values: Sequence[str] | None) -> str:
    if values is None:
        return "None (List Text)"
    return f"Some {_list([_quote(value) for value in values], 'Text')}"


def _optional_axis_group_id(value: str | None) -> str:
    return f"Some {_quote(value)}" if value is not None else "None Limelight.AxisGroupId"


# A4 with the margins the PDF exporter has always used.
DEFAULT_PAGE_WIDTH_MM = 210.0
DEFAULT_PAGE_HEIGHT_MM = 297.0
DEFAULT_PAGE_MARGIN_MM = 15.0


@dataclass(frozen=True)
class PageGeometry:
    """The page a story is laid out on.

    ``width_mm`` and ``height_mm`` describe the whole page with the margins
    inside them, so A4 is 210 by 297 with a 15mm ``margin_lr_mm`` and a 180mm
    text column. ``margin_lr_mm`` is the space either side of the column and
    ``margin_tb_mm`` the space above and below it. ``None`` means "not a
    physical measurement": a width fills whatever the document is shown in,
    and a height runs as long as the content.
    """

    width_mm: float | None = DEFAULT_PAGE_WIDTH_MM
    height_mm: float | None = DEFAULT_PAGE_HEIGHT_MM
    margin_lr_mm: float = DEFAULT_PAGE_MARGIN_MM
    margin_tb_mm: float = DEFAULT_PAGE_MARGIN_MM

    @classmethod
    def paged(
        cls,
        *,
        width_mm: float = DEFAULT_PAGE_WIDTH_MM,
        height_mm: float = DEFAULT_PAGE_HEIGHT_MM,
        margin_lr_mm: float = DEFAULT_PAGE_MARGIN_MM,
        margin_tb_mm: float = DEFAULT_PAGE_MARGIN_MM,
    ) -> "PageGeometry":
        """A page of a fixed size, broken across pages like A4."""

        return cls(
            width_mm=width_mm,
            height_mm=height_mm,
            margin_lr_mm=margin_lr_mm,
            margin_tb_mm=margin_tb_mm,
        )

    @classmethod
    def continuous(
        cls,
        *,
        width_mm: float = DEFAULT_PAGE_WIDTH_MM,
        margin_lr_mm: float = DEFAULT_PAGE_MARGIN_MM,
        margin_tb_mm: float = DEFAULT_PAGE_MARGIN_MM,
    ) -> "PageGeometry":
        """A fixed column that runs as long as the content."""

        return cls(
            width_mm=width_mm,
            height_mm=None,
            margin_lr_mm=margin_lr_mm,
            margin_tb_mm=margin_tb_mm,
        )

    @classmethod
    def fluid(
        cls,
        *,
        margin_lr_mm: float = DEFAULT_PAGE_MARGIN_MM,
        margin_tb_mm: float = DEFAULT_PAGE_MARGIN_MM,
    ) -> "PageGeometry":
        """Fills whatever it is shown in, and runs as long as the content."""

        return cls(
            width_mm=None,
            height_mm=None,
            margin_lr_mm=margin_lr_mm,
            margin_tb_mm=margin_tb_mm,
        )

    def render(self) -> str:
        width = (
            f"Limelight.PageWidth.millimetres {_dhall_float(self.width_mm)}"
            if self.width_mm is not None
            else "Limelight.PageWidth.viewport"
        )
        height = (
            f"Limelight.PageHeight.millimetres {_dhall_float(self.height_mm)}"
            if self.height_mm is not None
            else "Limelight.PageHeight.continuous"
        )
        return _record(
            [
                ("width", width),
                ("height", height),
                ("marginLR", _dhall_float(self.margin_lr_mm)),
                ("marginTB", _dhall_float(self.margin_tb_mm)),
            ]
        )


DEFAULT_BLOCK_GAP_MM = 3.0
DEFAULT_FIGURE_GAP_MM = 4.5
DEFAULT_HEADING_GAP_BEFORE_MM = 5.0
DEFAULT_HEADING_GAP_AFTER_MM = 2.0


@dataclass(frozen=True)
class StorySpacing:
    """The vertical rhythm of the column: how far apart its blocks sit.

    All in millimetres, like the page. Each gap is the whole distance between
    the two blocks it separates. ``block_gap_mm`` separates consecutive blocks
    of prose; ``figure_gap_mm`` sits above and below a figure, whether a
    figure view or a story image; ``heading_gap_before_mm`` and
    ``heading_gap_after_mm`` sit either side of a heading.
    """

    block_gap_mm: float = DEFAULT_BLOCK_GAP_MM
    figure_gap_mm: float = DEFAULT_FIGURE_GAP_MM
    heading_gap_before_mm: float = DEFAULT_HEADING_GAP_BEFORE_MM
    heading_gap_after_mm: float = DEFAULT_HEADING_GAP_AFTER_MM

    def render(self) -> str:
        return _record(
            [
                ("blockGap", _dhall_float(self.block_gap_mm)),
                ("figureGap", _dhall_float(self.figure_gap_mm)),
                ("headingGapBefore", _dhall_float(self.heading_gap_before_mm)),
                ("headingGapAfter", _dhall_float(self.heading_gap_after_mm)),
            ]
        )


@dataclass(frozen=True)
class SourceProvenance:
    origin: str | None = None
    release_date: str | None = None
    url: str | None = None

    def render(self) -> str:
        return _record(
            [
                ("origin", _optional_text(self.origin)),
                ("releaseDate", _optional_text(self.release_date)),
                ("url", _optional_text(self.url)),
            ]
        )


def _optional_provenance(provenance: SourceProvenance | None) -> str:
    if provenance is None:
        return "None Limelight.SourceProvenance"
    return f"Some\n{_indent(provenance.render(), 2)}"


def _render_document_signature(signature: DocumentSignature) -> str:
    return _record(
        [
            ("signer", _quote(signature.signer)),
            ("algorithm", f"Limelight.SignatureAlgorithm.{signature.algorithm}"),
            ("publicKey", _quote(signature.public_key_b64)),
            ("signature", _quote(signature.signature_b64)),
            ("contentSha256", _quote(signature.content_sha256)),
            ("signedAt", _optional_text(signature.signed_at)),
        ]
    )


def _render_signatures(signatures: Sequence[DocumentSignature]) -> str:
    return _list([_render_document_signature(signature) for signature in signatures], "Limelight.DocumentSignature")


def _sign_all(content: bytes, signers: Sequence[tuple[str, bytes]]) -> list[DocumentSignature]:
    return [sign_document(content, signer=name, private_key_bytes=private_key) for name, private_key in signers]


def _optional_double(value: float | None) -> str:
    return f"Some {_dhall_float(value)}" if value is not None else "None Double"


def _optional_natural(value: int | None) -> str:
    return f"Some {value}" if value is not None else "None Natural"


def _optional_integer(value: int | None) -> str:
    if value is None:
        return "None Integer"
    prefix = "+" if value >= 0 else ""
    return f"Some {prefix}{value}"


def _optional_expr(type_expr: str, expr: str | None) -> str:
    return f"Some ({expr})" if expr is not None else f"None {type_expr}"


def _constructor(name: str, payload: str | None = None) -> str:
    if payload is None:
        return name
    return f"{name}\n{_indent(payload, 2)}"


def _data_type(kind: str) -> str:
    if kind not in DATA_TYPES:
        raise ValueError(f"Unsupported data type {kind!r}")
    return f"Limelight.DataType.{kind}"


def _word48(value: int) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"Word48 values must be integers, got {value!r}")
    if not 0 <= value < WORD48_MAX:
        raise ValueError(f"Word48 value {value!r} is outside 0 <= value < 2^48")
    return str(value)


def _step_unit(value: str) -> str:
    try:
        normalized = STEP_UNITS[value]
    except KeyError as error:
        choices = ", ".join(sorted(STEP_UNITS))
        raise ValueError(f"Unsupported step unit {value!r}; expected one of {choices}") from error
    return f"Limelight.StepUnit.{normalized}"


def _infer_kind(values: Sequence[Any]) -> str:
    present = [value for value in values if value is not None]
    if not present:
        return "text"
    if all(isinstance(value, bool) for value in present):
        return "boolean"
    if all(isinstance(value, int) and not isinstance(value, bool) for value in present):
        return "natural" if all(value >= 0 for value in present) else "integer"
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in present):
        return "double"
    return "text"


def _format_csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return _dhall_float(value)
    return str(value)


@dataclass
class ArraySpec:
    values: list[Any]
    dtype: str | None = None
    label: str | None = None
    unit: str | None = None
    description: str | None = None
    nullable: bool | None = None

    def normalized(self) -> "ArraySpec":
        dtype = self.dtype or _infer_kind(self.values)
        if dtype not in DATA_TYPES:
            raise ValueError(f"Unsupported data type {dtype!r}")

        nullable = any(value is None for value in self.values) if self.nullable is None else self.nullable
        if not nullable and any(value is None for value in self.values):
            raise ValueError("Array contains None but nullable=False")

        return ArraySpec(
            values=list(self.values),
            dtype=dtype,
            label=self.label,
            unit=self.unit,
            description=self.description,
            nullable=nullable,
        )

    def render_schema(self, name: str) -> str:
        normalized = self.normalized()
        return _record(
            [
                ("name", _quote(name)),
                ("dtype", _data_type(normalized.dtype or "text")),
                ("label", _optional_display_text(normalized.label)),
                ("unit", _optional_text(normalized.unit)),
                ("description", _optional_display_text(normalized.description)),
                ("nullable", _dhall_bool(normalized.nullable or False)),
            ]
        )


@dataclass(frozen=True)
class EpochOffset:
    epoch_offset_gs: int
    epoch_offset_s: int
    epoch_offset_ns: int

    def render(self) -> str:
        if self.epoch_offset_s >= 1_000_000_000:
            raise ValueError("epoch_offset_s must be less than 1_000_000_000")
        if self.epoch_offset_ns >= 1_000_000_000:
            raise ValueError("epoch_offset_ns must be less than 1_000_000_000")
        return _record(
            [
                ("epochOffsetGs", _word48(self.epoch_offset_gs)),
                ("epochOffsetS", _word48(self.epoch_offset_s)),
                ("epochOffsetNs", _word48(self.epoch_offset_ns)),
            ]
        )


CALENDARS = {"prolepticGregorian"}
CALENDAR_UNITS = {"day", "week", "month", "quarter", "year"}
PERIOD_RENDER_ANCHORS = {"periodStart", "periodMidpoint", "periodEnd"}
ALIGNMENTS = {"Left", "Center", "Right"}
FONT_STYLES = {"Bold", "Italic"}
AXIS_SCALES = {"Linear", "Log"}


def _integer(value: int) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"Integer values must be int, got {value!r}")
    prefix = "+" if value >= 0 else ""
    return f"{prefix}{value}"


def _enum(dhall_type: str, choices: set[str], value: str) -> str:
    if value not in choices:
        raise ValueError(f"Unsupported {dhall_type} {value!r}; expected one of {', '.join(sorted(choices))}")
    return f"Limelight.{dhall_type}.{value}"


def _optional_enum(dhall_type: str, choices: set[str], value: str | None) -> str:
    if value is None:
        return f"None Limelight.{dhall_type}"
    return f"Some {_enum(dhall_type, choices, value)}"


def _time_origin(epoch_offset: EpochOffset | None) -> str:
    if epoch_offset is None:
        return "Limelight.TimeOrigin.relative"
    return _constructor("Limelight.TimeOrigin.absoluteUtc", epoch_offset.render())


@dataclass(frozen=True)
class Index:
    expr: str

    @classmethod
    def no_index(cls) -> "Index":
        return cls("Limelight.Index.noIndex")

    @classmethod
    def regular_time(
        cls,
        *,
        step_nom: int,
        step_denom: int,
        step_unit: str,
        epoch_offset: EpochOffset | None = None,
    ) -> "Index":
        if step_nom <= 0:
            raise ValueError("step_nom must be positive")
        if step_denom <= 0:
            raise ValueError("step_denom must be positive")
        payload = _record(
            [
                ("timeOrigin", _time_origin(epoch_offset)),
                ("timeStepNom", _word48(step_nom)),
                ("timeStepDenom", _word48(step_denom)),
                ("timeStepUnit", _step_unit(step_unit)),
            ]
        )
        return cls(_constructor("Limelight.Index.regularTime", payload))

    @classmethod
    def regular_int(cls, origin: int, step: int, unit: str | None = None) -> "Index":
        payload = _record(
            [
                ("intOrigin", _integer(origin)),
                ("intStep", _integer(step)),
                ("intUnit", _optional_text(unit)),
            ]
        )
        return cls(_constructor("Limelight.Index.regularInt", payload))

    @classmethod
    def regular_calendar(
        cls,
        *,
        calendar: str,
        unit: str,
        start_ordinal: int,
        step: int,
        render_anchor: str,
    ) -> "Index":
        payload = _record(
            [
                ("calendar", _enum("Calendar", CALENDARS, calendar)),
                ("calendarUnit", _enum("CalendarUnit", CALENDAR_UNITS, unit)),
                ("startOrdinal", _word48(start_ordinal)),
                ("calendarStep", _word48(step)),
                ("renderAnchor", _enum("PeriodRenderAnchor", PERIOD_RENDER_ANCHORS, render_anchor)),
            ]
        )
        return cls(_constructor("Limelight.Index.regularCalendar", payload))

    @classmethod
    def irregular_index_time(
        cls,
        coordinate_array: str,
        *,
        origin: EpochOffset | None,
        unit: str,
    ) -> "Index":
        payload = _record(
            [
                ("irregularTimeCoordArray", _quote(coordinate_array)),
                ("irregularTimeOrigin", _time_origin(origin)),
                ("irregularTimeUnit", _step_unit(unit)),
            ]
        )
        return cls(_constructor("Limelight.Index.irregularIndexTime", payload))

    @classmethod
    def irregular_index_calendar(
        cls,
        coordinate_array: str,
        *,
        calendar: str,
        unit: str,
        render_anchor: str,
    ) -> "Index":
        payload = _record(
            [
                ("irregularCalendarCoordArray", _quote(coordinate_array)),
                ("irregularCalendar", _enum("Calendar", CALENDARS, calendar)),
                ("irregularCalendarUnit", _enum("CalendarUnit", CALENDAR_UNITS, unit)),
                ("irregularRenderAnchor", _enum("PeriodRenderAnchor", PERIOD_RENDER_ANCHORS, render_anchor)),
            ]
        )
        return cls(_constructor("Limelight.Index.irregularIndexCalendar", payload))

    @classmethod
    def irregular_index_array(cls, coordinate_array: str, unit: str | None = None) -> "Index":
        payload = _record(
            [
                ("irregularArrayCoordArray", _quote(coordinate_array)),
                ("irregularArrayUnit", _optional_text(unit)),
            ]
        )
        return cls(_constructor("Limelight.Index.irregularIndexArray", payload))


@dataclass(frozen=True)
class AxisDataType:
    kind: str
    label: str | None = None
    unit: str | None = None
    calendar: str | None = None
    share_group: str | None = None
    scale: str = "Linear"

    @classmethod
    def continuous(
        cls,
        *,
        label: str | None = None,
        unit: str | None = None,
        share_group: str | None = None,
        scale: str = "Linear",
    ) -> "AxisDataType":
        return cls("continuous", label=label, unit=unit, share_group=share_group, scale=scale)

    @classmethod
    def discrete(
        cls,
        *,
        label: str | None = None,
        unit: str | None = None,
        share_group: str | None = None,
    ) -> "AxisDataType":
        return cls("discrete", label=label, unit=unit, share_group=share_group)

    @classmethod
    def time_series(
        cls,
        *,
        label: str | None = None,
        calendar: str | None = None,
        share_group: str | None = None,
    ) -> "AxisDataType":
        return cls("timeSeries", label=label, calendar=calendar, share_group=share_group)

    def render(self) -> str:
        if self.kind == "continuous":
            payload = _record(
                [
                    ("unit", f"Some {_quote(self.unit or '')}"),
                    ("scale", _enum("AxisScale", AXIS_SCALES, self.scale)),
                ]
            )
            return _constructor("Limelight.AxisDataType.continuous", payload)
        if self.kind == "discrete":
            return "Limelight.AxisDataType.discrete"
        if self.kind == "timeSeries":
            payload = _record([("calendar", f"Some {_quote(self.calendar or '')}")])
            return _constructor("Limelight.AxisDataType.timeSeries", payload)

        raise ValueError(f"Unsupported axis data type {self.kind!r}")


def _axis_spec(axis_id: str, axis_data_type: AxisDataType) -> str:
    return _record(
        [
            ("id", _quote(axis_id)),
            ("label", _optional_display_text(axis_data_type.label)),
            ("shareGroup", _optional_axis_group_id(axis_data_type.share_group)),
            ("dataType", axis_data_type.render()),
        ]
    )


@dataclass
class Dataset:
    id: str
    title: str | None
    path: str
    arrays: dict[str, ArraySpec]
    index: Index
    header: bool = True
    provenance: SourceProvenance | None = None
    signatures: list[DocumentSignature] = field(default_factory=list)

    def sample_count(self) -> int:
        lengths = {len(spec.values) for spec in self.arrays.values()}
        if len(lengths) != 1:
            raise ValueError(f"Dataset {self.id!r} arrays have different lengths: {sorted(lengths)}")
        return lengths.pop()

    def csv_bytes(self) -> bytes:
        handle = io.StringIO(newline="")
        writer = csv.writer(handle)
        names = list(self.arrays)
        row_count = self.sample_count()
        if self.header:
            writer.writerow(names)
        for row_index in range(row_count):
            writer.writerow([_format_csv_value(self.arrays[name].values[row_index]) for name in names])
        return handle.getvalue().encode("utf-8")

    def file_fingerprints(self) -> list[tuple[str, str]]:
        digest = hashlib.sha256(self.csv_bytes()).hexdigest()[:8]
        return [(self.path, digest)]

    def write_csv(self, package_root: Path) -> None:
        target = package_root / self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.csv_bytes())

    def render_source(self) -> str:
        schema = _list(
            [spec.render_schema(name) for name, spec in self.arrays.items()],
            "Limelight.ArraySchema",
        )
        payload = _record(
            [
                ("id", _quote(self.id)),
                ("title", _optional_display_text(self.title)),
                ("path", _quote(self.path)),
                ("fileFingerprints", _list(
                    [
                        _record(
                            [
                                ("path", _quote(path)),
                                ("sha256Prefix8", _quote(prefix)),
                            ]
                        )
                        for path, prefix in self.file_fingerprints()
                    ],
                    "Limelight.SourceFileFingerprint",
                )),
                ("schema", f"Some\n{_indent(schema, 2)}"),
                ("header", _dhall_bool(self.header)),
                ("index", self.index.expr),
                ("provenance", _optional_provenance(self.provenance)),
                ("signatures", _render_signatures(self.signatures)),
            ]
        )
        return _constructor("Limelight.Source.csv", payload)


@dataclass(frozen=True)
class HdfArraySpec:
    name: str
    dataset: str
    dtype: str = "double"
    label: str | None = None
    unit: str | None = None
    description: str | None = None
    nullable: bool = False

    def render(self) -> str:
        schema = _record(
            [
                ("name", _quote(self.name)),
                ("dtype", _data_type(self.dtype)),
                ("label", _optional_display_text(self.label)),
                ("unit", _optional_text(self.unit)),
                ("description", _optional_display_text(self.description)),
                ("nullable", _dhall_bool(self.nullable)),
            ]
        )
        return _record(
            [
                ("schema", schema),
                ("dataset", _quote(self.dataset)),
            ]
        )


def hdf_array(
    name: str,
    dataset: str,
    *,
    dtype: str = "double",
    label: str | None = None,
    unit: str | None = None,
    description: str | None = None,
    nullable: bool = False,
) -> HdfArraySpec:
    return HdfArraySpec(
        name=name,
        dataset=dataset,
        dtype=dtype,
        label=label,
        unit=unit,
        description=description,
        nullable=nullable,
    )


def _normalize_hdf_array(value: HdfArraySpec | tuple[str, str]) -> HdfArraySpec:
    if isinstance(value, HdfArraySpec):
        return value
    name, dataset = value
    return HdfArraySpec(name=name, dataset=dataset)


def _is_remote_reference(source: str) -> bool:
    lowered = source.lower()
    return lowered.startswith(("http://", "https://", "data:", "//"))


def _first_set(*values: int | None) -> int:
    for value in values:
        if value is not None:
            return value
    raise ValueError("No value was set")


def _slugify_image_stem(stem: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
    return slug or "image"


def _image_id_from_source(source: str) -> str:
    """The default id for an image nobody declared explicitly.

    Derived from the reference as written so it is predictable enough to use as
    a cross-reference anchor without reaching for add_image().
    """

    return _slugify_image_stem(Path(source).stem)


@dataclass(frozen=True)
class ImageWidth:
    """How wide an image is drawn, in millimetres or as a percent of the column.

    This is layout, not packaging: an image can be stored at 1600px for print
    quality and drawn at 40mm. Use `ImageWidth.millimetres` or
    `ImageWidth.percent` rather than constructing one directly.
    """

    millimetres_value: float | None = None
    percent_value: float | None = None

    @classmethod
    def millimetres(cls, value: float) -> "ImageWidth":
        return cls(millimetres_value=value)

    @classmethod
    def percent(cls, value: float) -> "ImageWidth":
        return cls(percent_value=value)

    def render(self) -> str:
        if self.millimetres_value is not None:
            value, unit = self.millimetres_value, "millimetres"
        elif self.percent_value is not None:
            value, unit = self.percent_value, "percent"
        else:
            raise ValueError("ImageWidth must be millimetres or percent")
        return _record(
            [
                ("value", _dhall_float(value)),
                ("unit", f"Limelight.ImageWidthUnit.{unit}"),
            ]
        )


def _optional_image_width(value: "ImageWidth | None") -> str:
    if value is None:
        return "None Limelight.ImageWidth"
    return f"Some\n{_indent(value.render(), 2)}"


@dataclass(frozen=True)
class _DeclaredImage:
    """Settings `add_image` pinned for one source file.

    Declaring an image does not put it in the package by itself. The story
    document is the dependency list, and discovery walks it; this only says
    what to do when a particular source turns up.
    """

    id: str
    source_path: Path
    path: str | None
    max_width: int | None
    format: str | None
    quality: int | None
    display_width: "ImageWidth | None"
    provenance: "SourceProvenance | None"
    signers: tuple[tuple[str, bytes], ...]


@dataclass(frozen=True)
class ResolvedStory:
    """A story document as it will be packaged.

    Image references have been rewritten to their packaged paths, numbers have
    been assigned from story order, and the signatures cover the rewritten
    bytes rather than what the author wrote.
    """

    markdown: str
    assets: list["ImageAsset"]
    signatures: list[DocumentSignature]
    figure_view_numbers: dict[str, int]


@dataclass
class ImageAsset:
    """One image bundled into a package.

    ``source_path`` is the file the author edited; ``path`` is where the
    converted copy lands in the package. The two are unrelated by design: an
    author organises a source tree for editing, and the project decides the
    package layout.
    """

    id: str
    source_path: Path
    path: str
    converted: ConvertedImage
    story_number: int | None = None
    display_width: "ImageWidth | None" = None
    provenance: SourceProvenance | None = None
    signatures: list[DocumentSignature] = field(default_factory=list)

    def file_fingerprints(self) -> list[tuple[str, str]]:
        return [(self.path, self.converted.sha256_prefix8())]

    def write_asset_file(self, package_root: Path) -> None:
        target = package_root / self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.converted.data)

    def render(self) -> str:
        derivation = _record(
            [
                ("sourceName", _quote(self.source_path.name)),
                ("sourceSha256", _quote(self.converted.source_sha256)),
                ("maxWidth", str(self.converted.max_width)),
                ("quality", _optional_natural(self.converted.quality)),
            ]
        )
        return _record(
            [
                ("id", _quote(self.id)),
                ("path", _quote(self.path)),
                ("format", _constructor(f"Limelight.ImageFormat.{self.converted.format}")),
                ("storyNumber", _optional_natural(self.story_number)),
                ("displayWidth", _optional_image_width(self.display_width)),
                ("fileFingerprints", _list(
                    [
                        _record(
                            [
                                ("path", _quote(path)),
                                ("sha256Prefix8", _quote(prefix)),
                            ]
                        )
                        for path, prefix in self.file_fingerprints()
                    ],
                    "Limelight.SourceFileFingerprint",
                )),
                ("derivedFrom", f"Some\n{_indent(derivation, 2)}"),
                ("provenance", _optional_provenance(self.provenance)),
                ("signatures", _render_signatures(self.signatures)),
            ]
        )


@dataclass
class HdfDataset:
    id: str
    title: str | None
    path: str
    hdf5_source_path: Path
    y_arrays: list[HdfArraySpec]
    index: Index
    chunk_size: int
    provenance: SourceProvenance | None = None
    signatures: list[DocumentSignature] = field(default_factory=list)

    def file_fingerprints(self) -> list[tuple[str, str]]:
        digest = hashlib.sha256(self.hdf5_source_path.read_bytes()).hexdigest()[:8]
        return [(self.path, digest)]

    def write_source_file(self, package_root: Path) -> None:
        target = package_root / self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.hdf5_source_path, target)

    def render_source(self) -> str:
        payload = _record(
            [
                ("id", _quote(self.id)),
                ("title", _optional_display_text(self.title)),
                ("path", _quote(self.path)),
                ("fileFingerprints", _list(
                    [
                        _record(
                            [
                                ("path", _quote(path)),
                                ("sha256Prefix8", _quote(prefix)),
                            ]
                        )
                        for path, prefix in self.file_fingerprints()
                    ],
                    "Limelight.SourceFileFingerprint",
                )),
                ("yArrays", _list(
                    [hdf_array_spec.render() for hdf_array_spec in self.y_arrays],
                    "{ schema : Limelight.ArraySchema, dataset : Text }",
                )),
                ("index", self.index.expr),
                ("largeSeriesChunkSize", str(self.chunk_size)),
                ("provenance", _optional_provenance(self.provenance)),
                ("signatures", _render_signatures(self.signatures)),
            ]
        )
        return _constructor("Limelight.Source.hdf", payload)


@dataclass
class LineArtist:
    array: str
    label: str | None = None
    id: str | None = None
    data: str | None = None
    alpha: float | None = None
    x: str | None = None
    x_data: str | None = None
    color: str | None = None
    linestyle: str | None = None
    marker: str | None = None
    visible_when: "TextControlParameterMatch | None" = None


@dataclass
class ScatterArtist:
    array: str
    label: str | None = None
    id: str | None = None
    data: str | None = None
    x: str | None = None
    x_data: str | None = None
    color_by: str | None = None
    size_by: str | None = None
    color: str | None = None
    marker: str | None = None
    visible_when: "TextControlParameterMatch | None" = None


@dataclass
class TimeSeriesArtist:
    data: str
    array: str
    id: str | None = None
    label: str | None = None
    target_buckets: int | None = None
    visible_when: "TextControlParameterMatch | None" = None


@dataclass
class StemArtist:
    array: str
    label: str | None = None
    id: str | None = None
    data: str | None = None
    x: str | None = None
    x_data: str | None = None
    baseline: float | None = None
    color: str | None = None
    visible_when: "TextControlParameterMatch | None" = None


@dataclass(frozen=True)
class TextOption:
    value: str
    label: str

    def render(self) -> str:
        return _record([("value", _quote(self.value)), ("label", _quote(self.label))])


@dataclass(frozen=True)
class ControlParameterDataType:
    expression: str

    @classmethod
    def discrete(
        cls,
        *,
        options: Sequence[TextOption | tuple[str, str]],
        default: str | None = None,
    ) -> "ControlParameterDataType":
        normalized_options = [
            option if isinstance(option, TextOption) else TextOption(value=option[0], label=option[1])
            for option in options
        ]
        if not normalized_options:
            raise ValueError("Discrete control parameters must define at least one option")
        option_values = {option.value for option in normalized_options}
        if default is not None and default not in option_values:
            raise ValueError(f"Default {default!r} is not a discrete control parameter option")
        payload = _record(
            [
                ("options", _list([option.render() for option in normalized_options], "Limelight.TextOption")),
                ("default", _optional_text(default)),
            ]
        )
        return cls(_constructor("Limelight.ControlParameterDataType.discrete", payload))

    @classmethod
    def integer(
        cls,
        *,
        default: int | None = None,
        min: int | None = None,
        max: int | None = None,
    ) -> "ControlParameterDataType":
        _validate_ordered_bounds("integer control parameter", default, min, max)
        payload = _record(
            [
                ("integerDefault", _optional_integer(default)),
                ("integerMin", _optional_integer(min)),
                ("integerMax", _optional_integer(max)),
            ]
        )
        return cls(_constructor("Limelight.ControlParameterDataType.integer", payload))

    @classmethod
    def float(
        cls,
        *,
        default: float | None = None,
        min: float | None = None,
        max: float | None = None,
    ) -> "ControlParameterDataType":
        _validate_ordered_bounds("float control parameter", default, min, max)
        payload = _record(
            [
                ("floatDefault", _optional_double(default)),
                ("floatMin", _optional_double(min)),
                ("floatMax", _optional_double(max)),
            ]
        )
        return cls(_constructor("Limelight.ControlParameterDataType.float", payload))

    @classmethod
    def time(
        cls,
        *,
        default: str | None = None,
        min: str | None = None,
        max: str | None = None,
    ) -> "ControlParameterDataType":
        payload = _record(
            [
                ("timeDefault", _optional_text(default)),
                ("timeMin", _optional_text(min)),
                ("timeMax", _optional_text(max)),
            ]
        )
        return cls(_constructor("Limelight.ControlParameterDataType.time", payload))

    def render(self) -> str:
        return self.expression


def _validate_ordered_bounds(
    label: str,
    default: int | float | None,
    minimum: int | float | None,
    maximum: int | float | None,
) -> None:
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError(f"{label} min {minimum!r} is greater than max {maximum!r}")
    if default is not None and minimum is not None and default < minimum:
        raise ValueError(f"{label} default {default!r} is less than min {minimum!r}")
    if default is not None and maximum is not None and default > maximum:
        raise ValueError(f"{label} default {default!r} is greater than max {maximum!r}")


@dataclass(frozen=True)
class ControlParameter:
    id: str
    label: str
    data_type: ControlParameterDataType

    def render(self) -> str:
        return _record(
            [
                ("id", _quote(self.id)),
                ("label", _quote(self.label)),
                ("dataType", self.data_type.render()),
            ]
        )


@dataclass(frozen=True)
class TextControlParameterMatch:
    control_parameter: str
    value: str

    def render(self) -> str:
        return _record([("controlParameter", _quote(self.control_parameter)), ("value", _quote(self.value))])


@dataclass(frozen=True)
class DropdownCtrlSpec:
    id: str
    control_parameter: str
    label: str | None = None

    def render(self) -> str:
        payload = _record(
            [
                ("id", _quote(self.id)),
                ("controlParameter", _quote(self.control_parameter)),
                ("label", _optional_display_text(self.label)),
            ]
        )
        return _constructor("Limelight.FormCtrlSpec.dropdown", payload)


@dataclass(frozen=True)
class SliderCtrlSpec:
    id: str
    control_parameter: str
    label: str | None = None

    def render(self) -> str:
        payload = _record(
            [
                ("id", _quote(self.id)),
                ("controlParameter", _quote(self.control_parameter)),
                ("label", _optional_display_text(self.label)),
            ]
        )
        return _constructor("Limelight.FormCtrlSpec.slider", payload)


@dataclass
class FigureSpec:
    id: str
    title: str
    data: str
    x: str
    lines: list[LineArtist]
    scatters: list[ScatterArtist]
    caption: str | None
    x_axis: AxisDataType
    y_axis: AxisDataType
    controls: list[DropdownCtrlSpec | SliderCtrlSpec] = field(default_factory=list)
    map_specs: list["MapSpec"] = field(default_factory=list)
    time_series: list[TimeSeriesArtist] = field(default_factory=list)
    table_views: list["TableViewSpec"] = field(default_factory=list)
    lines2: list[LineArtist] = field(default_factory=list)
    y2_axis: AxisDataType | None = None
    stems: list[StemArtist] = field(default_factory=list)
    form_title: str | None = None
    form_caption: str | None = None

    def _line_actions(self, lines: list[LineArtist]) -> list[str]:
        actions = []
        for line in lines:
            artist_id = line.id or f"{line.array}-line"
            data = line.data or self.data
            x_data = line.x_data or data
            x_column = line.x or self.x
            x_override = _column_ref(x_data, x_column) if x_column else None
            visible_when = line.visible_when.render() if line.visible_when is not None else None
            payload = _record(
                [
                    ("id", _quote(artist_id)),
                    ("y", _quote(_column_ref(data, line.array))),
                    ("label", _optional_display_text(line.label)),
                    ("alpha", _optional_double(line.alpha)),
                    ("xOverride", _optional_text(x_override)),
                    ("color", _optional_text(line.color)),
                    ("linestyle", _optional_text(line.linestyle)),
                    ("marker", _optional_text(line.marker)),
                    ("visibleWhen", _optional_expr("Limelight.TextControlParameterMatch", visible_when)),
                ]
            )
            plot_artist = _constructor("Limelight.PlotArtist.line", payload)
            actions.append(_constructor("Limelight.AxesAction.AxesActionAddData", f"({plot_artist})"))
        return actions

    def render(self) -> str:
        plot_id = f"{self.id}-plot"
        actions = self._line_actions(self.lines)

        for scatter in self.scatters:
            artist_id = scatter.id or f"{scatter.array}-points"
            data = scatter.data or self.data
            x_data = scatter.x_data or data
            x_column = scatter.x or self.x
            payload = _record(
                [
                    ("id", _quote(artist_id)),
                    ("x", _quote(_column_ref(x_data, x_column))),
                    ("y", _quote(_column_ref(data, scatter.array))),
                    ("label", _optional_display_text(scatter.label)),
                    ("colorBy", _optional_text(_column_ref(data, scatter.color_by) if scatter.color_by else None)),
                    ("sizeBy", _optional_text(_column_ref(data, scatter.size_by) if scatter.size_by else None)),
                    ("color", _optional_text(scatter.color)),
                    ("marker", _optional_text(scatter.marker)),
                    ("visibleWhen", _optional_expr(
                        "Limelight.TextControlParameterMatch",
                        scatter.visible_when.render() if scatter.visible_when is not None else None,
                    )),
                ]
            )
            plot_artist = _constructor("Limelight.PlotArtist.scatter", payload)
            actions.append(_constructor("Limelight.AxesAction.AxesActionAddData", f"({plot_artist})"))

        for stem in self.stems:
            artist_id = stem.id or f"{stem.array}-stem"
            data = stem.data or self.data
            x_data = stem.x_data or data
            x_column = stem.x or self.x
            payload = _record(
                [
                    ("id", _quote(artist_id)),
                    ("x", _quote(_column_ref(x_data, x_column))),
                    ("y", _quote(_column_ref(data, stem.array))),
                    ("label", _optional_display_text(stem.label)),
                    ("baseline", _dhall_float(stem.baseline if stem.baseline is not None else 0.0)),
                    ("color", _optional_text(stem.color)),
                    ("visibleWhen", _optional_expr(
                        "Limelight.TextControlParameterMatch",
                        stem.visible_when.render() if stem.visible_when is not None else None,
                    )),
                ]
            )
            plot_artist = _constructor("Limelight.PlotArtist.stem", payload)
            actions.append(_constructor("Limelight.AxesAction.AxesActionAddData", f"({plot_artist})"))

        for time_series in self.time_series:
            artist_id = time_series.id or f"{time_series.array}-timeseries"
            visible_when = time_series.visible_when.render() if time_series.visible_when is not None else None
            payload = _record(
                [
                    ("id", _quote(artist_id)),
                    ("y", _quote(_column_ref(time_series.data, time_series.array))),
                    ("label", _optional_display_text(time_series.label)),
                    ("transform", "Limelight.Transform.identity"),
                    ("targetBuckets", _optional_natural(time_series.target_buckets)),
                    ("visibleWhen", _optional_expr("Limelight.TextControlParameterMatch", visible_when)),
                ]
            )
            plot_artist = _constructor("Limelight.PlotArtist.timeSeries", payload)
            actions.append(_constructor("Limelight.AxesAction.AxesActionAddData", f"({plot_artist})"))

        x_axis = self.x_axis
        x_axis2 = None
        if self.lines2:
            shared_group = self.x_axis.share_group or f"{self.id}-x-axis-share"
            x_axis = replace(self.x_axis, share_group=shared_group)
            x_axis2 = replace(self.x_axis, share_group=shared_group)

        plot = _record(
            [
                ("id", _quote(plot_id)),
                (
                    "frame",
                    _record(
                        [
                            ("left", _dhall_float(0.10)),
                            ("bottom", _dhall_float(0.12)),
                            ("width", _dhall_float(0.82)),
                            ("height", _dhall_float(0.78)),
                        ]
                    ),
                ),
                ("title", "None Limelight.DisplayText"),
                ("caption", "None Limelight.DisplayText"),
                (
                    "xAxis",
                    _axis_spec(f"{self.id}-x-axis", x_axis),
                ),
                (
                    "yAxis",
                    _axis_spec(f"{self.id}-y-axis", self.y_axis),
                ),
                ("actions", _list(actions, "Limelight.AxesAction")),
            ]
        )

        axes_specs = [plot] if actions else []
        if self.lines2:
            plot2_actions = self._line_actions(self.lines2)
            axes_specs.append(
                _record(
                    [
                        ("id", _quote(f"{self.id}-plot2")),
                        (
                            "frame",
                            _record(
                                [
                                    ("left", _dhall_float(0.10)),
                                    ("bottom", _dhall_float(0.12)),
                                    ("width", _dhall_float(0.82)),
                                    ("height", _dhall_float(0.78)),
                                ]
                            ),
                        ),
                        ("title", "None Limelight.DisplayText"),
                        ("caption", "None Limelight.DisplayText"),
                        (
                            "xAxis",
                            _axis_spec(f"{self.id}-x2-axis", x_axis2),
                        ),
                        (
                            "yAxis",
                            _axis_spec(f"{self.id}-y2-axis", self.y2_axis or AxisDataType.continuous(label="Value")),
                        ),
                        ("actions", _list(plot2_actions, "Limelight.AxesAction")),
                    ]
                )
            )

        form_specs = []
        if self.controls:
            control_form = _record(
                [
                    ("id", _quote(f"{self.id}-controls")),
                    (
                        "frame",
                        _record(
                            [
                                ("left", _dhall_float(0.10)),
                                ("bottom", _dhall_float(0.92)),
                                ("width", _dhall_float(0.82)),
                                ("height", _dhall_float(0.06)),
                            ]
                        ),
                    ),
                    ("title", _optional_display_text(self.form_title)),
                    ("caption", _optional_display_text(self.form_caption)),
                    ("controls", _list([control.render() for control in self.controls], "Limelight.FormCtrlSpec")),
                ]
            )
            form_specs.append(control_form)

        return _record(
            [
                ("id", _quote(self.id)),
                ("title", _quote(self.title)),
                ("caption", _optional_display_text(self.caption)),
                ("axesSpecs", _list(axes_specs, "Limelight.AxesSpec")),
                ("mapSpecs", _list([map_spec.render() for map_spec in self.map_specs], "Limelight.MapSpec")),
                ("formSpecs", _list(form_specs, "Limelight.FormSpec")),
                (
                    "tableViewSpecs",
                    _list([table_view.render() for table_view in self.table_views], "Limelight.TableViewSpec"),
                ),
            ]
        )


@dataclass(frozen=True)
class MapAction:
    expression: str

    def render(self) -> str:
        return self.expression


def _map_action_from_value(value: Any) -> MapAction:
    if isinstance(value, MapAction):
        return value
    if isinstance(value, str):
        return MapAction(value)
    raise ValueError(f"Unsupported map action value {value!r}")


def map_action_set_limits(
    *,
    longitude_lower: float,
    longitude_upper: float,
    latitude_lower: float,
    latitude_upper: float,
) -> MapAction:
    limit = _record(
        [
            ("longitudeLower", _dhall_float(longitude_lower)),
            ("longitudeUpper", _dhall_float(longitude_upper)),
            ("latitudeLower", _dhall_float(latitude_lower)),
            ("latitudeUpper", _dhall_float(latitude_upper)),
        ]
    )
    return MapAction(_constructor("Limelight.MapAction.MapActionSetLimits", limit))


def map_action_add_dataset_scatter(
    *,
    id: str,
    data: str,
    longitude: str,
    latitude: str,
    label: str | None = None,
    color_by: str | None = None,
    size_by: str | None = None,
) -> MapAction:
    scatter = _record(
        [
            ("id", _quote(id)),
            ("longitude", _quote(_column_ref(data, longitude))),
            ("latitude", _quote(_column_ref(data, latitude))),
            ("label", _optional_display_text(label)),
            ("colorBy", _optional_text(_column_ref(data, color_by) if color_by else None)),
            ("sizeBy", _optional_text(_column_ref(data, size_by) if size_by else None)),
        ]
    )
    return MapAction(_constructor("Limelight.MapAction.MapActionAddDatasetScatter", scatter))


def map_action_add_geojson_layer(
    *,
    id: str,
    path: str,
    label: str | None = None,
    fill: str | None = "#eeeeee",
    stroke: str | None = "#888888",
    alpha: float | None = 0.75,
) -> MapAction:
    layer = _record(
        [
            ("id", _quote(id)),
            ("path", _quote(path)),
            ("label", _optional_display_text(label)),
            ("fill", _optional_text(fill)),
            ("stroke", _optional_text(stroke)),
            ("alpha", _optional_double(alpha)),
        ]
    )
    return MapAction(_constructor("Limelight.MapAction.MapActionAddGeoJsonLayer", layer))


@dataclass(frozen=True)
class MapSpec:
    id: str
    actions: list[MapAction | str]
    title: str | None = None
    caption: str | None = None
    frame: tuple[float, float, float, float] = (0.10, 0.12, 0.82, 0.78)

    def render(self) -> str:
        left, bottom, width, height = self.frame
        return _record(
            [
                ("id", _quote(self.id)),
                (
                    "frame",
                    _record(
                        [
                            ("left", _dhall_float(left)),
                            ("bottom", _dhall_float(bottom)),
                            ("width", _dhall_float(width)),
                            ("height", _dhall_float(height)),
                        ]
                    ),
                ),
                ("title", _optional_display_text(self.title)),
                ("caption", _optional_display_text(self.caption)),
                ("actions", _list([_map_action_from_value(value).render() for value in self.actions], "Limelight.MapAction")),
            ]
        )


@dataclass(frozen=True)
class ColumnFormat:
    column: str
    alignment: str | None = None
    format: str | None = None

    def render(self) -> str:
        return _record(
            [
                ("column", _quote(self.column)),
                ("alignment", _optional_enum("Alignment", ALIGNMENTS, self.alignment)),
                ("format", _optional_text(self.format)),
            ]
        )


@dataclass(frozen=True)
class CellStyleRule:
    styles: list[str]
    row: int | None = None
    column: int | None = None

    def render(self) -> str:
        return _record(
            [
                (
                    "selector",
                    _record(
                        [
                            ("row", _optional_natural(self.row)),
                            ("column", _optional_natural(self.column)),
                        ]
                    ),
                ),
                (
                    "styles",
                    _list([_enum("FontStyle", FONT_STYLES, style) for style in self.styles], "Limelight.FontStyle"),
                ),
            ]
        )


@dataclass(frozen=True)
class TableViewSpec:
    id: str
    data: str
    columns: list[str] | None = None
    title: str | None = None
    caption: str | None = None
    frame: tuple[float, float, float, float] = (0.10, 0.12, 0.82, 0.78)
    column_formats: list[ColumnFormat] = field(default_factory=list)
    header_style: list[str] = field(default_factory=list)
    cell_styles: list[CellStyleRule] = field(default_factory=list)

    def render(self) -> str:
        left, bottom, width, height = self.frame
        return _record(
            [
                ("id", _quote(self.id)),
                (
                    "frame",
                    _record(
                        [
                            ("left", _dhall_float(left)),
                            ("bottom", _dhall_float(bottom)),
                            ("width", _dhall_float(width)),
                            ("height", _dhall_float(height)),
                        ]
                    ),
                ),
                ("title", _optional_display_text(self.title)),
                ("caption", _optional_display_text(self.caption)),
                ("data", _quote(self.data)),
                ("columns", _optional_text_list(self.columns)),
                (
                    "columnFormats",
                    _list([column_format.render() for column_format in self.column_formats], "Limelight.ColumnFormat"),
                ),
                (
                    "headerStyle",
                    _list([_enum("FontStyle", FONT_STYLES, style) for style in self.header_style], "Limelight.FontStyle"),
                ),
                (
                    "cellStyles",
                    _list([cell_style.render() for cell_style in self.cell_styles], "Limelight.CellStyleRule"),
                ),
            ]
        )


@dataclass
class FigureView:
    id: str | None
    ref: str
    # The number printed as "Figure N.", assigned from story order. `index` is
    # the superseded field; nothing written today sets it, but it is still
    # rendered so manifests keep the shape v1 readers expect.
    story_number: int | None = None
    index: int | None = None
    actions: list["FigureViewAction | str"] = field(default_factory=list)

    def render(self) -> str:
        if self.id is None:
            raise ValueError("FigureView must have an id before rendering")
        return _record(
            [
                ("id", _quote(self.id)),
                ("ref", _quote(self.ref)),
                ("storyNumber", _optional_natural(self.story_number)),
                ("index", _optional_integer(self.index)),
                (
                    "actions",
                    _list(
                        [_figure_view_action_from_value(value).render() for value in self.actions],
                        "Limelight.FigureViewAction",
                    ),
                ),
            ]
        )


@dataclass(frozen=True)
class FigureViewAction:
    ref: str
    action: "AxesAction | str"

    def render(self) -> str:
        return _record(
            [
                ("ref", _quote(self.ref)),
                ("action", _axes_action_from_value(self.action).render()),
            ]
        )


def _figure_view_action_from_value(value: Any) -> FigureViewAction:
    if isinstance(value, FigureViewAction):
        return value
    if isinstance(value, str):
        return RawFigureViewAction(value)
    if isinstance(value, Mapping):
        axes_ref = value.get("ref")
        action = value.get("action")
        if isinstance(axes_ref, str) and action is not None:
            return FigureViewAction(ref=axes_ref, action=_axes_action_from_value(action))
    raise ValueError(f"Unsupported figure view action value {value!r}")


@dataclass(frozen=True)
class RawFigureViewAction:
    expression: str

    def render(self) -> str:
        return self.expression


@dataclass(frozen=True)
class AxesAction:
    expression: str

    def render(self) -> str:
        return self.expression


def _axes_action_from_value(value: Any) -> AxesAction:
    if isinstance(value, AxesAction):
        return value
    if isinstance(value, str):
        return AxesAction(value)
    raise ValueError(f"Unsupported axes action value {value!r}")


def axes_action_set_x_float_limits(lower: float, upper: float) -> AxesAction:
    limit = _constructor(
        "Limelight.AxisLimit.AxisLimitXFloat",
        _record([("xLower", _dhall_float(lower)), ("xUpper", _dhall_float(upper))]),
    )
    return AxesAction(_constructor("Limelight.AxesAction.AxesActionSetLimits", f"({limit})"))


def axes_action_set_y_float_limits(lower: float, upper: float) -> AxesAction:
    limit = _constructor(
        "Limelight.AxisLimit.AxisLimitYFloat",
        _record([("yLower", _dhall_float(lower)), ("yUpper", _dhall_float(upper))]),
    )
    return AxesAction(_constructor("Limelight.AxesAction.AxesActionSetLimits", f"({limit})"))


def axes_action_add_axis_window_decorator(
    *,
    lower: float,
    upper: float,
    label: str | None = None,
) -> AxesAction:
    decorator = _record(
        [
            (
                "xLimit",
                _constructor(
                    "Limelight.AxisLimit.AxisLimitXFloat",
                    _record([("xLower", _dhall_float(lower)), ("xUpper", _dhall_float(upper))]),
                ),
            ),
            ("label", _optional_display_text(label)),
        ]
    )
    return AxesAction(
        _constructor(
            "Limelight.AxesAction.AxesActionAddDecorator",
            f"({_constructor('Limelight.AxesDecorator.AxesDecoratorVSpan', decorator)})",
        )
    )


def axes_action_set_x_utc_time_limits(start: str, end: str) -> AxesAction:
    limit = _constructor(
        "Limelight.AxisLimit.AxisLimitXUtcTime",
        _record([("xStart", _quote(start)), ("xEnd", _quote(end))]),
    )
    return AxesAction(_constructor("Limelight.AxesAction.AxesActionSetLimits", f"({limit})"))


def axes_action_set_y_utc_time_limits(start: str, end: str) -> AxesAction:
    limit = _constructor(
        "Limelight.AxisLimit.AxisLimitYUtcTime",
        _record([("yStart", _quote(start)), ("yEnd", _quote(end))]),
    )
    return AxesAction(_constructor("Limelight.AxesAction.AxesActionSetLimits", f"({limit})"))


def axes_action_add_utc_time_window_decorator(
    *,
    start: str,
    end: str,
    label: str | None = None,
) -> AxesAction:
    decorator = _record(
        [
            (
                "xLimit",
                _constructor(
                    "Limelight.AxisLimit.AxisLimitXUtcTime",
                    _record([("xStart", _quote(start)), ("xEnd", _quote(end))]),
                ),
            ),
            ("label", _optional_display_text(label)),
        ]
    )
    return AxesAction(
        _constructor(
            "Limelight.AxesAction.AxesActionAddDecorator",
            f"({_constructor('Limelight.AxesDecorator.AxesDecoratorVSpan', decorator)})",
        )
    )


def _axis_limit(axis: str, lower: float | str, upper: float | str) -> str:
    """An AxisLimit for one axis: UTC-time when the bounds are ISO date strings,
    float otherwise."""

    if isinstance(lower, str) or isinstance(upper, str):
        if not (isinstance(lower, str) and isinstance(upper, str)):
            raise TypeError(f"{axis}-axis bounds must both be dates or both be numbers, got {lower!r} and {upper!r}")
        return _constructor(
            f"Limelight.AxisLimit.AxisLimit{axis.upper()}UtcTime",
            _record([(f"{axis}Start", _quote(lower)), (f"{axis}End", _quote(upper))]),
        )
    return _constructor(
        f"Limelight.AxisLimit.AxisLimit{axis.upper()}Float",
        _record([(f"{axis}Lower", _dhall_float(lower)), (f"{axis}Upper", _dhall_float(upper))]),
    )


def axes_action_add_rect_decorator(
    *,
    x_lower: float | str,
    x_upper: float | str,
    y_lower: float | str,
    y_upper: float | str,
    label: str | None = None,
    color: str | None = None,
    alpha: float | None = None,
) -> AxesAction:
    """A shaded box between the given bounds, drawn over the data without
    changing the axes' limits. Bounds on a time axis are ISO date strings, as
    for `axes_action_add_utc_time_window_decorator`; anywhere else they are
    numbers."""

    decorator = _record(
        [
            ("xLimit", _axis_limit("x", x_lower, x_upper)),
            ("yLimit", _axis_limit("y", y_lower, y_upper)),
            ("label", _optional_display_text(label)),
            ("color", _optional_text(color)),
            ("alpha", _optional_double(alpha)),
        ]
    )
    return AxesAction(
        _constructor(
            "Limelight.AxesAction.AxesActionAddDecorator",
            f"({_constructor('Limelight.AxesDecorator.AxesDecoratorRect', decorator)})",
        )
    )


def axes_action_add_annotation(
    arrow: PlotArrow,
    *,
    label: str | None = None,
    color: str | None = None,
    label_offset: tuple[float, float] | None = None,
) -> AxesAction:
    offset_dx, offset_dy = label_offset if label_offset is not None else (None, None)
    decorator = _record(
        [
            ("arrow", arrow.render()),
            ("label", _optional_display_text(label)),
            ("color", _optional_text(color)),
            ("labelOffsetDx", _optional_double(offset_dx)),
            ("labelOffsetDy", _optional_double(offset_dy)),
        ]
    )
    return AxesAction(
        _constructor(
            "Limelight.AxesAction.AxesActionAddDecorator",
            f"({_constructor('Limelight.AxesDecorator.AxesDecoratorAnnotation', decorator)})",
        )
    )


def figure_view_action(figure_id: str, action: AxesAction | str) -> FigureViewAction:
    return FigureViewAction(ref=f"{figure_id}-plot", action=action)


@dataclass(frozen=True)
class PlotArrow:
    start: tuple[float, float]
    end: tuple[float, float]

    def render(self) -> str:
        start_x, start_y = self.start
        end_x, end_y = self.end
        return _record(
            [
                ("start", _record([("x", _dhall_float(start_x)), ("y", _dhall_float(start_y))])),
                ("end", _record([("x", _dhall_float(end_x)), ("y", _dhall_float(end_y))])),
            ]
        )


class LimelightProject:
    def __init__(
        self,
        *,
        title: str,
        authors: Sequence[str],
        subtitle: str | None = None,
        description: str | None = None,
        created: str | None = None,
        updated: str | None = None,
        document_version: str | None = None,
        page: PageGeometry | None = None,
        spacing: StorySpacing | None = None,
    ) -> None:
        self.page = page if page is not None else PageGeometry()
        self.spacing = spacing if spacing is not None else StorySpacing()
        self.title = title
        self.authors = list(authors)
        self.subtitle = subtitle
        self.description = description
        self.created = created
        self.updated = updated
        self.document_version = document_version
        self.control_parameters: list[ControlParameter] = []
        self.datasets: list[Dataset] = []
        self.hdf_datasets: list[HdfDataset] = []
        self.figure_specs: list[FigureSpec] = []
        self.figures: list[FigureSpec] = self.figure_specs
        self.figure_views: list[FigureView] = []
        self.story_markdown: str | None = None
        self.story_base_dir = Path(".")
        self.story_signers: list[tuple[str, bytes]] = []
        self.image_assets: list[ImageAsset] = []
        self._declared_images: list[_DeclaredImage] = []
        self._resolved_story: ResolvedStory | None = None
        self._story_figure_view_index = 0
        self.image_max_width = DEFAULT_MAX_WIDTH
        self.image_package_prefix = "assets/images"

    def add_control_parameter(
        self,
        *,
        id: str,
        label: str,
        data_type: ControlParameterDataType,
    ) -> ControlParameter:
        control_parameter = ControlParameter(id=id, label=label, data_type=data_type)
        self.control_parameters.append(control_parameter)
        return control_parameter

    def add_discrete_control_parameter(
        self,
        *,
        id: str,
        label: str,
        options: Sequence[TextOption | tuple[str, str]],
        default: str | None = None,
    ) -> ControlParameter:
        return self.add_control_parameter(
            id=id,
            label=label,
            data_type=ControlParameterDataType.discrete(options=options, default=default),
        )

    def add_integer_control_parameter(
        self,
        *,
        id: str,
        label: str,
        default: int | None = None,
        min: int | None = None,
        max: int | None = None,
    ) -> ControlParameter:
        return self.add_control_parameter(
            id=id,
            label=label,
            data_type=ControlParameterDataType.integer(default=default, min=min, max=max),
        )

    def add_float_control_parameter(
        self,
        *,
        id: str,
        label: str,
        default: float | None = None,
        min: float | None = None,
        max: float | None = None,
    ) -> ControlParameter:
        return self.add_control_parameter(
            id=id,
            label=label,
            data_type=ControlParameterDataType.float(default=default, min=min, max=max),
        )

    def add_time_control_parameter(
        self,
        *,
        id: str,
        label: str,
        default: str | None = None,
        min: str | None = None,
        max: str | None = None,
    ) -> ControlParameter:
        return self.add_control_parameter(
            id=id,
            label=label,
            data_type=ControlParameterDataType.time(default=default, min=min, max=max),
        )

    def add_figure_view(
        self,
        *,
        id: str,
        ref: str,
        story_number: int | None = None,
        index: int | None = None,
        actions: Sequence[FigureViewAction | str] = (),
    ) -> FigureView:
        figure_view = FigureView(
            id=id,
            ref=ref,
            story_number=story_number,
            index=index,
            actions=[_figure_view_action_from_value(value) for value in actions],
        )
        self.figure_views.append(figure_view)
        return figure_view

    def story_figure(
        self,
        ref: str,
        *,
        id: str | None = None,
        number: int | None = None,
        actions: Sequence[FigureViewAction | str] = (),
    ) -> str:
        figure_view_id = id or f"figure-view-{self._story_figure_view_index}"
        self._story_figure_view_index += 1
        self.add_figure_view(
            id=figure_view_id,
            ref=ref,
            # Left unnumbered on purpose. Numbers are assigned from the finished
            # document, where figures and images are interleaved, rather than
            # from the order this happened to be called in. An explicit number
            # still wins.
            story_number=number,
            actions=actions,
        )
        return f"@figure({figure_view_id})"

    def set_story_markdown(
        self,
        markdown: str,
        *,
        base_dir: str | Path = ".",
        signers: Sequence[tuple[str, bytes]] = (),
    ) -> None:
        """Set the story from a string.

        ``base_dir`` is what relative image paths are resolved against. A story
        read from a file gets that for free; one built in Python has to say.
        """

        self.story_markdown = markdown.strip() + "\n"
        self.story_base_dir = Path(base_dir)
        self.story_signers = list(signers)
        self._resolved_story = None

    def set_story_markdown_file(
        self,
        path: str | Path,
        *,
        signers: Sequence[tuple[str, bytes]] = (),
    ) -> None:
        """Set the story from a Markdown file on disk.

        This is the form that lets a story be edited in an ordinary Markdown
        editor: image paths in the file are relative to the file, which is what
        every editor's preview assumes.
        """

        source = Path(path)
        self.set_story_markdown(
            source.read_text(encoding="utf-8"),
            base_dir=source.parent,
            signers=signers,
        )

    def add_image(
        self,
        *,
        id: str,
        source_path: str | Path,
        path: str | None = None,
        max_width: int | None = None,
        format: str | None = None,
        quality: int | None = None,
        display_width: ImageWidth | None = None,
        provenance: SourceProvenance | None = None,
        signers: Sequence[tuple[str, bytes]] = (),
    ) -> str:
        """Declare settings for one image and return the reference to write.

        The returned value is the source path as the story should reference it,
        so a generated story can interpolate it directly and go through exactly
        the same discovery pass a hand-written one does.
        """

        declared = _DeclaredImage(
            id=id,
            source_path=Path(source_path),
            path=path,
            max_width=max_width,
            format=format,
            quality=quality,
            display_width=display_width,
            provenance=provenance,
            signers=tuple(signers),
        )
        self._declared_images.append(declared)
        self._resolved_story = None
        return str(source_path)

    def add_csv_dataset(
        self,
        *,
        id: str,
        arrays: Mapping[str, ArraySpec | Sequence[Any]],
        title: str | None = None,
        index: Index | None = None,
        path: str | None = None,
        provenance: SourceProvenance | None = None,
        signers: Sequence[tuple[str, bytes]] = (),
    ) -> Dataset:
        normalized: dict[str, ArraySpec] = {}
        for name, spec in arrays.items():
            normalized[name] = spec.normalized() if isinstance(spec, ArraySpec) else array(spec).normalized()

        dataset = Dataset(
            id=id,
            title=title,
            path=path or f"data/{id}.csv",
            arrays=normalized,
            index=index or Index.no_index(),
            provenance=provenance,
        )
        dataset.sample_count()
        dataset.signatures = _sign_all(dataset.csv_bytes(), signers) if signers else []
        self.datasets.append(dataset)
        return dataset

    def add_hdf_dataset(
        self,
        *,
        id: str,
        hdf5_path: str | Path,
        y_arrays: Sequence[HdfArraySpec | tuple[str, str]],
        index: Index | None = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        title: str | None = None,
        path: str | None = None,
        provenance: SourceProvenance | None = None,
        signers: Sequence[tuple[str, bytes]] = (),
    ) -> HdfDataset:
        dataset = HdfDataset(
            id=id,
            title=title,
            path=path or f"data/{id}.h5",
            hdf5_source_path=Path(hdf5_path),
            y_arrays=[_normalize_hdf_array(entry) for entry in y_arrays],
            index=index or Index.no_index(),
            chunk_size=chunk_size,
            provenance=provenance,
        )
        dataset.signatures = _sign_all(dataset.hdf5_source_path.read_bytes(), signers) if signers else []
        self.hdf_datasets.append(dataset)
        return dataset

    def add_line_figure(
        self,
        *,
        id: str,
        title: str,
        data: str,
        x: str,
        y: Sequence[str | tuple[str, str] | LineArtist],
        caption: str | None = None,
        x_axis: AxisDataType | None = None,
        y_axis: AxisDataType | None = None,
        scatter: Sequence[ScatterArtist] = (),
        controls: Sequence[DropdownCtrlSpec | SliderCtrlSpec] = (),
        map_specs: Sequence[MapSpec] = (),
        time_series: Sequence[TimeSeriesArtist] = (),
        table_views: Sequence[TableViewSpec] = (),
        y2: Sequence[str | tuple[str, str] | LineArtist] = (),
        y2_axis: AxisDataType | None = None,
        stem: Sequence[StemArtist] = (),
        form_title: str | None = None,
        form_caption: str | None = None,
    ) -> FigureSpec:
        def _to_lines(items: Sequence[str | tuple[str, str] | LineArtist]) -> list[LineArtist]:
            lines: list[LineArtist] = []
            for item in items:
                if isinstance(item, LineArtist):
                    lines.append(item)
                elif isinstance(item, tuple):
                    lines.append(LineArtist(array=item[0], label=item[1]))
                else:
                    lines.append(LineArtist(array=item, label=item))
            return lines

        lines = _to_lines(y)
        lines2 = _to_lines(y2)
        inferred_x_axis = AxisDataType.time_series(label=x) if time_series else AxisDataType.continuous(label=x)
        figure_spec = FigureSpec(
            id=id,
            title=title,
            data=data,
            x=x,
            lines=lines,
            scatters=list(scatter),
            caption=caption,
            x_axis=x_axis or inferred_x_axis,
            y_axis=y_axis or AxisDataType.continuous(label="Value"),
            controls=list(controls),
            map_specs=list(map_specs),
            time_series=list(time_series),
            table_views=list(table_views),
            lines2=lines2,
            y2_axis=y2_axis,
            stems=list(stem),
            form_title=form_title,
            form_caption=form_caption,
        )
        self.figure_specs.append(figure_spec)
        return figure_spec

    def add_map_figure(
        self,
        *,
        id: str,
        title: str,
        map_specs: Sequence[MapSpec],
        caption: str | None = None,
        controls: Sequence[DropdownCtrlSpec | SliderCtrlSpec] = (),
        table_views: Sequence[TableViewSpec] = (),
        form_title: str | None = None,
        form_caption: str | None = None,
    ) -> FigureSpec:
        figure_spec = FigureSpec(
            id=id,
            title=title,
            data="",
            x="",
            lines=[],
            scatters=[],
            caption=caption,
            x_axis=AxisDataType.continuous(label=""),
            y_axis=AxisDataType.continuous(label=""),
            controls=list(controls),
            map_specs=list(map_specs),
            table_views=list(table_views),
            form_title=form_title,
            form_caption=form_caption,
        )
        self.figure_specs.append(figure_spec)
        return figure_spec

    def render_project_dhall(self) -> str:
        _, story_figure_views = self.render_story_markdown()
        figure_views = [*self.figure_views, *story_figure_views]
        resolved = self.resolve_story()

        # Numbers come from the packaged document rather than from the order
        # story_figure() happened to be called in, so a figure and an image
        # interleaved in the prose are numbered as a reader meets them. An
        # explicitly set number wins, since the author asked for it.
        for figure_view in figure_views:
            if figure_view.story_number is None and figure_view.id is not None:
                figure_view.story_number = resolved.figure_view_numbers.get(figure_view.id)

        project = _record(
            [
                ("title", _quote(self.title)),
                ("subtitle", _optional_display_text(self.subtitle)),
                ("description", _optional_display_text(self.description)),
                ("authors", _list([_quote(author) for author in self.authors], "Text")),
                ("created", _optional_text(self.created)),
                ("updated", _optional_text(self.updated)),
                ("documentVersion", _optional_text(self.document_version)),
            ]
        )
        story = _record(
            [
                ("documentPath", _quote("story/index.md")),
                ("format", "Limelight.StoryFormat.markdown"),
                ("page", self.page.render()),
                ("spacing", self.spacing.render()),
                ("signatures", _render_signatures(resolved.signatures)),
            ]
        )
        manifest = _record(
            [
                ("limelightVersion", _quote("1.0")),
                ("project", project),
                (
                    "controlParameters",
                    _list(
                        [control_parameter.render() for control_parameter in self.control_parameters],
                        "Limelight.ControlParameter",
                    ),
                ),
                ("sources", _list(
                    [dataset.render_source() for dataset in self.datasets]
                    + [dataset.render_source() for dataset in self.hdf_datasets],
                    "Limelight.Source",
                )),
                ("figures", _list([figure.render() for figure in self.figure_specs], "Limelight.FigureSpec")),
                ("figureViews", _list([figure_view.render() for figure_view in figure_views], "Limelight.FigureView")),
                ("assets", _list([asset.render() for asset in resolved.assets], "Limelight.ImageAsset")),
                ("story", story),
            ]
        )
        return "-- Generated by limelight-writer. Edit the Python authoring script instead of this file.\n\n" + "\n".join(
            [
                "let Limelight = ./language-reference/limelight-v1.dhall",
                "",
                "in    " + manifest,
                "    : Limelight.Manifest",
                "",
            ]
        )

    def render_story_markdown(self) -> tuple[str, list[FigureView]]:
        if self.story_markdown is not None:
            return self.story_markdown, []
        return f"# {self.title}\n", []

    def resolve_story(self) -> ResolvedStory:
        """Package the story: convert its images, rewrite it, number it, sign it.

        Memoised, because converting images is the expensive part of a build
        and both the manifest and the package folder need the result.
        """

        if self._resolved_story is None:
            self._resolved_story = self._resolve_story()
        return self._resolved_story

    def _resolve_story(self) -> ResolvedStory:
        authored, _ = self.render_story_markdown()
        assets, packaged_paths = self._package_story_images(authored)
        markdown = rewrite_image_destinations(authored, packaged_paths.get)

        # Numbering runs on the packaged document, so it sees exactly what a
        # reader will, and images are keyed by the packaged path the rewrite
        # just produced.
        figure_view_numbers: dict[str, int] = {}
        image_numbers: dict[str, int] = {}
        for item in numbered_story_items(markdown):
            if item.kind == "figureView":
                figure_view_numbers.setdefault(item.key, item.number)
            else:
                image_numbers.setdefault(item.key, item.number)

        for asset in assets:
            asset.story_number = image_numbers.get(asset.path)

        # Cross-references are resolved into literal text here, so the packaged
        # prose carries "Figure 3" and no reader has to re-derive it. A number
        # computed twice is a number that can disagree with itself, and the
        # document is signed.
        anchors = dict(figure_view_numbers)
        for asset in assets:
            if asset.story_number is not None:
                anchors[asset.id] = asset.story_number
        markdown = resolve_cross_references(markdown, anchors.get)

        signatures = (
            _sign_all(markdown.encode("utf-8"), self.story_signers) if self.story_signers else []
        )
        return ResolvedStory(
            markdown=markdown,
            assets=assets,
            signatures=signatures,
            figure_view_numbers=figure_view_numbers,
        )

    def _package_story_images(self, markdown: str) -> tuple[list[ImageAsset], dict[str, str]]:
        """Convert every image the story references and place it in the package."""

        declared_by_source = {
            declared.source_path.resolve(): declared for declared in self._declared_images
        }
        used_sources: set[Path] = set()
        assets: list[ImageAsset] = []
        packaged_paths: dict[str, str] = {}
        used_ids: set[str] = set()

        stray = unparsed_attribute_text(markdown)
        if stray:
            raise ImageWidthError(
                f"Story has attribute text that did not attach to anything: {', '.join(stray)}. "
                'A percentage has to be quoted, as in {width="60%"}.'
            )

        for reference in story_image_references(markdown):
            source = reference.src
            if source in packaged_paths:
                continue
            if _is_remote_reference(source):
                raise ImageConversionError(
                    f"Story image {source!r} is a remote reference. A package is self-contained and "
                    "signed, so images have to be bundled rather than fetched."
                )

            resolved_source = (self.story_base_dir / source).resolve()
            declared = declared_by_source.get(resolved_source)
            if declared is not None:
                used_sources.add(resolved_source)

            converted = convert_image(
                resolved_source,
                max_width=_first_set(
                    declared.max_width if declared is not None else None,
                    self.image_max_width,
                ),
                format=declared.format if declared is not None else None,
                quality=declared.quality if declared is not None else None,
            )

            asset_id = declared.id if declared is not None else _image_id_from_source(source)
            if asset_id in used_ids:
                raise ValueError(
                    f"Image id {asset_id!r} is used by more than one image; give one of them an "
                    "explicit id with add_image()"
                )
            used_ids.add(asset_id)

            package_path = (
                declared.path
                if declared is not None and declared.path is not None
                else self._image_package_path(resolved_source, converted)
            )
            assets.append(
                ImageAsset(
                    id=asset_id,
                    source_path=resolved_source,
                    path=package_path,
                    converted=converted,
                    display_width=declared.display_width if declared is not None else None,
                    provenance=declared.provenance if declared is not None else None,
                    signatures=(
                        _sign_all(converted.data, declared.signers)
                        if declared is not None and declared.signers
                        else []
                    ),
                )
            )
            packaged_paths[source] = package_path

        for declared in self._declared_images:
            if declared.source_path.resolve() not in used_sources:
                logger.warning(
                    "add_image(id=%r) declared %s, but the story does not reference it. "
                    "Check the path in the story matches the one declared.",
                    declared.id,
                    declared.source_path,
                )

        return assets, packaged_paths

    def _image_package_path(self, source: Path, converted: ConvertedImage) -> str:
        """Where a converted image lands in the package.

        Named from the source stem and a digest of the source bytes and the
        conversion settings, so two folders holding the same basename cannot
        collide, two references to one image share a file, and the name is
        stable across builds.
        """

        digest = hashlib.sha256(
            f"{converted.source_sha256}:{converted.max_width}:{converted.quality}".encode("utf-8")
        ).hexdigest()[:8]
        stem = _slugify_image_stem(source.stem)
        return f"{self.image_package_prefix}/{stem}-{digest}{converted.suffix}"

    def write_folder(
        self,
        path: str | Path,
        *,
        overwrite: bool = False,
        schema_dir: str | Path | None = None,
    ) -> Path:
        package_root = Path(path)
        if package_root.exists():
            if not overwrite:
                raise FileExistsError(f"{package_root} already exists")
            if package_root.is_file():
                package_root.unlink()
            else:
                if package_root.suffix != ".limelight":
                    raise ValueError(f"Refusing to recursively overwrite non-.limelight folder {package_root}")
                shutil.rmtree(package_root)

        package_root.mkdir(parents=True, exist_ok=True)
        for dataset in self.datasets:
            dataset.write_csv(package_root)
        for hdf_dataset in self.hdf_datasets:
            hdf_dataset.write_source_file(package_root)

        self._copy_schema(package_root, schema_dir=schema_dir)
        resolved = self.resolve_story()
        for asset in resolved.assets:
            asset.write_asset_file(package_root)
        story_path = package_root / "story" / "index.md"
        story_path.parent.mkdir(parents=True, exist_ok=True)
        story_path.write_text(resolved.markdown, encoding="utf-8", newline="\n")
        (package_root / "project.dhall").write_text(self.render_project_dhall(), encoding="utf-8")
        return package_root

    def write_archive(
        self,
        path: str | Path,
        *,
        overwrite: bool = False,
        schema_dir: str | Path | None = None,
    ) -> Path:
        archive_path = Path(path)
        if archive_path.exists():
            if not overwrite:
                raise FileExistsError(f"{archive_path} already exists")
            archive_path.unlink()

        with TemporaryDirectory() as tmp:
            folder = self.write_folder(Path(tmp) / "package", overwrite=True, schema_dir=schema_dir)
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for item in folder.rglob("*"):
                    if item.is_file():
                        archive.write(item, item.relative_to(folder))
        return archive_path

    @staticmethod
    def _copy_schema(package_root: Path, schema_dir: str | Path | None = None) -> None:
        source = Path(schema_dir) if schema_dir is not None else LANGUAGE_REFERENCE_DIR
        if not source.exists():
            raise FileNotFoundError(f"Cannot find Limelight language reference at {source}")

        target = package_root / "language-reference"
        shutil.copytree(source, target)
