"""The BrainBook Video pipeline manifest and its stage skills must resolve."""

from pathlib import Path

from lib.pipeline_loader import load_pipeline

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_manifest_declares_the_renderer_stages() -> None:
    manifest = load_pipeline("brainbook-video")
    assert [stage["name"] for stage in manifest["stages"]] == [
        "idea", "script", "scene_plan", "assets", "edit", "compose", "publish"
    ]
    compose = next(stage for stage in manifest["stages"] if stage["name"] == "compose")
    assert "bb_video_lesson" in compose["required_tools"]


def test_stage_and_meta_skills_exist() -> None:
    manifest = load_pipeline("brainbook-video")
    for skill in manifest["required_skills"]:
        path = REPO_ROOT / "skills" / f"{skill}.md"
        assert path.is_file(), f"missing skill: {skill}"
    for stage in manifest["stages"]:
        skill = stage.get("skill")
        if skill:
            assert (REPO_ROOT / "skills" / f"{skill}.md").is_file(), skill


def test_metadata_titles_the_pipeline() -> None:
    manifest = load_pipeline("brainbook-video")
    assert manifest["metadata"]["title"] == "BrainBook Video"
    assert manifest["metadata"]["renderer"] == "bb_video"
