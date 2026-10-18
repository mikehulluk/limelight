-- Limelight v1 asset types.
--
-- Assets are bundled files a story document references directly, as opposed
-- to Sources, which carry data a FigureSpec plots. In version 1 the only
-- asset kind is an image.

let Core = ./limelight-v1-00-core.dhall

let Datasets = ./limelight-v1-03-datasets.dhall

-- The packaged encoding, not the encoding the author supplied. A build may
-- accept any image format it can read - including SVG - but it rasterises
-- what it cannot ship, so a reader only ever loads one of these two. Keeping
-- the set closed here is what stops a script-bearing format reaching the
-- viewer, which matters because story Markdown is rendered with raw HTML
-- disabled precisely to avoid that.
let ImageFormat = < png | jpeg >

-- How a packaged image was produced from the file the author edited.
--
-- The recorded identity is the source, not the packaged bytes. Image encoders
-- do not promise byte-identical output across versions, and `limelight-cli pdf
-- --rolling-build` names its output from a hash of the manifest, so recording
-- output bytes as the identity would publish a new "version" of an unchanged
-- project every time the encoder was upgraded. The same fields are the key a
-- build cache needs to skip reconverting an unchanged image.
let ImageDerivation =
      { sourceName : Text
      , sourceSha256 : Text
      , maxWidth : Natural
      , quality : Optional Natural
      }

-- How wide an image is drawn.
--
-- `millimetres` is a physical width, which is meaningful because the story has
-- a page: an image declared 80mm wide is 80mm on paper and the same fraction
-- of the column on screen. `percent` is of the text column. Neither is the
-- packaged pixel size, which is a question about quality rather than layout -
-- an image can be stored at 1600px and drawn at 40mm.
-- A record rather than a union of two Doubles: dhall-to-json renders a union
-- alternative as its bare payload, so two alternatives that both carry a
-- number would arrive indistinguishable and the unit would be lost.
let ImageWidthUnit = < millimetres | percent >

let ImageWidth = { value : Double, unit : ImageWidthUnit }

-- One bundled image.
--
-- An image standing alone in its own paragraph is a figure, numbered from
-- the story along with the plots; one written inline within a sentence is
-- not. Neither number is stored here.
let ImageAsset =
      { id : Core.Id
      , path : Text
      , format : ImageFormat
      -- How wide to draw it when the story does not say. A story can override
      -- this per reference with `{width=80mm}` or `{width="60%"}`; absent from
      -- both, an image draws at its natural size, capped to the column.
      , displayWidth : Optional ImageWidth
      , fileFingerprints : List Datasets.SourceFileFingerprint
      , derivedFrom : Optional ImageDerivation
      , provenance : Optional Datasets.SourceProvenance
      , signatures : List Core.DocumentSignature
      }

-- A labelled code block's tokens, in a file of their own so the story's code
-- stays as written. The story's fence names one by id, after the code's
-- language: ```python {tokens=listing}. The file (JSON, at `path`,
-- conventionally under story/code/) is {"tokens": [[line, start, length,
-- label], ...]}: lines and columns count from 0, in Unicode code points,
-- over the block's text, and a label is syntax style names, general to
-- particular (see SyntaxStyle).
let CodeTokens = { id : Core.Id, path : Text }

in  { ImageFormat = ImageFormat
    , ImageWidthUnit = ImageWidthUnit
    , ImageWidth = ImageWidth
    , ImageDerivation = ImageDerivation
    , ImageAsset = ImageAsset
    , CodeTokens = CodeTokens
    }
