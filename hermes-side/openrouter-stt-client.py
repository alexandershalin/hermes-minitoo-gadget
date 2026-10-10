#!/usr/bin/env python3
"""STT через OpenRouter. Использование: openrouter-stt-client.py <input> <output.txt> [language] [model]
Ключ берётся из ~/.hermes/shared/secrets.env (OPENROUTER_API_KEY), прокси — OR_STT_PROXY (необязательно)."""
import base64, json, os, shutil, subprocess, sys, tempfile, urllib.request

def _load_env_file():
    """Необязательный ~/.config/hermes-minitoo/stt.env (KEY=VALUE): OR_STT_PROXY, OPENROUTER_API_KEY."""
    p = os.path.expanduser("~/.config/hermes-minitoo/stt.env")
    if os.path.exists(p):
        for line in open(p):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


_load_env_file()
PROXY = os.environ.get("OR_STT_PROXY", "")  # пусто = прямое соединение
DEFAULT_MODEL = "openai/whisper-large-v3-turbo"


def api_key():
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"]
    hh = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
    for path in (os.path.join(hh, ".env"), os.path.join(hh, "shared", "secrets.env"), os.path.expanduser("~/.hermes/shared/secrets.env")):
        if os.path.exists(path):
            for line in open(path):
                if line.startswith("OPENROUTER_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    sys.exit("OPENROUTER_API_KEY not found (env, $HERMES_HOME/.env, shared/secrets.env)")


def main():
    src, out = sys.argv[1], sys.argv[2]
    lang = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else "auto"
    model = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else DEFAULT_MODEL
    path, fmt = src, os.path.splitext(src)[1].lstrip(".").lower() or "wav"
    tmp = None
    if shutil.which("ffmpeg"):  # 16 кГц моно mp3: в разы меньше трафика
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-ar", "16000", "-ac", "1", "-b:a", "32k", tmp], check=True)
        path, fmt = tmp, "mp3"
    payload = {
        "model": model,
        "input_audio": {"data": base64.b64encode(open(path, "rb").read()).decode(), "format": fmt},
    }
    if lang.lower() not in ("auto", "", "none"):  # auto: Whisper сам определяет язык (ru/en)
        payload["language"] = lang
    body = json.dumps(payload).encode()
    req = urllib.request.Request("https://openrouter.ai/api/v1/audio/transcriptions", body,
                                 {"Authorization": "Bearer " + api_key(), "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": PROXY, "http": PROXY} if PROXY else {}))
    data, err = None, None
    try:
        for attempt in range(3):  # прокси VPS иногда рвёт соединение: короткий таймаут + повтор
            try:
                data = json.load(opener.open(req, timeout=15))
                break
            except Exception as e:  # noqa: BLE001
                err = e
                with open(os.path.expanduser("~/.hermes/logs/openrouter-stt-client.log"), "a") as lf:
                    lf.write(f"{__import__('time').strftime('%F %T')} attempt={attempt + 1} {type(e).__name__}: {e}\n")
    finally:
        if tmp:
            os.unlink(tmp)
    if data is None:
        sys.exit(f"openrouter stt failed: {err}")
    text = (data.get("text") or "").strip()
    if not text:
        sys.exit("empty transcript: " + json.dumps(data)[:200])
    # Системный промпт Гермеса почти весь русский, и бесплатная модель отвечает по-русски на любую речь.
    # Подсказка прямо в сообщении пользователя перевешивает контекст: нерусская речь -> отвечать на её языке.
    letters = [c for c in text if c.isalpha()]
    if letters and sum("\u0400" <= c <= "\u04ff" for c in letters) / len(letters) < 0.3:
        text += "\n\n[Language: the user spoke English. Reply in English only.]"
    open(out, "w").write(text)
    print(text)


main()
