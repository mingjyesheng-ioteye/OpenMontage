import React from "react";
import {
  AbsoluteFill,
  Audio,
  CalculateMetadataFunction,
  Img,
  OffthreadVideo,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { loadSpaceGrotesk } from "./lib/localFonts";
import { CaptionOverlay, WordCaption } from "./components/CaptionOverlay";
import { resolveTheme, type ThemeConfig } from "./Root";

// ---------------------------------------------------------------------------
// Contract — written by tools/video/lesson_video.py from a lesson module JSON
// ---------------------------------------------------------------------------

export type LessonSlideKind = "title" | "objectives" | "content" | "recap" | "quiz";

export type LessonSlide = {
  kind: LessonSlideKind;
  heading: string;
  sub?: string | null;
  bullets?: string[];
  /** Spoken copy. Rendered as audio only; it never appears on screen. */
  narration?: string | null;
  /** public/-relative narration file, already staged by the renderer tool. */
  audioSrc?: string | null;
  /** Real narration length + breathing room, measured by the renderer tool. */
  durationMs: number;
  /** Caption words relative to this slide's start. */
  captionWords?: WordCaption[];
  /** public/-relative b-roll clip, already trimmed to this slide. */
  brollSrc?: string | null;
  brollKind?: "video" | "image";
  brollLayout?: "panel" | "background";
};

export type LessonQuestion = {
  q: string;
  options: string[];
  answer?: number;
};

export type LessonPresenter = {
  portraitSrc?: string | null;
  /** Lip-synced presenter clip (audio-driven); takes precedence over the still. */
  videoSrc?: string | null;
  name?: string;
  role?: string;
  layout?: "pip-br" | "side-right" | "full";
};

/** Course context rendered on the title slide and in the header. */
export type LessonCourseInfo = {
  title?: string;
  description?: string;
  level?: string;
  moduleIndex?: number;
  moduleTotal?: number;
  summary?: string;
  objectives?: string[];
  passingPercent?: number;
  quizCount?: number;
};

export type LessonProps = {
  brand?: string;
  courseTitle?: string;
  moduleTitle?: string;
  level?: string;
  moduleIndex?: number;
  moduleTotal?: number;
  /** Playbook name resolved through Root's THEMES, or a themeConfig override. */
  theme?: string;
  themeConfig?: Partial<ThemeConfig>;
  accentColor?: string;
  slides?: LessonSlide[];
  musicSrc?: string | null;
  musicVolume?: number;
  presenter?: LessonPresenter | null;
  /** Course context (title, description, stats) from bb_exam or the module. */
  course?: LessonCourseInfo;
  assessment?: { passingPercent?: number; questions?: LessonQuestion[] } | null;
  showAssessment?: boolean;
  captions?: boolean;
  wordsPerPage?: number;
  quizSeconds?: number;
  fps?: number;
  width?: number;
  height?: number;
};

const { fontFamily } = loadSpaceGrotesk(["400", "500", "700"]);

const DEFAULT_SLIDE_MS = 4000;
const QUIZ_SECONDS = 7;

const slideFrames = (slide: LessonSlide, fps: number) =>
  Math.max(1, Math.round((Math.max(600, slide.durationMs || DEFAULT_SLIDE_MS) / 1000) * fps));

export const calculateLessonMetadata: CalculateMetadataFunction<LessonProps> = ({
  props,
}) => {
  const fps = props.fps || 30;
  const slides = props.slides || [];
  const spoken = slides.reduce((total, slide) => total + slideFrames(slide, fps), 0);
  const questions =
    props.showAssessment === false ? 0 : props.assessment?.questions?.length || 0;
  const quiz = Math.round(questions * (props.quizSeconds || QUIZ_SECONDS) * fps);
  return {
    durationInFrames: Math.max(1, spoken + quiz),
    fps,
    width: props.width || 1920,
    height: props.height || 1080,
  };
};

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

const alpha = (hex: string, value: number): string => {
  const clean = hex.replace("#", "");
  const expanded =
    clean.length === 3 ? clean.split("").map((c) => c + c).join("") : clean;
  const int = parseInt(expanded, 16);
  const r = (int >> 16) & 255;
  const g = (int >> 8) & 255;
  const b = int & 255;
  return `rgba(${r}, ${g}, ${b}, ${value})`;
};

const pad2 = (value?: number): string =>
  typeof value === "number" ? String(value).padStart(2, "0") : "—";

const clampText = (value: string | null | undefined, max: number): string => {
  const text = (value || "").trim();
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
};

/** Staggered entrance for a block of content. */
const useEnter = (delayFrames = 0) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({
    frame: Math.max(0, frame - delayFrames),
    fps,
    config: { damping: 22, stiffness: 110 },
  });
  return {
    opacity: enter,
    transform: `translateY(${interpolate(enter, [0, 1], [26, 0])}px)`,
  } as React.CSSProperties;
};

