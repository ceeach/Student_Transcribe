# NotionTranscribe 🎙

A complete, lightweight, cross-platform desktop application that performs **local, private speech-to-text transcription** using `faster-whisper` and automatically pushes formatted notes with timestamps directly into your Notion database via the official Notion API.

---

## 🌟 Key Features

- **Private & Local Speech-to-Text**: Powered by `faster-whisper` running locally on your machine with CPU or NVIDIA CUDA GPU acceleration.
- **Direct Notion Integration**: Creates pages formatted with timestamps, duration metadata, and category tags.
- **Smart Chunking & Batching**: Automatically breaks long transcripts into blocks under Notion's 2,000-character limit and handles batching beyond 100 blocks.
- **Live Audio Recording**:
  - Start, Pause, Resume, and Stop controls.
  - Live duration timer (`HH:MM:SS`).
  - Real-time VU volume meter with smooth decay.
  - Automatically exports to 16kHz WAV optimized for Whisper.
- **Audio File Upload**:
  - Drag-and-drop zone and file picker.
  - Supports `.mp3`, `.wav`, `.m4a`, `.ogg`, `.flac`, `.aac`, `.wma`.
- **Real-Time Live Preview**:
  - Live streaming segments with highlighted timestamp badges `[00:15 - 00:32]`.
  - Copy to clipboard with one click.
  - Word count and segment counters.
- **Non-Blocking Background Threads**: Full `QThread` pipeline keeps the user interface fluid and responsive during audio recording, speech decoding, and Notion API uploads.
- **Automatic Configuration Persistence**: Saves your Notion token, Database ID, and Whisper preferences in `~/.notion_transcribe/config.json`.

---

## 📁 Project Structure

```
notion_transcribe/
├── main.py                    # App entry point & main GUI window
├── transcriber.py             # faster-whisper worker thread (QThread) with segment streaming
├── notion_service.py          # Notion API client, text chunker (<1900 chars), and page builder
├── notion_worker.py           # Background QThread for Notion API upload
├── audio_recorder.py          # sounddevice microphone capture with RMS level monitoring
├── config_manager.py          # Configuration manager (~/.notion_transcribe/config.json)
├── test_app.py                # Unit test suite
├── requirements.txt           # Python dependencies
├── README.md                  # Documentation and setup instructions
└── ui/
    ├── __init__.py
    ├── audio_meter.py         # Custom PyQt6 visual audio VU meter widget
    ├── drop_zone.py           # Drag-and-drop file upload widget
    ├── settings_dialog.py     # Notion credentials and Whisper model configuration modal
    └── styles.py              # Modern dark-mode styling and UI layout definitions
```

---

## 🚀 Installation & Setup

### 1. Prerequisites
- **Python 3.9+** (Python 3.10 or 3.11 recommended)
- Working microphone (for live recording)
- (Optional) NVIDIA GPU with CUDA drivers for fast GPU inference

### 2. Install Dependencies

In your terminal or command prompt:

```bash
cd notion_transcribe
pip install -r requirements.txt
```

*Contents of `requirements.txt`:*
```
PyQt6>=6.6.0
faster-whisper>=1.0.0
sounddevice>=0.4.6
soundfile>=0.12.1
notion-client>=2.2.1
python-dotenv>=1.0.0
numpy>=1.24.0
```

---

## ⚙️ Notion Database Setup Guide

To enable automatic note publishing, connect your Notion workspace using the official API:

