"""The document's type: fonts and typography, from the writer through verify to the renderers."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from limelight.app import LimelightRuntime, story_typography_css
from limelight.cli import main as cli_main
from limelight.reader import LimelightError, open_limelight
from limelight.semantic import validate_manifest_semantics
from limelight.story_markdown import figure_caption_markup
from limelight.typography import (
    BOLD,
    FONTS_DIR,
    FigureText,
    TextStyle,
    Typography,
    builtin_fonts,
    default_manifest_fonts,
    register_fonts_with_matplotlib,
    resolve_fonts,
    typography_from_story,
    validate_fonts_and_typography,
    verify_fonts,
)
from limelight.writer import LimelightProject

UBUNTU = FONTS_DIR / "ubuntu"


def _project(**kwargs: object) -> LimelightProject:
    project = LimelightProject(title="Type", authors=["Test"], **kwargs)
    project.set_story_markdown("# Heading\n\nBody text.\n", base_dir=Path("."))
    return project


def _written(tmp_path: Path, project: LimelightProject) -> Path:
    return project.write_folder(tmp_path / "type.limelight")


# --- The shipped fonts ----------------------------------------------------


def test_every_builtin_font_ships_its_four_static_faces_and_a_licence() -> None:
    for font in builtin_fonts().values():
        assert font.licence.is_file(), font.id
        assert {(face.weight, face.italic) for face in font.faces} == {
            (400, False), (700, False), (400, True), (700, True)
        }
        for face in font.faces:
            assert face.path.is_file(), face.path


def test_the_defaults_name_only_builtin_fonts_and_end_every_stack_with_one() -> None:
    typography = Typography()
    for name, style in typography.styles().items():
        assert style.fonts[-1] in builtin_fonts(), name
    assert [font["id"] for font in default_manifest_fonts()] == typography.font_ids()


# --- Writer to reader -----------------------------------------------------


def test_a_project_that_says_nothing_writes_the_full_block_and_it_reads_back(tmp_path: Path) -> None:
    package = _written(tmp_path, _project())
    with open_limelight(package) as opened:
        manifest = opened.manifest_json()
        validate_manifest_semantics(manifest)
        verify_fonts(opened, manifest)
        assert [font["id"] for font in manifest["fonts"]] == ["ubuntu", "noto-sans", "ubuntu-mono", "dejavu-sans-mono"]
        assert typography_from_story(manifest["story"]) == Typography()


def test_a_custom_typography_round_trips(tmp_path: Path) -> None:
    typography = replace(
        Typography(),
        line_height=1.35,
        axis_label_pad_pt=9.0,
        figure_title_pad_pt=12.0,
        heading1=TextStyle(fonts=("noto-sans",), size_pt=24.0, weight=800, italic=True),
        code=TextStyle(fonts=("dejavu-sans-mono",), size_pt=9.0),
    )
    package = _written(tmp_path, _project(typography=typography))
    with open_limelight(package) as opened:
        manifest = opened.manifest_json()
        validate_manifest_semantics(manifest)
        read = typography_from_story(manifest["story"])
    assert read == typography
    # Only the fonts the typography names are declared.
    assert {font["id"] for font in manifest["fonts"]} == set(typography.font_ids())


def test_a_package_written_before_typography_existed_reads_as_the_defaults() -> None:
    story = {"documentPath": "story/index.md", "format": "markdown"}
    assert typography_from_story(story) == Typography()
    fonts = resolve_fonts({"story": story}, None)
    assert set(fonts) == {"ubuntu", "noto-sans", "ubuntu-mono", "dejavu-sans-mono"}
    assert all(font.source == "builtin" for font in fonts.values())


def test_a_typography_block_written_before_the_pads_reads_them_as_matplotlib_has_them(tmp_path: Path) -> None:
    package = _written(tmp_path, _project())
    with open_limelight(package) as opened:
        block = opened.manifest_json()["story"]["typography"]
    del block["axisLabelPadPt"], block["figureTitlePadPt"]
    read = typography_from_story({"typography": block})
    assert (read.axis_label_pad_pt, read.figure_title_pad_pt) == (4.0, 6.0)


# --- System and bundled fonts ---------------------------------------------


def test_a_system_font_goes_first_in_a_stack_never_last(tmp_path: Path) -> None:
    project = _project()
    project.add_system_font("segoe", "Segoe UI")
    project.typography = replace(project.typography, body=TextStyle(fonts=("segoe", "ubuntu"), size_pt=10.5))
    package = _written(tmp_path, project)
    with open_limelight(package) as opened:
        manifest = opened.manifest_json()
        validate_manifest_semantics(manifest)
        fonts = resolve_fonts(manifest, opened)
    assert fonts["segoe"].source == "system" and fonts["segoe"].faces == ()

    manifest["story"]["typography"]["body"]["fonts"] = ["ubuntu", "segoe"]
    with pytest.raises(LimelightError, match="ends its font stack with 'segoe'"):
        validate_fonts_and_typography(manifest)


def test_a_bundled_font_is_copied_in_with_its_licence_and_fingerprinted(tmp_path: Path) -> None:
    project = _project()
    project.add_bundled_font(
        "my-ubuntu",
        "Ubuntu",
        [(UBUNTU / "Ubuntu-Regular.ttf", 400, False), (UBUNTU / "Ubuntu-Bold.ttf", 700, False)],
        licence=UBUNTU / "LICENCE.txt",
    )
    project.typography = replace(project.typography, body=TextStyle(fonts=("my-ubuntu",), size_pt=11.0))
    package = _written(tmp_path, project)
    assert (package / "assets" / "fonts" / "my-ubuntu" / "Ubuntu-Bold.ttf").is_file()
    assert (package / "assets" / "fonts" / "my-ubuntu" / "LICENCE.txt").is_file()

    with open_limelight(package) as opened:
        manifest = opened.manifest_json()
        validate_manifest_semantics(manifest)
        verify_fonts(opened, manifest)
        fonts = resolve_fonts(manifest, opened)
        assert fonts["my-ubuntu"].source == "bundled"
        assert [face.weight for face in fonts["my-ubuntu"].faces] == [400, 700]

        # A swapped file is caught by its fingerprint.
        shutil.copyfile(UBUNTU / "Ubuntu-Italic.ttf", package / "assets" / "fonts" / "my-ubuntu" / "Ubuntu-Bold.ttf")
        with pytest.raises(LimelightError, match="fingerprint"):
            verify_fonts(opened, manifest)


def test_the_writer_refuses_a_variable_font_and_a_missing_licence(tmp_path: Path) -> None:
    project = _project()
    variable = Path("/usr/share/fonts/truetype/ubuntu/Ubuntu[wdth,wght].ttf")
    if variable.is_file():
        with pytest.raises(ValueError, match="variable font"):
            project.add_bundled_font("v", "Ubuntu", [(variable, 400, False)], licence=UBUNTU / "LICENCE.txt")
    with pytest.raises(FileNotFoundError):
        project.add_bundled_font(
            "u", "Ubuntu", [(UBUNTU / "Ubuntu-Regular.ttf", 400, False)], licence=tmp_path / "none.txt"
        )


def test_verify_refuses_an_unknown_font_and_an_unknown_builtin(tmp_path: Path) -> None:
    package = _written(tmp_path, _project())
    with open_limelight(package) as opened:
        manifest = opened.manifest_json()
    manifest["story"]["typography"]["legend"]["fonts"] = ["comic-sans", "ubuntu"]
    with pytest.raises(LimelightError, match="unknown font 'comic-sans'"):
        validate_fonts_and_typography(manifest)

    manifest["story"]["typography"]["legend"]["fonts"] = ["ubuntu"]
    manifest["fonts"][0]["family"] = "Comic Sans"
    with pytest.raises(LimelightError, match="no builtin font is called 'Comic Sans'"):
        validate_fonts_and_typography(manifest)


def test_ll_verify_reports_a_bad_bundled_font(tmp_path: Path, capsys) -> None:
    project = _project()
    project.add_bundled_font(
        "u", "Ubuntu", [(UBUNTU / "Ubuntu-Regular.ttf", 400, False)], licence=UBUNTU / "LICENCE.txt"
    )
    package = _written(tmp_path, project)
    (package / "assets" / "fonts" / "u" / "LICENCE.txt").unlink()
    assert cli_main(["verify", str(package)]) == 1
    assert "licence file" in capsys.readouterr().out


# --- What the renderers are given -----------------------------------------


def test_the_stylesheet_names_every_face_and_sets_every_kind_of_text() -> None:
    typography = Typography()
    fonts = resolve_fonts({"story": {}}, None)
    css = story_typography_css(typography, fonts)
    assert css.count("@font-face") == 16  # four faces of four fonts
    assert 'font-family: "Ubuntu";' in css
    assert "Ubuntu-BoldItalic.ttf" in css
    assert 'body {\n  font-family: "Ubuntu", "Noto Sans";\n  font-size: 10.5pt;\n  font-weight: 400;' in css
    assert "line-height: 1.5;" in css
    assert 'code, pre {\n  font-family: "Ubuntu Mono", "DejaVu Sans Mono";' in css
    assert ".limelight-caption-label {" in css and "font-weight: 700;" in css


def test_the_caption_label_is_its_own_style() -> None:
    assert figure_caption_markup(3, "a < b") == '<span class="limelight-caption-label">Figure 3.</span> a &lt; b'
    inline = figure_caption_markup(3, "x", label_style='font-weight: 700; font-family: "Ubuntu";')
    assert 'style="font-weight: 700; font-family: &quot;Ubuntu&quot;;"' in inline


def test_figure_text_sets_labels_and_titles_the_pads_apart() -> None:
    from matplotlib.figure import Figure

    fonts = resolve_fonts({"story": {}}, None)
    text = FigureText.from_typography(replace(Typography(), axis_label_pad_pt=11.0, figure_title_pad_pt=13.0), fonts)
    axes = Figure().add_subplot(111)
    text.set_title(axes, "T")
    text.set_xlabel(axes, "x")
    text.set_ylabel(axes, "y")
    assert axes.xaxis.labelpad == 11.0 and axes.yaxis.labelpad == 11.0
    assert axes.title.get_fontproperties().get_weight() == BOLD
    # matplotlib keeps the title pad only as an offset in inches.
    assert axes.titleOffsetTrans._t[1] * 72 == pytest.approx(13.0)  # noqa: SLF001


def test_figure_text_carries_the_styles_to_matplotlib() -> None:
    typography = replace(Typography(), tick_label=TextStyle(fonts=("noto-sans",), size_pt=7.0, weight=BOLD))
    fonts = resolve_fonts({"story": {}}, None)
    text = FigureText.from_typography(typography, fonts)
    assert text.tick_label.get_size() == 7.0
    assert text.tick_label.get_weight() == BOLD
    assert text.tick_families == ("Noto Sans",)
    assert text.title.get_weight() == BOLD and text.title.get_family() == ["Ubuntu", "Noto Sans"]


def test_matplotlib_finds_the_shipped_faces_at_their_weights() -> None:
    from matplotlib import font_manager

    fonts = resolve_fonts({"story": {}}, None)
    register_fonts_with_matplotlib(fonts, Typography())
    bold = font_manager.findfont(font_manager.FontProperties(family="Ubuntu", weight=700), fallback_to_default=False)
    assert Path(bold).name == "Ubuntu-Bold.ttf"
    italic = font_manager.findfont(
        font_manager.FontProperties(family="Noto Sans", style="italic"), fallback_to_default=False
    )
    assert Path(italic).name == "NotoSans-Italic.ttf"


def test_the_runtime_reads_typography_and_resolves_fonts(tmp_path: Path) -> None:
    package = _written(tmp_path, _project())
    with open_limelight(package) as opened:
        runtime = LimelightRuntime(opened, opened.manifest_json())
        assert runtime.typography == Typography()
        assert runtime.fonts["ubuntu"].faces[0].path.is_file()
        assert "Ubuntu" in runtime.figure_view_caption_markup(next(iter(runtime.figure_specs)), index=1) if runtime.figure_specs else True


def test_the_face_in_use_is_the_first_of_the_stack_this_machine_has() -> None:
    from limelight.typography import ResolvedFont, figure_face_in_use, figure_font_file

    fonts = resolve_fonts({"story": {}}, None)
    fonts["absent"] = ResolvedFont("absent", "No Such Family", "system", ())
    register_fonts_with_matplotlib(fonts, Typography())

    # The system font is not here, so the stack falls through to the builtin behind it.
    face = figure_face_in_use(TextStyle(fonts=("absent", "ubuntu"), size_pt=10.5, weight=BOLD), fonts)
    assert face.family == "Ubuntu" and face.path is not None and face.path.name == "Ubuntu-Bold.ttf"
    assert not face.substitute
    assert figure_font_file(fonts["absent"]) is None
    assert figure_font_file(fonts["ubuntu"]).name == "Ubuntu-Regular.ttf"

    # A stack with nothing on this machine is drawn in matplotlib's own default, and says so.
    face = figure_face_in_use(TextStyle(fonts=("absent",), size_pt=10.5), fonts)
    assert face.substitute and face.family not in ("No Such Family",)
    assert face.describe().endswith("- substitute")