// ---------------------------------------------------------------------------
// Deck chrome
// ---------------------------------------------------------------------------

const Header: React.FC<{
  brand?: string;
  moduleIndex?: number;
  moduleTotal?: number;
  theme: ThemeConfig;
  accent: string;
}> = ({ brand, moduleIndex, moduleTotal, theme, accent }) => (
  <div
    style={{
      position: "absolute",
      top: 46,
      left: 96,
      right: 96,
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      fontFamily,
    }}
  >
    <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
      <div
        style={{
          width: 14,
          height: 14,
          borderRadius: 4,
          background: accent,
          boxShadow: `0 0 18px ${alpha(accent, 0.7)}`,
        }}
      />
      <span
        style={{
          fontSize: 22,
          fontWeight: 500,
          letterSpacing: 3.5,
          textTransform: "uppercase",
          color: theme.mutedTextColor,
        }}
      >
        {clampText(brand || "Training", 46)}
      </span>
    </div>
    <span
      style={{
        fontSize: 20,
        fontWeight: 500,
        letterSpacing: 3,
        color: theme.mutedTextColor,
      }}
    >
      MODULE {pad2(moduleIndex)}
      {moduleTotal ? <span style={{ opacity: 0.55 }}> / {pad2(moduleTotal)}</span> : null}
    </span>
  </div>
);

