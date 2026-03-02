# chasingclaw Desktop (Tauri)

This folder contains a Tauri shell for Windows desktop distribution.

## Build on Windows

1. Build Python sidecar (`chasingclaw-ui.exe`)
2. Copy sidecar into `src-tauri/bin`
3. Run `tauri build`

Use the root script for one-step build:

```bat
scripts\windows\build-tauri-desktop.bat -Clean
```

Artifacts are generated under:

- `desktop/tauri/src-tauri/target/release/bundle`
