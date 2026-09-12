from __future__ import annotations

import sys
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from limelight import (
    Index,
    LimelightProject,
    PageGeometry,
    MapSpec,
    array,
    map_action_add_geojson_layer,
    map_action_add_dataset_scatter,
    map_action_set_limits,
)

COUNTRIES_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"name": "Ireland"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [-10.5, 51.4],
                        [-9.8, 54.4],
                        [-8.2, 55.4],
                        [-6.1, 55.2],
                        [-5.7, 53.1],
                        [-6.3, 51.5],
                        [-8.6, 51.2],
                        [-10.5, 51.4],
                    ]
                ],
            },
        },
        {
            "type": "Feature",
            "properties": {"name": "United Kingdom"},
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [
                    [
                        [
                            [-5.8, 50.0],
                            [-4.2, 51.4],
                            [-2.0, 51.0],
                            [0.2, 51.5],
                            [1.6, 52.9],
                            [1.0, 54.2],
                            [-0.8, 54.9],
                            [-2.9, 54.7],
                            [-4.7, 53.5],
                            [-5.8, 50.0],
                        ]
                    ],
                    [
                        [
                            [-6.3, 54.0],
                            [-5.3, 55.3],
                            [-4.9, 57.2],
                            [-3.0, 58.7],
                            [-1.0, 58.3],
                            [-1.7, 56.4],
                            [-3.0, 55.2],
                            [-4.7, 54.5],
                            [-6.3, 54.0],
                        ]
                    ],
                ],
            },
        },
        {
            "type": "Feature",
            "properties": {"name": "France"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [-5.1, 48.5],
                        [-1.8, 51.1],
                        [2.4, 50.9],
                        [7.6, 48.7],
                        [7.1, 43.5],
                        [3.0, 42.3],
                        [-1.6, 43.2],
                        [-5.1, 48.5],
                    ]
                ],
            },
        },
        {
            "type": "Feature",
            "properties": {"name": "Belgium and Netherlands"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [2.5, 49.5],
                        [6.2, 49.9],
                        [7.2, 53.4],
                        [5.0, 53.8],
                        [3.1, 51.4],
                        [2.5, 49.5],
                    ]
                ],
            },
        },
    ],
}


def build_project() -> LimelightProject:
    project = LimelightProject(
        title="Wiki map skeleton",
        subtitle="Placeholder package for map example development",
        description="Barebones Limelight package for developing map-oriented stories and figures.",
        authors=["Limelight examples"],
        page=PageGeometry.paged(margin_lr_mm=5.0),
        document_version="0.1",
    )

    project.add_csv_dataset(
        id="wiki-places",
        title="Wiki places",
        index=Index.no_index(),
        arrays={
            "name": array(
                ["Greenwich", "British Library", "Edinburgh Castle", "Cardiff Castle"],
                dtype="text",
                label="Place",
                nullable=False,
            ),
            "longitude": array(
                [-0.0015, -0.1275, -3.2008, -3.1817],
                dtype="double",
                label="Longitude",
                unit="deg",
                nullable=False,
            ),
            "latitude": array(
                [51.4769, 51.5299, 55.9486, 51.4816],
                dtype="double",
                label="Latitude",
                unit="deg",
                nullable=False,
            ),
            "article-count": array(
                [12, 24, 18, 9],
                dtype="double",
                label="Article count",
                nullable=False,
            ),
            "country": array(
                ["England", "England", "Scotland", "Wales"],
                dtype="category",
                label="Country",
                nullable=False,
            ),
        },
    )

    project.add_map_figure(
        id="wiki-place-points",
        title="Wiki place coordinates",
        caption="Map of wiki place coordinates, colored by country.",
        map_specs=[
            MapSpec(
                id="wiki-place-map",
                title="Wiki places",
                caption="Geographic point layer for the wiki places dataset.",
                actions=[
                    map_action_add_geojson_layer(
                        id="nearby-countries",
                        path="data/countries.geojson",
                        label="Countries",
                        fill="#eeeeee",
                        stroke="#8a8a8a",
                        alpha=0.85,
                    ),
                    map_action_set_limits(
                        longitude_lower=-6.0,
                        longitude_upper=2.0,
                        latitude_lower=49.0,
                        latitude_upper=57.5,
                    ),
                    map_action_add_dataset_scatter(
                        id="wiki-place-map-points",
                        data="wiki-places",
                        longitude="longitude",
                        latitude="latitude",
                        label="Wiki places",
                        size_by="article-count",
                        color_by="country",
                    ),
                ],
            )
        ],
    )

    project.set_story_markdown(
        f"""
# Wiki Map Skeleton

This package is a placeholder for developing map-oriented Limelight functionality.

{project.story_figure("wiki-place-points")}

## Next Steps

The current figure includes a map spec backed by the wiki places latitude/longitude data. Map tiles, projections, labels, and geographic interaction can be added as the renderer settles.
"""
    )

    return project


def main() -> None:
    output = ROOT / "_build" / "examples" / "example20-wiki.limelight"
    build_project().write_folder(output, overwrite=True)
    (output / "data" / "countries.geojson").write_text(
        json.dumps(COUNTRIES_GEOJSON, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    print(output)


if __name__ == "__main__":
    main()