const Progress: React.FC<{ accent: string; theme: ThemeConfig }> = ({
  accent,
  theme,
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const progress = interpolate(frame, [0, Math.max(1, durationInFrames - 1)], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <div
      style={{
        position: "absolute",
        left: 96,
        right: 96,
        bottom: 40,
        height: 4,
        borderRadius: 2,
        background: alpha(theme.textColor, 0.12),
      }}
    >
      <div
        style={{
          width: `${progress * 100}%`,
          height: "100%",
          borderRadius: 2,
          background: `linear-gradient(90deg, ${alpha(accent, 0.5)}, ${accent})`,
        }}
      />
    </div>
  );
};

const SlideCounter: React.FC<{ index: number; total: number; theme: ThemeConfig }> = ({
  index,
  total,
  theme,
}) => (
  <div
    style={{
      position: "absolute",
      right: 96,
      bottom: 64,
      fontFamily,
      fontSize: 18,
      letterSpacing: 2.5,
      color: alpha(theme.mutedTextColor, 0.85),
    }}
  >
    {pad2(index + 1)} / {pad2(total)}
  </div>
);

// ---------------------------------------------------------------------------
// Slide bodies
// ---------------------------------------------------------------------------

const StatChip: React.FC<{
  label: string;
  value: string;
  theme: ThemeConfig;
  accent: string;
}> = ({ label, value, theme, accent }) => (
  <div
    style={{
      padding: "14px 22px",
      borderRadius: 16,
      background: alpha(theme.surfaceColor, 0.9),
      border: `1px solid ${alpha(accent, 0.22)}`,
      minWidth: 150,
    }}
  >
    <div
      style={{
        fontSize: 13,
        letterSpacing: 2.5,
        textTransform: "uppercase",
        color: theme.mutedTextColor,
        marginBottom: 4,
      }}
    >
      {label}
    </div>
    <div style={{ fontSize: 26, fontWeight: 700, color: theme.textColor }}>{value}</div>
  </div>
);

const TitleSlide: React.FC<{
  slide: LessonSlide;
  theme: ThemeConfig;
  accent: string;
  brand?: string;
  courseTitle?: string;
  level?: string;
  course?: LessonCourseInfo;
}> = ({ slide, theme, accent, brand, courseTitle, level, course }) => {
  const headline = useEnter(0);
  const sub = useEnter(8);
  const rule = useEnter(14);

  const courseLabel = course?.title || courseTitle || brand || "";
  const moduleIndex = course?.moduleIndex;
  const moduleTotal = course?.moduleTotal;
  const chips: Array<{ label: string; value: string }> = [];
  if (course?.level || level) {
    chips.push({ label: "Level", value: clampText(course?.level || level, 24) });
  }
  if (typeof moduleIndex === "number") {
    chips.push({
      label: "Module",
      value: moduleTotal ? `${moduleIndex} of ${moduleTotal}` : String(moduleIndex),
    });
  }
  if (course?.quizCount) {
    chips.push({
      label: "Knowledge check",
      value: `${course.quizCount} question${course.quizCount === 1 ? "" : "s"}`,
    });
  }
  if (course?.passingPercent) {
    chips.push({ label: "Passing", value: `${Math.round(course.passingPercent)}%` });
  }

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        padding: "150px 140px 150px",
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        fontFamily,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 16,
          marginBottom: 30,
          ...headline,
        }}
      >
        <span
          style={{
            fontSize: 22,
            fontWeight: 500,
            letterSpacing: 4,
            textTransform: "uppercase",
            color: accent,
          }}
        >
          {clampText(courseLabel, 64)}
        </span>
        {level ? (
          <span
            style={{
              fontSize: 18,
              letterSpacing: 2,
              padding: "6px 14px",
              borderRadius: 999,
              color: theme.mutedTextColor,
              border: `1px solid ${alpha(theme.mutedTextColor, 0.35)}`,
            }}
          >
            {clampText(level, 28)}
          </span>
        ) : null}
      </div>
      <h1
        style={{
          margin: 0,
          fontSize: 92,
          fontWeight: 700,
          lineHeight: 1.06,
          letterSpacing: -2,
          color: theme.textColor,
          maxWidth: 1420,
          ...headline,
        }}
      >
        {clampText(slide.heading, 90)}
      </h1>
      {slide.sub ? (
        <p
          style={{
            margin: "30px 0 0",
            fontSize: 36,
            fontWeight: 400,
            lineHeight: 1.35,
            color: theme.mutedTextColor,
            maxWidth: 1180,
            ...sub,
          }}
        >
          {clampText(slide.sub, 180)}
        </p>
      ) : null}
      {course?.description ? (
        <p
          style={{
            margin: "24px 0 0",
            fontSize: 28,
            fontWeight: 400,
            lineHeight: 1.45,
            color: alpha(theme.textColor, 0.78),
            maxWidth: 1180,
            ...sub,
          }}
        >
          {clampText(course.description, 260)}
        </p>
      ) : null}
      {chips.length ? (
        <div
          style={{
            display: "flex",
            gap: 16,
            marginTop: 34,
            flexWrap: "wrap",
            ...rule,
          }}
        >
          {chips.map((chip) => (
            <StatChip
              key={chip.label}
              label={chip.label}
              value={chip.value}
              theme={theme}
              accent={accent}
            />
          ))}
        </div>
      ) : null}
      <div
        style={{
          marginTop: 40,
          width: 160,
          height: 8,
          borderRadius: 4,
          background: accent,
          ...rule,
        }}
      />
    </div>
  );
};

const BulletRow: React.FC<{
  bullet: string;
  index: number;
  theme: ThemeConfig;
  accent: string;
  numbered: boolean;
  fontSize: number;
}> = ({ bullet, index, theme, accent, numbered, fontSize }) => {
  const enter = useEnter(10 + index * 4);
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 26, ...enter }}>
      {numbered ? (
        <span
          style={{
            flex: "0 0 auto",
            minWidth: 54,
            height: 54,
            borderRadius: 999,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: alpha(accent, 0.14),
            border: `2px solid ${alpha(accent, 0.55)}`,
            color: accent,
            fontSize: 24,
            fontWeight: 700,
            fontFamily,
          }}
        >
          {index + 1}
        </span>
      ) : (
        <span
          style={{
            flex: "0 0 auto",
            width: 16,
            height: 16,
            marginTop: 18,
            borderRadius: 999,
            background: accent,
            boxShadow: `0 0 14px ${alpha(accent, 0.55)}`,
          }}
        />
      )}
      <span
        style={{
          fontSize,
          lineHeight: 1.34,
          fontWeight: 500,
          color: theme.textColor,
          paddingTop: numbered ? 4 : 2,
        }}
      >
        {clampText(bullet, 150)}
      </span>
    </div>
  );
};

