"""The document's type: the fonts it names and how each kind of text is set.

A manifest carries a `fonts` list and a `story.typography` block; this module
reads both into one model that the three renderers - the story's web view,
the Qt widgets around it, and matplotlib for the figures - all draw from, so
a heading, a caption and a tick label are set by the same rules on screen,
on paper, and on every machine.

Fonts come from three places. A `builtin` font is one Limelight ships and
can promise anywhere; a `bundled` font travels in the package; a `system`
font is whatever the reader's machine has under that name. The first two
are registered with Qt and matplotlib from their files. A system font needs
no registering, but where the operating system has it only as a variable
font, matplotlib cannot pick a weight from it, so the weights the document
asks for are instanced into static files, once, in the cache directory.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from .reader import LimelightError

if TYPE_CHECKING:
    from matplotlib.font_manager import FontProperties

    from .reader import LimelightPackage

logger = logging.getLogger(__name__)

FONTS_DIR = Path(__file__).resolve().parent / "fonts"

# The story's base size: 10.5 points is 14 CSS pixels, so screen and paper
# set the same text, and every default below is a step from it.
DEFAULT_BODY_SIZE_PT = 10.5
DEFAULT_LINE_HEIGHT = 1.5

# The figure renderer's own spacing, in points: from an axis to its label,
# and from the top of the axes to the title. matplotlib's defaults.
DEFAULT_AXIS_LABEL_PAD_PT = 4.0
DEFAULT_FIGURE_TITLE_PAD_PT = 6.0

# Weights are CSS weights, 100 to 900.
MIN_WEIGHT = 100
MAX_WEIGHT = 900
REGULAR = 400
BOLD = 700

# A stack's last font has to be one that is known to be there.
GUARANTEED_SOURCES = ("builtin", "bundled")


@dataclass(frozen=True)
class FontFace:
    """One static file of a font: one weight, upright or italic."""

    path: Path
    weight: int
    italic: bool


@dataclass(frozen=True)
class BuiltinFont:
    """A face Limelight ships, or one matplotlib does that is always installed with it."""

    id: str
    family: str
    faces: tuple[FontFace, ...]
    licence: Path
    monospace: bool = False


def _shipped(id: str, family: str, folder: str, stem: str, licence: str, *, monospace: bool = False) -> BuiltinFont:
    directory = FONTS_DIR / folder
    return BuiltinFont(
        id=id,
        family=family,
        faces=(
            FontFace(directory / f"{stem}-Regular.ttf", REGULAR, False),
            FontFace(directory / f"{stem}-Bold.ttf", BOLD, False),
            FontFace(directory / f"{stem}-Italic.ttf", REGULAR, True),
            FontFace(directory / f"{stem}-BoldItalic.ttf", BOLD, True),
        ),
        licence=directory / licence,
        monospace=monospace,
    )


def _matplotlib_dejavu(id: str, family: str, stem: str, *, monospace: bool = False) -> BuiltinFont:
    # matplotlib ships DejaVu as its own default face, so it is installed
    # wherever Limelight is, without Limelight carrying a second copy.
    import matplotlib

    directory = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    return BuiltinFont(
        id=id,
        family=family,
        faces=(
            FontFace(directory / f"{stem}.ttf", REGULAR, False),
            FontFace(directory / f"{stem}-Bold.ttf", BOLD, False),
            FontFace(directory / f"{stem}-Oblique.ttf", REGULAR, True),
            FontFace(directory / f"{stem}-BoldOblique.ttf", BOLD, True),
        ),
        licence=directory / "LICENSE_DEJAVU",
        monospace=monospace,
    )


_builtin_fonts: dict[str, BuiltinFont] | None = None


def builtin_fonts() -> dict[str, BuiltinFont]:
    """The faces every installation has, keyed by id."""

    global _builtin_fonts
    if _builtin_fonts is None:
        catalogue = [
            _shipped("ubuntu", "Ubuntu", "ubuntu", "Ubuntu", "LICENCE.txt"),
            _shipped("ubuntu-mono", "Ubuntu Mono", "ubuntu-mono", "UbuntuMono", "LICENCE.txt", monospace=True),
            _shipped("noto-sans", "Noto Sans", "noto-sans", "NotoSans", "LICENSE.txt"),
            _matplotlib_dejavu("dejavu-sans", "DejaVu Sans", "DejaVuSans"),
            _matplotlib_dejavu("dejavu-sans-mono", "DejaVu Sans Mono", "DejaVuSansMono", monospace=True),
        ]
        _builtin_fonts = {font.id: font for font in catalogue}
    return _builtin_fonts


def builtin_font_by_family(family: str) -> BuiltinFont | None:
    for font in builtin_fonts().values():
        if font.family == family:
            return font
    return None


@dataclass(frozen=True)
class ResolvedFont:
    """A manifest font with its files found, ready to register."""

    id: str
    family: str
    source: str
    faces: tuple[FontFace, ...]
    licence: Path | None = None

    @property
    def guaranteed(self) -> bool:
        return self.source in GUARANTEED_SOURCES


@dataclass(frozen=True)
class TextStyle:
    """How one kind of text is set: a stack of font ids, a size, a weight, a slant."""

    fonts: tuple[str, ...]
    size_pt: float
    weight: int = REGULAR
    italic: bool = False

    @property
    def size_px(self) -> float:
        """The size in CSS pixels: a point is 4/3 of one."""

        return self.size_pt * 96.0 / 72.0

    def families(self, fonts: Mapping[str, ResolvedFont]) -> list[str]:
        return [fonts[font_id].family for font_id in self.fonts if font_id in fonts]

    def css(self, fonts: Mapping[str, ResolvedFont], *, line_height: float | None = None) -> str:
        """The declarations that set this style, for a stylesheet rule."""

        families = ", ".join(f'"{family}"' for family in self.families(fonts))
        declarations = [
            f"font-family: {families};",
            f"font-size: {self.size_pt:g}pt;",
            f"font-weight: {self.weight};",
            f"font-style: {'italic' if self.italic else 'normal'};",
        ]
        if line_height is not None:
            declarations.append(f"line-height: {line_height:g};")
        return "\n  ".join(declarations)

    def inline_css(self, fonts: Mapping[str, ResolvedFont]) -> str:
        """The same declarations on one line, for a ``style`` attribute."""

        return " ".join(self.css(fonts).split("\n  "))

    def font_properties(self, fonts: Mapping[str, ResolvedFont]) -> "FontProperties":
        """This style as matplotlib draws it."""

        from matplotlib.font_manager import FontProperties

        return FontProperties(
            family=self.families(fonts),
            size=self.size_pt,
            weight=self.weight,
            style="italic" if self.italic else "normal",
        )


def _style(fonts: tuple[str, ...], scale: float, weight: int = REGULAR) -> TextStyle:
    return TextStyle(fonts=fonts, size_pt=round(DEFAULT_BODY_SIZE_PT * scale, 2), weight=weight)


DEFAULT_TEXT_FONTS = ("ubuntu", "noto-sans")
DEFAULT_CODE_FONTS = ("ubuntu-mono", "dejavu-sans-mono")


@dataclass(frozen=True)
class Typography:
    """Every kind of text in the document, each set in full.

    The defaults keep the proportions the stylesheets and matplotlib used
    before a document could say: headings at 1.7, 1.32 and 1.12 of the body,
    captions and table text at 0.86, axis and tick labels, legends and
    annotations at matplotlib's "small" (0.833), the badge at its "x-small"
    (0.694).

    Two of the figures' distances are set here too, in points, since they
    are set with the text they space: ``axis_label_pad_pt`` between an axis
    and its label, ``figure_title_pad_pt`` between the axes and the title.
    """

    line_height: float = DEFAULT_LINE_HEIGHT
    axis_label_pad_pt: float = DEFAULT_AXIS_LABEL_PAD_PT
    figure_title_pad_pt: float = DEFAULT_FIGURE_TITLE_PAD_PT
    body: TextStyle = _style(DEFAULT_TEXT_FONTS, 1.0)
    heading1: TextStyle = _style(DEFAULT_TEXT_FONTS, 1.7, BOLD)
    heading2: TextStyle = _style(DEFAULT_TEXT_FONTS, 1.32, BOLD)
    heading3: TextStyle = _style(DEFAULT_TEXT_FONTS, 1.12, BOLD)
    code: TextStyle = _style(DEFAULT_CODE_FONTS, 0.92)
    caption: TextStyle = _style(DEFAULT_TEXT_FONTS, 0.86)
    caption_label: TextStyle = _style(DEFAULT_TEXT_FONTS, 0.86, BOLD)
    figure_title: TextStyle = _style(DEFAULT_TEXT_FONTS, 1.0, BOLD)
    axis_label: TextStyle = _style(DEFAULT_TEXT_FONTS, 0.833)
    tick_label: TextStyle = _style(DEFAULT_TEXT_FONTS, 0.833)
    legend: TextStyle = _style(DEFAULT_TEXT_FONTS, 0.833)
    annotation: TextStyle = _style(DEFAULT_TEXT_FONTS, 0.833)
    badge: TextStyle = _style(DEFAULT_TEXT_FONTS, 0.694)
    table_heading: TextStyle = _style(DEFAULT_TEXT_FONTS, 0.929, BOLD)
    table_header: TextStyle = _style(DEFAULT_TEXT_FONTS, 0.86, BOLD)
    table_cell: TextStyle = _style(DEFAULT_TEXT_FONTS, 0.86)
    table_note: TextStyle = _style(DEFAULT_TEXT_FONTS, 0.85)

    def styles(self) -> dict[str, TextStyle]:
        """Every style by its manifest name (`figureTitle` for `figure_title`)."""

        return {
            _manifest_name(field.name): getattr(self, field.name)
            for field in fields(self)
            if isinstance(getattr(self, field.name), TextStyle)
        }

    def font_ids(self) -> list[str]:
        """Every font the styles name, in the order they first name them."""

        seen: dict[str, None] = {}
        for style in self.styles().values():
            for font_id in style.fonts:
                seen.setdefault(font_id, None)
        return list(seen)

    def weights_used(self, font_id: str) -> set[tuple[int, bool]]:
        """The (weight, italic) faces a font is asked for."""

        return {
            (style.weight, style.italic)
            for style in self.styles().values()
            if font_id in style.fonts
        }


STYLE_NAMES = tuple(_name for _name in (
    "body", "heading1", "heading2", "heading3", "code", "caption", "captionLabel",
    "figureTitle", "axisLabel", "tickLabel", "legend", "annotation", "badge",
    "tableHeading", "tableHeader", "tableCell", "tableNote",
))


def _manifest_name(field_name: str) -> str:
    head, *rest = field_name.split("_")
    return head + "".join(part.capitalize() for part in rest)


def _field_name(manifest_name: str) -> str:
    out = []
    for character in manifest_name:
        if character.isupper():
            out.append("_" + character.lower())
        else:
            out.append(character)
    return "".join(out)


def text_style_from_manifest(record: dict[str, Any], context: str) -> TextStyle:
    fonts = tuple(str(font_id) for font_id in record["fonts"])
    if not fonts:
        raise LimelightError(f"{context} names no fonts")
    size_pt = float(record["sizePt"])
    if size_pt <= 0:
        raise LimelightError(f"{context} sizePt {size_pt!r} must be positive")
    weight = int(record["weight"])
    if not MIN_WEIGHT <= weight <= MAX_WEIGHT:
        raise LimelightError(f"{context} weight {weight!r} is not between {MIN_WEIGHT} and {MAX_WEIGHT}")
    return TextStyle(fonts=fonts, size_pt=size_pt, weight=weight, italic=bool(record["italic"]))


def typography_from_story(story: dict[str, Any]) -> Typography:
    """The story's typography block, or the defaults for a package written before there was one."""

    block = story.get("typography")
    if block is None:
        return Typography()
    line_height = float(block["lineHeight"])
    if line_height <= 0:
        raise LimelightError(f"Typography lineHeight {line_height!r} must be positive")
    # The pads came after the block did; a document without them has matplotlib's.
    axis_label_pad_pt = float(block.get("axisLabelPadPt", DEFAULT_AXIS_LABEL_PAD_PT))
    figure_title_pad_pt = float(block.get("figureTitlePadPt", DEFAULT_FIGURE_TITLE_PAD_PT))
    styles = {
        _field_name(name): text_style_from_manifest(block[name], f"Typography {name}")
        for name in STYLE_NAMES
    }
    return Typography(
        line_height=line_height,
        axis_label_pad_pt=axis_label_pad_pt,
        figure_title_pad_pt=figure_title_pad_pt,
        **styles,
    )


