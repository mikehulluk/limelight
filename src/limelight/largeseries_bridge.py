from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import largeseries
from . import refs
from .reader import LimelightPackage

if TYPE_CHECKING:
    from matplotlib.axes import Axes

    from .app import LimelightRuntime


def _epoch_offset_in_unit(time_origin: Any, unit: str) -> float:
    if time_origin == "relative":
        return 0.0
    return refs._epoch_offset_ns(time_origin) / refs._UNIT_NS[unit]


def hdf_source_spec(source: dict[str, Any], package: LimelightPackage, column_name: str) -> largeseries.SourceSpec:
    hdf5_path = package.package_path(source["path"])
    y_datasets = {entry["schema"]["name"]: entry["dataset"] for entry in source["yArrays"]}
    if column_name not in y_datasets:
        raise KeyError(f"Unknown yArrays column {column_name!r} in source {source['id']!r}")
    y_dataset = y_datasets[column_name]

    index = source["index"]
    if index == "noIndex":
        return largeseries.SourceSpec(hdf5_path=hdf5_path, y_dataset=y_dataset, x0=0.0, dx=1.0)

    if "intOrigin" in index:
        return largeseries.SourceSpec(
            hdf5_path=hdf5_path,
            y_dataset=y_dataset,
            x0=float(index["intOrigin"]),
            dx=float(index["intStep"]),
        )

    if "timeStepNom" in index:
        dx = index["timeStepNom"] / index["timeStepDenom"]
        x0 = _epoch_offset_in_unit(index["timeOrigin"], index["timeStepUnit"])
        return largeseries.SourceSpec(hdf5_path=hdf5_path, y_dataset=y_dataset, x0=x0, dx=dx)

    if "irregularArrayCoordArray" in index or "irregularTimeCoordArray" in index:
        coord_column = index.get("irregularArrayCoordArray") or index.get("irregularTimeCoordArray")
        if coord_column not in y_datasets:
            raise KeyError(
                f"Index coordinate array {coord_column!r} is not declared in yArrays of source {source['id']!r}"
            )
        return largeseries.SourceSpec(
            hdf5_path=hdf5_path,
            y_dataset=y_dataset,
            x_dataset=y_datasets[coord_column],
            irregular_x=True,
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
            spec = hdf_source_spec(source, runtime.package, column_name)
            handle = largeseries.build_or_get_cache(
                spec,
                chunk_size=source["largeSeriesChunkSize"],
                timing=runtime.timing,
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
    timing: Any | None = None,
) -> largeseries.ZoomSync:
    kwargs: dict[str, Any] = {"color": color, "fill_color": fill_color, "fill_alpha": fill_alpha}
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