const BulletList: React.FC<{
  bullets: string[];
  theme: ThemeConfig;
  accent: string;
  numbered?: boolean;
  fontSize?: number;
}> = ({ bullets, theme, accent, numbered = false, fontSize = 40 }) => (
  <div style={{ display: "flex", flexDirection: "column", gap: 26, marginTop: 46 }}>
    {bullets.slice(0, 5).map((bullet, index) => (
      <BulletRow
        key={`${index}-${bullet.slice(0, 12)}`}
        bullet={bullet}
        index={index}
        theme={theme}
        accent={accent}
        numbered={numbered}
        fontSize={fontSize}
      />
    ))}
  </div>
);

const BrollMedia: React.FC<{
  src: string;
  kind: "video" | "image";
  layout: "panel" | "background";
  theme: ThemeConfig;
}> = ({ src, kind, layout, theme }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const progress = interpolate(frame, [0, Math.max(1, durationInFrames)], [0, 1], {
    extrapolateRight: "clamp",
  });

  const Media = () =>
    kind === "image" ? (
      <Img
        src={staticFile(src)}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          transform: `scale(${1.02 + progress * 0.1})`,
        }}
      />
    ) : (
      <OffthreadVideo
        src={staticFile(src)}
        muted
        style={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          transform: `scale(${1.04 + progress * 0.05})`,
        }}
      />
    );

  if (layout === "background") {
    return (
      <>
        <div
          style={{
            position: "absolute",
            inset: 0,
            overflow: "hidden",
          }}
        >
          <Media />
        </div>
        <div
          style={{
            position: "absolute",
            inset: 0,
            background: `linear-gradient(90deg, ${alpha(theme.backgroundColor, 0.96)} 0%, ${alpha(
              theme.backgroundColor,
              0.82,
            )} 52%, ${alpha(theme.backgroundColor, 0.35)} 100%)`,
          }}
        />
      </>
    );
  }

  return (
    <div
      style={{
        position: "absolute",
        top: 158,
        right: 96,
        bottom: 132,
        width: "38%",
        borderRadius: 30,
        overflow: "hidden",
        boxShadow: `0 30px 70px ${alpha("#000000", 0.22)}`,
        border: `1px solid ${alpha(theme.textColor, 0.1)}`,
      }}
    >
      <Media />
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: `linear-gradient(120deg, ${alpha(theme.backgroundColor, 0.12)}, ${alpha(
            theme.backgroundColor,
            0,
          )})`,
        }}
      />
    </div>
  );
};

const BodySlide: React.FC<{
  slide: LessonSlide;
  theme: ThemeConfig;
  accent: string;
  numbered?: boolean;
  tinted?: boolean;
}> = ({ slide, theme, accent, numbered = false, tinted = false }) => {
  const headline = useEnter(0);
  const lead = useEnter(6);
  const bullets = (slide.bullets || []).filter((b) => b && b.trim());
  const hasBroll = Boolean(slide.brollSrc);
  const brollLayout = slide.brollLayout || "panel";
  const panel = hasBroll && brollLayout === "panel";

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        padding: "178px 140px 150px",
        display: "flex",
        flexDirection: "column",
        fontFamily,
        overflow: "hidden",
      }}
    >
      {hasBroll && brollLayout === "background" ? (
        <BrollMedia
          src={slide.brollSrc as string}
          kind={slide.brollKind || "video"}
          layout="background"
          theme={theme}
        />
      ) : null}
      {tinted ? (
        <div
          style={{
            position: "absolute",
            inset: "140px 96px 120px",
            borderRadius: 36,
            background: alpha(theme.primaryColor, 0.06),
            border: `1px solid ${alpha(theme.primaryColor, 0.12)}`,
          }}
        />
      ) : null}
      {panel ? (
        <BrollMedia
          src={slide.brollSrc as string}
          kind={slide.brollKind || "video"}
          layout="panel"
          theme={theme}
        />
      ) : null}
      <div
        style={{
          position: "relative",
          maxWidth: panel ? "52%" : undefined,
        }}
      >
        <h2
          style={{
            margin: 0,
            fontSize: panel ? 58 : 66,
            fontWeight: 700,
            lineHeight: 1.14,
            letterSpacing: -1,
            color: theme.textColor,
            maxWidth: 1380,
            ...headline,
          }}
        >
          {clampText(slide.heading, 110)}
        </h2>
        {slide.sub ? (
          <p
            style={{
              margin: "24px 0 0",
              fontSize: panel ? 30 : 34,
              lineHeight: 1.4,
              color: theme.mutedTextColor,
              maxWidth: 1220,
              ...lead,
            }}
          >
            {clampText(slide.sub, 220)}
          </p>
        ) : null}
        {bullets.length ? (
          <BulletList
            bullets={bullets}
            theme={theme}
            accent={accent}
            numbered={numbered}
            fontSize={panel ? 34 : 40}
          />
        ) : null}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Knowledge check (module assessment, no narration)