### Step 1: Create a Notion Internal Integration
1. Go to [https://www.notion.so/my-integrations](https://www.notion.so/my-integrations) in your browser.
2. Click **"+ New integration"**.
3. Fill in the fields:
   - **Name**: `NotionTranscribe` (or any name you prefer)
   - **Associated workspace**: Select your Notion workspace
   - **Capabilities**: Ensure **Read content**, **Update content**, and **Insert content** are checked.
4. Click **Save** / **Submit**.
5. Copy the **Internal Integration Secret** (it begins with `secret_...`).

### Step 2: Create a Target Database in Notion
Create a database in Notion (Full page database or inline table). The application automatically inspects your database schema, but for best results configure:

| Property Name | Property Type | Description |
| :--- | :--- | :--- |
| **Name** (or **Title**) | `title` | Page title (e.g. `Transcript - 2026-09-21 18:30`) *(Default / Required)* |
| **Tags** (or **Category**) | `multi_select` | Categories such as `Meeting`, `Memo`, `Interview`, `Ideas` |
| **Created** (or **Date**) | `date` | Timestamp when the recording was transcribed |

> [!NOTE]
> If your database only has a Title property, NotionTranscribe will still work seamlessly and publish the transcript. If `Tags` or `Created` properties are present, it will automatically populate them!

### Step 3: Grant the Integration Access to Your Database
By default, Notion integrations cannot access any page or database until explicitly invited:
1. Open your target Database page in Notion.
2. Click the **three dots (`...`)** in the upper right corner.
3. Scroll down and click **"Connections"** (or **"Add connections"**).
4. Search for your integration name (e.g., `NotionTranscribe`) and click **Confirm**.

### Step 4: Find Your Database ID
Look at the URL of your Notion database in the browser:
```
https://www.notion.so/myworkspace/8a93e970b8a34bc3a18a93e970b8a34b?v=345678...
                                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                           Database ID
```
- The Database ID is the **32-character hexadecimal string** between your workspace name and the `?v=` parameter.
- You can paste either the 32-character ID or the **entire URL** directly into NotionTranscribe—the app will automatically extract and normalize the UUID!

---

## 🖥 Running the Application

Launch the desktop app with:

```bash
python main.py
```

### First-Time Configuration
1. Click the **⚙ Settings** button in the top right corner.
2. Paste your **Internal Integration Token** (`secret_...`).
3. Paste your **Database ID** or Notion Database URL.
4. Select your preferred **Whisper Model** size (`base` is recommended for general use).
5. Choose your compute device (`cpu` or `cuda`).
6. Click **⚡ Test Connection**. You will see a green checkmark confirming connection to your database.
7. Click **Save**. Settings are securely saved to `~/.notion_transcribe/config.json`.

---

## 🎧 Usage Modes

### Mode 1: Record Live
1. Select the **🎙 Record Live** tab.
2. Click **● Start Recording** and speak into your microphone.
3. Observe the live timer and audio level VU meter.
4. Use **❚❚ Pause** to temporarily pause and resume recording.
5. Click **■ Stop** to finalize the audio clip.
6. Customize the Notion Page Title and optional Tags.
7. Click **⚡ Transcribe & Push to Notion**.
8. Follow the real-time transcription in the preview pane; once complete, the transcript will automatically upload to Notion and provide a direct link to open the page.

### Mode 2: Upload File
1. Select the **📁 Upload File** tab.
2. Drag and drop any `.mp3`, `.wav`, `.m4a`, `.ogg`, or `.flac` file into the target zone (or click **Browse Files...** to pick from your past recordings).
3. The page title defaults to the file name.
4. Click **⚡ Transcribe & Push to Notion** (or use **Transcribe Audio** to inspect the text before uploading).

---

## 💾 Recordings Archive & Multi-Model Testing

### Where Are Recordings Stored?
All live microphone recordings are permanently saved into a dedicated directory on your computer:
📂 `~/.notion_transcribe/recordings/` (`C:\Users\Charb\.notion_transcribe\recordings\`)

Files are saved as standard 16kHz WAV files named with timestamps (e.g. `Recording_2026-09-21_19-45-00.wav`). Click **📂 Open Recordings Folder** on the "Record Live" tab to open this directory in Windows Explorer at any time.

### Comparing Different Whisper Models
You can run the same audio recording through multiple Whisper models to compare accuracy vs speed:
1. After recording (or uploading) an audio file, select a model from the **Model** dropdown on the bottom toolbar (e.g., `base`).
2. Click **Transcribe Audio** to see the initial transcript preview.
3. Want higher accuracy for complex terminology? Switch the **Model** dropdown to `small` or `medium` and click **Transcribe Audio** again.
4. The application immediately re-runs speech recognition on the existing audio file without re-recording.
5. Once you are satisfied with the result, click **Push to Notion**!

---

## 🧠 Whisper Model Recommendations

| Model | Size | Speed | Memory (RAM / VRAM) | Recommended Use |
| :--- | :--- | :--- | :--- | :--- |
| `tiny` | ~75 MB | Extremely Fast | ~1 GB | Quick tests, simple clear speech |
| `base` | ~145 MB | Fast | ~1.5 GB | **Default / Best balance for CPU** |
| `small` | ~460 MB | Moderate | ~2 GB | High accuracy on standard laptops |
| `medium` | ~1.5 GB | Slower on CPU | ~5 GB | Complex vocabulary, multiple speakers |
| `large-v3` | ~3 GB | Requires GPU | ~8 GB | Professional audio, complex accents |

---

## ⚖ Multi-Model Comparison & Audio Audition

1. Transcribe your recording with an initial model (e.g., `base`).
2. Click **"⚖ Compare Models..."** in the action toolbar and pick a second model (e.g., `small` or `medium`).
3. The app aligns both transcripts word-by-word, highlights conflicting words in full sentence context, and lets you:
   - Click **`▶ Play Audio (-2s to +2s)`** to audition 2 seconds of audio before and after the word.
   - Choose Model A, Model B, or type a Custom correction.
   - Click **`✔ Apply Verified Transcript`** to update the text with verified badges and push to Notion.

---

## 📹 Video Ingestion, Audio Extraction & Auto-Deletion

You can automatically extract audio from videos, delete the large video file, and transcribe the audio:
1. **Video Inbox Watch Folder**:
   - The app monitors `~/.notion_transcribe/video_inbox/` (or your custom folder in Settings).
   - In the **📹 Video Inbox** tab, click **"📂 Open Video Folder in Explorer"** and drop any video (`.mp4`, `.mkv`, `.mov`, `.avi`, `.webm`, `.wmv`, etc.).
2. **Automatic Pipeline**:
   - The app extracts speech into a 16kHz mono WAV file in `~/.notion_transcribe/recordings/`.
   - Strict verification ensures the audio is valid and complete before safely deleting the source video file to reclaim storage.
   - Speech-to-text transcription automatically begins on the extracted audio.
3. **Direct Drag & Drop**:
   - You can also drop a video file directly into the **📁 Upload File** tab!

---

## 🛠 Troubleshooting & FAQs

- **"Database not found or access denied"**:
  Ensure you shared the database with your integration (Step 3 above: `...` > `Connections` > Select your integration).
- **"Invalid Notion Integration Token"**:
  Ensure your secret begins with `secret_` and that you copied the full key from your Notion integrations dashboard.
- **CUDA Out of Memory**:
  In **Settings**, change the compute device to `cpu` or choose a smaller model like `tiny` or `base`.
- **Microphone Error**:
  Verify that your operating system has granted microphone access permissions to Python and that your default audio input device is enabled.

---

## 🧪 Running Tests

Run the test suite to verify the text chunker, Notion payload builder, and Whisper worker:

```bash
python -m unittest test_app.py
```
