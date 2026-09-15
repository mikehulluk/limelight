-- Limelight v1 document types.
--
-- These describe project metadata and the user-facing story tree.

let Core = ./limelight-v1-00-core.dhall

let Project =
      { title : Core.DisplayText
      , subtitle : Optional Core.DisplayText
      , description : Optional Core.DisplayText
      , authors : List Text
      , created : Optional Text
      , updated : Optional Text
      , documentVersion : Optional Core.Version
      }

-- Metadata the author attaches to the package: whatever facts should travel
-- with it - a publish date, a run number, the pipeline version that made it.
-- The value is written as text and read as the type says; `verify` checks
-- that it does, so `LL meta` can hand out typed JSON. A `datetime` is ISO
-- 8601; a `version` is semantic versioning (MAJOR.MINOR.PATCH[-pre][+build]).
let MetadataType = < int | float | datetime | string | version >

let Metadata = { name : Text, type : MetadataType, value : Text }

let StoryFormat = < markdown >

-- How wide the page is.
--
-- `millimetres` is a physical width the document keeps whatever it is shown
-- on, so a story reads with the same line length on screen as on paper.
-- `viewport` lets the document fill whatever it is given, which suits a story
-- meant to be explored in a window rather than read straight through.
let PageWidth = < millimetres : Double | viewport >

-- How tall the page is.
--
-- `millimetres` gives a paged document: content is broken across pages of that
-- height, the way A4 does. `continuous` gives one page that runs as long as
-- the content, which is what a story scrolled on screen already is and what a
-- reader following a long argument usually wants from its PDF too.
let PageHeight = < millimetres : Double | continuous >

-- The page a story is laid out on.
--
-- `width` and `height` are the whole page, with the margins inside them, so
-- A4 is 210 by 297 with a `marginLR` of 15 and a text column of 180mm.
-- `marginLR` is the space left and right of the column and `marginTB` the
-- space above and below it, both in millimetres. A viewport width still
-- honours `marginLR`, as padding either side of the column, and a continuous
-- height still honours `marginTB`, at the top and foot of the one long page.
let PageGeometry =
      { width : PageWidth
      , height : PageHeight
      , marginLR : Double
      , marginTB : Double
      }

-- The vertical rhythm of the column: how far apart its blocks sit.
--
-- All in millimetres, like the page, so a story keeps the same spacing on
-- screen as on paper and zooming scales it with everything else. Each gap is
-- the whole distance between the two blocks it separates, not something added
-- to a neighbour's gap:
--
-- `blockGap` separates consecutive blocks of prose - paragraphs, lists, code,
-- display maths. `figureGap` sits above and below a figure, whether a figure
-- view or a story image. `headingGapBefore` and `headingGapAfter` sit either
-- side of a heading, so a section can stand off from the prose before it and
-- hold its first paragraph close.
let StorySpacing =
      { blockGap : Double
      , figureGap : Double
      , headingGapBefore : Double
      , headingGapAfter : Double
      }

let Story =
      { documentPath : Text
      , format : StoryFormat
      , page : PageGeometry
      , spacing : StorySpacing
      , signatures : List Core.DocumentSignature
      }

in  { Project = Project
    , MetadataType = MetadataType
    , Metadata = Metadata
    , StoryFormat = StoryFormat
    , PageWidth = PageWidth
    , PageHeight = PageHeight
    , PageGeometry = PageGeometry
    , StorySpacing = StorySpacing
    , Story = Story
    }