// ---------------------------------------------------------------------------

const OptionCard: React.FC<{
  option: string;
  optionIndex: number;
  isAnswer: boolean;
  reveal: number;
  theme: ThemeConfig;
  accent: string;
}> = ({ option, optionIndex, isAnswer, reveal, theme, accent }) => {
  const enter = useEnter(6 + optionIndex * 4);
  const highlight = isAnswer ? reveal : 0;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 20,
        padding: "24px 28px",
        borderRadius: 20,
        background:
          highlight > 0.05
            ? alpha(accent, 0.12 + 0.16 * highlight)
            : theme.surfaceColor,
        border: `2px solid ${
          highlight > 0.05
            ? alpha(accent, 0.4 + 0.5 * highlight)
            : alpha(theme.textColor, 0.12)
        }`,
        ...enter,
      }}
    >
      <span
        style={{
          width: 44,
          height: 44,
          borderRadius: 12,
          flex: "0 0 auto",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontWeight: 700,
          fontSize: 22,
          background: alpha(accent, 0.16),
          color: accent,
        }}
      >
        {String.fromCharCode(65 + optionIndex)}
      </span>
      <span
        style={{
          fontSize: 30,
          lineHeight: 1.3,
          color: theme.textColor,
          fontWeight: 500,
        }}
      >
        {clampText(option, 120)}
      </span>
    </div>
  );
};

const QuestionSlide: React.FC<{
  question: LessonQuestion;
  index: number;
  total: number;
  theme: ThemeConfig;
  accent: string;
  revealAt: number;
}> = ({ question, index, total, theme, accent, revealAt }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const reveal = spring({
    frame: Math.max(0, frame - revealAt),
    fps,
    config: { damping: 20, stiffness: 120 },
  });
  const headline = useEnter(0);
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        padding: "170px 140px 140px",
        display: "flex",
        flexDirection: "column",
        fontFamily,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 18,
          marginBottom: 18,
          ...headline,
        }}
      >
        <span
          style={{
            fontSize: 20,
            letterSpacing: 3.5,
            textTransform: "uppercase",
            color: accent,
            fontWeight: 700,
          }}
        >
          Knowledge check
        </span>
        <span style={{ fontSize: 20, color: theme.mutedTextColor }}>
          Question {index + 1} of {total}
        </span>
      </div>
      <h2
        style={{
          margin: "0 0 40px",
          fontSize: 52,
          fontWeight: 700,
          lineHeight: 1.25,
          color: theme.textColor,
          maxWidth: 1440,
          ...headline,
        }}
      >
        {clampText(question.q, 220)}
      </h2>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 24,
          maxWidth: 1400,
        }}
      >
        {(question.options || []).slice(0, 4).map((option, optionIndex) => (
          <OptionCard
            key={`${optionIndex}-${option.slice(0, 10)}`}
            option={option}
            optionIndex={optionIndex}
            isAnswer={question.answer === optionIndex}
            reveal={reveal}
            theme={theme}
            accent={accent}
          />
        ))}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Presenter
// ---------------------------------------------------------------------------

