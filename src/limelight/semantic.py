from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from matplotlib.colors import is_color_like

from . import refs
from .reader import LimelightError

_VALID_MARKERS = ("o", "s", "^", "v", "D", "x", "+", "*", "p", "h", "<", ">")
_VALID_LINESTYLES = ("-", "--", "-.", ":")


@dataclass(frozen=True)
class AxisLimitShape:
    axis: str
    kind: str


def validate_manifest_semantics(manifest: dict[str, Any]) -> None:
    validator = _SemanticValidator(manifest)
    validator.validate()


def axis_limit_shape(limit: dict[str, Any]) -> AxisLimitShape:
    if "xStart" in limit and "xEnd" in limit:
        return AxisLimitShape(axis="x", kind="timeSeries")
    if "xLower" in limit and "xUpper" in limit:
        return AxisLimitShape(axis="x", kind="continuous")
    if "yStart" in limit and "yEnd" in limit:
        return AxisLimitShape(axis="y", kind="timeSeries")
    if "yLower" in limit and "yUpper" in limit:
        return AxisLimitShape(axis="y", kind="continuous")
    raise TypeError(f"Unknown AxisLimit payload: {limit!r}")


def is_axis_limit_payload(value: dict[str, Any]) -> bool:
    return (
        ("xStart" in value and "xEnd" in value)
        or ("xLower" in value and "xUpper" in value)
        or ("yStart" in value and "yEnd" in value)
        or ("yLower" in value and "yUpper" in value)
    )


def axis_data_type_signature(axis_spec: dict[str, Any]) -> str:
    return json.dumps(axis_spec["dataType"], sort_keys=True, separators=(",", ":"))


AXIS_SCALES = {"Linear", "Log"}


def axis_data_type_kind(axis_spec: dict[str, Any]) -> str:
    data_type = axis_spec["dataType"]
    if data_type == "discrete":
        return "discrete"
    keys = set(data_type.keys())
    if keys == {"calendar"}:
        return "timeSeries"
    if keys == {"unit", "scale"}:
        return "continuous"
    raise LimelightError(f"AxisSpec {axis_spec.get('id', '?')!r} has unrecognized dataType shape {data_type!r}")


def control_parameter_data_type_kind(control_parameter: dict[str, Any]) -> str:
    data_type = control_parameter["dataType"]
    if "discrete" in data_type or "options" in data_type:
        return "discrete"
    if "integer" in data_type or "integerDefault" in data_type:
        return "integer"
    if "float" in data_type or "floatDefault" in data_type:
        return "float"
    if "time" in data_type or "timeDefault" in data_type:
        return "time"
    raise TypeError(f"ControlParameter {control_parameter['id']!r} has unknown dataType payload {data_type!r}")


def control_parameter_data_type_payload(control_parameter: dict[str, Any]) -> dict[str, Any]:
    data_type = control_parameter["dataType"]
    kind = control_parameter_data_type_kind(control_parameter)
    if kind in data_type:
        return data_type[kind]
    return data_type


def control_parameter_default_min_max(data_type: dict[str, Any], kind: str) -> tuple[Any, Any, Any]:
    if kind == "integer":
        return data_type["integerDefault"], data_type["integerMin"], data_type["integerMax"]
    if kind == "float":
        return data_type["floatDefault"], data_type["floatMin"], data_type["floatMax"]
    if kind == "time":
        return data_type["timeDefault"], data_type["timeMin"], data_type["timeMax"]
    raise TypeError(f"ControlParameter kind {kind!r} does not have min/max fields")


def optional_field(record: dict[str, Any], field: str) -> Any | None:
    return record[field] if field in record else None


def iter_axes_actions(axes_spec: dict[str, Any], figure_view_actions: Iterable[dict[str, Any]] = ()) -> Iterable[dict[str, Any]]:
    yield from axes_spec["actions"]
    for action_binding in figure_view_actions:
        yield action_binding["action"]


def _is_hdf_source(source: dict[str, Any]) -> bool:
    return "yArrays" in source


def _is_timeseries_artist(artist: dict[str, Any]) -> bool:
    return "transform" in artist


