from __future__ import annotations

import numpy as np

from limelight.largeseries.pyramid import (
    LevelZeroBuilder,
    cascade_levels,
    reduce_block_to_buckets,
)


def test_reduce_block_exact_multiple() -> None:
    y = np.array([3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0], dtype=np.float64)
    y_min, y_max, x_min, x_max = reduce_block_to_buckets(y, None, chunk_size=4)

    assert x_min is None and x_max is None
    np.testing.assert_array_equal(y_min, [1.0, 2.0])
    np.testing.assert_array_equal(y_max, [4.0, 9.0])


def test_reduce_block_partial_last_bucket_is_kept() -> None:
    y = np.arange(17, dtype=np.float64)
    y_min, y_max, _, _ = reduce_block_to_buckets(y, None, chunk_size=4)

    assert y_min.shape[0] == 5
    np.testing.assert_array_equal(y_min, [0.0, 4.0, 8.0, 12.0, 16.0])
    np.testing.assert_array_equal(y_max, [3.0, 7.0, 11.0, 15.0, 16.0])


def test_level_zero_builder_carry_across_block_boundaries() -> None:
    y = np.arange(18, dtype=np.float64)

    one_shot = LevelZeroBuilder(chunk_size=4, irregular_x=False)
    one_shot.feed(y)
    expected = one_shot.finish()

    streamed = LevelZeroBuilder(chunk_size=4, irregular_x=False)
    streamed.feed(y[:5])
    streamed.feed(y[5:12])
    streamed.feed(y[12:18])
    actual = streamed.finish()

    np.testing.assert_array_equal(actual.y_min, expected.y_min)
    np.testing.assert_array_equal(actual.y_max, expected.y_max)


def test_level_zero_builder_irregular_x() -> None:
    y = np.array([5.0, 2.0, 8.0, 1.0, 9.0], dtype=np.float64)
    x = np.array([0.0, 1.5, 2.0, 5.0, 5.5], dtype=np.float64)

    builder = LevelZeroBuilder(chunk_size=2, irregular_x=True)
    builder.feed(y[:3], x[:3])
    builder.feed(y[3:], x[3:])
    level0 = builder.finish()

    np.testing.assert_array_equal(level0.y_min, [2.0, 1.0, 9.0])
    np.testing.assert_array_equal(level0.y_max, [5.0, 8.0, 9.0])
    np.testing.assert_array_equal(level0.x_min, [0.0, 2.0, 5.5])
    np.testing.assert_array_equal(level0.x_max, [1.5, 5.0, 5.5])


def test_cascade_levels_odd_bucket_count_self_pairs_trailing_bucket() -> None:
    builder = LevelZeroBuilder(chunk_size=1, irregular_x=False)
    builder.feed(np.array([1.0, 2.0, 3.0, 4.0, 5.0], dtype=np.float64))
    level0 = builder.finish()
    assert level0.y_min.shape[0] == 5

    levels = cascade_levels(level0, min_level_buckets=0)
    level1 = levels[1]
    assert level1.y_min.shape[0] == 3
    np.testing.assert_array_equal(level1.y_min, [1.0, 3.0, 5.0])
    np.testing.assert_array_equal(level1.y_max, [2.0, 4.0, 5.0])


def test_cascade_levels_stops_at_min_level_buckets() -> None:
    builder = LevelZeroBuilder(chunk_size=1, irregular_x=False)
    builder.feed(np.arange(100, dtype=np.float64))
    level0 = builder.finish()

    levels = cascade_levels(level0, min_level_buckets=8)
    assert levels[0].y_min.shape[0] == 100
    assert levels[-1].y_min.shape[0] <= 8
    for level in levels[:-1]:
        assert level.y_min.shape[0] > 8


def test_cascade_top_level_matches_global_min_max() -> None:
    rng = np.random.default_rng(42)
    values = rng.normal(size=1000)

    builder = LevelZeroBuilder(chunk_size=4, irregular_x=False)
    builder.feed(values)
    level0 = builder.finish()

    levels = cascade_levels(level0, min_level_buckets=1)
    top = levels[-1]

    assert top.y_min.min() == values.min()
    assert top.y_max.max() == values.max()