const Presenter: React.FC<{
  presenter: LessonPresenter;
  theme: ThemeConfig;
  accent: string;
  /** Frame windows where the presenter should sit on the left (b-roll panel). */
  pipWindows?: Array<{ from: number; to: number; left: boolean }>;
}> = ({ presenter, theme, accent, pipWindows }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const progress = interpolate(frame, [0, Math.max(1, durationInFrames)], [0, 1], {
    extrapolateRight: "clamp",
  });
  const pipLeft =
    pipWindows?.find((window) => frame >= window.from && frame < window.to)?.left ??
    false;

  if (!presenter.portraitSrc && !presenter.videoSrc) return null;

  if (presenter.layout === "side-right" || presenter.layout === "full") {
    const width = presenter.layout === "full" ? 520 : 440;
    return (
      <div
        style={{
          position: "absolute",
          top: 0,
          right: 0,
          bottom: 0,
          width,
          overflow: "hidden",
        }}
      >
        {presenter.videoSrc ? (
          <OffthreadVideo
            src={staticFile(presenter.videoSrc)}
            muted
            style={{ width: "100%", height: "100%", objectFit: "cover" }}
          />
        ) : (
          <Img
            src={staticFile(presenter.portraitSrc as string)}
            style={{
              width: "100%",
              height: "100%",
              objectFit: "cover",
              transform: `scale(${1.02 + progress * 0.06})`,
            }}
          />
        )}
        <div
          style={{
            position: "absolute",
            inset: 0,
            background: `linear-gradient(90deg, ${theme.backgroundColor} 0%, ${alpha(
              theme.backgroundColor,
              0.15,
            )} 30%, ${alpha(theme.backgroundColor, 0)} 100%)`,
          }}
        />
        <div
          style={{
            position: "absolute",
            left: 28,
            bottom: 40,
            fontFamily,
            color: theme.textColor,
          }}
        >
          <div style={{ fontSize: 26, fontWeight: 700 }}>{presenter.name || ""}</div>
          <div style={{ fontSize: 18, color: theme.mutedTextColor }}>
            {presenter.role || ""}
          </div>
        </div>
      </div>
    );
  }

  // pip-br (moves to the left on slides with a b-roll panel)
  return (
    <div
      style={{
        position: "absolute",
        ...(pipLeft ? { left: 96 } : { right: 92 }),
        bottom: 104,
        width: 320,
        height: 320,
        borderRadius: 28,
        overflow: "hidden",
        border: `3px solid ${alpha(accent, 0.75)}`,
        boxShadow: `0 24px 60px ${alpha("#000000", 0.35)}`,
      }}
    >
      {presenter.videoSrc ? (
        <OffthreadVideo
          src={staticFile(presenter.videoSrc)}
          muted
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
        />
      ) : (
        <Img
          src={staticFile(presenter.portraitSrc as string)}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
            transform: `scale(${1.04 + progress * 0.05})`,
          }}
        />
      )}
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          bottom: 0,
          padding: "14px 16px",
          fontFamily,
          color: "#FFFFFF",
          background: `linear-gradient(0deg, ${alpha("#000000", 0.7)}, ${alpha(
            "#000000",
            0,
          )})`,
        }}
      >
        <div style={{ fontSize: 20, fontWeight: 700 }}>{presenter.name || ""}</div>
        <div style={{ fontSize: 14, opacity: 0.8 }}>{presenter.role || ""}</div>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Composition
// ---------------------------------------------------------------------------

const Background: React.FC<{ theme: ThemeConfig; accent: string }> = ({
  theme,
  accent,
}) => (
  <AbsoluteFill
    style={{
      background: `linear-gradient(135deg, ${theme.backgroundColor} 0%, ${alpha(
        theme.primaryColor,
        0.07,
      )} 100%)`,
    }}
  >
    <div
      style={{
        position: "absolute",
        top: -260,
        right: -180,
        width: 760,
        height: 760,
        borderRadius: 999,
        background: `radial-gradient(circle, ${alpha(accent, 0.18)} 0%, ${alpha(
          accent,
          0,
        )} 68%)`,
      }}
    />
    <div
      style={{
        position: "absolute",
        bottom: -320,
        left: -220,
        width: 820,
        height: 820,
        borderRadius: 999,
        background: `radial-gradient(circle, ${alpha(
          theme.primaryColor,
          0.14,
        )} 0%, ${alpha(theme.primaryColor, 0)} 70%)`,
      }}
    />
  </AbsoluteFill>
);