class _SemanticValidator:
    def __init__(self, manifest: dict[str, Any]) -> None:
        self.manifest = manifest
        self.control_parameters = {control_parameter["id"]: control_parameter for control_parameter in manifest["controlParameters"]}
        self.sources = {source["id"]: source for source in manifest["sources"]}
        self.figures = {figure["id"]: figure for figure in manifest["figures"]}
        self.figure_views = {figure_view["id"]: figure_view for figure_view in manifest["figureViews"]}
        self.axes_specs: dict[str, dict[str, Any]] = {}
        self.dataset_arrays: dict[str, set[str]] = {}
        self.hdf_source_ids: set[str] = set()

    def validate(self) -> None:
        self._require_unique_ids("ControlParameter", self.manifest["controlParameters"])
        self._require_unique_ids("Source", self.manifest["sources"])
        self._require_unique_ids("FigureSpec", self.manifest["figures"])
        self._require_unique_ids("FigureView", self.manifest["figureViews"])
        self._require_unique_ids("ImageAsset", self.manifest.get("assets") or [])
        self._collect_dataset_arrays()
        self._validate_control_parameters()
        self._collect_axes_specs()
        self._validate_axis_share_groups()
        self._validate_figures()
        self._validate_figure_views()
        self._validate_assets()
        self._validate_story()

    def _require_unique_ids(self, label: str, items: list[dict[str, Any]]) -> None:
        seen: set[str] = set()
        for item in items:
            item_id = item["id"]
            if item_id in seen:
                raise LimelightError(f"{label} id {item_id!r} is declared more than once")
            seen.add(item_id)

    def _collect_axes_specs(self) -> None:
        seen_axis_ids: set[str] = set()
        for figure in self.manifest["figures"]:
            for axes_spec in figure["axesSpecs"]:
                axes_id = axes_spec["id"]
                if axes_id in self.axes_specs:
                    raise LimelightError(f"AxesSpec id {axes_id!r} is declared more than once")
                self.axes_specs[axes_id] = axes_spec
                for axis_name in ("xAxis", "yAxis"):
                    axis_spec = axes_spec[axis_name]
                    axis_id = axis_spec["id"]
                    if axis_id in seen_axis_ids:
                        raise LimelightError(f"AxisSpec id {axis_id!r} is declared more than once")
                    seen_axis_ids.add(axis_id)
                    self._validate_axis_data_type(axis_spec)

    def _validate_axis_data_type(self, axis_spec: dict[str, Any]) -> None:
        kind = axis_data_type_kind(axis_spec)
        if kind == "continuous":
            scale = axis_spec["dataType"]["scale"]
            if scale not in AXIS_SCALES:
                raise LimelightError(f"AxisSpec {axis_spec['id']!r} has unknown scale {scale!r}")

    def _collect_dataset_arrays(self) -> None:
        for source in self.manifest["sources"]:
            if _is_hdf_source(source):
                self.hdf_source_ids.add(source["id"])
                self.dataset_arrays[source["id"]] = {
                    y_array["schema"]["name"] for y_array in source["yArrays"]
                }
                continue
            self.dataset_arrays[source["id"]] = {
                array["name"]
                for array in (source.get("schema") or [])
            }

    def _validate_control_parameters(self) -> None:
        for control_parameter in self.manifest["controlParameters"]:
            kind = control_parameter_data_type_kind(control_parameter)
            data_type = control_parameter_data_type_payload(control_parameter)
            if kind == "discrete":
                self._validate_discrete_control_parameter(control_parameter, data_type)
                continue
            if kind == "integer":
                self._validate_bounded_control_parameter(control_parameter, data_type, kind)
                continue
            if kind == "float":
                self._validate_bounded_control_parameter(control_parameter, data_type, kind)
                continue
            if kind == "time":
                self._validate_bounded_control_parameter(control_parameter, data_type, kind)
                continue
            raise TypeError(f"ControlParameter {control_parameter['id']!r} has unknown dataType kind {kind!r}")

    def _validate_discrete_control_parameter(self, control_parameter: dict[str, Any], data_type: dict[str, Any]) -> None:
        options = data_type["options"]
        if not options:
            raise LimelightError(f"Discrete control parameter {control_parameter['id']!r} must declare at least one option")
        values: set[str] = set()
        for option in options:
            value = option["value"]
            if value in values:
                raise LimelightError(f"Discrete control parameter {control_parameter['id']!r} repeats option value {value!r}")
            values.add(value)
        default = data_type["default"]
        if default is not None and default not in values:
            raise LimelightError(f"Discrete control parameter {control_parameter['id']!r} default {default!r} is not an option")

    def _validate_bounded_control_parameter(self, control_parameter: dict[str, Any], data_type: dict[str, Any], kind: str) -> None:
        default, minimum, maximum = control_parameter_default_min_max(data_type, kind)
        if minimum is not None and maximum is not None and minimum > maximum:
            raise LimelightError(f"ControlParameter {control_parameter['id']!r} min {minimum!r} is greater than max {maximum!r}")
        if default is not None and minimum is not None and default < minimum:
            raise LimelightError(f"ControlParameter {control_parameter['id']!r} default {default!r} is below min {minimum!r}")
        if default is not None and maximum is not None and default > maximum:
            raise LimelightError(f"ControlParameter {control_parameter['id']!r} default {default!r} is above max {maximum!r}")

    def _validate_axis_share_groups(self) -> None:
        groups: dict[str, tuple[str, str]] = {}
        for axes_spec in self.axes_specs.values():
            for axis_name in ("xAxis", "yAxis"):
                axis_spec = axes_spec[axis_name]
                share_group = optional_field(axis_spec, "shareGroup")
                if share_group is None:
                    continue
                signature = axis_data_type_signature(axis_spec)
                existing = groups.get(share_group)
                if existing is None:
                    groups[share_group] = (axis_spec["id"], signature)
                    continue
                existing_axis_id, existing_signature = existing
                if existing_signature != signature:
                    raise LimelightError(
                        f"Axis shareGroup {share_group!r} mixes incompatible AxisDataType values: "
                        f"{existing_axis_id!r} has {existing_signature}, {axis_spec['id']!r} has {signature}"
                    )

    def _validate_figures(self) -> None:
        for figure in self.manifest["figures"]:
            for axes_spec in figure["axesSpecs"]:
                self._validate_axes_spec(figure, axes_spec)
            for map_spec in figure["mapSpecs"]:
                self._validate_map_spec(figure, map_spec)
            for form_spec in figure["formSpecs"]:
                for control in form_spec["controls"]:
                    self._validate_control(form_spec, control)
            for table_view_spec in figure["tableViewSpecs"]:
                self._validate_table_view_spec(figure, table_view_spec)

    def _validate_figure_views(self) -> None:
        story_numbers: dict[int, str] = {}
        for figure_view in self.manifest["figureViews"]:
            self._validate_figure_view(figure_view)
            story_number = optional_field(figure_view, "storyNumber")
            if story_number is None:
                continue
            # Two views printing the same "Figure N." would leave a reader with
            # no way to tell which one a cross-reference meant.
            claimed_by = story_numbers.get(story_number)
            if claimed_by is not None:
                raise LimelightError(
                    f"FigureView {figure_view['id']!r} and {claimed_by!r} both claim storyNumber {story_number!r}"
                )
            story_numbers[story_number] = figure_view["id"]

    def _validate_axes_spec(self, figure: dict[str, Any], axes_spec: dict[str, Any]) -> None:
        for action in axes_spec["actions"]:
            self._validate_axes_action(figure["id"], axes_spec, action)

    def _validate_axes_action(self, figure_id: str, axes_spec: dict[str, Any], action: dict[str, Any]) -> None:
        if "y" in action:
            self._validate_plot_artist(figure_id, axes_spec, action)
            return
        limit = action.get("AxesActionSetLimits")
        if limit is not None:
            self._validate_axis_limit(axes_spec, limit)
            return
        if is_axis_limit_payload(action):
            self._validate_axis_limit(axes_spec, action)
            return
        decorator = action.get("AxesActionAddDecorator")
        if decorator is not None:
            self._validate_axes_decorator(axes_spec, decorator)
            return
        if "xLimit" in action or "arrow" in action:
            self._validate_axes_decorator(axes_spec, action)
            return
        raise LimelightError(f"AxesSpec {axes_spec['id']!r} has unknown AxesAction payload {action!r}")

    def _validate_plot_artist(self, figure_id: str, axes_spec: dict[str, Any], artist: dict[str, Any]) -> None:
        kind = refs.artist_kind(artist)
        if axis_data_type_kind(axes_spec["xAxis"]) == "discrete" and kind not in ("line", "scatter", "stem"):
            raise LimelightError(
                f"FigureSpec {figure_id!r} AxesSpec {axes_spec['id']!r} artist {artist['id']!r} "
                f"cannot use a discrete x-axis; only line/scatter/stem artists support it"
            )
        fields = ("x", "y") if kind in ("scatter", "stem") else ("y",)
        for field in fields:
            source_id, array_name = refs.parse_column_ref(artist[field])
            if source_id not in self.dataset_arrays:
                raise LimelightError(
                    f"FigureSpec {figure_id!r} AxesSpec {axes_spec['id']!r} artist {artist['id']!r} "
                    f"references unknown dataset {source_id!r}"
                )
            is_hdf_source = source_id in self.hdf_source_ids
            if kind == "timeSeries":
                if not is_hdf_source:
                    raise LimelightError(
                        f"FigureSpec {figure_id!r} AxesSpec {axes_spec['id']!r} timeSeries artist {artist['id']!r} "
                        f"must reference an hdf source, got source {source_id!r}"
                    )
            elif is_hdf_source:
                raise LimelightError(
                    f"FigureSpec {figure_id!r} AxesSpec {axes_spec['id']!r} artist {artist['id']!r} "
                    f"cannot reference hdf source {source_id!r}; use a timeSeries artist"
                )
            if array_name not in self.dataset_arrays[source_id]:
                raise LimelightError(
                    f"FigureSpec {figure_id!r} AxesSpec {axes_spec['id']!r} artist {artist['id']!r} "
                    f"references missing array {array_name!r} in source {source_id!r}"
                )
        if kind == "timeSeries" and artist["transform"] != "identity":
            raise LimelightError(
                f"FigureSpec {figure_id!r} AxesSpec {axes_spec['id']!r} timeSeries artist {artist['id']!r} "
                f"has an unsupported transform; only identity is supported"
            )
        visible_when = artist.get("visibleWhen")
        if visible_when is not None:
            self._require_text_control_parameter(visible_when["controlParameter"], f"artist {artist['id']!r} visibleWhen")
        color = artist.get("color")
        if color is not None and not is_color_like(color):
            raise LimelightError(
                f"FigureSpec {figure_id!r} AxesSpec {axes_spec['id']!r} artist {artist['id']!r} "
                f"has an invalid color {color!r}"
            )
        marker = artist.get("marker")
        if marker is not None and kind in ("line", "scatter") and marker not in _VALID_MARKERS:
            raise LimelightError(
                f"FigureSpec {figure_id!r} AxesSpec {axes_spec['id']!r} artist {artist['id']!r} "
                f"has an unsupported marker {marker!r}; expected one of {_VALID_MARKERS}"
            )
        linestyle = artist.get("linestyle")
        if linestyle is not None and kind == "line" and linestyle not in _VALID_LINESTYLES:
            raise LimelightError(
                f"FigureSpec {figure_id!r} AxesSpec {axes_spec['id']!r} artist {artist['id']!r} "
                f"has an unsupported linestyle {linestyle!r}; expected one of {_VALID_LINESTYLES}"
            )
        if kind == "scatter":
            for field in ("colorBy", "sizeBy"):
                ref = artist.get(field)
                if ref is None:
                    continue
                source_id, array_name = refs.parse_column_ref(ref)
                if source_id not in self.dataset_arrays:
                    raise LimelightError(
                        f"FigureSpec {figure_id!r} AxesSpec {axes_spec['id']!r} artist {artist['id']!r} "
                        f"{field} references unknown dataset {source_id!r}"
                    )
                if array_name not in self.dataset_arrays[source_id]:
                    raise LimelightError(
                        f"FigureSpec {figure_id!r} AxesSpec {axes_spec['id']!r} artist {artist['id']!r} "
                        f"{field} references missing array {array_name!r} in source {source_id!r}"
                    )

    def _validate_table_view_spec(self, figure: dict[str, Any], table_view_spec: dict[str, Any]) -> None:
        source_id = table_view_spec["data"]
        if source_id not in self.dataset_arrays:
            raise LimelightError(
                f"FigureSpec {figure['id']!r} TableViewSpec {table_view_spec['id']!r} "
                f"references unknown dataset {source_id!r}"
            )
        columns = optional_field(table_view_spec, "columns")
        if columns is not None:
            for column in columns:
                if column not in self.dataset_arrays[source_id]:
                    raise LimelightError(
                        f"FigureSpec {figure['id']!r} TableViewSpec {table_view_spec['id']!r} "
                        f"references missing column {column!r} in source {source_id!r}"
                    )

        displayed_columns = set(columns) if columns is not None else self.dataset_arrays[source_id]
        for column_format in table_view_spec["columnFormats"]:
            column = column_format["column"]
            if column not in displayed_columns:
                raise LimelightError(
                    f"FigureSpec {figure['id']!r} TableViewSpec {table_view_spec['id']!r} "
                    f"columnFormats references column {column!r} not shown by this table view"
                )
            format_spec = optional_field(column_format, "format")
            if format_spec is not None:
                try:
                    format(0.0, format_spec)
                except ValueError:
                    raise LimelightError(
                        f"FigureSpec {figure['id']!r} TableViewSpec {table_view_spec['id']!r} "
                        f"columnFormats has an invalid format spec {format_spec!r} for column {column!r}"
                    )

    def _validate_map_spec(self, figure: dict[str, Any], map_spec: dict[str, Any]) -> None:
        for action in map_spec["actions"]:
            self._validate_map_action(figure, map_spec, action)

    def _validate_map_action(self, figure: dict[str, Any], map_spec: dict[str, Any], action: dict[str, Any]) -> None:
        limits = action.get("MapActionSetLimits")
        if limits is not None:
            if limits["longitudeLower"] > limits["longitudeUpper"]:
                raise LimelightError(
                    f"FigureSpec {figure['id']!r} MapSpec {map_spec['id']!r} longitude lower bound "
                    f"{limits['longitudeLower']!r} is greater than upper bound {limits['longitudeUpper']!r}"
                )
            if limits["latitudeLower"] > limits["latitudeUpper"]:
                raise LimelightError(
                    f"FigureSpec {figure['id']!r} MapSpec {map_spec['id']!r} latitude lower bound "
                    f"{limits['latitudeLower']!r} is greater than upper bound {limits['latitudeUpper']!r}"
                )
            return
        if "longitudeLower" in action:
            if action["longitudeLower"] > action["longitudeUpper"]:
                raise LimelightError(
                    f"FigureSpec {figure['id']!r} MapSpec {map_spec['id']!r} longitude lower bound "
                    f"{action['longitudeLower']!r} is greater than upper bound {action['longitudeUpper']!r}"
                )
            if action["latitudeLower"] > action["latitudeUpper"]:
                raise LimelightError(
                    f"FigureSpec {figure['id']!r} MapSpec {map_spec['id']!r} latitude lower bound "
                    f"{action['latitudeLower']!r} is greater than upper bound {action['latitudeUpper']!r}"
                )
            return
        scatter = action.get("MapActionAddDatasetScatter")
        if scatter is not None:
            self._validate_map_dataset_scatter(figure, map_spec, scatter)
            return
        if "longitude" in action and "latitude" in action:
            self._validate_map_dataset_scatter(figure, map_spec, action)
            return
        geojson_layer = action.get("MapActionAddGeoJsonLayer")
        if geojson_layer is not None:
            self._validate_map_geojson_layer(map_spec, geojson_layer)
            return
        if "path" in action:
            self._validate_map_geojson_layer(map_spec, action)
            return
        raise LimelightError(f"MapSpec {map_spec['id']!r} has unknown MapAction payload {action!r}")

    def _validate_map_dataset_scatter(self, figure: dict[str, Any], map_spec: dict[str, Any], scatter: dict[str, Any]) -> None:
        for field in ("longitude", "latitude"):
            self._require_column_ref(figure, map_spec, scatter[field])
        for field in ("colorBy", "sizeBy"):
            ref = optional_field(scatter, field)
            if ref is not None:
                self._require_column_ref(figure, map_spec, ref)

    def _require_column_ref(self, figure: dict[str, Any], map_spec: dict[str, Any], ref: str) -> None:
        source_id, array_name = refs.parse_column_ref(ref)
        if source_id not in self.dataset_arrays:
            raise LimelightError(f"FigureSpec {figure['id']!r} MapSpec {map_spec['id']!r} references unknown dataset {source_id!r}")
        if array_name not in self.dataset_arrays[source_id]:
            raise LimelightError(
                f"FigureSpec {figure['id']!r} MapSpec {map_spec['id']!r} "
                f"references missing array {array_name!r} in source {source_id!r}"
            )

    def _validate_map_geojson_layer(self, map_spec: dict[str, Any], layer: dict[str, Any]) -> None:
        self._require_safe_package_path(
            layer["path"],
            f"MapSpec {map_spec['id']!r} GeoJSON layer {layer['id']!r} path",
        )
        alpha = optional_field(layer, "alpha")
        if alpha is not None and not 0.0 <= alpha <= 1.0:
            raise LimelightError(f"MapSpec {map_spec['id']!r} GeoJSON layer {layer['id']!r} alpha {alpha!r} is outside 0..1")

    def _validate_axis_limit(self, axes_spec: dict[str, Any], limit: dict[str, Any]) -> None:
        shape = axis_limit_shape(limit)
        axis_spec = axes_spec["xAxis"] if shape.axis == "x" else axes_spec["yAxis"]
        axis_kind = axis_data_type_kind(axis_spec)
        if axis_kind != shape.kind:
            raise LimelightError(
                f"AxesSpec {axes_spec['id']!r} uses {shape.kind} {shape.axis}-axis limit "
                f"on {axis_kind} axis {axis_spec['id']!r}"
            )

    def _validate_axes_decorator(self, axes_spec: dict[str, Any], decorator: dict[str, Any]) -> None:
        annotation = decorator.get("AxesDecoratorAnnotation")
        if annotation is not None:
            arrow = annotation["arrow"]
            arrow["start"]["x"]
            arrow["start"]["y"]
            arrow["end"]["x"]
            arrow["end"]["y"]
            self._validate_annotation_color(axes_spec, annotation)
            return
        if "arrow" in decorator:
            arrow = decorator["arrow"]
            arrow["start"]["x"]
            arrow["start"]["y"]
            arrow["end"]["x"]
            arrow["end"]["y"]
            self._validate_annotation_color(axes_spec, decorator)
            return
        rect = decorator.get("AxesDecoratorRect")
        if rect is None and "yLimit" in decorator:
            rect = decorator
        if rect is not None:
            self._validate_rect_decorator(axes_spec, rect)
            return
        vspan = decorator.get("AxesDecoratorVSpan")
        if vspan is not None:
            shape = axis_limit_shape(vspan["xLimit"])
            if shape.axis != "x":
                raise LimelightError(f"AxesSpec {axes_spec['id']!r} AxesDecoratorVSpan must use an x-axis AxisLimit")
            self._validate_axis_limit(axes_spec, vspan["xLimit"])
            return
        if "xLimit" in decorator:
            shape = axis_limit_shape(decorator["xLimit"])
            if shape.axis != "x":
                raise LimelightError(f"AxesSpec {axes_spec['id']!r} AxesDecoratorVSpan must use an x-axis AxisLimit")
            self._validate_axis_limit(axes_spec, decorator["xLimit"])
            return
        raise LimelightError(f"AxesSpec {axes_spec['id']!r} has unknown AxesDecorator payload {decorator!r}")

    def _validate_rect_decorator(self, axes_spec: dict[str, Any], rect: dict[str, Any]) -> None:
        for axis, key in (("x", "xLimit"), ("y", "yLimit")):
            limit = rect[key]
            shape = axis_limit_shape(limit)
            if shape.axis != axis:
                raise LimelightError(
                    f"AxesSpec {axes_spec['id']!r} AxesDecoratorRect {key} must be a {axis}-axis AxisLimit"
                )
            self._validate_axis_limit(axes_spec, limit)
            # A VSpan collapses equal bounds into a marker line; a box with no
            # width or height is just a mistake.
            bounds = (limit.get(f"{axis}Lower"), limit.get(f"{axis}Upper"))
            if shape.kind != "continuous":
                bounds = (limit.get(f"{axis}Start"), limit.get(f"{axis}End"))
            if bounds[0] == bounds[1]:
                raise LimelightError(
                    f"AxesSpec {axes_spec['id']!r} AxesDecoratorRect {key} has equal bounds {bounds[0]!r}"
                )
        color = optional_field(rect, "color")
        if color is not None and not is_color_like(color):
            raise LimelightError(f"AxesSpec {axes_spec['id']!r} AxesDecoratorRect has an invalid color {color!r}")
        alpha = optional_field(rect, "alpha")
        if alpha is not None and not 0.0 <= float(alpha) <= 1.0:
            raise LimelightError(f"AxesSpec {axes_spec['id']!r} AxesDecoratorRect alpha {alpha!r} is not within 0..1")

    def _validate_annotation_color(self, axes_spec: dict[str, Any], annotation: dict[str, Any]) -> None:
        color = optional_field(annotation, "color")
        if color is not None and not is_color_like(color):
            raise LimelightError(
                f"AxesSpec {axes_spec['id']!r} AxesDecoratorAnnotation has an invalid color {color!r}"
            )

    def _validate_control(self, form_spec: dict[str, Any], control: dict[str, Any]) -> None:
        if "controlParameter" not in control:
            raise LimelightError(f"FormSpec {form_spec['id']!r} has unsupported control payload {control!r}")
        self._require_renderable_control_parameter(control["controlParameter"], f"FormSpec {form_spec['id']!r} control {control['id']!r}")

    def _require_safe_package_path(self, path: str, context: str) -> None:
        """Every path in a manifest names a file inside the package."""

        if not path:
            raise LimelightError(f"{context} must not be empty")
        if path.startswith("/") or "\\" in path:
            raise LimelightError(f"{context} {path!r} must be a relative POSIX path")
        if any(part in {"", "."} for part in path.split("/")):
            raise LimelightError(f"{context} {path!r} contains an empty or current-directory segment")
        if ".." in path.split("/"):
            raise LimelightError(f"{context} {path!r} must not escape the package root")

    def _validate_assets(self) -> None:
        story_numbers: dict[int, str] = {}
        paths: dict[str, str] = {}
        for asset in self.manifest.get("assets") or []:
            asset_id = asset["id"]
            self._require_safe_package_path(asset["path"], f"ImageAsset {asset_id!r} path")

            claimed_path = paths.get(asset["path"])
            if claimed_path is not None:
                raise LimelightError(
                    f"ImageAsset {asset_id!r} and {claimed_path!r} both claim path {asset['path']!r}"
                )
            paths[asset["path"]] = asset_id

            story_number = optional_field(asset, "storyNumber")
            if story_number is None:
                continue
            if story_number < 1:
                raise LimelightError(
                    f"ImageAsset {asset_id!r} storyNumber {story_number!r} must be one or greater"
                )
            # Images and figure views share one sequence, so this only catches
            # images colliding with each other; the cross-check against figure
            # views happens once both are loaded.
            claimed_by = story_numbers.get(story_number)
            if claimed_by is not None:
                raise LimelightError(
                    f"ImageAsset {asset_id!r} and {claimed_by!r} both claim storyNumber {story_number!r}"
                )
            story_numbers[story_number] = asset_id

    def _validate_story(self) -> None:
        story = self.manifest["story"]
        self._require_safe_package_path(story["documentPath"], "Story documentPath")
        if story["format"] != "markdown":
            raise LimelightError(f"Unsupported StoryFormat {story['format']!r}")

    def _validate_figure_view(self, figure_view: dict[str, Any]) -> None:
        figure_id = figure_view["ref"]
        if figure_id not in self.figures:
            raise LimelightError(f"FigureView {figure_view['id']!r} references unknown FigureSpec {figure_id!r}")
        index = optional_field(figure_view, "index")
        if index is not None and index < 0:
            raise LimelightError(f"FigureView {figure_view['id']!r} index {index!r} must be zero or greater")
        story_number = optional_field(figure_view, "storyNumber")
        if story_number is not None and story_number < 1:
            raise LimelightError(
                f"FigureView {figure_view['id']!r} storyNumber {story_number!r} must be one or greater"
            )
        for action_binding in figure_view["actions"]:
            axes_ref = action_binding["ref"]
            if axes_ref not in self.axes_specs:
                raise LimelightError(f"FigureView {figure_view['id']!r} references unknown AxesSpec {axes_ref!r}")
            self._validate_axes_action(figure_id, self.axes_specs[axes_ref], action_binding["action"])

    def _require_text_control_parameter(self, control_parameter_id: str, context: str) -> None:
        control_parameter = self.control_parameters.get(control_parameter_id)
        if control_parameter is None:
            raise LimelightError(f"{context} references unknown control parameter {control_parameter_id!r}")
        if control_parameter_data_type_kind(control_parameter) != "discrete":
            raise LimelightError(f"{context} references non-discrete control parameter {control_parameter_id!r}")

    def _require_renderable_control_parameter(self, control_parameter_id: str, context: str) -> None:
        control_parameter = self.control_parameters.get(control_parameter_id)
        if control_parameter is None:
            raise LimelightError(f"{context} references unknown control parameter {control_parameter_id!r}")
        kind = control_parameter_data_type_kind(control_parameter)
        if kind not in ("discrete", "integer", "float"):
            raise LimelightError(f"{context} references unsupported control parameter kind {kind!r}")
