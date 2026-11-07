from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import largeseries
from . import refs
from .hdf_sources import hdf_file_path
from .largeseries.mpl import MissingStyle
from .largeseries.status import FIRST_DECLARED_KIND, StatusMap
from .reader import LimelightPackage

if TYPE_CHECKING:
    from matplotlib.axes import Axes

    from .app import LimelightRuntime


def status_map_of(source: dict[str, Any]) -> StatusMap:
    """What the source's status codes mean, missing kinds numbered in declaration order."""
    return StatusMap.from_meanings([(int(entry["code"]), entry["meaning"]) for entry in source.get("statusCodes") or []])


def missing_styles_of(source: dict[str, Any]) -> dict[int, MissingStyle]:
    """How each declared absent code is marked, by its kind (the order `status_map_of` numbers them in)."""
    styles: dict[int, MissingStyle] = {}
    kind = FIRST_DECLARED_KIND
    for entry in source.get("statusCodes") or []:
        if entry["meaning"] != "absent":
            continue
        defaults = MissingStyle()
        styles[kind] = MissingStyle(
            label=entry["label"],
            marker=entry.get("marker") or defaults.marker,
            color=entry.get("color") or defaults.color,
        )
        kind += 1
    return styles


def hdf_source_spec(source: dict[str, Any], package: LimelightPackage, column_name: str) -> largeseries.SourceSpec:
    spec = _hdf_values_spec(source, package, column_name)
    entry = next(e for e in source["yArrays"] if e["schema"]["name"] == column_name)
    status = entry.get("status")
    if not status:
        return spec
    return spec.with_status(status["dataset"], status_map_of(source), column=status.get("column"))


def _hdf_values_spec(source: dict[str, Any], package: LimelightPackage, column_name: str) -> largeseries.SourceSpec:
    hdf5_path = hdf_file_path(package, source)
    # name -> (dataset, column): a 1-D dataset, or column j of a 2-D one.
    y_datasets = {entry["schema"]["name"]: (entry["dataset"], entry.get("column")) for entry in source["yArrays"]}
    if column_name not in y_datasets:
        raise KeyError(f"Unknown yArrays column {column_name!r} in source {source['id']!r}")
    y_dataset, y_column = y_datasets[column_name]

    index = source["index"]
    if index == "noIndex":
        return largeseries.SourceSpec.uniform(hdf5_path, y_dataset, x0=0.0, dx=1.0, y_column=y_column)

    if "intOrigin" in index:
        return largeseries.SourceSpec.uniform(
            hdf5_path, y_dataset, x0=float(index["intOrigin"]), dx=float(index["intStep"]), y_column=y_column
        )

    if "timeStepNom" in index:
        # Sample i sits at i * step in the index's unit; time_axis_affine then
        # puts that on the axis (days for an absolute origin).
        step = index["timeStepNom"] / index["timeStepDenom"]
        x0, dx = refs.time_axis_affine(index)
        return largeseries.SourceSpec.uniform(hdf5_path, y_dataset, x0=x0, dx=step * dx, y_column=y_column)

    if "irregularArrayCoordArray" in index or "irregularTimeCoordArray" in index:
        coord_name = index.get("irregularArrayCoordArray") or index.get("irregularTimeCoordArray")
        if coord_name not in y_datasets:
            raise KeyError(
                f"Index coordinate array {coord_name!r} is not declared in yArrays of source {source['id']!r}"
            )
        x_dataset, x_column = y_datasets[coord_name]
        x0, dx = refs.time_axis_affine(index) if "irregularTimeCoordArray" in index else (0.0, 1.0)
        return largeseries.SourceSpec.irregular(
            hdf5_path, y_dataset, x_dataset, x0=x0, dx=dx, y_column=y_column, x_column=x_column
        )

    if "calendarStep" in index or "irregularCalendarCoordArray" in index:
        raise NotImplementedError(
            "Calendar-bucketed large-series caching (regularCalendar/irregularIndexCalendar) is not supported"
        )

    raise ValueError(f"Unknown Index payload {index!r}")


def get_or_build_cache(
    runtime: "LimelightRuntime", source_id: str, column_name: str, source: dict[str, Any]
) -> largeseries.CacheHandle:
    cache_key = (source_id, column_name)
    with runtime._largeseries_caches_lock:
        handle = runtime._largeseries_caches.get(cache_key)
        if handle is None:
            start_time = runtime.timing.start()
            runtime.hdf_path(source)  # fetches a remote file, if it may
            spec = hdf_source_spec(source, runtime.package, column_name)
            report = runtime.cache_progress
            progress = (
                (lambda done, total: report(source_id, column_name, done, total))
                if report is not None
                else None
            )
            handle = largeseries.build_or_get_cache(
                spec,
                chunk_size=source["largeSeriesChunkSize"],
                timing=runtime.timing,
                progress=progress,
            )
            runtime.timing.log(
                "largeseries.cache.build_or_get",
                start_time,
                [("source", source_id), ("column", column_name)],
            )
            runtime._largeseries_caches[cache_key] = handle
        return handle


def install_timeseries_artist(
    ax: "Axes",
    handle: largeseries.CacheHandle,
    spec: largeseries.SourceSpec,
    *,
    target_buckets: int | None = None,
    color: str | None = None,
    fill_color: str | None = None,
    fill_alpha: float | None = None,
    missing_marker: str | None = None,
    missing_styles: dict[int, MissingStyle] | None = None,
    indicator: bool = True,
    indicator_font: Any | None = None,
    timing: Any | None = None,
) -> largeseries.ZoomSync:
    kwargs: dict[str, Any] = {
        "color": color, "fill_color": fill_color, "fill_alpha": fill_alpha, "indicator": indicator,
        "indicator_font": indicator_font, "missing_styles": missing_styles,
    }
    if missing_marker is not None:
        kwargs["missing_marker"] = missing_marker
    if target_buckets is not None:
        kwargs["target_buckets"] = target_buckets
    if timing is not None:
        kwargs["timing"] = timing
    return largeseries.ZoomSync(ax=ax, handle=handle, spec=spec, **kwargs)


def close_runtime_caches(runtime: "LimelightRuntime") -> None:
    with runtime._largeseries_caches_lock:
        for handle in runtime._largeseries_caches.values():
            largeseries.close_cache(handle)
        runtime._largeseries_caches.clear()
