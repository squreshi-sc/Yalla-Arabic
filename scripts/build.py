"""Build the Yalla Arabic site (Arabic, German, French).

Reads lessons/*.json and dialogues/*.json (each file holds one unit or a list
of units), makes MP3 audio for every phrase or dialogue line (Arabic, slow
Arabic, English, Urdu) plus one "car mode" track per unit, and writes the
finished site into ./_site for GitHub Pages.

Each unit has "lang" ("ar" default, "de", "fr"). The target-language text is
always in the "ar" field. Dialogue lines use different voices per speaker:
  g = "you" -> male (you), "m" -> second male, "f" -> female.

Audio is cached in ./audio-cache by a hash of (voice, speed, text).
Set TTS_MOCK=1 to make silent clips instead (for testing without internet).
"""
import asyncio, hashlib, json, os, re, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "audio-cache"
SITE = ROOT / "_site"
MOCK = os.environ.get("TTS_MOCK") == "1"

VOICES = {
    "ar": {"you": "ar-AE-HamdanNeural", "m": "ar-KW-FahedNeural", "f": "ar-AE-FatimaNeural"},
    "de": {"you": "de-DE-ConradNeural", "m": "de-DE-KillianNeural", "f": "de-DE-KatjaNeural"},
    "fr": {"you": "fr-FR-HenriNeural", "m": "fr-FR-RemyMultilingualNeural", "f": "fr-FR-DeniseNeural"},
}
EN_VOICE = "en-GB-RyanNeural"
UR_VOICE = "ur-PK-AsadNeural"
GTTS_LANG = {"ar": "ar", "en": "en", "ur": "ur", "de": "de", "fr": "fr"}


def sh(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def key(voice, rate, text):
    return hashlib.sha1(f"{voice}|{rate}|{text}".encode()).hexdigest()[:16]


async def edge(text, voice, rate, out):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate).save(str(out))


def make_clip(lang, text, voice, rate="+0%"):
    """Return the cached MP3 for this text, generating it if needed."""
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
        print(f"  edge-tts failed ({voice}: {e}); using gTTS", file=sys.stderr)
        from gtts import gTTS
        gTTS(text=text, lang=GTTS_LANG[lang], slow=rate.startswith("-")).save(str(out))
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


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 2.0


def concat(parts, out):
    lst = CACHE / "concat.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    sh("ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c:a", "libmp3lame", "-b:a", "64k", str(out))


def lesson_track(unit, clips, out):
    """Arabic, pause, slow Arabic, pause, English, Urdu, Arabic again, pause to repeat."""
    parts = [to_wav(make_clip("en", f"Lesson {unit['day']}. {unit.get('title', '')}", EN_VOICE)), silence(1.5)]
    for c in clips:
        parts += [to_wav(c["ar"]), silence(1.5), to_wav(c["ar_slow"]), silence(1.5)]
        if "en" in c: parts += [to_wav(c["en"]), silence(0.8)]
        if "ur" in c: parts += [to_wav(c["ur"]), silence(0.8)]
        parts += [to_wav(c["ar"]), silence(4)]
    concat(parts, out)


def dialogue_track(unit, lines, clips, out):
    """Part 1: listen to the dialogue with English after each line.
    Part 2: role-play. The other person speaks; on your lines there is a pause
    for you to speak, then the correct line is played."""
    parts = [to_wav(make_clip("en", f"Dialogue {unit['day']}. {unit.get('title', '')}. First, listen.", EN_VOICE)), silence(1.2)]
    for ln, c in zip(lines, clips):
        parts += [to_wav(c["ar"]), silence(0.7)]
        if "en" in c: parts += [to_wav(c["en"]), silence(1.2)]
    parts += [silence(1.5), to_wav(make_clip("en", "Now it's your turn. Speak your lines in the pause.", EN_VOICE)), silence(1.5)]
    for ln, c in zip(lines, clips):
        if ln.get("g") == "you":
            if "en" in c: parts += [to_wav(c["en"]), silence(0.5)]
            parts += [silence(round(max(2.5, duration(c["ar"]) * 1.8), 1)), to_wav(c["ar"]), silence(1.2)]
        else:
            parts += [to_wav(c["ar"]), silence(0.9)]
    concat(parts, out)


