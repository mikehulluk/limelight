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

-- One face of a bundled font: a static file, at one weight, upright or italic.
--
-- `path` is where the file sits in the package and `sha256Prefix8` the first
-- eight hex digits of its SHA-256, the same fingerprint a Source carries, so
-- `verify` can tell a swapped file from the one the manifest was written
-- against. `weight` is the CSS weight the file is (400 regular, 700 bold);
-- a document asking for a weight the font has no face for gets the nearest.
-- Variable fonts are refused: the figure renderer cannot select a weight
-- from one, so a bundled font is static instances, one file per face.
let FontFace =
      { path : Text
      , sha256Prefix8 : Text
      , weight : Natural
      , italic : Bool
      }

-- Where a font's files come from.
--
-- `builtin` is a face Limelight ships and can therefore promise on every
-- machine: `ubuntu`, `ubuntu-mono`, `noto-sans` and `dejavu-sans`,
-- `dejavu-sans-mono`, named by the Font's `family` in its display form
-- ("Ubuntu", "Noto Sans"). `system` is whatever the reader's operating
-- system has under that family name, and nothing if it has none, so it
-- belongs first in a stack rather than last. `bundled` carries the files in
-- the package, with the licence they were distributed under beside them;
-- most open licences (OFL, UFL, Apache) allow this and ask for exactly that,
-- while a font that came with an operating system usually may not be
-- redistributed and should be named as `system` instead.
let FontSource =
      < builtin
      | system
      | bundled : { faces : List FontFace, licence : Text }
      >

-- A font the document's text can be set in, referenced by `id` from a
-- TextStyle. `family` is the face's name as its files declare it, which is
-- what the stylesheets and the figure renderer look it up by.
let Font =
      { id : Text
      , family : Text
      , source : FontSource
      }

-- How one kind of text is set.
--
-- `fonts` is a stack of Font ids tried in order: the first is used where it
-- can be, and the rest fill in - for a whole face the reader's machine lacks
-- (a `system` font not installed there) and for single characters the face
-- has no glyph for. The last entry must be a `builtin` or `bundled` font, so
-- the stack always bottoms out in a face that is known to be there and a
-- document never depends on what the reader happens to have installed.
-- `sizePt` is in points: the page is physical, and 10.5pt is 14 CSS pixels
-- on screen. `weight` is a CSS weight, 100 to 900.
let TextStyle =
      { fonts : List Text
      , sizePt : Double
      , weight : Natural
      , italic : Bool
      }

-- Every kind of text in the document, each set in full.
--
-- The prose: `body` (paragraphs, lists, block quotes; `lineHeight` is its
-- line spacing as a multiple of the size), `heading1` to `heading3` (deeper
-- headings use `heading3`), `code` (inline code and code blocks, a monospace
-- face), `caption` (under figure views and story images) and `captionLabel`
-- (the "Figure 3." that opens a caption). The figures: `figureTitle`,
-- `axisLabel`, `tickLabel`, `legend` (entries and title), `annotation`
-- (decorator labels, point and arrow annotations) and `badge` (the
-- large-series corner mark). Tables: `tableHeading` (a table figure's
-- title above it), `tableHeader` (column headers), `tableCell`, `tableNote`
-- (notes and notices under a table). Mathematics is set by MathJax in its
-- own face, at the size of the text around it. The application's own
-- controls - toolbars, the sidebar, dialogs - are not the document's and
-- keep the platform's font.
let Typography =
      { lineHeight : Double
      , body : TextStyle
      , heading1 : TextStyle
      , heading2 : TextStyle
      , heading3 : TextStyle
      , code : TextStyle
      , caption : TextStyle
      , captionLabel : TextStyle
      , figureTitle : TextStyle
      , axisLabel : TextStyle
      , tickLabel : TextStyle
      , legend : TextStyle
      , annotation : TextStyle
      , badge : TextStyle
      , tableHeading : TextStyle
      , tableHeader : TextStyle
      , tableCell : TextStyle
      , tableNote : TextStyle
      }

let Story =
      { documentPath : Text
      , format : StoryFormat
      , page : PageGeometry
      , spacing : StorySpacing
      , typography : Typography
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
    , FontFace = FontFace
    , FontSource = FontSource
    , Font = Font
    , TextStyle = TextStyle
    , Typography = Typography
    , Story = Story
    }