def default_manifest_fonts() -> list[dict[str, Any]]:
    """The `fonts` list a package written before there was one is read as having."""

    return [
        {"id": font_id, "family": builtin_fonts()[font_id].family, "source": "builtin"}
        for font_id in (*DEFAULT_TEXT_FONTS, *DEFAULT_CODE_FONTS)
    ]


def manifest_fonts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    fonts = manifest.get("fonts")
    return default_manifest_fonts() if fonts is None else list(fonts)


def font_source_kind(font: dict[str, Any]) -> str:
    """`builtin`, `system` or `bundled`: the union alternative the source is."""

    source = font["source"]
    if isinstance(source, str):
        return source
    return "bundled"


def resolve_fonts(manifest: dict[str, Any], package: "LimelightPackage | None") -> dict[str, ResolvedFont]:
    """Every font the manifest names, with its files located; raises on one that cannot be."""

    resolved: dict[str, ResolvedFont] = {}
    for font in manifest_fonts(manifest):
        font_id = font["id"]
        family = font["family"]
        kind = font_source_kind(font)
        if kind == "builtin":
            builtin = builtin_font_by_family(family)
            if builtin is None:
                known = ", ".join(sorted(entry.family for entry in builtin_fonts().values()))
                raise LimelightError(f"Font {font_id!r}: no builtin font is called {family!r}; there are {known}")
            resolved[font_id] = ResolvedFont(font_id, family, "builtin", builtin.faces, builtin.licence)
        elif kind == "system":
            resolved[font_id] = ResolvedFont(font_id, family, "system", ())
        else:
            payload = font["source"]
            if package is None:
                faces = tuple(
                    FontFace(Path(face["path"]), int(face["weight"]), bool(face["italic"]))
                    for face in payload["faces"]
                )
                resolved[font_id] = ResolvedFont(font_id, family, "bundled", faces, Path(payload["licence"]))
                continue
            faces = tuple(
                FontFace(package.package_path(face["path"]), int(face["weight"]), bool(face["italic"]))
                for face in payload["faces"]
            )
            resolved[font_id] = ResolvedFont(
                font_id, family, "bundled", faces, package.package_path(payload["licence"])
            )
    return resolved