def load_units():
    units = []
    for folder, default_type in (("lessons", "lesson"), ("dialogues", "dialogue")):
        for path in sorted((ROOT / folder).glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                print(f"SKIPPED {path}: not valid JSON ({e})", file=sys.stderr)
                continue
            items = data if isinstance(data, list) else [data]
            for i, u in enumerate(items):
                u.setdefault("type", default_type)
                u.setdefault("level", "Starter")
                u.setdefault("id", path.stem if len(items) == 1 else f"{path.stem}-{i + 1}")
                units.append(u)
    return units


def main():
    CACHE.mkdir(exist_ok=True)
    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "audio").mkdir(parents=True)
    for f in ["index.html", "manifest.webmanifest", "icon.svg", "CHANGELOG.md", "sw.js"]:
        if (ROOT / f).exists():
            shutil.copy(ROOT / f, SITE / f)

    built = []
    for unit in load_units():
        is_dialogue = unit["type"] == "dialogue"
        lang = unit.setdefault("lang", "ar")
        lines = unit.get("lines" if is_dialogue else "phrases", [])
        print(f"{lang} {unit['level']} {unit['type']} {unit.get('day')}: {unit.get('title')} ({len(lines)})")
        clips = []
        for p in lines:
            vs = VOICES[lang]
            v = vs.get(p.get("g", "you"), vs["you"])
            c = {"ar": make_clip(lang, p["ar"], v), "ar_slow": make_clip(lang, p["ar"], v, "-35%")}
            if p.get("en"): c["en"] = make_clip("en", p["en"], EN_VOICE)
            if p.get("ur"): c["ur"] = make_clip("ur", p["ur"], UR_VOICE)
            clips.append(c)
            p["audio"] = {}
            for k, src in c.items():
                shutil.copy(src, SITE / "audio" / src.name)
                p["audio"][k] = f"audio/{src.name}"
        # The track name includes a hash of its clips, so a saved offline copy is never out of date.
        tag = hashlib.sha1("|".join(f"{c[k].name}" for c in clips for k in sorted(c)).encode() + unit.get("title", "").encode()).hexdigest()[:8]
        track = SITE / "audio" / f"track-{unit['id']}-{tag}.mp3"
        (dialogue_track(unit, lines, clips, track) if is_dialogue else lesson_track(unit, clips, track))
        unit["track"] = f"audio/{track.name}"
        if is_dialogue:
            unit["phrases"] = unit.pop("lines")
        built.append(unit)

    order = {"Starter": 0, "Medium": 1, "Advanced": 2}
    langs = {"ar": 0, "de": 1, "fr": 2}
    built.sort(key=lambda u: (langs.get(u["lang"], 9), order.get(u["level"], 9), u["type"], u.get("day", 0), u["id"]))
    (SITE / "lessons.json").write_text(json.dumps(built, ensure_ascii=False), encoding="utf-8")
    # offline.json: every audio file per language with its size, for the "Download for offline" button.
    # The version comes from the first "## vX.Y" heading in CHANGELOG.md.
    m = re.search(r"^## v([\d.]+)", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)
    offline = {"version": m.group(1) if m else "0", "langs": {}}
    for u in built:
        o = offline["langs"].setdefault(u["lang"], {"files": [], "sizes": [], "bytes": 0})
        for f in [u["track"]] + [src for p in u["phrases"] for src in p["audio"].values()]:
            if f not in o["files"]:
                size = (SITE / f).stat().st_size
                o["files"].append(f); o["sizes"].append(size); o["bytes"] += size
    (SITE / "offline.json").write_text(json.dumps(offline), encoding="utf-8")
    for w in CACHE.glob("*.wav"):
        if not w.name.startswith("silence"):
            w.unlink()
    print(f"Built {len(built)} units into _site/")


if __name__ == "__main__":
    main()