export const LessonVideo: React.FC<LessonProps> = (props) => {
  const { fps } = useVideoConfig();
  const theme = resolveTheme(props as unknown as Record<string, unknown>);
  const accent = props.accentColor || theme.accentColor;
  const slides = props.slides || [];
  const questions =
    props.showAssessment === false ? [] : props.assessment?.questions || [];
  const quizSeconds = props.quizSeconds || QUIZ_SECONDS;
  const presenter = props.presenter;
  const sidePresenter =
    presenter?.layout === "side-right" || presenter?.layout === "full";
  const contentPadding = sidePresenter ? 520 : 0;

  const slideWindows: Array<{ from: number; frames: number; left: boolean }> = [];
  let cursor = 0;
  for (const slide of slides) {
    const frames = slideFrames(slide, fps);
    slideWindows.push({
      from: cursor,
      frames,
      left: Boolean(slide.brollSrc) && (slide.brollLayout || "panel") === "panel",
    });
    cursor += frames;
  }
  const quizStart = cursor;

  return (
    <AbsoluteFill style={{ backgroundColor: theme.backgroundColor }}>
      <Background theme={theme} accent={accent} />

      {props.musicSrc ? (
        <Audio src={staticFile(props.musicSrc)} volume={props.musicVolume ?? 0.12} loop />
      ) : null}

      <div style={{ position: "absolute", inset: 0, right: contentPadding }}>
        {slides.map((slide, index) => {
          const window = slideWindows[index];
          const from = window.from;
          const frames = window.frames;
          return (
            <Sequence key={`slide-${index}`} from={from} durationInFrames={frames}>
              {slide.audioSrc ? <Audio src={staticFile(slide.audioSrc)} /> : null}
              {slide.kind === "title" ? (
                <TitleSlide
                  slide={slide}
                  theme={theme}
                  accent={accent}
                  brand={props.brand}
                  courseTitle={props.courseTitle}
                  level={props.level}
                  course={props.course}
                />
              ) : (
                <BodySlide
                  slide={slide}
                  theme={theme}
                  accent={accent}
                  numbered={slide.kind === "objectives"}
                  tinted={slide.kind === "recap"}
                />
              )}
              {props.captions !== false && (slide.captionWords || []).length ? (
                <CaptionOverlay
                  words={slide.captionWords || []}
                  wordsPerPage={props.wordsPerPage || 4}
                  fontSize={42}
                  color={theme.textColor}
                  highlightColor={accent}
                  backgroundColor={theme.captionBackgroundColor}
                  fontFamily={fontFamily}
                  bottomOffset={132}
                />
              ) : null}
              <SlideCounter index={index} total={slides.length} theme={theme} />
            </Sequence>
          );
        })}

        {questions.length ? (
          <Sequence
            from={quizStart}
            durationInFrames={Math.round(questions.length * quizSeconds * fps)}
          >
            {questions.map((question, index) => {
              const frames = Math.round(quizSeconds * fps);
              return (
                <Sequence
                  key={`question-${index}`}
                  from={index * frames}
                  durationInFrames={frames}
                >
                  <QuestionSlide
                    question={question}
                    index={index}
                    total={questions.length}
                    theme={theme}
                    accent={accent}
                    revealAt={
                      question.answer === undefined
                        ? Number.MAX_SAFE_INTEGER
                        : Math.round(fps * 2.2)
                    }
                  />
                </Sequence>
              );
            })}
          </Sequence>
        ) : null}
      </div>

      <Header
        brand={props.brand}
        moduleIndex={props.moduleIndex}
        moduleTotal={props.moduleTotal}
        theme={theme}
        accent={accent}
      />
      {presenter?.portraitSrc || presenter?.videoSrc ? (
        <Presenter
          presenter={presenter}
          theme={theme}
          accent={accent}
          pipWindows={slideWindows.map((window) => ({
            from: window.from,
            to: window.from + window.frames,
            left: window.left,
          }))}
        />
      ) : null}
      <Progress accent={accent} theme={theme} />
    </AbsoluteFill>
  );
};