# --- Checks ---------------------------------------------------------------


def validate_fonts_and_typography(manifest: dict[str, Any]) -> None:
    """What can be checked from the manifest alone; `verify_fonts` checks the files."""

    fonts = manifest_fonts(manifest)
    seen: set[str] = set()
    for font in fonts:
        font_id = font["id"]
        if font_id in seen:
            raise LimelightError(f"Font {font_id!r} is declared twice")
        seen.add(font_id)
        kind = font_source_kind(font)
        if kind == "builtin" and builtin_font_by_family(font["family"]) is None:
            known = ", ".join(sorted(entry.family for entry in builtin_fonts().values()))
            raise LimelightError(f"Font {font_id!r}: no builtin font is called {font['family']!r}; there are {known}")
        if kind == "bundled":
            payload = font["source"]
            if not payload["faces"]:
                raise LimelightError(f"Font {font_id!r} bundles no faces")
            for face in payload["faces"]:
                weight = int(face["weight"])
                if not MIN_WEIGHT <= weight <= MAX_WEIGHT:
                    raise LimelightError(
                        f"Font {font_id!r} face {face['path']!r} weight {weight!r} is not between {MIN_WEIGHT} and {MAX_WEIGHT}"
                    )
    kinds = {font["id"]: font_source_kind(font) for font in fonts}

    typography = typography_from_story(manifest["story"])
    for name, style in typography.styles().items():
        for font_id in style.fonts:
            if font_id not in kinds:
                raise LimelightError(f"Typography {name} names unknown font {font_id!r}")
        last = style.fonts[-1]
        if kinds[last] not in GUARANTEED_SOURCES:
            raise LimelightError(
                f"Typography {name} ends its font stack with {last!r}, a system font; "
                "the last font must be builtin or bundled, so the stack always has a face"
            )


