"""Build the Yalla Arabic site.

Reads lessons/*.json, makes MP3 audio for every phrase (Arabic, slow Arabic,
English, Urdu) plus one "car mode" track per lesson, and writes the finished
site into ./_site for GitHub Pages.

Audio is cached in ./audio-cache by a hash of (voice, speed, text), so only new
phrases are generated on each run.

Set TTS_MOCK=1 to make silent clips instead (for testing without internet).
"""
import asyncio, hashlib, json, os, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LESSONS = ROOT / "lessons"
CACHE = ROOT / "audio-cache"
SITE = ROOT / "_site"
MOCK = os.environ.get("TTS_MOCK") == "1"

VOICES = {
    "ar": ("ar-AE-HamdanNeural", "+0%", "ar"),
    "ar_slow": ("ar-AE-HamdanNeural", "-35%", "ar"),
    "en": ("en-GB-RyanNeural", "+0%", "en"),
    "ur": ("ur-PK-AsadNeural", "+0%", "ur"),
}


def sh(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def key(voice, rate, text):
    return hashlib.sha1(f"{voice}|{rate}|{text}".encode()).hexdigest()[:16]


async def edge(text, voice, rate, out):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate).save(str(out))


def gtts_fallback(text, lang, slow, out):
    from gtts import gTTS
    gTTS(text=text, lang=lang, slow=slow).save(str(out))


def make_clip(kind, text):
    """Return cached MP3 path for this text, generating it if needed."""
    voice, rate, glang = VOICES[kind]
    out = CACHE / f"{key(voice, rate, text)}.mp3"
    if out.exists() and out.stat().st_size > 0:
        return out
    if MOCK:
        secs = max(1.0, len(text) / 12)
        sh("ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", f"{secs:.2f}",
           "-c:a", "libmp3lame", "-b:a", "48k", str(out))
        return out
    try:
        asyncio.run(edge(text, voice, rate, out))
        if out.stat().st_size == 0:
            raise RuntimeError("empty audio")
    except Exception as e:  # Microsoft voice failed: fall back to Google voice
        print(f"  edge-tts failed for {kind} ({e}); using gTTS", file=sys.stderr)
        gtts_fallback(text, glang, kind == "ar_slow", out)
    return out


def silence(seconds):
    out = CACHE / f"silence-{seconds}.wav"
    if not out.exists():
        sh("ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", str(seconds), str(out))
    return out


def to_wav(mp3):
    wav = mp3.with_suffix(".wav")
    if not wav.exists():
        sh("ffmpeg", "-y", "-i", str(mp3), "-ar", "24000", "-ac", "1", str(wav))
    return wav


def car_track(lesson, clips, out):
    """Arabic, pause, slow Arabic, pause, English, Urdu, long pause to repeat aloud."""
    parts = [to_wav(make_clip("en", f"Lesson {lesson['day']}. {lesson.get('title', '')}")), silence(1.5)]
    for c in clips:
        parts += [to_wav(c["ar"]), silence(1.5), to_wav(c["ar_slow"]), silence(1.5)]
        if "en" in c: parts += [to_wav(c["en"]), silence(0.8)]
        if "ur" in c: parts += [to_wav(c["ur"]), silence(0.8)]
        parts += [to_wav(c["ar"]), silence(4)]  # say it again, then your turn
    lst = CACHE / "concat.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    sh("ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c:a", "libmp3lame", "-b:a", "64k", str(out))


def main():
    CACHE.mkdir(exist_ok=True)
    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "audio").mkdir(parents=True)
    for f in ["index.html", "manifest.webmanifest", "icon.svg"]:
        if (ROOT / f).exists():
            shutil.copy(ROOT / f, SITE / f)

    built = []
    for path in sorted(LESSONS.glob("*.json")):
        try:
            lesson = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"SKIPPED {path.name}: not valid JSON ({e})", file=sys.stderr)
            continue
        print(f"Lesson {lesson.get('day')}: {lesson.get('title')}")
        clips = []
        for p in lesson.get("phrases", []):
            c = {"ar": make_clip("ar", p["ar"]), "ar_slow": make_clip("ar_slow", p["ar"])}
            if p.get("en"): c["en"] = make_clip("en", p["en"])
            if p.get("ur"): c["ur"] = make_clip("ur", p["ur"])
            clips.append(c)
            p["audio"] = {}
            for k, src in c.items():
                shutil.copy(src, SITE / "audio" / src.name)
                p["audio"][k] = f"audio/{src.name}"
        track = SITE / "audio" / f"lesson-{path.stem}.mp3"
        car_track(lesson, clips, track)
        lesson["track"] = f"audio/{track.name}"
        lesson["id"] = path.stem
        built.append(lesson)

    built.sort(key=lambda l: (l.get("day", 0), l["id"]))
    (SITE / "lessons.json").write_text(json.dumps(built, ensure_ascii=False), encoding="utf-8")
    for w in CACHE.glob("*.wav"):
        if not w.name.startswith("silence"):
            w.unlink()
    print(f"Built {len(built)} lessons into _site/")


if __name__ == "__main__":
    main()
