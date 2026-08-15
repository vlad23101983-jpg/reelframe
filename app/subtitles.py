"""
Kinomotor — app/subtitles.py
Караоке-субтитры: текущее слово подсвечивается по ходу речи.

Тайминги приходят от ElevenLabs (app/voice.py) и относятся к ИСХОДНОЙ озвучке.
Но перед сборкой ролика дорожка меняется дважды:
    1. silenceremove вырезает паузы тишины
    2. atempo подгоняет скорость под нужную длительность ролика
Поэтому времена нужно пересчитать, иначе субтитры уедут от речи — при типичном
ускорении 1.25x конец ролика расходится примерно на 2.5 секунды.

Рисуется через формат ASS и фильтр ffmpeg `ass` (libass). Отдельного прохода
кодирования не нужно — фильтр встраивается в тот же вызов, что и хук.
"""

import os

FONT_NAME = "Montserrat ExtraBold"
FONTS_DIR = os.path.join("media", "fonts")

# Сколько слов держать в одной строке. Русский заметно длиннее английского,
# поэтому строки короткие — иначе текст занимает пол-экрана.
WORDS_PER_LINE = 4

# Цвета в ASS задаются как &HAABBGGRR — порядок байтов обратный привычному RGB.
COLOR_TEXT = "&H00FFFFFF"      # белый — ещё не прозвучавшие слова
COLOR_HIGHLIGHT = "&H0000D7FF" # жёлтый — слово, которое звучит сейчас
COLOR_OUTLINE = "&H00000000"   # чёрная обводка держит текст на любом фоне

FONT_SIZE = 74
OUTLINE_WIDTH = 7
SHADOW_DEPTH = 4
MARGIN_BOTTOM = 470  # выше интерфейса TikTok/Reels с кнопками и описанием
MARGIN_SIDE = 100


def rescale_word_timings(words, transform):
    """
    Переводит тайминги слов из исходной озвучки в систему координат финального аудио.

    transform — то, что вернул process_smart_audio:
        original_duration — длина исходной озвучки
        clean_duration    — длина после вырезания пауз
        speed_ratio       — коэффициент, ушедший в atempo

    Вырезанная тишина распределяется пропорционально: на практике её около 1%
    от длины, и точное расположение пауз погоды не делает. Ускорение же
    учитывается точно — именно оно даёт основной сдвиг.
    """
    if not words or not transform:
        return []

    original = transform.get("original_duration")
    clean = transform.get("clean_duration")
    ratio = transform.get("speed_ratio")

    # Проверяем ДО подстановки значений по умолчанию: ratio=0 означает, что
    # обработка аудио пошла не так. Лучше остаться без субтитров, чем показать
    # их не в такт речи.
    if not original or original <= 0 or not ratio or ratio <= 0:
        return []

    keep = (clean / original) if clean and clean > 0 else 1.0

    rescaled = []
    for w in words:
        start = w["start"] * keep / ratio
        end = w["end"] * keep / ratio
        if end <= start:
            end = start + 0.05
        rescaled.append({"word": w["word"], "start": start, "end": end})

    return rescaled


def _fmt_time(seconds: float) -> str:
    """Время в формате ASS: 0:00:01.23 (сотые доли, не миллисекунды)."""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _escape(text: str) -> str:
    """
    Обезвреживает текст для ASS: фигурные скобки открывают блок тегов,
    обратный слэш — управляющий символ. Слово из речи не должно
    случайно превратиться в команду разметки.
    """
    return (
        text.replace("\\", "")
        .replace("{", "(")
        .replace("}", ")")
        .replace("\n", " ")
    )


def _group_into_lines(words, per_line: int):
    """Режет поток слов на строки по per_line штук."""
    return [words[i:i + per_line] for i in range(0, len(words), per_line)]


def build_ass(words, output_path: str, video_width: int = 1080, video_height: int = 1920) -> str:
    """
    Собирает ASS-файл с караоке-подсветкой.

    Тег \\k задаёт длительность подсветки слова в сотых долях секунды —
    libass сам ведёт подсветку слева направо внутри строки.
    Возвращает путь к файлу или пустую строку, если рисовать нечего.
    """
    if not words:
        return ""

    lines = _group_into_lines(words, WORDS_PER_LINE)

    events = []
    for line in lines:
        if not line:
            continue

        start = line[0]["start"]
        end = line[-1]["end"]
        if end <= start:
            continue

        parts = []
        cursor = start
        for w in line:
            # Длительность подсветки — от текущей позиции до конца слова,
            # чтобы паузы между словами не съедали подсветку.
            duration_cs = max(1, int(round((w["end"] - cursor) * 100)))
            parts.append(f"{{\\k{duration_cs}}}{_escape(w['word'])}")
            cursor = w["end"]

        text = " ".join(parts)
        events.append(
            f"Dialogue: 0,{_fmt_time(start)},{_fmt_time(end)},Karaoke,,0,0,0,,{text}"
        )

    if not events:
        return ""

    content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{FONT_NAME},{FONT_SIZE},{COLOR_TEXT},{COLOR_HIGHLIGHT},{COLOR_OUTLINE},{COLOR_OUTLINE},0,0,0,0,100,100,0,0,1,{OUTLINE_WIDTH},{SHADOW_DEPTH},2,{MARGIN_SIDE},{MARGIN_SIDE},{MARGIN_BOTTOM},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(events) + "\n"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    return output_path


def ass_filter(ass_path: str) -> str:
    """
    Возвращает готовый фрагмент фильтра ffmpeg или пустую строку.

    Шрифт берётся прямо из папки проекта через fontsdir — устанавливать его
    в систему не нужно, файл едет на сервер обычным git pull.
    Пути экранируются: двоеточие и обратный слэш имеют особый смысл
    в синтаксисе фильтров ffmpeg.
    """
    if not ass_path or not os.path.exists(ass_path):
        return ""

    def esc(p):
        return p.replace("\\", "/").replace(":", "\\:").replace("'", "")

    parts = [f"ass={esc(ass_path)}"]
    if os.path.isdir(FONTS_DIR):
        parts.append(f"fontsdir={esc(FONTS_DIR)}")

    return ":".join(parts)