def verify_fonts(package: "LimelightPackage", manifest: dict[str, Any]) -> None:
    """Check a package's bundled fonts against their files: present, fingerprinted, static, licensed."""

    for font in manifest_fonts(manifest):
        if font_source_kind(font) != "bundled":
            continue
        font_id = font["id"]
        payload = font["source"]
        licence = package.package_path(payload["licence"])
        if not licence.is_file():
            raise LimelightError(f"Font {font_id!r}: licence file {payload['licence']!r} is not in the package")
        for face in payload["faces"]:
            path = package.package_path(face["path"])
            if not path.is_file():
                raise LimelightError(f"Font {font_id!r}: face {face['path']!r} is not in the package")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:8]
            if digest != face["sha256Prefix8"]:
                raise LimelightError(
                    f"Font {font_id!r}: face {face['path']!r} has fingerprint {digest}, "
                    f"not the {face['sha256Prefix8']} the manifest records"
                )
            if is_variable_font(path):
                raise LimelightError(
                    f"Font {font_id!r}: face {face['path']!r} is a variable font; "
                    "bundle static instances, one file per weight"
                )


def is_variable_font(path: Path) -> bool:
    from fontTools.ttLib import TTFont

    with TTFont(path, lazy=True) as font:
        return "fvar" in font


