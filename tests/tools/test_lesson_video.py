"""The native lesson renderer's timeline and caption contract."""

from pathlib import Path

from tools.video.lesson_video import (
    LessonVideo,
    _course_context,
    _estimated_word_timings,
    _normalize_slides,
    _provider_word_timings,
)


def test_estimated_timings_span_the_audio_and_break_on_sentences() -> None:
    words = _estimated_word_timings("First point. Then a longer second point.", 4000)
    assert [word["word"] for word in words] == [
        "First", "point.", "Then", "a", "longer", "second", "point."
    ]
    assert words[0]["startMs"] == 0
    assert words[-1]["endMs"] == 4000
    # Monotonic, non-overlapping.
    for previous, current in zip(words, words[1:]):
        assert previous["endMs"] <= current["startMs"]
    # Page breaks follow sentence punctuation so a caption page stays in one sentence.
    assert words[1].get("pageBreakAfter") is True
    assert words[-1].get("pageBreakAfter") is True
    assert "pageBreakAfter" not in words[0]


def test_estimated_timings_tolerate_empty_narration() -> None:
    assert _estimated_word_timings("", 3000) == []
    assert _estimated_word_timings("   ", 3000) == []


def test_provider_timings_are_seconds_to_milliseconds() -> None:
    words = _provider_word_timings({
        "word_timestamps": [
            {"word": " Hello", "start": 0.0, "end": 0.42},
            {"word": "world", "start": 0.42, "end": 0.9},
            {"word": " ", "start": 0.9, "end": 0.95},
        ]
    })
    assert words == [
        {"word": "Hello", "startMs": 0, "endMs": 420},
        {"word": "world", "startMs": 420, "endMs": 900},
    ]


def test_slides_are_normalized_with_kind_and_bullets() -> None:
    slides = _normalize_slides({
        "slides": [
            {"kind": "objectives", "heading": "Goals", "narration": "…",
             "on_screen_text": ["One", "Two"]},
            {"heading": "Loose slide", "narration": "…"},
            {"kind": "mystery", "heading": "Unknown kind"},
            "not a slide",
        ]
    })
    assert [slide["kind"] for slide in slides] == ["objectives", "content", "content"]
    assert slides[0]["bullets"] == ["One", "Two"]
    assert slides[1]["bullets"] == []


def test_slides_keep_broll_sources() -> None:
    slides = _normalize_slides({
        "slides": [{
            "kind": "content",
            "heading": "Observation",
            "broll_path": "assets/classroom.png",
            "broll_query": "classroom observation",
        }]
    })
    assert slides[0]["broll_path"] == "assets/classroom.png"
    assert slides[0]["broll_query"] == "classroom observation"


def test_course_context_comes_from_the_bb_exam_source() -> None:
    course = _course_context(
        {
            "courseTitle": "Old title",
            "level": "Entry (E)",
            "assessment": {"passingPercent": 80},
        },
        {
            "course_title": "Understanding Behavior",
            "course_description": "A practical course for support professionals.",
            "module_index": 1,
            "module_total": 7,
            "passing_percent": 75,
            "module": {
                "summary": "Every behavior has a purpose.",
                "objectives": ["Name the functions"],
            },
        },
        [{"q": "Why?"}],
    )
    assert course["title"] == "Understanding Behavior"
    assert course["description"].startswith("A practical course")
    assert course["moduleIndex"] == 1
    assert course["moduleTotal"] == 7
    assert course["summary"] == "Every behavior has a purpose."
    assert course["objectives"] == ["Name the functions"]
    # The bb_exam source's passing threshold is authoritative over the module copy.
    assert course["passingPercent"] == 75
    assert course["quizCount"] == 1


def test_course_context_counts_the_lms_quiz_when_the_module_has_none() -> None:
    course = _course_context(
        {"courseTitle": "Understanding Behavior"},
        {
            "module": {
                "quiz": [
                    {"prompt": "Q1", "options": []},
                    {"prompt": "Q2", "options": []},
                ]
            }
        },
        [],
    )
    assert course["quizCount"] == 2


def test_course_context_falls_back_to_the_module() -> None:
    course = _course_context(
        {
            "courseTitle": "Standalone",
            "courseDescription": "Module-only description",
            "level": "Advanced",
            "moduleIndex": 3,
            "moduleTotal": 5,
        },
        None,
        [],
    )
    assert course["title"] == "Standalone"
    assert course["description"] == "Module-only description"
    assert course["moduleIndex"] == 3
    assert course["quizCount"] == 0


def test_tool_contract_is_renderable_without_an_external_checkout() -> None:
    tool = LessonVideo()
    info = tool.get_info()
    assert info["name"] == "lesson_video"
    assert info["capability"] == "lesson_video"
    assert info["render_composition"] == "LessonVideo"
    assert "module_path" in tool.input_schema["required"]
    assert "piper" in tool.input_schema["properties"]["voice_provider"]["enum"]
    # No dependency on another repository's renderer.
    assert "BB_VIDEO_DIR" not in str(info)


def test_composition_file_exists() -> None:
    root = Path(__file__).resolve().parents[2]
    composition = root / "remotion-composer" / "src" / "LessonVideo.tsx"
    assert composition.is_file()
    source = composition.read_text(encoding="utf-8")
    assert "calculateLessonMetadata" in source
    assert "CaptionOverlay" in source
