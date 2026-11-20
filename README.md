# Vid Downloader & Cutter

Local web app: download videos/streams (anything yt-dlp supports), then cut out parts.

- **Start:** double-click the **Vid Downloader** icon on your Desktop. The app runs in the background with no console window and opens http://127.0.0.1:8765. Clicking the icon again while it's running just reopens the tab. Stop the app with **⏻ Quit app** at the top right.
- **Shortcut missing?** Run `create_shortcut.ps1` (right-click → Run with PowerShell). `run.bat` still works too and shows a console with logs.
- **Won't start?** Check `app.log` in the app folder.
- **Setup only:** `setup.bat` creates `.venv` and downloads portable ffmpeg into `bin/`. Nothing is installed system-wide.
- **Downloads fail?** Run `update.bat`, because sites change often and yt-dlp updates frequently.

Files go to `downloads/`; clips go to `clips/`.

## Downloading long videos in parts
Paste the URL, then click **✂ Split into parts…**. Pick a part length (15 min to 2 h, or a custom length) and click **Download** next to any part.
**Queue all parts** downloads every part, two at a time. Only the selected time range is fetched, and each part is saved as its own file,
e.g. `Title [id] Part 2 of 5 (01h00m00s-02h00m00s).mp4`. Parts may overlap by a few seconds at the joins (keyframes), so nothing is lost.

## Cutting
| Mode | Speed | Accuracy | Quality |
|---|---|---|---|
| Fast | Instant, even on multi-hour files | Starts at the keyframe just before your In point, so it may include a few extra seconds | Lossless (stream copy) |
| Precise | Re-encodes (H.264/AAC) | Frame-accurate | Set by the CRF option |

Editor keys: `Space` play/pause, `I`/`O` set In/Out, `A` add segment, `←`/`→` ±5s (`Shift` ±60s),
`,`/`.` step one frame, `J`/`K`/`L` change speed, `Del` remove the selected segment. Scroll on the timeline to zoom; `Shift`+scroll pans.

## Notes
- The server only listens on 127.0.0.1. Its API can delete files, so don't expose it to a network.
- Cancelled or failed downloads keep their `.part` data. **Resume** continues where it stopped.
- Only download content you have the right to download.
