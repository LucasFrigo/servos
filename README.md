# Pardilston

A local voice assistant for Windows. Speech recognition, the language model, and the voice all run on this machine. Web search is the only step that uses the network.

The face is a small borderless window. Settings stay hidden until you open them from the tray icon or by right-clicking the face.

## Installer

`installer\build.ps1` builds `dist\Manivela\Manivela.exe`. With [Inno Setup 6](https://jrsoftware.org/isinfo.php) installed, it also builds `installer\Output\Manivela-Setup.exe`.

The setup installs Pardilston for the current user, installs Ollama when it is missing, and downloads `qwen2.5:7b`. Opening `Manivela.exe` starts Ollama if it is stopped, and closes that Ollama process when Pardilston exits. Add `-BundleOllama` when building if the setup file should carry the Ollama installer itself. Speech files already in `models\` are copied next to the executable.

## Setup

Install [Ollama](https://ollama.com), then:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
ollama pull qwen2.5:7b
python -m servos
```

`pythonw -m servos` starts Pardilston without a console window.

The first launch downloads the Piper voice, the Silero voice-activity model, and the English speech-to-text weights into `models/`.

Pardilston starts Ollama when the program opens, if Ollama is installed and the server is stopped. When this launch is the one that started Ollama, quitting Pardilston stops that process. A missing Ollama install shows `NO LINK` on the face. Speech recognition stays on the CPU until Ollama has loaded the language model; start Pardilston again after that and it will use the GPU when there is memory left.

## Use

- Push-to-talk is the default. Hold `Ctrl+Space`, speak, and release.
- Always listening is in the settings dashboard. With the wake word on, say "Pardilston" before the request. Saying only the name arms a few seconds for the next phrase.
- The same hold shortcut cuts him off if he is already talking.
- Pick `qwen2.5:3b` in settings when you want faster replies and can accept weaker search.

Allow microphone access for Python if Windows asks.