# --- Registration ---------------------------------------------------------

_registered_with_matplotlib: set[Path] = set()
_registered_with_qt: set[Path] = set()


def register_fonts_with_matplotlib(fonts: Mapping[str, ResolvedFont], typography: Typography) -> None:
    """Make every font's faces findable by matplotlib, instancing system variable fonts."""

    from matplotlib import font_manager

    for font in fonts.values():
        faces: Iterable[FontFace]
        if font.source == "system":
            faces = _system_font_static_faces(font, typography.weights_used(font.id))
        else:
            faces = font.faces
        for face in faces:
            if face.path in _registered_with_matplotlib:
                continue
            if not face.path.is_file():
                logger.warning("Font %r: face %s is missing", font.id, face.path)
                continue
            font_manager.fontManager.addfont(str(face.path))
            # addfont appends, and on a tie - the reader's machine has the
            # same family installed - findfont takes the earliest entry, so
            # the document's own file goes to the front to be the one used.
            entries = font_manager.fontManager.ttflist
            entries.insert(0, entries.pop())
            _registered_with_matplotlib.add(face.path)


def register_fonts_with_qt(fonts: Mapping[str, ResolvedFont]) -> None:
    """Load every builtin and bundled face into the application's font database."""

    from PySide6.QtGui import QFontDatabase

    for font in fonts.values():
        for face in font.faces:
            if face.path in _registered_with_qt or not face.path.is_file():
                continue
            if QFontDatabase.addApplicationFont(str(face.path)) < 0:
                logger.warning("Font %r: Qt could not load %s", font.id, face.path)
            _registered_with_qt.add(face.path)


def font_face_css(fonts: Mapping[str, ResolvedFont]) -> str:
    """`@font-face` rules for every face that comes from a file, for the story's stylesheet.

    The story is loaded from a file, so a file URL beside it is reachable. A
    system font has no rule: the browser knows it, or it does not.
    """

    rules = []
    for font in fonts.values():
        for face in font.faces:
            if not face.path.is_file():
                continue
            rules.append(
                "@font-face {\n"
                f'  font-family: "{font.family}";\n'
                f'  src: url("{face.path.resolve().as_uri()}");\n'
                f"  font-weight: {face.weight};\n"
                f"  font-style: {'italic' if face.italic else 'normal'};\n"
                "}"
            )
    return "\n".join(rules)


def _system_font_static_faces(font: ResolvedFont, weights: set[tuple[int, bool]]) -> list[FontFace]:
    """Static files for the faces a system font is asked for, instanced from a variable one.

    matplotlib finds a system font's file itself; this only steps in when
    that file is a variable font, which matplotlib draws at its default
    weight whatever is asked. Each instance is made once and kept in the
    cache directory, keyed on the file it came from.
    """

    from matplotlib import font_manager

    faces: list[FontFace] = []
    for weight, italic in sorted(weights):
        properties = font_manager.FontProperties(
            family=font.family, weight=weight, style="italic" if italic else "normal"
        )
        try:
            found = Path(font_manager.findfont(properties, fallback_to_default=False))
        except ValueError:
            continue
        if not is_variable_font(found):
            continue
        faces.append(FontFace(_instance_variable_font(found, weight), weight, italic))
    return faces


def _instance_variable_font(source: Path, weight: int) -> Path:
    from .largeseries.cachedir import resolve_cache_dir

    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    target = resolve_cache_dir() / "fonts" / f"{digest}-w{weight}.ttf"
    if target.is_file():
        return target
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    logger.info("Instancing %s at weight %d into %s", source.name, weight, target)
    with TTFont(source) as variable:
        axes = {axis.axisTag: axis for axis in variable["fvar"].axes}
        location: dict[str, float] = {}
        if "wght" in axes:
            location["wght"] = float(min(max(weight, axes["wght"].minValue), axes["wght"].maxValue))
        static = instancer.instantiateVariableFont(variable, location)
        target.parent.mkdir(parents=True, exist_ok=True)
        static.save(target)
    return target


