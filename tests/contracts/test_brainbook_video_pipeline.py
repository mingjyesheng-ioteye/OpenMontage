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
    assert "lesson_video" in compose["required_tools"]
    assert "lesson_video" in compose["tools_available"]


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
    assert manifest["metadata"]["renderer"] == "lesson_video"
    assert manifest["metadata"]["render_composition"] == (
        "remotion-composer/src/LessonVideo.tsx"
    )


def test_publish_stage_can_deliver_to_the_lms() -> None:
    manifest = load_pipeline("brainbook-video")
    publish = next(stage for stage in manifest["stages"] if stage["name"] == "publish")
    assert "bb_exam_media" in publish["tools_available"]
