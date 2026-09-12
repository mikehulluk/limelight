-- Limelight v1 transform types.
--
-- A Transform is optional, post-load machinery: it maps an already-Indexed
-- IndexedArray/IndexedTable (see the Index type in limelight-v1-02-coredata.dhall
-- - every Source declares its own index shape directly) onto another
-- IndexedArray/IndexedTable, e.g. via
-- resampling, aggregation, or filtering. It does not construct an Index -
-- that's already fixed by the Source's declaration by the time a Transform
-- would run. Version 1 has exactly one variant: `identity`, meaning no
-- transform is applied (the raw array is rendered as-is, via the min/max
-- envelope pyramid). Future variants (resampling, aggregation, etc.) would be
-- added here.
let Transform = < identity >

in  { Transform = Transform }