# --- Figures --------------------------------------------------------------


@dataclass(frozen=True)
class FigureText:
    """The typography as matplotlib takes it: a FontProperties per kind of figure text.

    Handed to every drawing function rather than set through rcParams, which
    are process-wide: two documents open in tabs, or a figure drawn on a
    worker while another is drawn on the main thread, would otherwise
    borrow each other's type.
    """

    title: "FontProperties"
    axis_label: "FontProperties"
    tick_label: "FontProperties"
    legend: "FontProperties"
    annotation: "FontProperties"
    badge: "FontProperties"
    body: "FontProperties"
    tick_families: tuple[str, ...]
    tick_size_pt: float
    axis_label_pad_pt: float
    title_pad_pt: float

    @classmethod
    def from_typography(cls, typography: Typography, fonts: Mapping[str, ResolvedFont]) -> "FigureText":
        return cls(
            axis_label_pad_pt=typography.axis_label_pad_pt,
            title_pad_pt=typography.figure_title_pad_pt,
            title=typography.figure_title.font_properties(fonts),
            axis_label=typography.axis_label.font_properties(fonts),
            tick_label=typography.tick_label.font_properties(fonts),
            legend=typography.legend.font_properties(fonts),
            annotation=typography.annotation.font_properties(fonts),
            badge=typography.badge.font_properties(fonts),
            body=typography.body.font_properties(fonts),
            tick_families=tuple(typography.tick_label.families(fonts)),
            tick_size_pt=typography.tick_label.size_pt,
        )

    def style_ticks(self, axes: Any) -> None:
        """Tick labels are made afresh at every draw, so they are set on the axis, not on each label."""

        axes.tick_params(axis="both", which="both", labelsize=self.tick_size_pt, labelfontfamily=list(self.tick_families))

    def add_legend(self, axes: Any, *args: Any, **kwargs: Any) -> Any:
        return axes.legend(*args, prop=self.legend, title_fontproperties=self.legend, **kwargs)

    def set_title(self, axes: Any, title: str) -> None:
        axes.set_title(title, fontproperties=self.title, pad=self.title_pad_pt)

    def set_xlabel(self, axes: Any, label: str) -> None:
        axes.set_xlabel(label, fontproperties=self.axis_label, labelpad=self.axis_label_pad_pt)

    def set_ylabel(self, axes: Any, label: str) -> None:
        axes.set_ylabel(label, fontproperties=self.axis_label, labelpad=self.axis_label_pad_pt)


# --- What is really in use -----------------------------------------------


@dataclass(frozen=True)
class FaceInUse:
    """The face a renderer really sets a style in, once its stack has been looked up on this machine.

    ``family`` is the one the lookup landed on; ``substitute`` says it is
    not one the style asked for, so the whole stack was missing and the
    renderer fell back to its own default. ``path`` is the file, where the
    renderer knows it.
    """

    family: str
    substitute: bool = False
    path: Path | None = None

    def describe(self) -> str:
        text = self.family
        if self.path is not None:
            text += f" ({self.path.name})"
        if self.substitute:
            text += " - substitute"
        return text


def figure_face_in_use(style: TextStyle, fonts: Mapping[str, ResolvedFont]) -> FaceInUse:
    """The file matplotlib draws this style from, after `register_fonts_with_matplotlib`."""

    from matplotlib import font_manager

    properties = style.font_properties(fonts)
    substitute = False
    try:
        path = Path(font_manager.findfont(properties, fallback_to_default=False))
    except ValueError:
        path = Path(font_manager.findfont(properties))
        substitute = True
    family = next(
        (entry.name for entry in font_manager.fontManager.ttflist if Path(entry.fname) == path),
        path.stem,
    )
    return FaceInUse(family=family, substitute=substitute, path=path)


def figure_font_file(font: ResolvedFont) -> Path | None:
    """The file matplotlib has for a font's regular face, or None when it has no such family."""

    from matplotlib import font_manager

    try:
        return Path(font_manager.findfont(font_manager.FontProperties(family=font.family), fallback_to_default=False))
    except ValueError:
        return None
