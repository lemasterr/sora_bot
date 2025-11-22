Sora Suite – Technical Project Specification
Overview of Sora Suite (Original PyQt Application)
Sora Suite is a desktop application (originally built with Python + PyQt6) that automates AI content creation and processing. It integrates prompt submission to an AI video generation service (referred to as Sora), bulk video downloading, watermark removal, and social media uploads into one workflow. The UI is organized into multiple pages, each corresponding to a stage in the pipeline (prompts, generation, downloading, editing, etc.). Key features of the original Sora Suite include:
Multi-Profile Chrome Sessions (Workspaces): Manage multiple Chrome user profiles and DevTools connections for parallel AI content generation sessions. Each “workspace” ties together a Chrome profile, prompt files, and output directories.
Prompt Automation (Autogen): Automatically inject text prompts (and optional images) into the Sora web interface using Playwright (Chrome DevTools Protocol). This includes queuing prompts, handling rate limits, and logging results.
Image Generation Integration: Use Google’s GenAI (Imagen API) to generate images for prompts. These images can be automatically attached to video prompts in Sora (for richer video generation inputs).
Automated Video Downloading: Scrape the Sora web app for newly generated video drafts, download them sequentially, and name them using a list of titles.
Watermark Removal: Detect and remove the moving watermark in downloaded videos by reconstructing background patches from neighboring frames (avoiding simple blurring). This uses computer vision (OpenCV) to seamlessly clone background over the watermark.
Telegram Notifications: Optional integration with Telegram bot API to send status updates and allow the user to send templated messages (like alerts when tasks complete).
YouTube/TikTok Upload (Original Only): Queuing and scheduling of video uploads to YouTube and TikTok (with OAuth integration for YouTube). (This feature will be omitted in the new implementation.)
The application is configured via YAML and text files and logs its actions for transparency. The UI in the original app is a classic desktop interface with a sidebar navigation of pages and a top toolbar with shortcut buttons (opening project folders, quick commands, etc.). A “Start/Stop” control is provided to launch or abort the pipeline or automations.
Project Configuration Structure
Several configuration and data files define the behavior of Sora Suite. All these are found next to the main application script. The primary config is **app_config.yaml**, supplemented by specific files for prompts, selectors, and logs:
app_config.yaml: Central YAML config storing paths, settings, and session definitions. Key sections include:
Project Paths:
project_root: .  
downloads_dir: ./downloads  
blurred_dir: ./blurred  
merged_dir: ./merged  
history_file: ./history.jsonl  
titles_file: ./titles.txt  
These define where output files go. For example, downloaded videos default to a downloads/ folder, blurred videos to blurred/, merged compilations to merged/. A JSON Lines file history.jsonl logs events, and titles.txt holds a list of custom titles for naming downloads.
Sessions (Workspaces): Under autogen.sessions in the YAML, each Chrome session is described. For example:
autogen:
  sessions:
    - id: default
      name: "Session 1"
      prompt_profile: __general__
      chrome_profile: ""
      cdp_port: null
      prompts_file: ""
      image_prompts_file: ""
      submitted_log: ""
      failed_log: ""
      notes: ""
      auto_launch_chrome: false
      auto_launch_autogen: idle
Each session has: an id (identifier), a human-readable name, an associated prompt profile key (prompt_profile), and Chrome profile info. chrome_profile can link to a Chrome user data profile (by name or path) if needed (blank means use default). cdp_port is the DevTools port for that session’s Chrome (if null, the app uses a default or next available port). The session also tracks file paths: it can override the prompt text file, image prompt file, and log file locations (if left empty, defaults are used based on the profile). The notes field is for user notes. Flags auto_launch_chrome and auto_launch_autogen control whether to automatically open Chrome and start prompting when the app launches (in the YAML above, it’s off/idle by default).
Prompt Files and Logs: The config can store an active prompt profile (active_prompts_profile) which indicates which prompt set is currently in use. By default this is __general__, meaning the generic prompts file. The YAML also specifies default filenames for prompt logs:
submitted_log: ./workers/autogen/submitted.log  
failed_log: ./workers/autogen/failed.log  
(These may be overridden per session as seen above.) Each session will use its own prompt file and logs if specified, otherwise falling back to global defaults with profile-based naming. For example, if prompt_profile is "profileA", the app will look for prompts_profileA.txt and maintain separate submitted_profileA.log/failed_profileA.log. Submitted logs record successfully queued prompts; failed logs record prompts that were not accepted (with reasons).
Google GenAI Settings: Under google_genai the YAML stores image generation parameters:
google_genai:
  enabled: false
  api_key: ""  
  model: "models/imagen-4.0-generate-001"  
  aspect_ratio: "1:1"  
  image_size: 1K  
  number_of_images: 1  
  output_dir: ./generated_images  
  manifest_file: ./generated_images/manifest.json  
  rate_limit_per_minute: 0  
  max_retries: 3  
  attach_to_sora: true  
  seeds: ""  
  consistent_character_design: false  
  lens_type: ""  
  color_palette: ""  
  style: ""  
  reference_prompt: ""  
  notifications_enabled: true  
  daily_quota: 0  
  quota_warning_prompts: 5  
  quota_enforce: false  
  usage_file: ./generated_images/usage.json  
This config provides the API key and model for Google’s Imagen API, default image properties (aspect ratio, resolution, count per prompt), and output paths. It also includes advanced options like style presets, seeds (for reproducibility), and quota enforcement (e.g., daily_quota and warnings). attach_to_sora: true means generated images will be used in the Sora prompt automation (attached to the prompts, explained later). A manifest.json in the generated_images directory keeps track of generated images and which prompt/spec they belong to. A usage.json tracks daily usage for quota management.
Downloader Settings:
downloader:
  workdir: ./workers/downloader  
  entry: download_all.py  
  max_videos: 0  
This indicates the script for video downloading (download_all.py) and a global max_videos limit (0 means “download all available”). The app can override this per session or per run.
Watermark Cleaner Settings:
watermark_cleaner:
  workdir: ./workers/watermark_cleaner  
  entry: restore.py  
  source_dir: ./downloads  
  output_dir: ./restored  
  template: ./watermark.png  
  mask_threshold: 8  
  threshold: 0.78  
  frames: 120  
  downscale: 1080  
  scale_min: 0.85  
  scale_max: 1.2  
  scale_steps: 9  
  padding_px: 12  
  padding_pct: 0.18  
  min_size: 32  
  search_span: 12  
  pool: 4  
  max_iou: 0.25  
  blend: normal  
  inpaint_radius: 6  
  inpaint_method: telea  
  full_scan: false  
This config drives the watermark removal algorithm. template is the path to the watermark image file (PNG with transparency). The detection uses a template-matching threshold (0.78) and will scan up to frames: 120 frames per video (or the full video if full_scan is true) at multiple scales (from 85% to 120% size, in 9 steps) to find the watermark. It will downscale frames to 1080p for detection (for speed) and use an alpha mask threshold of 8 (to separate the watermark shape from background). The removal algorithm uses a padding of 12px (and 18% of watermark size) around the detected area to gather donor patches, requires a minimum watermark size of 32px to consider, and searches ±12 frames around a frame for clean patches. It limits patch donors to 4 and avoids donors where overlap (iou) > 0.25 (to ensure donors don’t themselves contain the watermark). The blending mode is “normal” (OpenCV normal seamlessClone) unless set to “mixed”. If patch replacement fails, it falls back to inpainting (radius 6, Telea method by default). source_dir and output_dir define where original videos are read from and where cleaned videos are saved (default from downloads/ to restored/).
YouTube & TikTok Uploader:
youtube:
  workdir: ./workers/uploader  
  entry: upload_queue.py  
  channels: []  
  active_channel: ""  
  upload_src_dir: ./merged  
  schedule_minutes_from_now: 60  
  draft_only: false  
  archive_dir: ./uploaded  
  batch_step_minutes: 60  
  batch_limit: 0  
  last_publish_at: ''  
tiktok:
  workdir: ./workers/tiktok  
  entry: upload_queue.py  
  profiles: []  
  active_profile: ""  
  upload_src_dir: ./merged  
  archive_dir: ./uploaded_tiktok  
  schedule_minutes_from_now: 0  
  schedule_enabled: true  
  batch_step_minutes: 60  
  batch_limit: 0  
  draft_only: false  
  last_publish_at: ''  
  github_workflow: .github/workflows/tiktok-upload.yml  
  github_ref: main  
These sections configure optional video upload automation. YouTube: expects a list of channel configs (each with OAuth credentials), an active_channel name, and uses upload_queue.py to upload videos from merged/ (final merged video output directory). It can schedule publishing by a certain offset (e.g., 60 minutes from now) or mark as draft, and will move uploaded files to an uploaded/ archive folder. Batch settings allow spacing out uploads (batch_step_minutes) or limiting how many to upload at once. TikTok: similarly uses an upload script, but here integration is done via a GitHub Actions workflow (tiktok-upload.yml). The app could push videos to a repo branch to trigger the workflow (schedule details and draft options are similar to YouTube). (In the new implementation, these upload features will be removed.)
FFmpeg & Video Processing:
ffmpeg:
  binary: ffmpeg  
  post_chain: "boxblur=1:1,noise=alls=2:allf=t,unsharp=3:3:0.5:3:3:0.0"  
  vcodec: libx264  
  crf: 18  
  preset: medium  
  format: mp4  
  copy_audio: true  
  blur_threads: 2  
  auto_watermark:
    enabled: false  
    template: ''  
    threshold: 0.75  
    frames: 8  
    downscale: 0  
    bbox_padding: 12  
    bbox_padding_pct: 0.15  
    bbox_min_size: 48  
  presets:
    portrait_9x16:
      zones:
        - {x: 30, y: 105, w: 157, h: 62}
        - {x: 515, y: 610, w: 157, h: 62}
        - {x: 30, y: 1110, w: 157, h: 62}
    landscape_16x9:
      zones:
        - {x: 40, y: 60, w: 175, h: 65}
        - {x: 1060, y: 320, w: 175, h: 65}
        - {x: 40, y: 580, w: 175, h: 65}
  active_preset: portrait_9x16  
merge:
  group_size: 2  
  pattern: '*.mp4'  
This defines how videos are processed with FFmpeg. For example, when blurring watermarks as a fallback: post_chain is a filter chain (slight blur, noise, unsharp) applied to blurred regions to improve visual consistency. Videos are encoded to H.264 (libx264) at CRF 18 (high quality) and mp4 format, preserving audio if copy_audio: true. blur_threads = 2 means it can parallelize blur processing. The presets define blur mask zones for common aspect ratios (portrait 9:16 and landscape 16:9) – these are coordinate rectangles where a static watermark might appear (for simpler blur overlay). The active_preset is used for the blur tool on the UI (not the advanced watermark remover, but a quick blur option). There is also an auto_watermark sub-config (disabled by default) that could automatically detect watermark position with given parameters and blur it (this is separate from the full restoration algorithm).
The merge section defines how final merging of clips works: it will take all video files matching a pattern (e.g., *.mp4) in the merge source directory and concatenate them in groups of group_size (2 by default). This allows combining short clips into compilations of a given size. (The UI likely allows the user to adjust group size before merging.)
Chrome and Browser Settings:
chrome:
  cdp_port: 9222  
  binary: ''  
  profiles:
    - name: Default (Windows)
      user_data_dir: "%LOCALAPPDATA%/Google/Chrome/User Data"
      profile_directory: Default
    - name: Default (macOS)
      user_data_dir: "~/Library/Application Support/Google/Chrome"
      profile_directory: Default
    - name: Default (Linux)
      user_data_dir: "~/.config/google-chrome"
      profile_directory: Default
  active_profile: ''  
  user_data_dir: ''  
This config helps the app find Chrome. If binary is left blank, the app will try common paths for Chrome/Chromium/Edge (depending on OS). The profiles list provides default Chrome user-data locations for convenience. active_profile and user_data_dir can be set to choose a particular browser profile globally. Typically, Sora Suite launches Chrome with --remote-debugging-port=9222 (or the specified cdp_port) and attaches to it. Each session can also specify a different port or use the global one. The portable_config.py module in the original app reads these settings to locate Chrome and ensure the remote debugging endpoint is up. It will auto-launch Chrome if not already running on the specified port, using the provided user data dir and profile (to preserve cookies/login to Sora). For example, by default on Windows it might launch Chrome at port 9222 with the default Chrome profile; if a session’s chrome_profile corresponds to a different profile, it could launch another instance with a different user-data-dir and port.
Telegram Settings:
telegram:
  enabled: false  
  bot_token: ''  
  chat_id: ''  
(Plus other fields managed at runtime in the UI, like templates and quick delay – see below.) Enabling this and providing a Telegram bot API token and target chat ID allows the app to send Telegram messages. Additional template messages and scheduling can be configured from the UI (and are saved back into this config under telegram.templates, last_template, etc.). In Sora Suite, the Telegram bot is used to notify the user of important events (like completion or errors) and can send custom messages on demand.
Maintenance & UI Settings:
maintenance:
  auto_cleanup_on_start: false  
  retention_days:
    downloads: 7  
    blurred: 14  
    merged: 30  
ui:
  show_activity: true  
  accent_kind: info  
  activity_density: compact  
  show_context: true  
  custom_commands: []  
These control application maintenance tasks (auto-cleanup old files on startup; e.g., remove downloads older than 7 days, etc.) and UI preferences. The show_activity and show_context flags toggle the visibility of the right-side activity panel and context panel in the UI. activity_density can be "compact" or "comfortable" for log text spacing. accent_kind may set the color theme of UI accents. custom_commands is a list of user-defined quick commands (each with a name and script to run) that can appear in the UI for convenience (empty by default). The maintenance section is also where an “auto-update” would be configured (the app had a script for self-update), though it’s not explicitly shown in this snippet.
In addition to app_config.yaml, the app uses other files:
prompts.txt: The list of text prompts to send to Sora. By default, a single file prompts.txt (or profile-specific prompts_<profile>.txt) contains one prompt per line. For example, a prompts.txt might contain:
A funny windy scene in a city street.  
Dramatic sunset over a mountain lake.  
Each line is a separate request to generate a video. The application reads this file and will send each prompt in sequence (unless it’s been sent before, according to logs). Prompts can include newline characters or be very long; the app takes care to inject them correctly.
image_prompts.txt: The list of image generation prompts for GenAI. This file supports two formats per line: a simple string (treated as a prompt), or a JSON object with fields for more complex specs. For example, image_prompts.txt might contain:
{"prompt": "A futuristic city skyline at night", "count": 2, "video_prompt": "A science fiction city scene", "key": "sci-fi-city"}  
A serene landscape of rolling hills under a blue sky.  
In the first line (JSON), prompt gives the text for image generation, count specifies how many images to generate for that prompt, video_prompt associates these images with a specific video prompt (so that when that video prompt is sent to Sora, the images are attached), and key is an arbitrary identifier for this prompt spec. The second line is a plain prompt string (equivalent to {"prompt": "...", "count": 1} if interpreted) – with no specific video association, it might just generate one image for standalone use. The GenAI integration will parse this file into a list of ImagePromptSpec objects in code. Each spec can have multiple prompts (if the JSON has an array field "prompts" instead of a single prompt, meaning multiple variants for the same concept) and optionally a video_prompt to match with the text prompts.
selectors.yaml: CSS selectors and heuristics for the Playwright automation to interact with Sora’s web UI. For example, the YAML defines how to find the text input area, submit button, etc.:
textarea:
  css: "textarea, [contenteditable='true']"
textarea_alternatives:
  - "role=textbox"
  - "textarea[placeholder*='Describe']"
  - "[data-testid*='textbox']"
generate_button:
  css: "button:has(svg), [role='button']:has(svg)"
image_upload:
  css: "input[type='file']"
  trigger: "button:has-text('Upload'), button:has-text('Add media'), …"
  wait_for: "img, video"
  wait_timeout_ms: 8000
  clear: "[data-testid*='remove'], button:has-text('Remove media')"
error_toast:
  container: "[role='alert'], [data-testid*='toast'], .toast, .SnackbarRoot"
  text_contains:
    - "limit"
    - "queue"
    - "try again"
    - "подождите"
    - "слишком много"
    - "переполнена"
media_agreement:
  dialog: "[role='dialog']"
  checkboxes: "[role='dialog'] input[type='checkbox']"
  accept_btn: "[role='dialog'] button:has-text('Accept'), …"
queue_generating:
  css: "video:has(+ [data-state*='generating']), [data-status*='generating']"
queue_ready:
  css: "[data-status='ready'], video + div:has-text('Ready')"
This tells the automation script how to locate elements on the Sora site:
The textarea for entering prompts: find any <textarea> or contenteditable field (Sora’s input might be an editable div). Also alternative heuristics (placeholder text containing "Describe", etc.) in case the main selector fails.
The generate button: often a button with an SVG icon (perhaps a paper airplane icon). The selector tries to find any <button> containing an <svg> (the send icon) or any role=button with svg – essentially capturing the “send” button next to the prompt field.
Image upload: The input for file attachments is of type file. The script may need to first click a trigger (like an “Upload” or “Add media” button) to open the file picker. The YAML lists possible text or aria labels for that trigger. After uploading files, it waits for an <img> or <video> element to appear (preview of the attached media) within 8 seconds. The clear selector is for a UI element to remove an attached media (if reusing the same prompt area for multiple submissions, it may need to click "Remove media" to clear previous attachments).
Error toast: If Sora shows an error message (like “too many requests” or “please wait”), it likely appears in an alert/toast element. The YAML provides a container selector and keywords (including Russian phrases for “wait” or “too many” etc.) to detect if such a toast is present, indicating a rate limit or queue error.
Media agreement: If the first time using media upload triggers a dialog (e.g., asking the user to agree to terms or confirm something), the script will look for a dialog and any checkboxes inside to tick them, then click the Accept/OK button. This ensures the automation isn’t stuck behind a modal the first time it attaches an image.
Queue status: The site might show generating videos in a queue. The selectors queue_generating and queue_ready are used to count how many videos are currently in progress or ready. For example, Sora could label video elements or have a status text “Generating…” on thumbnails. These CSS patterns help the script decide if a new prompt was successfully added to the queue (the count of ready/generating items increases).
submitted.log & failed.log: These are plain text log files that record prompt submissions. Each entry is one line with tab-separated fields:
submitted.log entries format:
YYYY-MM-DD hh:mm:ss<TAB>SessionName<TAB>prompt_key<TAB>prompt_text [media: file1.png, file2.png]
Example:
2025-11-22 12:00:00	Session 1	cat-sun	A realistic street camera video showing an old lady racing a delivery scooter and winning. [media: cat.png]
This indicates on Nov 22, 2025 at noon, Session 1 submitted a prompt (the prompt_key was “cat-sun”, and the full prompt text is given). It also notes that an image file cat.png was attached as media. The prompt_key is typically either a user-defined key from the prompt text or, if none, a sanitized version of the prompt itself or an auto-assigned identifier. The key is used to avoid resubmitting the same prompt twice – the system collects all keys from the log on startup.
failed.log entries format:
YYYY-MM-DD hh:mm:ss<TAB>SessionName<TAB>prompt_key<TAB>prompt_text<TAB>failure_reason
Example:
2025-11-22 12:05:30	Session 1	cat-sun	A realistic street camera video showing an old lady racing…	queue-limit/backoff-180s
This would log that the prompt “cat-sun” failed to submit at that time because the Sora queue was full (hence queue-limit/backoff-180s indicating the app encountered a queue limit and is backing off for 180 seconds). Subsequent retries are also logged; if a prompt fails multiple times, reasons might be prefixed with retry: in the log. For instance, a second failure might appear with reason retry:queue-limit/backoff-180s. These logs help identify which prompts didn’t go through and why.
manifest.json: Located in the generated_images/ directory, this JSON file keeps track of all images generated via GenAI. It is an array of entries, each entry linking image files to prompts. An example snippet from manifest.json:
[
  {
    "spec_index": 0,
    "key": "sci-fi-city",
    "prompts": ["A futuristic city skyline at night"],
    "video_prompt": "A science fiction city scene",
    "files": [
      "/path/to/generated_images/001-01.jpg",
      "/path/to/generated_images/001-02.jpg"
    ],
    "generated_at": 1700641234.123
  },
  {
    "spec_index": 1,
    "key": "",
    "prompts": ["A serene landscape of rolling hills under a blue sky."],
    "video_prompt": "",
    "files": [
      "/path/to/generated_images/002-01.jpg"
    ],
    "generated_at": 1700641250.456
  }
]
Each object includes:
spec_index: the line number (0-based) in image_prompts.txt that this entry corresponds to. (In this example, the first JSON prompt was at index 0, the second line at index 1).
key: the identifier if provided (otherwise empty). In entry 0, the key sci-fi-city ties these images to any video prompt that references that key.
prompts: an array of the image prompt(s) used. (In entry 0, there was one prompt string. If multiple variants were used for that spec, they’d all be listed.)
video_prompt: if the spec was linked to a specific video prompt, it’s recorded here. (Entry 0’s images relate to the video prompt “A science fiction city scene”; entry 1 has no video_prompt, as it was a standalone image prompt).
files: a list of file paths of the generated images for this spec. The app saves images with a naming pattern that includes the spec index and prompt index (e.g., 001-01.jpg = spec 1, first prompt variant’s image).
generated_at: a timestamp (epoch time in seconds) when the images were generated. The manifest is sorted by spec index and time so that the latest images for a given prompt spec can be retrieved easily.
titles.txt and titles.cursor: titles.txt contains a list of titles (one per line) to be used when renaming downloaded videos. For example:
Cat Wins The Race  
Doorbell Prank  
Windy City Surprise  
The downloader will take each new video and name it using the next title from this list. It maintains a pointer to the next title in a sidecar file (same base name with .cursor). The titles.cursor file simply stores the index of the next title to use. For instance, if 3 videos have been downloaded and named, the cursor might contain “3” to indicate the next video gets the 4th title. The app automatically increments this as it assigns names. If it reaches end of the list or the title is empty, it will default to numeric naming.
history.jsonl: A log of high-level events in JSON Lines format. Each line is a JSON object representing an event or action in the app (used for the global process log or history page). Examples of events might include:
{"event": "chrome_launch", "port": 9222, "profile": "", "shadow": "/path/to/shadow/profile", "ts": 1760483172}
{"event": "scenario_start", "steps": ["autogen"], "ts": 1760483178}
{"event": "autogen_finish", "rc": 0, "ts": 1760483179}
Here, a Chrome instance was launched on port 9222 (with maybe a temporary “shadow” profile directory for an empty profile), then a pipeline scenario started (with only the autogen step), and finished successfully (rc:0). The ts is a timestamp. This file is appended to over time and is used to reconstruct a timeline of operations for the user to review or for debugging.
Chrome Sessions (Workspaces) and Launching Mechanism
Workspaces (Sessions): Sora Suite supports managing multiple Chrome sessions in parallel. Each session is essentially a workspace with its own Chrome browser context, prompt files, and output areas. In the UI, the “🗂️ Workspaces” page lists all sessions defined in app_config.yaml. For each session, the user can see its name, the associated Chrome profile (if any), and status indicators. There are controls to quickly perform actions per session:
Launch Chrome – opens a Chrome window for that session. Under the hood, the app uses the portable_config logic to either connect to an existing Chrome DevTools endpoint or launch a new Chrome process with the specified user data directory and profile. For example, if Session 1 has cdp_port: 9222 and chrome_profile blank (meaning use default), clicking “Launch Chrome” will call ensure_cdp_endpoint which:
Checks if a Chrome instance is already listening on 127.0.0.1:9222. If not, it constructs the launch command.
Determines the user-data directory: if the session’s chrome_profile name matches one in the config profiles list, it uses that path; otherwise it falls back to the global chrome.user_data_dir or the OS default path for Chrome. It will ensure that directory exists and then launch Chrome with --remote-debugging-port=9222, --user-data-dir=<...>, --profile-directory=<profile name> (e.g. “Default”).
Additional flags like --disable-features=AutomationControlled (to reduce detection of automation), --no-first-run, etc., are included. On Windows it launches detached, on macOS/Linux it suppresses output.
The function then waits up to ~15 seconds for the port to open. If successful, it returns the endpoint URL (e.g. http://127.0.0.1:9222). If it times out, it reports an error.
Each session could specify a different cdp_port – if the user wants to run multiple Chromes concurrently, they must use distinct ports. The config’s default has only one port, so by default sessions might reuse the same browser instance sequentially. However, advanced users can clone the session entry with different ports to truly parallelize.
Session Prompt Automation – a button (often labeled “⚡ Prompts” or similar in the UI) that starts the autogen worker for that session, sending all prompts. When clicked, the app will spawn the workers/autogen/main.py script with environment variables pointing to that session’s data:
SORA_PROMPTS_FILE set to the session’s prompt file (or the default prompts.txt if not customized).
SORA_IMAGE_PROMPTS_FILE for the image prompts file.
SORA_SUBMITTED_LOG and SORA_FAILED_LOG pointing to that session’s log files (so the worker writes to session-specific logs).
SORA_CDP_ENDPOINT set to the Chrome DevTools URL (e.g., http://localhost:9222) for that session’s Chrome.
SORA_INSTANCE_NAME set to the session’s name (used to label log entries with the session).
GenAI API settings like GENAI_ENABLED, GENAI_API_KEY, etc., are also passed via environment (populated from app_config’s google_genai section). This allows the autogen worker to know if image generation is enabled and what parameters to use.
The session entry in the UI will show the progress (number of prompts sent, any errors) in real-time. In the right-hand context panel, details about the “active session” are displayed: which profile is active, which prompt file is in use, and the state of the last job (idle, running, etc.).
Session Image Generation – a button to generate images (using GenAI) for that session’s image prompts. This likely runs the autogen script in a special mode (by setting IMAGES_ONLY=1 environment variable or similar) to only do the image generation part. The UI might label this “🖼️ Images”. When run, it will produce images in generated_images/ and update manifest.json, but not submit any prompts to Sora yet. The user can review the images, then proceed with autogen (which will attach them).
Session Download – a button to auto-download videos for that session. This invokes the downloader script (download_all.py) for that session’s Chrome. It will connect to the same Chrome instance (via the DevTools port) and begin downloading drafts (explained in detail in the next section). The session config can have an override for max_videos (number of videos to fetch) or it will use the global downloader.max_videos. In the UI, the user can specify, for example, “Download last 30 videos” by setting a limit before clicking the button. The app then launches the downloader with environment variables: CDP_ENDPOINT (the Chrome’s debugging URL), DOWNLOAD_DIR (destination directory, typically session-specific as we will note), TITLES_FILE (which may be session-specific, see below), TITLES_CURSOR_FILE, and MAX_VIDEOS (the limit). Each session can have its own download directory: by default, the app arranges downloads into subfolders per session (e.g., downloads/sessions/Session_1/). In code, _session_download_dir(session, ensure=True) computes this path (downloads_dir/sessions/<session-slug>). Similarly, each session can use its own titles.txt – if not explicitly set, the app will derive a session-specific titles file (copying the global one or naming it titles_<session>.txt). This prevents different sessions from consuming each other’s titles if downloads happen in parallel. If the session’s name is "Session 1", the first video might be named "Cat Wins The Race.mp4", the second "Doorbell Prank.mp4", etc., as per its titles list.
Session Watermark Removal – a button (🧼) to clean the watermark from that session’s downloaded videos. When clicked, the watermark_cleaner restore.py script is executed for that session. Environment variables passed include:
WMR_SOURCE_DIR = that session’s download folder (or a user-specified folder of videos).
WMR_OUTPUT_DIR = an output folder for clean videos (the config’s watermark_cleaner.output_dir usually, but the app may isolate by session: e.g., restored/Session_1/).
WMR_TEMPLATE = path to the watermark image (the app uses watermark.png by default).
All the numeric parameters (thresholds, scales, etc. from config) as WMR_THRESHOLD, WMR_FRAMES, etc.
WMR_FULL_SCAN = “1” or “0” depending on whether full frame scanning is desired (tied to the UI setting “full scan each frame”). By default, if not set, the script assumed full scan is enabled (scans entire video) unless explicitly turned off.
The UI will show progress for each file being processed. Each session’s card has its own log of these actions (either shown on the card or in the session logs page). On completion, the cleaned videos (with watermark removed) are saved in the output directory.
Other session data: The UI might allow adding notes per session (for example, to remember what that profile is for or any manual steps). This is stored in the session config (notes field). Also, the UI context panel for a session shows things like the currently selected prompt file path, the “RAW” directory (downloads), “BLR” directory (blurred or cleaned videos), “MRG” directory (merged videos), etc., often with quick-open buttons labeled “PRJ, RAW, BLR, MRG, IMG, RST” (Project, Raw downloads, Blurred, Merged, Generated Images, ReStored (watermark removed)). These open the respective folders in the OS file explorer for convenience.
Session State and Parallelism: Each session in the app maintains its own state including whether an active_task is running. The app ensures tasks like autogen, download, etc., don’t overlap in the same session (to avoid conflicts). However, different sessions can run different tasks simultaneously on their separate Chrome instances. The underlying design uses a small state machine: when a session task is launched, the session’s active_task might be set (e.g., "autogen_prompts", "download", etc.), and when it finishes, it clears. The automator (see below) uses this state to coordinate sequential steps.
Session Example: To illustrate, imagine Session 1 corresponds to the user’s main Sora account, using the default Chrome profile. It has id: "default", name: "Session 1", prompt_profile: "__general__" meaning it uses prompts.txt for prompts. When the app starts, Session 1 is listed with an “idle” status. The user clicks “Launch Chrome” for Session 1 – the app finds Chrome (using the default path since binary was empty and a default profile path for Windows, etc.), opens it with remote debugging on port 9222, and navigates automatically to Sora’s drafts page (the autogen script will do this if needed). Now Session 1’s card might show Chrome as “online” (maybe a green indicator). Next, the user clicks “Generate Images” – the app uses the Google API key in config to generate images from image_prompts.txt. Suppose image_prompts.txt had 5 entries with various prompts; the worker generates images and saves them, updating manifest.json with keys and file paths. Then the user clicks “Autogen Prompts” – now the autogen worker reads prompts.txt which contains, say, 10 prompts. It also reads manifest.json and image_prompts.txt to see if any image should be attached to any prompt:
For each prompt entry, if there’s a matching video_prompt or key in the image specs, it will attach those images. For example, if prompt #3 in prompts.txt was “A science fiction city scene” and the manifest (from earlier image generation) has images for key sci-fi-city associated with that video_prompt, the autogen will attach those images when submitting that prompt.
The autogen submits each prompt in turn through the Session 1’s Chrome, with delays and retries as needed (detailed next). As it runs, the UI logs each submitted prompt (and any errors). Let’s say prompt #3 hits a queue limit – Sora returns a “Please wait, too many requests” message. The autogen script detects this error toast and logs a warning. In failed.log you’d see an entry for that prompt: reason queue-limit/backoff-180s. The script will then wait 180 seconds (as configured) before retrying. During that time, the UI might show Session 1 as waiting/backing off. After 3 minutes, it tries again, presumably succeeds if a slot freed up, and logs it as submitted. (If it failed again, it would log retry:queue-limit/backoff-180s and wait again.)
Once all prompts are submitted (or marked failed after some attempts), the autogen script exits. Session 1 now has perhaps some prompts in Sora’s generation queue. The user can then click “Download” for Session 1, requesting (for example) the last 10 videos. The downloader script in Session 1’s context scrolls through the drafts feed and downloads videos one by one, naming them using titles.txt. If it reaches a title that’s an empty string or runs out of titles, it will fallback to numeric naming (e.g., “7.mp4”, “8.mp4”, etc.). The UI shows each file downloaded. If the Sora feed had less videos than requested (say only 8 new videos but user asked for 10), the script will stop after 8 and could log a warning like “Not enough new videos – downloaded 8 out of 10 requested”.
Finally, the user clicks Session 1’s “Watermark Removal” button. The watermark cleaner loads watermark.png (which should be an image of Sora’s logo or watermark pattern). It then processes each video in downloads/sessions/Session_1/:
It scans ~120 frames per video (or full video if configured) for the watermark. It might print status messages like [WMR] Processing video1.mp4 and for every 10 frames: [WMR] Processing video1.mp4: frame 11/120 etc.
For each detected watermark location, it finds donor frames around it. If at frame 50 a watermark is at position (x,y,width,height), it will check frames 49,51,48,52… up to 12 frames away (search_span) for frames where either the watermark isn’t detected or if detected, the region doesn’t overlap (IoU < 0.25). It collects up to 4 such frames. Then it extracts those patches of the same region and computes a median patch (pixel-wise median). The median patch effectively averages out any moving content, ideally leaving just background.
It then uses OpenCV seamlessClone to paste this patch into the original frame. If blend=normal, it uses NORMAL_CLONE mode (preserving colors of the patch). If blend=mixed, it uses MIXED_CLONE to adapt to surroundings. If seamlessClone fails (perhaps due to mask issues), it falls back to inpainting that region.
This is done for each frame that had a watermark. Many frames might have it in slightly different positions (if the watermark bounces around). The algorithm effectively erases the watermark by replacing each occurrence with background from other frames where that spot was visible.
The result frames are then written to a new video file in restored/Session_1/ using the same frame rate and resolution as original. The output videos have the same filenames as input (so “video1.mp4” becomes “video1.mp4” in restored/Session_1).
Summary logs are printed: e.g., after processing 8 videos, it might log {"event":"watermark_restore","processed":8,"errors":0,...} in JSON (which goes to history.jsonl), and a final status like [WMR] Completed: 8 files, 32.5s in plaintext.
At this point, Session 1’s pipeline is done: prompts were generated, videos downloaded, watermark removed. The user can review the final videos or proceed to merging them (if they want to create compilations) or uploading (original app could auto-upload to YouTube/TikTok if configured, but in the new app this step is not applicable). Session Isolation: Each session has its own folder structure to avoid collisions:
Raw downloads: by default downloads/sessions/<SessionName>/ (the code slugifies the session name for the folder). This prevents two sessions from writing into the same downloads directory if they run concurrently.
Blurred videos: (if using quick blur instead of watermark removal) could similarly be under blurred/sessions/<SessionName>/ (though the config didn’t explicitly show that, it’s likely managed similarly to downloads).
Restored videos: restored/<SessionName>/.
Merged videos: If the user merges videos, it could place results in merged/ or a subfolder of it (some users might merge across sessions though, so that might remain global).
The UI also allows viewing Session Logs: a dedicated page where you can select a session and see snippets of its submitted.log, failed.log, and maybe a “download.log”. In practice, the downloader didn’t write a separate log file but printed to console; the app captures those prints (prefixed with [✓] or [!] etc.) and may show them in the UI under “Download Log”. For example, after a download run, the session’s download log area might display:
[✓] Downloaded: Cat Wins The Race.mp4  
[✓] Downloaded: Doorbell Prank.mp4  
[!] Card already seen, cannot scroll further — stop.  
These lines come from download_all.py prints (where [✓] indicates success, [!] warnings). The session logs page thus gives a quick overview of what was sent (submitted log), what failed (failed log), and what was downloaded, per workspace.
Prompt Automation Logic (Autogen via Playwright)
One of Sora Suite’s core functions is Autogen, which automatically feeds prompts (and images) into the Sora web app. The logic is implemented in workers/autogen/main.py (a Python script using Playwright). When triggered (for a specific session), it performs the following steps:
Connecting to Chrome (CDP): The autogen script starts by attaching to the running Chrome instance via the DevTools Protocol. It uses chromium.connect_over_cdp(endpoint_url) where endpoint_url is typically http://localhost:9222 (or the session’s port). Once connected, it obtains a browser context. It tries to find an existing page with "sora" in the URL (drafts page); if not found, it opens a new page and navigates to the Sora drafts URL (https://sora.chatgpt.com/drafts as per config). It waits for the page to load DOM content. At this point, the Chrome browser (which the user may also see) is ready on the drafts listing, or possibly a specific draft page if left open.
Preparing UI Elements: Using the selectors from selectors.yaml, the script locates the prompt input and the send button. It calls resolve_textarea(page, selectors) which tries the given CSS and alternative selectors to find the prompt text box (this function likely returns whether to use CSS or role selectors). If found, it then retrieves a handle to that element. Next, it tries to find the submit (generate) button adjacent to that text area: find_button_in_same_container(page, ta_handle) scans the DOM hierarchy around the text box to find a button with an SVG icon that is near the text area. The code employs heuristics: it filters out buttons that are too large or too far away, ignores any “+” add-media buttons, and tries to pick the rightmost suitable button (often the send arrow). If it fails to find via this method, it falls back to the generate_button.css selector from YAML (which might directly target a known button CSS). If still not found, it throws a timeout error (“Failed to find submit button”). Once the textarea and submit button are found, the script is ready to send prompts.
Loading Prompts and State: The script reads all prompt lines from the designated prompts.txt. It filters out any that have been sent before by loading the submitted.log: load_submitted() parses the log and collects all keys (if a line has at least 3 tab-separated parts, it uses the third part as the key, otherwise the prompt text itself). This set already is used to skip prompts that are done. Then it creates a queue (deque) of PromptEntry objects for each new prompt. (A PromptEntry encapsulates the prompt text, an optional key, and placeholders for attached images, etc.) If images are enabled, it also reads the image_prompts.txt and the manifest.json. It matches image prompts to the text prompts: essentially, if an ImagePromptSpec has a key or video_prompt matching a text prompt, it links them. The script then “hydrates” prompt entries with any images available:
apply_image_prompt_specs(prompts, specs) – likely tags PromptEntry objects with references to image prompts by key.
hydrate_entries_from_manifest(mapping, specs, manifest) – fills in the PromptEntry with actual image file paths if the manifest has generated images for that prompt’s key.
After this, each PromptEntry might have attachment_paths list populated (e.g., ["/path/to/image1.jpg", "/path/to/image2.jpg"] if images are to be attached).
Prompt Submission Loop: The main loop (run_loop) begins by printing a [NOTIFY] AUTOGEN_START message (which the UI listens for to indicate the process started). It optionally auto-accepts any media usage dialog by calling maybe_accept_media_agreement (ticking checkboxes and clicking Accept if such a dialog exists). Then for each prompt in the queue:
It calls submit_prompt_once(...) for that prompt, which attempts to enter the text, attach images, and click send.
Typing and Attachment: Before clicking send, the script must populate the text field:
It focuses the textarea and tries to clear any existing text (locator.fill("")).
It obtains a handle to the element and calls js_inject_text(page, element_handle, text). This is a small JavaScript snippet injected into the page that sets the element’s value or innerText directly and triggers input/change events to mimic a real user paste. This approach ensures even multi-line prompts or special characters are inserted reliably (sometimes Playwright’s .type() might not handle newlines well). The code covers <textarea> and contenteditable differently (setting .value vs .innerText) and fires events so the webapp knows text was entered.
To further simulate human typing (and likely to ensure any internal state updates), after injection it sends a couple of keystrokes: it types a space then a backspace, a period then backspace, with small delays. This trick can trigger the webapp’s input handlers (some apps only enable the send button after a keypress event, etc.). After this, the prompt text is in place. It logs to console “[i] prompt typed into field.” in debug mode.
If the prompt requires image attachments (entry.attachment_paths is non-empty), the function prepare_media = ensure_media is passed in. This ensure_media will:
Gather the actual image files (ensuring they exist) via gather_media(entry, genai_client, manifest). This can resolve relative paths or directories of images into a list of file Paths. If the entry’s spec had a directory, it will include all images in it. If images are missing and GenAI is enabled, it might even trigger image generation on the fly (though typically images should be generated beforehand).
Then call upload_images(page, selectors, files) to attach them. upload_images uses the YAML image_upload config: it first clears any old attachments (clicking any “remove” buttons if present). Then it clicks the trigger (e.g., the “+” or “Upload” button) to open the file input. It tries a few selectors to find an <input type="file"> (with accept filters for images). When found, it sets the files into it (equivalent to selecting files in the file picker). After uploading, it waits for a short time or until an <img> appears (as confirmation the image preview is loaded). If anything fails, it logs a warning. The function returns True if images attached successfully. If ensure_media returns False (e.g., image file missing or upload field not found), the submission for this prompt will be skipped with reason "media-upload" recorded as failure.
Clicking the Send Button: Once the text is in place and media (if any) attached, the script is ready to hit send. But it must ensure the send button is enabled. It captures the count of queue items before clicking (q_before = queue_count_snapshot(page, selectors)) which uses the queue_generating and queue_ready selectors to count current videos in the generation queue (generating or ready). Then it waits until the send button element is enabled (is_button_enabled_handle(btn_handle) checks that it’s not disabled). This avoids clicking too early. When enabled, it does btn_handle.click() on the arrow button. Confirmation & Error Handling: After clicking, it calls confirm_start_strict(...), which polls for either:
The prompt field becoming empty (meaning the Sora webapp accepted the input and cleared the field for a new prompt), or
The queue count increasing (meaning a new video was added to the generating list), or
An error toast appearing. It does this loop for up to start_confirmation_timeout_ms (e.g. 8000 ms).
If confirm_start_strict returns True, it means the prompt was accepted by the UI. The script logs "[OK] accepted by UI." and returns success for that prompt. It then calls mark_submitted(entry, attachments) which appends a line to submitted.log with timestamp, session name, prompt key/text, and attached filenames.
If confirm_start_strict sees that the textarea is still filled and no new queue item appeared, it means the submission did not go through within the timeout. The code then checks error_toast_present(page, selectors) – which looks for any alert with text like “limit” or “try again”. If an error toast is found, it interprets it as a queue capacity issue. In that case, it sets reason = "queue-limit/backoff-180s" (if backoff_seconds_on_reject is 180) and logs "[RETRY] queue-limit/backoff-180s". It then sleeps for 180 seconds (backoff) and returns False for this attempt, with reason indicating the need to retry after backoff. If no explicit error toast, the code assumes the slot was locked (maybe the queue was full but no toast, or the site didn’t clear input due to some other minor issue). It prints "[RETRY] slot-locked" and enters a loop to keep retrying the click every couple seconds until something changes or an error appears. Essentially:
While not accepted: if images need re-upload (in case the UI dropped them due to re-click, it re-calls ensure_media), wait for button enabled, click again, and check confirm_start. If at any point an error toast shows up, it treats it like above (queue-limit with backoff).
This loop prevents the script from giving up immediately if the first click didn’t register. It only exits when either the prompt is accepted or an error triggers a backoff.
During retries, the script prints relevant messages. These console outputs end up in the UI’s process log. For instance, an attempt might produce:
[WARN] prompt not accepted yet: slot-locked  
[RETRY] queue-limit/backoff-180s  
indicating the first click did nothing (slot locked), then on retry an error told it to back off. The UI’s “activity” feed will show something like “Autogen: prompt X – temporarily refused (queue full, waiting 180s)” – based on these log lines. After waiting, the prompt will be put back in the retry queue.
Retry Queue Processing: Prompts that failed to send on first try (returned False from submit_prompt_once) are collected with their reason. The main loop above continues with the next prompt in the queue. After one pass, any failed prompts are in a retry_queue. The script then enters a retry cycle loop. For each cycle, it takes all prompts in retry_queue and attempts them again (in the same order). Each retry attempt again uses submit_prompt_once. If it succeeds this time, it logs as OK and marks submitted. If it fails again, it logs "[WARN] still refused: reason" and appends the prompt back for another cycle (but note it prefixes reason with retry: in failed log via mark_failed). Between cycles it waits 20 seconds (besides any internal backoffs). This will continue indefinitely until either all retries succeed or until a stop condition (the script doesn’t have a hard limit on retry cycles in the code shown, it will loop potentially forever if the site keeps refusing and not erroring out fully). In practice, the queue-limit might clear after some time or the daily limit might hit (in which case the site might keep refusing). The script outputs periodic status: e.g. “Retry cycle #2, remaining: 1 prompt…”. The UI would show these as well.
Image-Only Mode: If the autogen is invoked in images-only mode (no prompt submission, just generate images), the script handles that at the start: it checks an IMAGES_ONLY flag. If true, and GenAI is enabled with an API key:
It prints “Launching image generation without Sora…”.
Calls generate_images_batch(specs, client, manifest) which iterates through each ImagePromptSpec:
For each spec (line) that has at least one prompt, it will generate count images per prompt variant. For example, if prompts=["prompt1","prompt2"] and count=1, it will generate one image for prompt1 and one for prompt2 (with tags like 001-01 and 001-02 if spec index is 0). The GenAiClient handles the actual API calls (upload prompt to Google, download the image). Each returned image file path is collected.
If images were successfully generated for that spec, it calls manifest.record(spec_index, key, prompts, video_prompt, files) to add/update the manifest. If an entry for that spec or key existed, it replaces it with the new one (so the manifest always has the latest images for a given prompt spec). It prints a warning if none were obtained for a spec.
After all specs, it logs how many images were saved: e.g., "[OK] Saved 3 images -> ./generated_images" and prints a [NOTIFY] IMAGE_AUTOGEN_FINISH_OK (or _EMPTY if none). The UI listens for this to show a completion status for image generation.
Then the script exits (without doing any prompt submissions).
If not in images-only mode, the script proceeds to do the normal prompt loop as above, optionally with attachments if attach_to_sora was true and images exist for prompts.
Quota Management: The script has a QuotaTracker for GenAI usage (as part of GenAiClient). If daily_quota is set (e.g., 100 prompts/day) and quota_enforce: true, the client will check before each generation or each prompt submit:
It logs warnings if the remaining quota is low: e.g., “Only 5 prompts left before daily quota”.
If quota exceeded, it logs a warning and (if enforcement on) will skip image generation or prompts (though in the code we saw, it primarily tracks usage for image generation calls).
The usage file (usage.json) stores how many prompts used per model per day. This ensures the app doesn’t unexpectedly exceed API limits – notifications are sent in the UI if enabled (notifications_enabled: true).
Wrap Up: When the prompt queue (and retries) are fully processed, the script prints a final statistic line:
[STAT] success=X failed=Y elapsed=ZZs
and then a notification event: either [NOTIFY] AUTOGEN_FINISH_OK if all prompts eventually went through, or [NOTIFY] AUTOGEN_FINISH_PARTIAL if some never succeeded. The UI uses this to display a completion status (e.g., “Prompts insertion complete” or “completed with some failures”). If it was aborted by the user, the process might be terminated externally, but otherwise it runs to completion.
Autogen Error/Limit Example: Suppose during autogen, the Sora service imposes a limit of 2 concurrent video generations. If the user submits prompts too quickly, by the 3rd prompt the site might refuse. The autogen console/log might look like:
[STEP] 3/10 — submitting…  
[RETRY] queue-limit/backoff-180s  
[WARN] not accepted yet: queue-limit/backoff-180s  
The first line indicates it’s on prompt 3 of 10. Upon clicking send, it got a response indicating the queue is full, so it logs a [RETRY] with the reason and waits. The next [WARN] line (if present) might be the mark_failed logging saying it didn’t send (yet) due to that reason. In the UI, the user might see a small message on that prompt like “Deferred (queue limit, retrying in 180s)”. The prompt is moved to retry. After 180s, autogen will try prompt 3 again. If successful, it logs [OK] accepted by UI. and continues. If not and the limit persists, it will again log a retry. These log statements help the user understand that the system is handling Sora’s limits by pausing and reattempting rather than just failing outright. Submission Logging: Each successful submission is added to submitted.log immediately when accepted. Each failed attempt is added to failed.log right when the attempt fails (before retry). Therefore, if a prompt eventually succeeds on a second try, it will appear in both logs (first in failed with reason, then in submitted when it went through). The presence of that prompt’s key in submitted.log ensures it won’t be re-sent next run. Multi-Session Handling: Each session runs its own autogen process (if launched). They connect to different Chrome ports. They operate independently but the main UI can coordinate (for example, the Automator might trigger autogen on two sessions one after the other, not truly simultaneously in the original design, to avoid the heavy load of simultaneous runs, though parallel runs are possible if user starts them manually).
Image Generation with GenAI (Google AI Studio Integration)
Sora Suite integrates with Google’s Imagen GenAI API to allow generating images from text prompts and using those images in video generation. This feature is configured via the google_genai section in YAML and operates through the autogen worker and a GenAI client library. Setup: The user must enter a valid API key for Google AI Studio and select a model (default "models/imagen-4.0-generate-001"). Other options like aspect ratio (e.g. "1:1", "16:9"), image resolution (they used shorthand like "1K" meaning 1024x1024 perhaps), number of images per prompt, etc., are set in settings. The UI provides a page Settings → Image Generation where these can be filled in. For example, the user might set aspect "16:9" and image_size "2K" if they want widescreen images at 2048px height (assuming the client interprets that). They can also provide stylistic hints: “person_generation” (maybe a special setting for faces), lens type, color palette, or a reference prompt to guide style. Additionally, they can input a list of seeds (to control randomness), or enable "consistent character design" if the model supports it (ensuring the same character looks similar across images). Quota notifications can be toggled so the app warns “you have X prompts left today” as described. image_prompts.txt usage: The user composes prompts for images in the Content → Image Prompts editor. They can mix plain lines and JSON specs. Some examples:
Plain line:
A majestic dragon flying over a medieval city.
This would be treated as one prompt, generate the default number of images (e.g., 1).
JSON line:
{
  "prompts": ["A futuristic robot in a neon-lit alley", "A cyberpunk city skyline at dusk"],
  "count": 1,
  "video_prompt": "Futuristic robot scene",
  "key": "robot-cyberpunk"
}
This instructs: for spec index N, generate 1 image for each of the two prompts in the list. It ties these images to the video prompt "Futuristic robot scene" and labels them with key "robot-cyberpunk". In practice, this means two images will be made: one for the robot, one for the city skyline. Both will be associated with that video prompt/key, so when the autogen sends "Futuristic robot scene" to Sora, both images will be attached (Sora likely supports multiple image inputs for a single prompt). If count were 2, it would generate 2 images for each sub-prompt variant, potentially giving 4 images in total for that spec (with names like 00N-01.jpg, 00N-02.jpg for first variant, 00N-03.jpg, 00N-04.jpg for second).
GenAiClient: The autogen’s GenAiClient is configured with the API key and model details from environment (set from YAML via GenAiConfig.from_env()). When client.generate(prompt, count, tag) is called, it interacts with Google’s API:
It likely sends the prompt along with parameters (size, aspect, etc.).
If rate_limit_per_minute is set, the client ensures not to exceed that (sleeping if necessary between calls).
It will retry up to max_retries times if the API call fails.
The images returned (probably as binary data or URLs) are saved to files in output_dir (which is ./generated_images by default). The tag parameter (like "003-01") is often used to construct the filename. The code doesn’t show the implementation, but we can infer that if tag = "003-01", it might save as 003-01.jpg (with an appropriate extension based on output_mime_type which is image/jpeg by default).
The client also updates the usage_file (usage.json) by incrementing how many prompts were used today for this model.
Manifest maintenance: As images are generated, the script calls manifest.record(...). This function will:
Remove any existing entry with the same spec_index or same key (so outdated images are cleared).
Create a new entry with all prompts (some specs may include multiple prompt strings), video_prompt (if any), and the list of file paths, plus a timestamp.
Save the manifest.json (sorted by spec_index and generated_at) in a human-readable indented form.
This manifest allows the app to later retrieve relevant images for a given video prompt. The matching logic works like:
If a prompt entry has a key in the manifest that matches, use those images.
If not but the manifest has an entry for spec_index matching the prompt’s image spec reference, use that.
If none, and attach_to_sora is true, it might still try to call GenAI on-the-fly for that prompt (though this is not explicitly described in the UI, it could be a feature to generate missing images just in time, but typically users would pre-generate to curate the images).
Example Flow: The user writes a script for a video, e.g., prompt: "A day in the life of a medieval blacksmith." They also think an image of a blacksmith’s forge would help the AI, so in image_prompts.txt they add:
{"prompt": "a medieval blacksmith working at a forge, flames and sparks", "count": 1, "video_prompt": "A day in the life of a medieval blacksmith.", "key": "blacksmith-forge"}
Then they go to Settings and ensure GenAI is enabled with their API key. They click “Generate Images (Google)” in the UI. The app runs image generation:
It sends the above prompt to Imagen, gets back, say, one image 001-01.jpg.
Saves it to generated_images/ and writes manifest.json:
{"spec_index": 0, "key": "blacksmith-forge", "prompts": ["a medieval blacksmith working at a forge, flames and sparks"], "video_prompt": "A day in the life of a medieval blacksmith.", "files": [".../generated_images/001-01.jpg"], "generated_at": 1760490000.0}
The UI might show a thumbnail of the generated image or at least indicate success.
Later, the user clicks “Save and Run Autogen (video)”. At this point, the autogen script pairs the image with the text prompt:
It reads prompts.txt which has "A day in the life of a medieval blacksmith." as one of the lines.
It sees in manifest that for video_prompt equal to that line, there’s an image file.
So when sending that prompt to Sora, it will call upload_images with that image file before hitting send. This means the Sora web interface will receive the prompt text and the attached forge image. This can significantly improve the video generation relevance (the image acts like a keyframe or style guide).
All images for prompts are stored in the generated_images directory, so the user can inspect them. They’re also accessible in the UI via the “IMG” quick folder button. The manifest allows reusing images on subsequent runs without regenerating (unless the user explicitly regenerates, which will update the manifest). If attach_to_sora were false, the script would ignore attaching images to prompts – but typically we keep it true to use this feature. Daily Quota Example: If the user set a daily quota (say 50 images) and uses up 48, the next usage might print: [WARN] Only 2 prompts left before daily quota for model imagen-4.0.. If they attempt beyond 50 and quota_enforce is true, the GenAI client may refuse to generate and log something like: [WARN] Daily quota for model imagen-4.0 is exhausted. and skip generation (the UI can alert the user). The notifications_enabled: true means the app would send these warnings possibly as Telegram or desktop notifications as well, not just console log.
Automated Video Downloading (Drafts Scraping)
After generating videos in Sora, Sora Suite can automatically download them. This function is handled by workers/downloader/download_all.py. It uses Playwright to navigate the Sora web interface (already logged in via the Chrome session) and systematically save video files. Key points of the download logic:
Attaching to Browser: The script attaches to the existing Chrome session via play.chromium.connect_over_cdp(CDP_ENDPOINT). CDP_ENDPOINT is set to the session’s DevTools address (like http://127.0.0.1:9222). This yields a Browser object. It then obtains the browser context (the first context if any, else creates a new one). It then either opens the drafts page or uses an existing page:
If env OPEN_DRAFTS_FIRST is true (default is "1"/true), it will call open_drafts_page(context). This function ensures a page is at the drafts listing: it either navigates the current page to https://sora.chatgpt.com/drafts or opens a new one if none exist. It waits for network idle to ensure content is loaded.
If OPEN_DRAFTS_FIRST is false, it will instead try to use the current open page (maybe the last opened card).
It prints "[i] Working in existing window: {page.url}" to indicate which page it’s using (and also bring it to front).
Finding the First Draft Card: The script must enter the feed scroll mode (like TikTok style, one video at a time). If the current page is already a draft (single video view), fine. If it’s the list of all drafts, it needs to click the first item. It calls _open_first_card_on_page(page):
It looks for elements matching CARD_LINKS = "a[href*='/d/']" (anchor tags linking to a draft ID, which presumably are the thumbnails on the drafts page).
Waits up to 15s for at least one to be visible.
If none visible and allow_reload is true, it reloads the page once and tries again.
Once visible, it moves the mouse to the first card (perhaps to make any hover UI appear), then clicks it.
Waits for the URL to change to a pattern containing /d/ (the draft detail page) – i.e., the video viewer.
It then waits for the right-hand panel (controls) to appear (meaning the video player and menu loaded).
If anything fails (timeout or no card), it returns False.
After this, ideally, the page is now showing the first video in the drafts feed.
Download Current Video: The script defines two helper actions:
open_kebab_menu(page): This finds the “kebab” menu (three dots) on the right panel. The selector is KEBAB_IN_RIGHT_PANEL = "div.absolute.right-0.top-0 button[aria-haspopup='menu']:not([aria-label='Settings'])". It waits for that to be visible (the three-dot button that opens the menu with download option), moves the mouse over it (for reliability), and clicks it. Then waits for the menu to appear (MENU_ROOT = "[role='menu']") up to 6s.
click_download_in_menu(page, save_dir): After the menu is open, this finds a menu item that contains text “Download” or similar (the DOWNLOAD_MENU_LABELS list includes "Download", "Скачать" (Russian), "Save video", "Export"). If none of those specifically is found, it falls back to the first menu item. Then it uses page.expect_download() context manager to wait for a download to start when clicking that item. Playwright captures the Download object (with file name). The script prepares a target path:
It uses the suggested filename’s extension (or .mp4 if none).
It attempts to get a custom title from the titles.txt: next_custom_title() reads the list, reads the cursor, picks the next title. It increments the cursor file for next time. It sanitizes the title for filesystem (removing illegal chars, trimming length). If a custom title is available, it will use that plus the extension. If not (list empty or exhausted), it calls next_numbered_filename(save_dir) which scans the directory for numeric filenames, finds the highest number, and returns the next number (this way if some videos were named “1.mp4”, “2.mp4”, the next will be “3.mp4” and so on).
It also ensures uniqueness: if the chosen name already exists, it appends " (1)", "(2)", etc. (though if using numbers or new titles that shouldn’t happen, but this accounts for if the user re-runs download in same folder).
Then download.save_as(target_path) writes the file to disk.
It returns the final target_path so the caller can log it.
Using these, the main action to download the current video is:
open_kebab_menu(page)
path = click_download_in_menu(page, DOWNLOAD_DIR)
print(f"[✓] Downloaded: {os.path.basename(path)}")
The script wraps this in a try/except to handle timeouts: it tries once, if clicking download doesn’t trigger a download within 20s, it prints a warning and retries one more time after 1.5s. If it still fails, it logs an error "[x] Failed to download: {exc}". This could happen if the menu is present but the “Download” option is disabled or network issues, etc.
Scrolling to Next Video: After downloading the current video, the script needs to advance the feed to the next draft. This is done with scroll_to_next_card(page):
It records the current page URL and current video src (by evaluating document.querySelector('video')?.currentSrc).
It calls _wait_for_cloudflare(page): this function checks if the page content suggests a Cloudflare challenge (common if multiple requests triggered bot protection). If found, it logs [NOTIFY] CLOUDFLARE_ALERT and prints a message for the user “Please solve the Cloudflare check—I will wait.” Then it loops until the Cloudflare challenge is gone, printing periodic waiting messages. Once cleared, it continues, and resets start_url and start_src because solving the challenge might reload or navigate the page.
It then performs a scroll: _long_swipe_once(page) which simulates a scroll gesture (it tries page.mouse.wheel(0, 900) a few times with small pauses, or a window.scrollBy fallback). This should swipe the feed up to show the next video. It then waits pause_ms (default ~1800ms) for the next video to load.
After scrolling, it again checks Cloudflare (in case the scroll triggered another challenge). If so, handles it (which might pause until solved).
Then _wait_for_change(timeout_ms) waits a few seconds (default 9s) for the page URL or the video source to change from the starting ones. If either the URL changes (to a new draft ID) or the video file changes (sometimes the URL might stay same but video element source changes), it considers it a success. If time passes without change, it tries a second scroll:
If after first scroll no change, it does a long_jitter (random 0.7-1.2s) and calls _long_swipe_once again, then waits slightly shorter.
If then a change is detected, good.
If still not, it returns whether any change happened at all (true/false).
It also waits for the right panel to be visible again (just to ensure the video controls loaded) but it won’t fail even if timeout.
If it returns False, it means it could not scroll to a new video (perhaps reached the end of feed or something broke).
Feed Download Loop: The main function download_feed_mode(page, desired) uses the above pieces to iterate:
It calls ensure_card_open(page). This function ensures the page is currently showing a draft card. If the page is still on the listing, it calls _open_first_card_on_page. If it’s on a draft but the right panel is missing (maybe not fully loaded), it might try alternatives. Essentially, it makes sure we are in the single-video viewer mode. If this fails initially, the script logs “[x] Could not open first card — stopping.” and aborts.
Once in feed mode, it initializes done = 0 and an empty set seen. The seen set keeps track of video URLs already downloaded to avoid infinite loops if the feed cycles.
It enters a while True:
Again checks Cloudflare (calls _wait_for_cloudflare) at top of loop to handle any mid-loop challenge.
Gets current_url. If this URL is in seen, it means we have come back to a video we saw before – likely the feed is not providing new videos (could be the end or a glitch). It logs “[!] Card already seen, cannot scroll further — stop.” and breaks the loop.
Otherwise, it brings the page to front (just to focus the browser, not strictly needed but ensures no hidden tab issues) and calls download_current_card(page, DOWNLOAD_DIR).
If download_current_card returns True (download succeeded), it increments done and adds the URL to seen.
If a desired number of videos was specified (non-zero) and done >= desired, it breaks out (we’ve downloaded the requested count).
Else, it attempts to scroll: if not scroll_to_next_card(page): means it failed to go to next (maybe end of list, or Cloudflare that couldn’t be solved – though Cloudflare is handled inside, so failure likely means end of list or UI issue). In that case it logs “[!] Could not move to next video — stopping.” and breaks.
If scrolling succeeded, it waits 0.6s, then a random short delay (long_jitter() which is ~0.8-1.8s) to mimic natural viewing time.
Then loop repeats.
When loop exits, it prints a completion message "[i] Done." or similar.
Max Videos and Already Downloaded Handling: The code doesn’t explicitly skip already-downloaded drafts; it relies on the user limiting via max_videos to “last N” which assumes new videos. However, the logic of seen prevents double-downloading within one run. Also, the feed is chronological with newest first typically, so downloading the “last 30” effectively gets 30 newest. If run again later, since it always starts from the latest, it might re-download the same ones unless the user moves the cursor in titles or uses a different approach. The app’s UI addresses this by warning if “new videos are fewer than requested”. The code itself doesn’t count available videos beforehand, but it does detect if it sees a repeat (which likely implies it looped back to the first video after hitting end). In such case, it stops. So if you requested 30 but only 8 new videos existed, it will stop after those 8 and you’ll have done=8 and a log warning about a repeat card.
Output and Files: Downloaded files go into the configured DOWNLOAD_DIR. By default, this is ./downloads or a session subfolder. The file naming logic ensures they are numbered or titled. For example, if Session 1’s titles.txt had 5 lines and you downloaded 8 videos, the first 5 would use those titles, the 6th through 8th would be named “6.mp4”, “7.mp4”, “8.mp4”. The titles.cursor will have advanced by 8 (so next time it won’t reuse those titles). Titles with spaces or special characters are sanitized to not break filesystem rules (replacing slashes, etc., with spaces).
Cloudflare Mitigation: A notable feature is detection of Cloudflare’s anti-bot challenge. If encountered, the script essentially pauses the automation and asks the user (in console/UI) to manually solve the CAPTCHA or challenge in the browser. It prints statuses while waiting. Once the user completes it, the script resumes automatically. The UI would highlight this with an alert icon and likely forward the [NOTIFY] CLOUDFLARE_ALERT to, e.g., Telegram or an on-screen notification so the user knows to intervene. This is critical in keeping the automation running for long periods without being completely stopped by Cloudflare protections.
Example Download Sequence: The user selects Session 1 and sets “Max videos: 5” in the pipeline or workspace UI for downloading. They click “Download”. The app launches the downloader with MAX_VIDEOS=5. The browser is already on Sora (maybe at drafts listing or already in feed from autogen). The script navigates to drafts list and clicks the first video (newest). It downloads video 1 (names it “Cat Wins The Race.mp4” from titles[0]), then scrolls. Downloads video 2 (“Doorbell Prank.mp4”), scrolls, video 3 (“Windy City Surprise.mp4”), scrolls, video 4 (“Untitled 4.mp4” if no title given for 4th maybe), scrolls, video 5 (“Untitled 5.mp4”). After 5, done meets target and loop breaks. If the user had left max_videos as 0 (meaning “download all”), the loop would only break when it reaches a repeat or fails to scroll – effectively grabbing every new video in the feed. If that feed was long, the script could scroll quite far, making dozens of requests (hence the need for Cloudflare handling and pacing). All these actions – downloads and scrolls – are captured in the UI logs. The Session Logs page for Session 1’s Download log would show something like:
[i] Working in existing window: https://sora.chatgpt.com/drafts  
[i] Opened first draft card — switching to scroll mode.  
[✓] Downloaded: Cat Wins The Race.mp4  
[✓] Downloaded: Doorbell Prank.mp4  
[✓] Downloaded: Windy City Surprise.mp4  
[✓]
...
[✓] Downloaded: Windy City Surprise.mp4
[✓] Downloaded: Untitled 4.mp4
[✓] Downloaded: Untitled 5.mp4
[i] Done.
And if Cloudflare had intervened, you’d see lines like:  
[NOTIFY] CLOUDFLARE_ALERT
[!] Waiting for Cloudflare challenge to be solved...
indicating the pause and resume.

This automated download mechanism thus reliably fetches a batch of latest videos with minimal user intervention. The original app UI lets the user specify the global default limit (in **Pipeline page or Settings**; e.g., “Download the last N videos”) and also override per session if needed. After downloading, users can review the videos (the UI had quick-open buttons for the download directory), then proceed to the next steps (blur/watermark or merge).

## Watermark Removal Algorithm (No-Blur Restoration)

Instead of blurring out the Sora watermark (which is typically a semi-transparent logo that might bounce around the frame), Sora Suite implements a more sophisticated **watermark restoration** to actually remove it and reconstruct the covered background. This is handled by `workers/watermark_cleaner/restore.py` using OpenCV.

**Overview:** The watermark removal works by finding the watermark in various frames, gathering “clean” background patches from other frames where the watermark has moved, and then seamlessly cloning those patches over the watermark area in each frame. This yields a final video with the watermark replaced by what was behind it, often nearly imperceptible if done well【2†L95-L103】【2†L105-L113】.

Key steps in the algorithm:

- **Template Preparation:** The user provides a watermark image file (e.g., `watermark.png`). This should be the exact logo used as watermark (with transparency if available). The script reads this image and prepares a template for matching:
  - Converts it to BGR and grayscale.
  - Builds a binary mask from the alpha channel (or luma) using `mask_threshold` (e.g., 8) to differentiate the opaque logo from background【19†L5-L13】【19†L18-L26】.
  - Stores the original dimensions of the template. This yields a TemplatePackage (template, mask, grayscale, etc.) to use in matching.

- **Detecting Watermark Positions:** For each video file in the source directory (typically the downloaded videos directory, or blurred videos if those were made):
  - The script uses `detect_watermark(video_path, template_path, ...)` from `watermark_detector.py`【19†L49-L63】. It opens the video with OpenCV and determines how many frames to scan:
    - If `frames` parameter is 120 (default) and the video has more than 120 frames, it will sample frames at a regular interval (e.g., if video has 600 frames, it might analyze every 5th frame to get ~120 frames)【19†L28-L37】. If `full_scan` is enabled, it will consider all frames.
    - For each frame to check, it possibly resizes it if `downscale` (1080) is set and the frame is larger (for speed)【19†L40-L47】.
    - It converts the frame to grayscale.
    - It then tries template matching at multiple scales: it generates a list of scales from `scale_min` (0.85) to `scale_max` (1.2) in `scale_steps` (9) increments【19†L50-L57】. This accounts for slight resizing (the watermark might appear smaller or larger depending on video resolution or if the video was scaled).
    - For each scale, it resizes the grayscale template accordingly and its mask, and runs `cv2.matchTemplate(frame_gray, scaled_tpl, method=cv2.TM_CCOEFF_NORMED, mask=scaled_mask)`【19†L57-L63】. It finds the best match score and location.
    - It tracks the best score/location among all scales for that frame. If the best score >= `threshold` (0.78), it considers the watermark *found* on that frame at that location.
    - It then computes the bounding box of that detected region in the **original frame’s scale** (undoing any downscale and template scale)【19†L63-L72】. For example, if the template at 0.9 scale matched at (x,y) on a downscaled frame that was half size, it scales those coordinates up to the original frame coordinates. The `bbox` is (left, top, width, height) of where the watermark is.
    - It records this in a `series` list if accepted. The `series` essentially is the list of detections across frames that met the threshold.
    - After processing frames, `detect_watermark` returns a dict with `series` (list of {frame, bbox, score, ...})【19†L72-L80】. It may also include `details` if requested (all attempts), but in use here we pass `return_series=True` and likely focus on `series`.

  - The restoration script builds a **detection map** from this series: a dict mapping `frame_index -> list of bboxes`【20†L1-L17】【20†L22-L30】. This accounts for the possibility that the watermark could appear multiple times or in multiple places (in practice Sora watermark is one instance, but this structure allows multiple if any).
    - It filters out detections below a secondary threshold (in code, it uses `if not accepted and score < threshold: continue` – essentially skipping unaccepted ones, but since we only added accepted ones, that’s fine)【20†L22-L30】.
    - The result `detections` might look like: `{0: [(x0,y0,w,h)], 10: [(x1,y1,w,h)], 20: [(x2,y2,w,h)], ...}` indicating at frame 0 watermark at location0, frame 10 at location1, etc.

  - If `detections` comes out empty (no watermark found in any frame), the script logs `[WMR] Watermark not found -> video_name`【20†L32-L39】, and simply copies the video to output (so it doesn’t fail the pipeline, it just leaves it as is).

- **Reconstructing Background:** If watermark detections exist:
  - The script loads **all frames** of the video into memory (since it likely needs random access for donors)【20†L1-L9】. It also grabs the FPS of the video to write out later.
  - It prepares an output frame list `processed` initialized as a copy of original frames.
  - It sets up inpainting parameters: chooses Telea vs Navier-Stokes based on `inpaint_method` (telea or ns)【20†L40-L44】.
  - It then iterates frame by frame (`for idx, frame in enumerate(frames)`)【20†L46-L49】:
    - It checks if `idx` is in `detections`. If not, it means no watermark on that frame, so it leaves `processed[idx]` as original.
    - If yes, it gets one or more `regions` (bboxes). In Sora’s case, likely only one region per frame (the watermark doesn’t appear in multiple distinct places at once). But the code loops through each region in that frame (for completeness)【20†L49-L58】.
    - For each `bbox` (x,y,w,h):
      - It first filters out any very small ones (smaller than `min_size`, e.g. 32 px) – likely not needed here since watermark is bigger, but just in case.
      - It then **expands** the bbox a bit to capture context: `_expand_bbox(bbox, frame_w, frame_h, padding_px, padding_pct)`【20†L9-L17】. This adds a padding border around the region (12px or 18% of width/height). So if watermark is 100x50 at (500,100), expanded region might be, say, 512x62 at (488,88) (assuming 12px pad). This expanded region is where we will replace.
      - It chooses donor frames via `_choose_donors(frames, detections, idx, region, search_span, max_iou, pool_size)`【20†L19-L30】:
        * It will look at frames `idx-1, idx+1, idx-2, idx+2, ...` out to `search_span` (12) frames away.
        * For each candidate frame, it checks if that frame has a detected watermark overlapping this region (by checking `detections` and comparing IoU > max_iou 0.25)【20†L19-L30】. If the watermark in that frame overlaps too much, it’s not a clean frame for this region. 
        * It collects up to `pool_size` (4) frames that either have no watermark or the watermark is in a sufficiently different place.
        * If the watermark tends to move around, many nearby frames might still have it but maybe offset – as long as the overlap is small (less than 25%), they might be used. Often, watermarks move along a path so adjacent frames might not be fully clear. The algorithm might pick frames further away where the watermark was on the other side of the video or disappeared (some watermark implementations toggle visibility, though likely not in Sora’s case).
      - After getting donor frame indices, it extracts patches from those donor frames: for each donor index, it takes `donor_frame = frames[donor_idx]` and slices the same region (dx,dy,dw,dh)【20†L31-L39】. It ensures the patch size matches the region (sometimes at the edge of frame it might need bounds checking, but we padded carefully).
      - It now has a list of `patches` (each a numpy array of shape (dh, dw, 3)). It calls `_median_patch(patches)`【20†L1-L8】:
        * If no patches (which can happen if no donors found), returns None.
        * If only one patch, returns it.
        * If multiple, it stacks them and computes the median along the stack axis for each pixel, resulting in a median image patch. This median filter across time effectively removes any transient object (like if in one donor frame a person was behind watermark, another had different background, median will blend them). The output is of dtype uint8 (after conversion).
      - If `patch` is None (no donors), the algorithm falls back to inpainting: it calls `_inpaint_region(frame, region, radius, method)` to fill the region using OpenCV’s inpaint (Telea or NS as chosen)【20†L79-L88】. Inpainting uses surrounding pixels to interpolate the area – Telea tends to blur edges inward, NS tries to continue lines inward.
      - If `patch` exists (median patch computed):
        * It attempts to paste it in with seamless cloning: `_replace_bbox(frame, patch, region, blend_mode)`【20†L68-L77】. This creates a mask of full white for the patch area and calls `cv2.seamlessClone(patch, frame, mask, center, clone_flag)` where `clone_flag` is `NORMAL_CLONE` or `MIXED_CLONE` depending on `blend` setting. This algorithm blends the patch into the frame so that the borders are less noticeable. If `blend_mode` is "mixed", the cloning will mix the texture of patch with the target to better hide seams when background colors differ.
        * If seamlessClone throws an exception (it can if the mask is the size of the image or other odd cases), it catches and instead does a direct paste (frame[y:y+h, x:x+w] = patch) or falls back to inpaint as last resort【20†L68-L77】.
        * On success, `processed[idx]` now has the watermark region replaced by that patch.
      - This is done for each region and each frame. The script logs progress every 10 frames:  
        `[WMR] Processing video_name: frame X/Y`  
        so the UI can show a progress indicator per video.
  - After processing all frames, it writes out the `processed` frames to a new video file in the output directory. It uses `cv2.VideoWriter` with codec MP4V (MPEG-4 part 2) since it’s simple and widely compatible, and at the original FPS【20†L1-L9】【20†L68-L77】. The output video has same dimensions as input (it resizes any off-size frame back to original, but since we used original frames there should be none mismatched). Once saved, it logs `[WMR] ✅ video_name done` for that video.

- **Parameters Influence:** The `threshold` (0.78) is tuned to detect the watermark reliably but not false-detect random patterns. The `scale_min/max` and steps allow matching if the video was resized by up to ~15%. The `full_scan` parameter, if true, sets `per_frame=True` causing the detector to examine **every frame** (by setting `frames_to_scan = total_frames`)【20†L1-L9】. By default (full_scan not set), it scans 120 frames. The idea is that if the watermark is static (like always on screen), scanning 120 evenly spread frames in the video might find it in enough positions. But if the watermark disappears for a while or the background changes drastically, scanning all frames (full scan) might yield more donors or ensure detection on all frames. However, full scanning is slower for long videos, hence optional.

- **Edge Cases:** If the watermark doesn’t move at all (say it’s static in corner), then every frame’s detection yields the same bbox. The donor selection might then never find a frame without overlap (because it’s always there). In that case, after failing to find donors, it will inpaint those areas, or if some frames randomly the watermark might be slightly transparent and appear as not detected in a frame or two, those could be donors. But generally, a fully static watermark might best be blurred or require a provided clean background reference (the algorithm as is struggles if the object never moves – because you have no clean sample behind it). However, Sora’s watermark likely animates or toggles so this method works (the movement provides glimpses of background).

- **Result:** The output videos in `restored/` should look like the originals but with the watermark area restored. If done well, viewers might not notice a watermark was ever there. Some slight blurring or artifacts can occur if the background had motion or if inpaint was used (inpaint can create slight smudging). The algorithm tries to minimize that by using real patches and seamless blending.

**Module Responsibilities:** 
- `watermark_detector.py`: houses the detection logic (template matching). It also defines a `WaterMarkDetector` class as a wrapper used by the UI for quick scans or previews (the UI might have a tool to highlight watermark zones on a representative frame). For example, the UI’s **Watermark** page might allow the user to click “Probe” which runs `WaterMarkDetector.scan()` on a sample frame and then `get_zone_masks()` to display masks of where it found watermarks (the config had a “global_probe” step as well). The class basically encapsulates the functional approach above for reusability.
- `restore.py`: orchestrates loading videos, calling detection, and applying patches/inpaint. It logs structured messages (JSON and plain) that the UI reads to update the global process log and possibly notify via Telegram. For instance, at the end it logs a summary event:
  ```json
  {"event": "watermark_restore", "processed": N, "errors": M, "source": ".../downloads", "output": ".../restored", "template": ".../watermark.png", "seconds": 32.47}
If errors > 0, it returns a non-zero exit code and prints [WMR] Completed with errors: N ok, M errors【20†L93-L100】, otherwise [WMR] Completed: N files, 32.5 s【20†L101-L107】. The UI displays this in the Process Log and possibly sends a Telegram alert if configured. Example Outcome: After running watermark cleaner on Session 1’s 5 videos, the user checks restored/Session_1/. There are 5 MP4 files, named the same as originals (“Cat Wins The Race.mp4”, etc.). On watching them, the Sora logo that was bouncing around is gone – instead you just see the video content. If you look closely, maybe a slight blur where it was if background texture was hard to reconstruct, but often it’s quite clean. This is a big improvement over simply blurring out a rectangle (which was another feature in FFmpeg presets – Sora Suite had a blur tool as a faster alternative, but the restore tool is the advanced approach). The UI for Watermark page in the original app allows the user to configure the parameters (threshold, frames, etc.) and test them. It also had an option to do a “probe” which likely scans one video or a set of frames to show where the watermark is detected (could flip the watermark image or scan horizontally if needed – the config had a global_probe (flip=true/false) in automator steps, maybe to test detection). But the general usage is: fill in template path (or use default watermark.png), adjust settings if needed, then click “Replace Watermark” to run the restore worker.
Telegram Integration (Status Notifications & Templates)
Sora Suite can integrate with Telegram to send messages about the progress of tasks and allow the user to send quick updates from the app. The Telegram section in the UI has fields for the bot token and chat ID, as well as a list of message templates the user can define. Configuration: In Settings → Telegram, the user enables Telegram by providing:
Bot Token: The API token from BotFather for their bot.
Chat ID: The destination chat (could be the user’s chat ID or a group/channel ID).
The user then tests the connection by sending a test message. Once enabled, the app will use Telegram API (via https://api.telegram.org/bot<token>/sendMessage) to send notifications.
Automated Status Messages: Throughout the code, we saw lines with [NOTIFY] SOMETHING printed. The main app monitors the output of workers for such lines and maps them to human-readable events【22†L1-L9】. For example:
[NOTIFY] AUTOGEN_START is mapped to (“Autogen”, “Prompt insertion started”)【22†L1-L4】.
[NOTIFY] AUTOGEN_FINISH_OK -> (“Autogen”, “Prompt insertion — successful”)【22†L1-L4】.
[NOTIFY] AUTOGEN_FINISH_PARTIAL -> (“Autogen”, “Prompt insertion — partial (some failures)”)【22†L1-L4】.
[NOTIFY] DOWNLOAD_START -> (“Downloader”, “Auto-download started”)【22†L2-L5】.
[NOTIFY] DOWNLOAD_FINISH -> (“Downloader”, “Auto-download completed”)【22†L2-L5】.
[NOTIFY] CLOUDFLARE_ALERT -> (“Downloader”, perhaps “⚠️ Cloudflare challenge detected”) – in code it’s a tuple with a third None (maybe no immediate message? Possibly handled differently)【22†L2-L5】.
There could be similar for IMAGE_AUTOGEN_FINISH_OK, IMAGE_AUTOGEN_FINISH_EMPTY, UPLOAD_FINISH, etc.
When such an event occurs, the main application likely:
Updates the UI (e.g., showing a small toast or updating status text).
If Telegram is enabled, sends a message to the configured chat. The content of the message might use a template or default. Possibly it sends something like: "✅ Autogen finished successfully for Session 1 (10 prompts)" or "⚠️ Autogen completed with some errors." The code at lines 783-791 (not fully shown) constructs a text and posts to Telegram via requests or urllib call to the bot API【21†L7-L15】. It probably includes an emoji (ℹ️, ✅, ⚠️, ❌ corresponding to info, success, warning, error states).
The mapping table above likely provides the category and base message; the app might prepend an emoji and combine with session name or count info.
User Templates & Quick Messages: In the Telegram UI, there is a list of Message Templates that the user can define. Each template has a name and body text【21†L15-L23】【21†L33-L40】. For example, a user might create:
Name: “Daily Summary”, Text: “Today we generated {count} videos with {success} successes and {failures} failures.”
Name: “Check Queue”, Text: “Sora queue status: {queue_status}”
These templates allow the user to quickly send pre-defined messages, possibly with placeholders that the app fills in (the code suggests they might not have dynamic placeholders beyond maybe session name or last event; but they could manually edit before sending). The UI shows these templates in a dropdown. The user can select one, then either send it immediately or schedule it. There’s a field quick_delay_minutes which could be used to send a message after a delay (like “send this message 15 minutes later”). The config has telegram.quick_delay_minutes (default 0) and telegram.last_template to remember which template was last selected【21†L1-L8】.
Sending a Quick Message: The user can type a custom message in a text box (or auto-fill from a template) and hit Send. The _send_quick_telegram() function in app.py is called【21†L51-L59】【21†L61-L69】:
It retrieves the text from the input (possibly replacing placeholders with actual values if implemented).
It calls the Telegram API to send it. If successful, it records the activity via _record_telegram_activity(message, "success"), if failed, "error"【21†L53-L59】【21†L61-L69】.
It also possibly respects the quick_delay_minutes: if that is >0, the app might schedule the message to be sent after that many minutes instead of immediately. The code hints at scheduling, but likely they kept it simple (maybe the scheduling was more for YouTube uploads; for Telegram, immediate send is typical, but they might allow a delay to coordinate with daily schedules).
Telegram History: The app maintains a deque _telegram_activity_cache (max length 200) of past Telegram send events【21†L45-L53】. Each entry might be a tuple of (message text, status). The UI's Telegram panel shows a history list of last few messages sent (with a ✓ or ✗ to indicate success/fail, or some icon). There are refresh and clear buttons:
Refresh simply reloads the cached items into the UI list (in case new events came).
Clear empties the cache (and maybe the UI list).
The user can click a past message and perhaps re-send it (the readme mentioned “resend if needed”). The code has a function to handle selecting a history item and re-sending or copying text, likely by populating the input with that template.
Notifications via Telegram: When long processes complete or need attention, the app sends out messages. For example, after autogen finishes, it could send:
“✅ Session 1: 10 prompts submitted successfully.” (if all good), or
“⚠️ Session 1: Prompt insertion finished with errors (8 OK, 2 failed).”
After downloads:
“✅ Session 1: Downloaded 5 new videos.”
On Cloudflare:
“⚠️ Session 1: Cloudflare check required – please verify manually.”
These templates might be built-in or the user can edit them in telegram.templates. The config actually stores templates as a list of {name, text} dicts【21†L1-L8】, which the user can add/edit from the UI. The code for saving templates shows using _persist_telegram_templates and selecting by name, etc.【21†L27-L35】【21†L37-L44】.
Scheduling: The readme suggests the Telegram page also allows scheduling messages. Possibly the user can set up a template to send daily at a certain time (though we didn’t see explicit scheduling fields besides quick_delay_minutes). Perhaps they intended to implement a simple scheduler to send a specific template every day or when certain events happen. In practice, it might not have been fully automated, but the interface might allow setting a timer for a message (like sending a report at end of day). For our specification, we can note the possibility, but since part 3 asks for “schedule messages” we should consider implementing a scheduler in the new version’s Telegram module.
Usage Example: The user enables Telegram and sets up a template “Generation Done” = “All tasks completed for {session} ✅”. In the Automator or pipeline settings, they check “Send Telegram on finish”. So when the automator finishes a sequence, the app sends “All tasks completed for Session 1 ✅” to their phone. The user could also open the Telegram tab, select a template like “Daily Summary”, maybe type in some stats or let the app fill them, and press Send – the message goes to their chat and appears in the history list with a green check icon. Internally, the Telegram integration is relatively straightforward: it’s essentially an HTTP POST to the bot API. The main complexity is providing a user-friendly interface for templates and logging. The original app likely uses the standard requests library or urllib to send messages (the code snippet at line 791 shows constructing the URL and maybe posting JSON with chat_id and text)【21†L7-L15】. In summary, Telegram integration in Sora Suite serves as a convenience for remote monitoring and quick reporting. In the new implementation, we will preserve this functionality, but we can enhance the UI to show messages in a chat format (bubbles for sent messages, etc., since the user requested a “chat-like UI”). We will also maintain template management and possibly allow scheduling via a cron or simple scheduler within the app.
Original GUI Structure and Modules
The original Sora Suite UI is divided into multiple pages, accessible via a sidebar with collapsible sections【2†L19-L27】. Each page corresponds to a major feature set. Below we outline each page and its functionality, and mention the underlying modules or data it interacts with:
Home (🏠 Main Dashboard): This is an overview page showing a brief description of the app’s capabilities, some quick stats, and shortcut links【2†L29-L37】. It likely shows cards for total prompts sent, total videos downloaded, etc., drawn from the history or logs. It might have buttons to directly open a workspace or run a pipeline. It’s mostly informational and not deeply interactive beyond links.
Workspaces (🗂️ Chrome Sessions Manager): This page lists all defined sessions (workspaces). Each session is typically displayed as a card or row containing:
Session name (from config) and perhaps an icon or status indicator (idle/running).
The Chrome profile directory or profile name associated (maybe shown or selectable).
Buttons for key actions: “Launch Chrome” (🚀), “Prompts” (⚡ start autogen), “Images” (🖼️ generate images), “Download” (⬇️ fetch videos), “Watermark” (🧼 remove watermark). Each of these triggers the respective worker for that session.
Possibly a “Notes” field or icon that opens a small editor to edit session.notes (for user’s reference).
A display of recent activity for that session: e.g., the last prompt sent or last video downloaded or an error message. The app might show the tail of submitted/failed logs here for quick reference. In the readme, they mention “quick actions available right on the page and recent events”【2†L39-L47】.
When a session is selected (perhaps by clicking on its card), the right-side context panel updates to show details about that session: the Chrome profile path in use, the current prompt file name, the “RAW” directory path, etc., and possibly real-time status of its current task【2†L39-L47】. The context panel also duplicates the quick action buttons (open Chrome, autogen, download, watermark) so the user can trigger them from either place. Under the hood, these buttons call methods in app.py:
_open_chrome(session) uses portable_config to launch or attach Chrome.
_run_session_autogen(session_id, force_images=False) calls the autogen runner for that session (spawning the process and managing its output).
_run_session_images(session_id) does image-only generation for that session.
_run_session_download(session_id, override_limit=None, open_drafts_override=None) spawns the downloader with given limit (if override provided, else uses session/global config) and optionally to not auto-open drafts page if not needed.
_run_session_watermark(session_id) launches the restore process for that session.
Each of these uses the _session_env to assemble environment and ProcRunner (or QProcess) to execute the corresponding script. The app stores these processes in a dictionary per session, to manage stopping them if needed (the “⛔ Stop All” button stops all running tasks by terminating processes).
Pipeline (🧠 Pipeline): This page provided a way to run a sequence of operations for a single profile (like a scenario or macro). It had checkboxes for each stage of the pipeline and some global controls:
Checkboxes: Images, Autogen (prompts), Download, Blur, Watermark, Merge, Upload, TikTok【2†L65-L73】. The user could tick which stages to run.
A profile selector at the top (or it uses the currently active session as the context – the readme says “Title shows chosen Chrome profile”【2†L65-L73】).
Options for the selected stages: e.g. if Download is checked, a field to input the number of videos to download (the spinbox that binds to downloader.max_videos and possibly session max_videos). If Merge is checked, a field for group size (the spinbox bound to merge.group_size)【33†L5-L13】.
A Start Pipeline button (“⚡ Start selected”) and a Stop All button (⛔).
A progress bar indicating progress through the pipeline steps (which stage currently running). The context panel on the right would list the active stages and maybe any limits set, updating as each completes.
Internally, clicking Start saves any changed settings (like max_videos, merge group) via _apply_dl_limit()【33†L1-L7】 and _apply_merge_opts()【33†L1-L7】, then triggers _run_scenario()【36†L1-L10】. _run_scenario reads which checkboxes are checked and compiles a list steps in order (images -> autogen -> download -> blur -> watermark -> merge -> upload -> tiktok as per code)【36†L1-L10】. It logs an event “scenario_start” with these steps【36†L4-L10】. Then it launches a thread that sequentially calls each stage’s synchronous function:
_run_autogen_sync() will run autogen for the active session and block until done (it uses the same mechanisms as the session tasks but synchronous).
_run_download_sync(), _run_blur_presets_sync(), _run_watermark_restore_sync(), _run_merge_sync(), _run_upload_sync(), _run_tiktok_sync() respectively, each executing the job and waiting.
If any returns failure (False), it aborts the sequence, logging an error status for that stage and not proceeding.
If all succeed, it logs “scenario_finish ok:true”.
This Pipeline page is essentially a manual way to run through the pipeline for one profile, one step after another, with minimal supervision. The UI shows a live status and after completion, possibly a summary (e.g., “Scenario completed”). Important: In the new design, the pipeline page is slated to be removed. Its functionality is largely replaced by either running individual tasks from Workspaces or designing an Automator sequence (for multi-session or complex flows). We will carry over the logic for pipeline steps (so backend can still run a sequential scenario if needed), but the UI will not have a dedicated “Pipeline” page.
Automator (🤖 Automator): This page is a more powerful version of pipeline where the user can build a sequence of steps that can involve multiple sessions and parallel execution in a controlled way【2†L99-L107】. It acts like a small workflow editor:
The user can add a step and choose a Step Type from a dropdown:
Session Prompts: insert prompts (autogen) on selected sessions.
Session Images: generate images on selected sessions.
Session Mix: (the combo of images+prompts, likely generate images then immediately autogen with them) on sessions.
Session Download: download videos on selected sessions (with an optional “limit” field per step).
Session Watermark: run watermark removal on selected sessions.
Session Chrome: launch Chrome for selected sessions (ensures they’re open before subsequent steps).
Global Blur: apply blur preset to all videos in blurred_dir (not session-specific, but likely uses active preset on all videos).
Global Merge: merge videos (across all or specific set? Probably just uses the global merged_dir and group size). When adding this step, UI asks for a group size (with default coming from settings, but user can override for this step).
Global Watermark: run watermark removal on the global source dir (perhaps if the user wants to clean all videos in the global downloads at once outside of session context).
Global Probe: test watermark detection on current videos (with an option flip meaning maybe also try a flipped template horizontally, in case the watermark might appear mirrored in some contexts).
After choosing a step type, if it’s a session type, the UI shows a multi-select list of sessions. The user picks one or more sessions that this step will run on. (For example, “Session Prompts” on Session1 and Session2 means: in that step, it will run autogen on both Session1 and Session2).
For “session download” step, an extra numeric field for the download limit appears (if left blank or 0, it will use the global/session config default; if provided, overrides max_videos just for that step)【33†L1-L9】.
The user can add multiple steps, reorder them (with up/down arrows), edit or remove them. The steps list is shown with a summary (like “1. ✍️ Prompts: Session 1, Session 2”, “2. ⬇️ Download: Session 1 · 30 vids”, “3. 🧵 Merge by 2”)【33†L13-L21】. Icons (emoji) are used as shown in _describe_automator_step mapping【33†L13-L21】 to identify the type visually.
The Automator supports saving sequences as presets. The UI has a presets dropdown and buttons: “Apply Preset”, “Append Preset”, “Save Preset”, “Delete Preset”【33†L23-L35】. A preset is essentially a named sequence of steps saved in config under automator.presets (with an id, name, and steps list)【33†L23-L35】. This is useful for common routines (e.g., a preset for “Full Pipeline on Session1 and Session2 then Merge”).
The Run Automator button starts executing the sequence. When clicked, the app copies the steps list and starts the automation thread:
It logs the summary of steps as an activity “Automation: Step1 → Step2 → ...”【35†L4-L12】.
It sets up counters and marks automator as running.
Then it calls _automator_tick() which processes steps one by one:
It prints “Automation: step i/N — [Description]” and sets status to running for that step【35†L13-L21】.
It calls _execute_automator_step(step, i, total, async_done=callback).
Inside _execute_automator_step:
If the step is a session type and sessions list is empty, returns False (error). If not empty:
If type is "session_chrome": it simply loops sessions and calls _open_chrome(session) for each, logging error if any fails【35†L57-L68】.
For other session types (prompts, images, mix, download, watermark):
If an async_done callback is provided (meaning we want to run possibly in parallel or at least asynchronously step-level), the code goes into an asynchronous branch【35†L70-L98】. It copies the session IDs into a remaining list. Then defines a run_next() function that pops one session at a time and runs it, and when done, calls itself for the next, and if all done calls async_done(True). Each session task is started by calling _automator_run_session_task(session_id, step_type, limit, async_done=after_one_done). This will launch the worker for that session and immediately return (so tasks run concurrently only if async_done for the step was None, but here we pass a callback, so it runs them sequentially but without blocking the main thread – effectively sequential in our code, but they possibly intended to allow concurrently? Actually, reading it: they do run_next() then return None, marking _execute_automator_step as not finished yet (since it returns None). The control goes back to _automator_tick, which sees it returned None and sets _automator_waiting = True meaning it’s waiting for the async callbacks to finish before moving on【35†L4-L12】. So they could have run in parallel by kicking off all sessions at once, but they chose to chain them vicity. Thus, in the current code, even though it’s async, it still executes one session at a time per step. The difference is it doesn’t freeze the UI thread – it uses callbacks and a thread inside _automator_run_session_task to wait for completion).
If async_done is not provided (meaning we want a blocking call), it simply loops through sessions and calls _automator_run_session_task synchronously for each, stopping on any failure【35†L100-L109】. But in our invocation from _automator_tick, we do provide async_done (on_done in _automator_tick), so it always goes the async route. Essentially, the automator never blocks the UI; it runs steps asynchronously but still sequentially relative to each other.
If step is global type:
"global_blur": calls _run_blur_presets_sync() (blurs all videos in source using active preset).
"global_merge": calls _run_merge_sync(group_override=... if specified) to merge videos, possibly overriding the group size for this run.
"global_watermark": calls _run_watermark_restore_sync() to clean all videos in the global source folder.
"global_probe": calls _watermark_probe_batch_job(flip=...) whi watermark and perhaps logs results (maybe this was used to fine-tune template position or to preview where the watermark is).
These are called synchronously (return True/False).
The function returns True if the step(s) launched/executed successfully (for session steps, success means tasks launched; actual completion is signaled via callback). Returns False if any immediate error (like session not found, or a global function failed).
In _automator_tick, if _execute_automator_step returned None, it means it’s waiting for async completion. It sets _automator_waiting = True and returns (so not proceeding to next step yet)【35†L13-L21】. When each session task completes, it triggers on_done which calls _finish_automator_step(ok, idx)【35†L23-L31】.
_finish_automator_step(ok, idx) logs either success or error for that step in the activity log (“step i done” or “step i failed”)【35†L23-L31】. If failed (ok=False), it stops the whole automation: sets _automator_ok_all = False, posts status “Automation stopped at step i” (with error state), marks running false, updates stats, etc. If succeeded, it updates progress (i/total done) and then calls _automator_tick() again to process the next step【35†L33-L41】.
Thus, it goes step by step. If all steps complete (_automator_index reaches total), _automator_tick will detect that and finalize: it sets status “Automation finished” (state ok or error depending on _automator_ok_all) and stops running flag【35†L1-L12】.
From a user perspective, the Automator allows orchestrating multi-session workflows in one go. For instance, one could make a sequence:
Launch Chrome for Session1 and Session2.
Session Images for Session1 and Session2 (generate all images first in parallel).
Session Prompts for Session1 and Session2 (submit prompts on both).
Session Download for Session1 and Session2 (maybe limit 10 each).
Session Watermark for Session1 and Session2.
Global Merge (group=2).
Running this automation would handle two sessions slightly staggered (it would actually do Session1 images then Session2 images due to sequential in code, but fairly back-to-back, then prompts similarly). If any step fails (say Session2’s autogen had too many failures and returned error), it stops the chain and the UI highlights which step failed. The user can then fix the issue and possibly resume from that step (though resume isn’t built-in, they can remove completed steps and run again). The Automator steps and presets are saved to app_config.yaml under automator: { steps: [...], presets: [...] } when changed【33†L23-L35】. So the app remembers the last sequence even after restart (helpful if working on a project repeatedly).
Session Logs (🗒️ Logs): This page provides a convenient view of each session’s logs【2†L113-L121】. The UI likely has a dropdown or list of sessions. When a session is selected, it displays the tail (last ~N lines) of that session’s submitted.log, failed.log, and download output. Possibly each log is in a separate text box or tab:
Submitted Log: showing prompts that have been sent successfully. This helps the user see which prompts went through (and maybe copy them if needed).
Failed Log: showing any prompts that hit errors (with reasons like “queue-limit/backoff-180s” or “media-upload” etc.). This tells the user which prompts might need attention or re-run.
Download Log: showing videos downloaded recent a persistent file for it, the app probably buffers the output of the downloader process and stores some lines per session (e.g., in _session_state or the history JSON). The UI tose lines with timestamps or icons.
The user can switch between sessions to quickly inspect what happened in each. This is very useful if multiple sessions are running overnight and the user wants to see if any prompts failed or howone.
Process Log (📜 Global Journal): This page is a global live log of all major events and status updates【2†L123-L131】. It aggregates messages from all sessions and system events (like automator start/stop, errors, etc.). Each entry is typically one line prefixed with a state emoji:
ℹ️ info messages (blue or neutral) for normal updates.
🔄 running (spin icon) for things in progress.
✅ success messages fo️ warnings for recoverable issues.
❌ errors for failures.
For example, it might show:
ℹ️ Chrome launched for Session 1  
🔄 Session 1: Sending prompts...  
✅ Session 1: Prompts finished (10/10 sent)  
🔄 Session 1: Downloading 5 videos...  
⚠️ Session 1: Download stopped (only 3 new videos)  
✅ Session 1: Download complete (3 videos)  
🔄 Session 1: Removing watermark...  
✅ Session 1: Watermark removed (3 files)  
✅ Automation finished  
The UI displays these in a scrolling list (limited to last 200 entries by default to keep UI snappy)【2†L131-L138】. There's a “Clear log” button which asks confirmation and then clears the list (and possibly truncates the history file)【2†L131-L138】. Also an export button to save the history (the readme mentions export). Internally, the app uses append_history(cfg, event) to write events to history.jsonl and also _append_activity(display_text, kind) to update the in-memory list for the UI real-time feed. The UI likely updates whenever a new message is appended (the code might emit a signal or use a timer to poll a queue of new log lines).
Content (📝 Content Editors): This page allows the user to edit the various text files used in the workflow【2†L139-L147】:
Prompts Editor: The UI likely has a dropdown to select a prompt profile (each session’s prompt file). By default __general__ profile corresponds to prompts.txt. If the user has defined additional profiles, they might have separate prompt files like prompts_profileA.txt, etc. The app’s _prompts_path(key) resolves to either prompts.txt or prompts_<key>.txt【26†L5-L13】. In the UI, the user picks the profile, the text area loads that file’s content, and they can edit the list of prompts (one per line or however). When they save (or move away, possibly auto-save), it writes to disk.
The UI might also show the number of prompts, and possibly the history of submissions for that profile. The readme says “history of sent lines for each profile is kept separately”【2†L139-L147】. Perhaps they maintain a file or memory of which prompts were already submitted (which is basically the submitted.log filtered by profile). It’s possible the content page shows, for the selected profile, a list of prompts and maybe highlights those already sent (or a separate list of “used prompts” vs “new prompts”). However, it’s not explicitly described beyond mention of history. In code, load_submitted() returns a set of keys, not the actual lines except logs contain them. They might simply rely on the logs page for that. The content editor might not show history directly, only allow editing the prompt file and let the logs page show what was sent.
Image Prompts Editor: The content page likea sub-tab or toggle for image_prompts.txt. There the user can edit lines (including JSON). Ideally, the UI could have a structured editor (maybe a table or form for prompt, count, etc.), but likely it was a plain text area. They mention support for le templates. Possibly the UI provides some help in formatting (maybe not, likely just raw editing).
The user writests here and saves. The app might validate JSON format or at least ensure it can parse (maybe highlighting errors).
The history of image generation prompts might not be tracked separately (they’re usually reused or one-time).
Titles Editor: A section to edit titles.txt. Possibly a simple list UI where each line is an editable title string, possibly with an index number. The user can maintain this list of video titles. There might also be a display of the current cursor (like “next title in button to reset cursor if needed). In fact, the readme mentions a “table of titles for renaming downloaded videos and exporting final lists”【2†L139-L147】. It could be that the UI is tabular with two columns: one for title text, one for an optional link or something (maybe they planned to allf used titles after uploading?). But likely it’s just a text list in practice.
The user can clear or reset the .cursor if they want to reuse titles from beginning (the UI has a “Reset curreset_titles_cursor()` which deletes the cursor file)【33†L1-L9】.
They can also export titles (maybe to a CSV or copy to clipboard) – readme hint lists (perhaps after merging/uploading, to have a list of YouTube video URLs and titles, but that might be part of upload).
Possibly History Editor: The content section might allow viewing history.jsonl or other logs in a text viewer (but actually there was a separate maintenance/doc section for full log).
The content editors allow quick tweaks without leaving the app – e.g., you finish one batch of prompts, then can paste in a new batch for the next run, or adjust some titles, etc.
**Telegram (📨 Telegrelegram page is divided into a configuration panel and an interaction panel【2†L147-L154】:
Top part: Settings (token and chat id fields, and an “Enable” toggle). Possibly a test button “Send test message” to verify.
Middle: Templates – a list or dropdown of saved templates. Buttons: Add Template, Edit Template, Delete Template. When adding/editing, a dialog asks for Name and Message text (we saw code for AutomatorStepDialog but not for Telegram template dialog, likely similar structure).
The last_template from config dch template is currently selected by default (so if user often uses one, it stays selected)【21†L31-L39】.
Middle-lower: Compose and Send – a text box (possibly pre-filled with the selected template’s text) that the user can edit freely. Buttons: “Send Now”, maybe “Schedule” (if scheduling is implemented, e.g. send after X minutes or at set time; the config has quick_delay_minutes which might be used if schedule is clicked).
Bottom: History – a scrollable list of recent messages sent via the bot. Each entry might show an icon (✅ or ❌) and the message (maybe truncated or just first few words), and possibly a timestaman click an entry to quickly re-send that text (the code suggests _select_telegram_template_by_name can also apply text to the field, maybe history uses sim21†L41-L49】. There are “Refresh” and “Clear” buttons for the history list【21†L43-L49】. Clearing just empties the cache (doesn’t delete anything on Telegram side).
The Telegram module doesn’t involve external scripts; it’s handled inside app.py using requests or similar. The app runs a background thread or uses the GUI thread to senork call, likely fine). In original app, Telegram is not deeply integrated into the automateyond sending notifications. But the user can manually use it to send any message to themselves (like if they want to craft a custom report or note on the phone). Security: The bot token is sensitive. The app likely stores it in app_config.yaml in plaintext. Ideally, in a real scenario, encryption or at least caution is needed, but given this is a localized tool, plaintext in config is accepted.
Settings (⚙️ Settings): This section is actually a grouping of many sub-tabs as per config categories【2†L154-L163】:
Directories – fields for downloads_dir, blurred_dir, merged_dir, maybe project_root if changeable, etc., and maybe the history/images directories. T allow browsing for a folder via a file dialog for each path. This tab corresponds to project_root and the path settings in YAML. Changing these will move where files are stored (the app might prompt to move existing files or just apply to new ones).
Image Generation – fields from google_genai: API Key (with a “test” button possibly to validate connectivity), Model (perhaps a dropdown if the GenAI client can list available models), aspect ratio (dropdown: "1:1", "16:9", "9:16", etc.), imropdown or input: "1K", "2K", etc.), number_of_images (input), style, seeds (a text of comma-separated seeds), toggles for consistent_character_design, fields for lens, color palette, reference prompt. Also a toggle for notifications_enabled (to enable warnings when nearing quota) and fields for daily_quota and quota_warning_prompts. The UI ensures these tie to enviroe worker.
Autogen – settings for prompt injection: e.g., poll_interval_ms, human_typing_delay_ms, start_confirmation_tiqueue_retry.retry_interval_ms, backoff_seconds_on_reject, success_pause_every_n aause_seconds, etc. These come from workers/autogen/config.yaml. The UI likely allows adjuor example, if the user finds 180s backoff too long or short, they could change it). Also auto_accept_media_agreement` toggle. Possibly options for maximum concurrent prompts or anything (not explicitly in cybe user could limit how many to send total).
Also in Autogen settings might be where you configure the Sora URL or any CDP overrides, but those are advanced (the config has sora_url in portable_config, not exposed likely).
Additionally, maybe an option to automatically launch Chrome on startup for certain sessions (auto_launch_chrome flags) and auto start autogen (auto_launch_autogen can be idle/auto). But those might be configured in Workspaces UI rather than global settings.
FFmpeg – includes fields for ffmpeg.binary path (if user has a custom ffmpeg), video codec, CRF, preset (fast/medium/slow), whether to copy audio, etc. Also the post_chain fg for blurring (the UI might present this as a series of sub-settings or just a single text field – likely advanced users wouldilter string directly). Also the Blur Presets: A sub-section where the user can define the cr blur zones for different aspect ratios. The UI might not allow graphical editing, but maybes for each zone’s x,y,w,h. Or at least selecting the active preset. Possibly a preview function (the app had blur_preview.py which might generate an image showing blur zones on a dummy frame).
It also includes auto_watermark detection settings (if one wants ffmpeg to auto blur watermark via a simpler method) – but since our advanced removr, that might not be heavily used.
Chrome – settings for Chrome path and profiles. In the YAML we have a list of default profile locations. The UI could allow arofile entry (for custom Chrome location or using Chromium, etc.). It has fields:
Chrome executable path (if blank, using system default; user can browse te.exe or so if they want to override).
DevTools Port (global default port).
User Data Dir and Profile Dir (global override if they want to point Chrome to a specific profile outside defaults).
Possibly a butt all Chrome processes” or something if user needs to reset debugging (not sure, but maybe).
YouTube / TikTok – these might be separate tabs or combined:
YouTube: Fields to configure upload: perhaps a table of channels, each with Name, client_secret.json path, cre path. The UI might offer a “Connect Account” button which opens a browser for OAuth (the code does that in ensure_credentials with flow.run_local_server). The app config expects channels: [] – user can add channel with name and file paths. Once connected, credentials.json is saved. Also fields for scheduling: schedule_minutes_from_now (how far in future to schedule by default), draft_only toggle, batch_step_minutes (gap between viding multiple), batch_limit (max videos per run), archive_dir (where to move videos after upload).
TikTok: Possibly similar: a list of profiles (maybe representing TikTok accounts or device profiles). However, TikTok integration is via GitHub workflow. The UI might have fields for schedule (the YAML has schedule_enabled, schedule_minutes_from_now, batch_limit, etc.). Possibly a button “Trigger TikTok Upload Now” which would create a commit or run the workflow (maybe by calling GitHub API or local git push).
These features require more complex coordination and given they will be removed in the new implementation, we won’t detail further. In the original, if implemented, after merging videos the user could queue them for upload. The app’s uploader script would handle uploading using YouTube API (with the OAuth tokens from the config) – it can set publishAt times and mark as draft. The TikTok one might rely on a GitHub Action (the app could drop files in a repo folder and push).
Telegram – (though it has its own page, they also listed it under settings categories for completeness) shows bot token, chat id, etc., as described, plus list of templates here as well or just referencing to the main Telegram page. Possibly the Settings->Telegram is just an embedded version of the Telegram page’s confie UIs do that to ensure user can find it either place).
Interface – preferences for the UI behavior:
activity_density (Compact vs Comfortable log text spacing).
show_activity (toggle right-hand activity panel visibility).
show_context (toggle the context detail panel).
accent_kind (perhaps choose a color theme or an emoji set for statuses).
m Commands:** The user can define cummands or scripts to run from within the app. The YAML ui.custom_commands is a list of objects with presumably {name, command}. The UI likely provides a form to add commands with a display name and the command line to execute. These commands then appear in the top toolbar under a “🧭 Commands” menu and in the command palette (Ctrl+K) for quick access【2†L165-L172】. For example, a user might add a custom command to open a certain folder or to run a maintenance script. The app can execute these via Python’s subprocess when triggered.
Maintenance & Docs – possibly combined or separate:
Maintenance: The UI may have buttons like “Clear cache/temp files”, “Reset configuration to default”, and toggo_cleanup_on_start` and retention days for downloads, blurred, merged (as in config). The user can set how long to keep files before the app suggests cleaning (though deletion might run on startup if enabled). There might be a “Clean Now” button to purge files older than those limits immediately.
Auto-update: The app possibly has a button “Check for Updates” which runs scripts/self_update.py (if it was meant to do a git pull or pip update). This is advanced; since it’s offline, probably not automatic, but the code exists.
Documentation: A help section maybe with a markdown viewer that loads an included README or online docs. The UI might open an external browser to the GitHub or have an embedded viewer. The config’s maintenance group mentntation” and “full event log”. So likely the last tab might show the entire history log (concatenating history.jsonl, or some help file).
The settings changes typically apply immediately (the code calls save_cfg(self.cfg) after changes)【33†L1-L9】, but some might require restart (like changing directories might not move already loaded data; or changing Chrome binary might only apply on next ChromeThe original application’s modular breakdown in code is roughly:
app.py – Main application class (likely subclass of QMainWindow) that sets up the UI components, reads the config (with help of portable_config), and contains all the slots for UI actions and logic for starting/stopping workers. It orchestrates everything and updates the config and logs. It also likely includes the command palette implementation (Ctrl+K to search through pages or commands)【2†L17-L24】.
portable_config.py – Handles locating Chrome and preparing config paths (Documents folder, etc.) so the app can run portably. It’s used on startup to load config and ensure directories existnch Chrome.
blur_preview.py – Probably generates a preview image or short video snippet to show how blur presets will look. It might take the first frame of a video and apply the blur filter to show the zones. The UI might call this when user selects a different blur preset to update a preview on screen. Not critical for core, but a nice utility.
Workers package:
workers/autogen/main.py – Prompt injection and image gen logic (as covered in depth).
workers/downloader/download_all.py – video download (covered).
workers/watermark_cleaner/restore.py – watermark removal (covered), and its helper watermark_detector.py.
workers/uploader/upload_queue.py – YouTube uploader (we saw partial code showing OAuth and uploa API)【38†L1-L17】【38†L38-L47】. It likely reads environment like YOUTUBE_CHANNEL_NAME, finds that channel’s credentials, ensures tokens, then picks videos from upload_src_dir (merged videos), uploads them (title/description could be derived from titles.txt or maybe manifest of merges), and then moves them to archive. It supports scheduling: if YOUTUBE_PUBLISH_AT env is set (the app would compute a timestamp like now+schedule_minutes), it sets the video’s scheduled publish time (via YouTube API). If draft_only=1, it doesn’t schedule but leaves it private. Batch settings allow uploading a certain number and spacing them (maybe for every run if user sets up as a cron).
workers/tiktok/upload_queue.py – Possibly similar structure but instead of using an official API (TikTok didn’t have a public API for upload at the time), they used a GitHub Actions approach: It might simply move files to a watched directory or commit to a repo to trigger a workflow that uploads via a phone automation or some unofficial API. The presence of github_workflow and github_ref suggests it triggers a GitHub Action dispatch or instructs the user to push changes. The code likely logs steps like “[TT] Starting upload for file X”.
There might not be separate code for merging and blurring because those are done via ffmpeg command invoked from the main app (e.g., _run_merge_sync might call ffmpeg to concatenate files using ffmpeg -f concat or similar, or more simply might rely on a script in workers/downloader/downloads/ but likely not since merging is straightforward to do in Python by calling ffmpeg).
However, since blur_presets exists, maybe blur_preview.py and actual blur application might be done directly via ffmpeg filters in _run_blur_presets_sync() by building an ffmpeg command with the filter from config. Alternatively, they could have implemented blurring in Python with OpenCV for each zone but that’s slower; more likely they call ffmpeg with the configured post_chain filter for each zone’s coordinates (by overlaying boxblur on those coordinates). Given they had an ffmpeg.binary config, they likely spawn ffmpeg processes with appropriate filters (the post_chain appears to be a combination of filters applied globally to video). If needed, they may have a snippet in app.py for blur:
It would iterate all videos in blur_src_dir (downloads or restored), and for each:
Determine resolution (to pick preset zones).
Build a filtergraph: e.g., for each zone, something like crop=w:h:x:y,boxblur=... [blurred_zone_n]; [base][blurred_zone_n] overlay=x:y constructing a video where each zone is blurred and overlaid on base. The post_chain might be applied to the blurred areas or entire video as final touches (grain, sharpen).
Actually, they simplified it by using that post_chain (adds a slight blur+noise everywhere to hide imperfections), and the presets define zones presumably where be applied by ffmpeg’s boxblur. Possibly the integration is: if a user doesn’t use the advanced watermark remover, they can choose to blur out up to 3 common watermark positions using these presets. The UI’s Pipeline had a Blur checkbox for this. If blur is checked and a preset is active, _run_blur_presets_sync() will call ffmpeg on each file in, say, downloads/ to output to blurred/ directory, applying blur zones.
They also mention UI preview for blur meaning they likely load an image and draw rectangles to show where blur will happen (that's probably blur_preview.py duty).
Documentation / Help: The README we parsed is likely shown in the app’s documentation tab or an external link. The UI might have a “Help” that opens that README in the user’s browser or inside the app (PyQt can render markdown or HTML). They also might provide quick tooltips on fields.
To summarize main modules and their roles:
sora_suite/app.py: Main Application – coordinates UI events and launches worker processes. Implements:
UI Pages and their widgets (either building manually or via Qt .ui forms; given references to e.g. self.lst_automator etc., it looks like built in code).
Session management (start/stop tasks, maintain _session_cache with details like active_task, and update config on changes).
Logging (append to activity log, refresh UI elements).
Integrations (calls portable_config to launch Chrome, uses GenAI client to handle image generation calls, uses ffmpeg for blur/merge).
Provides functions _run_autogen_sync, _run_download_sync, etc., which run the respective processes synchronously by invoking ProcRunner objects that wrap QProcess or threads. The code snippet at the end shows _await_runner(runner, tag, starter) which likely starts a process and then waits on an event for it to finish, capturing exit code in _scenario_results dict【36†L12-L20】.
Houses the ProcRunner and SessionState constructs possibly to manage external processes.
UI utions (file dialogs, confirmation messages, etc).
sora_suite/portable_config.py: Chrome and Paths Config – deals with figuring out where to store data and how to find Chrome:
Searching for Chrome executables on different OS (_chrome_candidates())【23†L33-L61】.
Setting up SuiteConfig with default directories (like using user_documents_dir() from platformdirs to place default sora_suite/ in Documents if not running from source).
ensure_cdp_endpoint(cfg) to launch Chrome if not open on port【23†L128-L150】.
On Windows, uses CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS flags to not spawn a console【23†L132-L138】.
Waits for port to open or errors out if it fails.
sora_suite/workers/autogen/*: Autogen Module – includes:
config.yaml (timings and toggles),
selectors.yaml (DOM selectors),
prompts.txt (initial prompt file provided, but typically user’s data),
submitted.log, failed.log (which initbe empty or a small test content as we saw),
main.py (the heart of prompt automation),
(Possibly other files like a PromptEntry class definition, GenAiClient etc., defined within main.py as we saw dataclasses for Config and Spec).
sora_suite/workers/downloader/*: Downloader Module – includes:
download_config.yaml (we saw one with just 483 bytes – probably it might list some configuration like user agent or other environment toggles for downloader, but likely minimal because env is used).
download_all.py (the main script handling feed navigation and saving files).
Possibly it writes a download.log but we didn’t see one – maybe it logs to stdout and the app captures it, not to a file.
sora_suite/workers/watermark_cleaner/*: Watermark Cleaner – includes:
watermark_detector.py (detection routines),
restore.py (orchestrator),
__init__.py (maybe empty or short).
watermark.png likely provided by user (not in zip, but they mention default path).
sora_suite/workers/uploader/*: YouTube Uploader –
upload_queue.py (which handles both YouTube and TikTok depending on environment variables, or they copied it to tiktok subfolder but they appear identical in size listing except slight differences – likely one for YT API and one for some TikTok mechanism).
Possibly some config or data files for upload (like a JSON to track uploaded videos or to store video descriptions).
sora_suite/history.jsonl, sora_suite/titles.txt, etc., as data files.
Given all these, the new architecture will have to replicate these functionalities using the specified stack (Electron+React, etc.).
New Implementation: Electron + React (CodeGemini/Codex Oriented Tech Prompt)
Now, translating everything to the new stack and design: We aim to rebuild Sora Suite from scratch in a modern web-tech-based desktop app using:
Electron for the main desktop application container (Node.js in the main process, Chromium for UI).
React 19 (actually React 18 is latest at 2025, but assume they mean the newest React version) with Vite for the front-end development environment (fast bundler).
Tailwind CSS for styling, to create a modern dark-themed admin interface (likely similar aesthetic to original, but web-based).
Zustand for state management in React (a lightweight state store, good for maintaining global app state outside ofent tree).
puppeteer-core for controlling Chrome instead of Playwright. Puppeteer-core will connect to the Chrome instance(s) for autogen and scraping.
fluent-ffmpeg (an NPM library) to handle video processing tasks like merging and perhaps watermark removal (though fluent-ffmpeg is just an ffmpeg wrapper; the algorithm might require more manual image processing – we may integrate with OpenCV via a Node addon or use ffmpeg filters where possible).
We will drop all TikTok and YouTube upload functionality to focus on core features (content generation and processing). This simplifies the app (no OAuth or API integration needed in new version). We also restructure the UI:
Workspaces as separate cards: Instead of a single table list as before, we can present each Chrome profile as a card component in a grid or list. Each card will show the profile name, status, and have action buttons. We can also have a detail panel when you click a card to see logs and more controls (or a modal).
Automator as Visual Editor: We will build a UI where each step in the automation sequence is perhaps a draggable block or a row with fields (React could manage state of a steps array, with each step having type and details). We might allow parallel execution representation (maybe in future, but initial can stick to sequential flow as original did).
The mention of “try/catch” suggests perhaps enhancing Automator to allow error handling flows (like if a step fails, maybe continue next session or skip to another step). The original stops on error. The new could optionally allow marking a step as non-critical or provide a fallback. Implementing fully might be complex, but CodeGemini could haified try/catch mechanism if described.
At minimum, we should allow steps to have a flag “continue on error” or similar (then automator wouldn’t stop if that step fails, just logs it and moves on). This addresses the “try/catch” somewhat.
We also should display statuses of each step live (like a progress dot or color on each step box as it executes).
Settingsage: We will use a tabbed interface in React for settings categories like Paths, GenAI, FFmpeg, Chrome, Telegram, UI, etc., similar grouping as original. Tailwind can style tab buttons and content nicely.
Content Editor: We’ll implement a content management page where the user can switch between prompt files, image prompt file, and titles file, each displayed in a text editor (maybe a simple <textarea> or a code editor component for JSON).
Could use a library like Monaco or CodeMirror for better experience (especially for JSON editing with syntax highlight).
Provide save buttons or auto-save on blur.
Telegram: In the new UI, this will be more chat-like:
We can show messages in bubbles (right side for outgoing messages from te bot, possibly left side for any responses from the bot? But the bot likely isn’t interactive beyond echoing what we send as message in user’s chat).
We mainly show what we sent (with status and time) in a chat transcript style, instead of just a list. This means designing a component to display each message as a bubble with a timestamp and maybe a check mark icon for success.
The in remain with template selection and send button. The user’s templates can be a dropdown or maybe something like a quick insert menu.
The user can still manage templates via a modal or side list.
Essentially, mimic a messaging afor what is essentially one-way notifications for now.
History & Errors pages: We add:
History Page: which can list past automation runs or tasks in a table form. Perhaps each time an automator or pipeline scenario runs, we record it (like history.jsonl events, but we can aggregate by run). Fo create an entry “Run on 2025-11-22 12:00 – 10 prompts, downloaded 5 videos, status: partial” and allow the user to expand to see details or logs of that run.
Or simpler, we show the raw event log as a timeline with filtering options. But we already have Process Log for live view. History page could focus on completed runs, not every event.
The question explicitly says "Add pages: History (automation)" – likely meaning a page to see the history of automated runs. Possibly eachnario or pipeline run is logged and can be reviewed. We could implement it by reading the history.jsonl and grouping events. For example, find all events where event: scenario_start or event: autogen_start and show those as entries with start/end times and outcome.
**Errors Page: pulls out all error events or error log lines and displays them. This helps user quickly spot issues without scrolling through the entire process log. We could pa maintain an error log list (like filter lines with ❌ or ⚠️).
Or an advanced approactack traces or error messages from processes (like if a worker crashes or exceptions occur). Since the code doesn’t explicitly produce stack dumps (mostly just error statuses), the might just re-list the failed log prompts and any [ERR] lines from YouTube upload, etc.
We can gather from submitted/failed logs and from the events with state "error". Present them with context, maybe grouped by session or by time. Possibly provide suggestions or documentation links next to known errors (like if queue-limit error occurs often, we might hint "Consider increasing queue retry interval or reduce prompt rate").
Rem/YouTube:
UI: We won’t include the YouTube/TikTok settings tabs or pages. Also no upload step in automator or pipeline. Merge remains as final output stage.
Code: we won’t implement upload_queue. But we should keep Merged videos output for user’s manual upload if they want.
Limits relocation:
The downloader.max_videos and merge.group_size which were on pipeline page will be accessible through:
Possibly in Workspaces card: For each session, next to the Download button, we can have a small number input or a dropdown (like “Download: [ 5 ] videos”) or a settings icon that pops a modal to set that session’s download limit. Or even simpe a global default from Settings and if user wants to override per run, prompt them.
Another approach: when user clicks “Download” on a workspace, we can pop up a small dialog “Enter number of videos to download (0 for all): [ 5 ]” and an OK. If they cancel, do nothing. We can remember the last used value perhaps.
Merging similarly:licks a “Merge” button (which might be on the Workspaces page or maybe global if merging across sessions?), we can prompt fif needed (or take from settings).
Alternatively, put these in Settings:
Under “Pipeline/Download” category: a global default “Max videos to download per session Group size for merge”.
Then the workspace UI just triggers using those values unless overridden in a prompt.
The question specifically says “move download/merge limits to Workspaces or modal” – implying they should not be hidden in settings alone. Probably a modal on action is a good user experience so they don’t have to dig in settings or we don’t clutter each card with input fields that are rarely changed.
We will implement: On clicking the Download button for a workspace, if a default max is 0 (meaning “all”), just ad started. If a default max is set but user wants to adjust, they can right-click or a small dropdown arrow on the button to “Download last 5 / 10 / 20 / All” quick choices. If we want to keep it simple, a modal with numeric input will do.
Removal of Pipeline Tab: The explicit pipeline UI is gone, but the user can achieve the same via Automator (create a sequence with images->autogen->download->blur->watermark->session). We should ensure the new Automator can handle single-session flows easily (maybe provide a preset “Full Pipeline” that user can use or copy).
Also, the “Start/Stop All” top buttons from original can be replaced by enabling the user to run automations or individual tasks per session. A global “Stop All” might still be handy to kill any running processes across sessions (we can include that in a toolbar or menu).
“Start Selected” from pipeline is not needed as user will use either the workspace buttons (for one action) or automator (for multi-step).
Internally, we keep ogic if needed (like the scenario function in app.py) but it’s not directly exposed.
Backend Changes:
The Node main process will manage launching Chrome instances and controlling them:
We can use Puppeteer to launch Chrome with remote debugging, or we cr to run Chrome manually with a flag? Better to automate. Actually, because Electron itself comes with a Chromium instance, we might potentially use that as the controlled browser? But not exactly, we probably treat the video generation separately from the Electron UI browser. We will use puppeteer to either launch an external Chrome (user’s installed) or possibly use puppeteer-core to control a headless or headful Chrome window. But since the user might need to log in to Sora, we likely run it headful (so they can see or at least a visible window to solve Cloudflare).
We might mimic the original by launching real Chrome with user profile. Alternatively, use a Chrome instance in headless mode and intercept network to handle Cloudflare automatically? That could be complex; better to run headed and just instruct user to handle Cloudflare in that window if needed.
So the Electron main process can spawn Chrome processes with child_process.spawn or use puppeteer’s built-in chromium. Actually, using puppeteer normally spawns a bundled Chromium (unless configured to use system Chrome). We should allow specifying Chrome path (like original did).
We'll have to handle multiple sessions (if user wants multiple Chrome profiles). We can launch multiple Chrome instances on different debugging ports, or one Chrome with multiple contexts if Sora allows multiple logins (safer to do separate processes to isolate accounts).
The main process will store session info: for each session, a Browser instance from puppeteer and possibly references to pages.
The autogen logic will be translated to use puppeteer’s API (which is similar to Playwright but with differences in selectors and methods).
Playwright’s page.locator(...).first.click() is analogous to puppeteer’s page.$eval or using page.waitForSelector then click. Also, puppeteer doesn’t have built-in role selectors or :has() pseudo-class (Playwright’s has allows complex selectors). We might have to adjust selectors (e.g., use XPath for some or iterate all buttons and filter by innerHTML or use evaluate).
Alternatively, we could bring in Playwright to Node as well, but the requirement explicitly says puppeteer-core, likely to avoid shipping full Playwright.
We'll implement using puppeteer. It supports using Chrome DevTools Protocol directly for some tasks like intercepting network if needed.
The video downloading logic with puppeteer is similar: page.click triggers a download event, but in Node, capturing downloads is a bit different than Playwright. Puppeteer’s page.on('download') or browser.on('targetcreated') might be nally, puppeteer has page._client().send('Page.setDownloadBehavior', {behavior: 'allow', downloadPath: ...}) to allow auto download without prompt, or we can intercept the download link.
We can simply find the video URL (the code used expect_download which auto saved via Playwright’s context). In puppeteer, maybe easier approach: after clicking Download, the download might actually navigate or create a blob URL – but likely Sora triggers a real download via an <a> with href blob or so. Perhaps easier: we could fetch the video file by grabbing the video’s source URL from the <video> tag and downloading via HTTP (but the site might require an auth cookie).
Actually, in the script they did page.expect_download which implies the site triggers a download (perhaps via an href with download attribute). So in Chrome, that would normally open a save dialog unless there's an auto-accept. They likely had that by setting the context’s download behavior to allow and specify path (Playwright does that beed to do similar in puppeteer.
We'll use page._client().send('Page.setDownloadBehavior', {behavior: 'allow', downloadPath: <dir>}) to auto-save.
Then we can listen to page.on('response') for content disposition or something – but maybe simpler: once we allow, Chrome will save the file to that folder automatically. We'll then watch the file system for new files or use CDP events (Page.downloadWillBegin, etc. in new Chrome versions).
The alternative is to rely on obtaining the video link directly. But because Sora might watermark the video or something if downloaded through UI vs stream link might be different (not sure).
Cloudflare handling: We might have to detect Cloudflare page in puppeteer and then pause, maybe even pipe an alert to UI to have user solve in Chrome window. Possibly allow user to open the Chrome window by attaching via Chrome’s remote debugging with a GUI. Or since our puppeteer Chrome might be headless by default, perhaps run it headful so user can see a Chrome window and solve it. That requires coordination: maybe when Cloudflare is detected, we bring the Chrome window to front (if headful) or we screenshot it for UI to show user if headless.
Simpler: launch Chrome headful (with a window), but maybe minimized/in background, then if Cloudflare needed, notify user to bring it up. Because controlling a cloudflare challenge purely in code is difficult (requires solving a captcha).
The Node main process will also use fluent-ffmpeg to perform merge and possibly blur:
Merging: fluent-ffmpeg can concat videos either by re-encoding or using concat demuxer. We will re-encode or copy? The original likely re-encoded to ensure consistent encoding settings for final output (since it had CRF and preset defined). We will do the same: use ffmpeg to concat while re-encoding (or if group size is 1 essentially skip).
Blurring: We can use fluent-ffmpeg to apply filter_complex with boxblur. But building that filter programmatically is doable. Alternatively, since Node can call OpenCV (via opencv4nodejs) or canvas, but implementing multi-region blur is easier via ffmpeg filter.
We have the coordinates in config. We can create filter expressions:
example: [0:v]crop=157:62:30:105,boxblur=1:1[v0]; [0:v]crop=157:62:515:610,boxblur=1:1[v1]; [0:v]crop=157:62:30:1110,boxblur=1:1[v2]; [0:v][v0] overlay=30:105 [o1]; [o1][v1] overlay=515:610 [o2]; [o2][v2] overlay=30:1110
This would blur those three zones for preset portrait_9x16. The post_chain (noise, sharpen) can be applied after all overlays as one more filter stage.
fluent-ffmpeg allows specifying complex filters via .complexFilter([...]).
We'll implement blur in the Node process using ffmpeg (not via puppeteer).
Watermark removal: This is the hardest to port to Node. Options:
Use an OpenCV binding for Node (there is one called opencv4nodejs which is C++ addon). That would let us reuse similar logic: read frames, process, write video. But implementing that is quite involved and error-prone to get right in an AI code generation scenario.
Use fluent-ffmpeg with filters to attempt similar removal: There is no straightforward ffmpeg filter to remove a watermark by grabbing from other frames. FFmpeg has delogo which covers a region by interpolation (similar to inpaint), but not as advanced as our approach. There's no built-in median of multiple frames filter for a region. W filter to average frames but controlling it for specific region over time with ffmpeg is extremely complex, if not impossible, in one command.
Possibly skip advanced removal in codegen and instruct that for now, we may fall back to a simpler method: like use ffmpeg's delogo filter (which basically inpaints the region or fills with nearby pixels) which won't be as good as our algorithm but is easier to automate (just need coordinates).
r specifically outlined the algorithm and expects it to be replicated for exact behavior. Ideally, we reimplement it. Another approach:
We could actually reuse Python code for watermark removal by calling it from Node (like spawn the Python script as a subprocess when needed). But since we aim to rewrite fully, that might not be desired.
Another idea: Possibly use **OpenCV via WArenderer – there's OpenCV.js that can run in browser (though performance might be an issue for long videos and it can't easily handle video files, would need to load entire video or segments).
Or leverage GPU accelerated tools – maybe not feasible in codegen scope.
Foation with limited reasoning, maybe it's safer to implement simpler method with available tools (like delogo or a combination of ffmpeg filters).
But since the prompt escribes the full algorithm, CodeGemini might attempt to implement it in Node using opencv4nodejs. It's a tough task but possible. We'll describe at a high level that we will use opencv4nodejs or a similardetect and remove watermark, following the same steps. If we want to lighten it, we could mention "or use Python script via child_process as fallback," but that might diverge from the requirement of rewriting from scratch.
Let's plan to instruct CodeGemini to use opencv for Node. (It can be installed via npm openhich requires OpenCV installed on system or prebuilt binary, but let's assume we can get it working).
We will detail using opencv4nodejs: it gives access to cv.Mat and functions like matchTemplate, seamlessClone (not sure if the binding covers seamlessClone, but presumably yes if core functions are included), inpaint, etc. If not, we may have to implement clly with Poisson blending algorithm in JS which is too much. The fallback is inpaint, which OpenCV binding likely has.
It's an ambitious part, but since the user placed heavy detail on watermark, they likely expect it in new version too.
State management with Zustand: We'll keep app state like:
List of sessions (with fields like name, chrome status, active tasks, logs, etc.) – probably as a global store so multiple components (workspaces list, context panel, logs pcess it.
Current automator steps and presets – as another slice of state (especially since user can navigate away from automator page, we want to preserve the sequence in state).
Config settings – possibly keep in a Zustand store as well, loadile at startup. Could differentiate between persistent config (stored on disk via JSON) and transient UI state.
We need to ensure certain operations (like launching Chrome, starting autogen) happen in main process and we update the state in renderer via IPC.
Plan: Use Electron's ipcMain and ipcRenderer to communicate between React front-end and Node backend for actions like "start autogen on se * We can maintain state also in main process (since sessions and processes are managed there) and push updates to renderer via IPC events, then Zustand store updates accordingly. Or use something like electron-store for config persistence.
Design / Tailwind: The app will have a dark theme UI, likely withlike original). Tailwind can help with quick styling of grid, flex, spacing, etc. We should outline some components:
Sidebar with icons and labels for pages: Could be a vertical menu with icons (Home, Workspaces, Automator, Logs, History, Content, Telegram, Settings, etbly allow collapse grouping as original did (in original, clicking a group heading toggled subpages – in our case, we might not need that if pages are fewer, but we have many pages so maybe group as:
“General”: Home, Workspaces,
“Automation”: it existed) and Automator,
“Logs”: Session Logs, Process Log, History, * “Content”: Content editors,
“Integrations”: Telegram,
“Settings”: (with maybe subpage or modal).
But grouping could complicate navigation; maybe just list them flat or some minimal grouping.
Each page as a React component, loaded in main area. Right panel (context panel) we might drop in new design because we can incorporate context info directly into or instance, when viewing Workspaces, clicking a workspace can open a drawer or modal with details; the original had a persistent right panel to show active session context and quick actions from anywhere – we could mimic that with a context bar at top or a global status bar).
Possibly simpler: when user is on page, they see everything needed for sessions. On Automator page, they choose sessions per step, etc. A separate context panel might not be necessary.
However, they might want to always see some “Active tasks” view globally (like the right panel showing what’s running). We can implement an “Activity Panel” that can slide in or a status bar ating running tasks. The original had show/hide context panel toggle and show activity panel toggle in UI settings.
We can incorporate a small footer or sidebar section for current activities: e.g., "Session1: Autogen running (3/10 prompts) ... Session2: Idle".
Maybon in header that when clicked toggles an overlay of current tasks and logs – akin to a console drawer.
Use Tailwind to ensure consistency and dark mode styling. Use modern UI style: e.g., cards with slight shadow, toggles, modals with nice design.
In summary for new architecture: We will create a cohesive document to guide an AI (CodeGemini/Codex) to implement the above. It will list modules and responsibilities:
Main Process Modules (Node/Electron):
main.js (or electron/main.ts): sets up Electron app, creates BrowserWindow, and handles IPC events. It will also initialize puppeteer and manage Chrome processes and tasks. Possibly separate files for each aspect: chromeManager.js, tasks/downloadManager.js, etc., to keep code organized.
chromeManager: launches Chrome instances for sessions, keeps track of them, provides functionslose, and maybe ensures one instance per session with given user-data-dir (like original).
autogenManager: functions to handle connecting to Chrome via puppeteer and executing the prompt injection (likely inside an async function that does what d). It may utilize the same selectors and logic, but using puppeteer API.
downloadManager: similar, use puppeteer to scroll and download. Possibly integrate parts of autogen logic (like find first card).
watermarkRemover: uses opencv4nodejs to implement the detection & patching. This could be heavy but we'll outline it.
Possibly a unified taskManager that runs tasks sequentially or in parallel as needed and reports progress via events.
Renderer Process (React app):
We'll likely structure as pages: HomePage, WorkspacesPage, AutomatorPage, LogsPage, HistoryPage, ErrorsPage, ContentPage, TelegramPage, SettingsPage. Each corresponds to one menu item.
Use Rear navigation or our own simple state to switch pages (since it's a desktop app, either works).
Components for specific UI elements: e.g., WorkspaceCard, AutomatorStepRow, LogTable, TemplateList, etc.
Zustand store to hold:
sessions state array (with info like id, name, statuse only last few lines to show summary?), etc.). UI subscribes to updates to sessions.
automatorSteps and automatorPresets.
activityLog or globalLog for process log if needed (though process log could be just in LogsPage reading from main process events rather than storing all in React state).
config state if needed for two-way binding of settings form (or we can call IPC to load/save config on demand).
Perhaps telegramHistory for messages and telegramTemplates.
The renderer will request initial state from main on load (like asking for list of sessions from config, current statuses, logs, etc.), then subscribe ta IPC events (e.g., main sends an event when a prompt is submitted or done, or when a video downloaded, we update state accordingly).
Actions initiated by user in UI will call an IPC to main:
e.g., "startAutogen(sessionId)" triggers main to run autogen for that session. Main then emits progress events (like "promptSubmitted", "autogenFinished") which the renderer handles to update logs and status.
"stopAll" triggers main to kill all running tasks (close puppet pages or kill processes).
"generateImages(sessionIdn GenAI for that session's image prompts, etc.
"openChrome(sessionId)" triggers main to launch Chrome for that session (if not launched yet).
For some actions like editing prompt text or titles, the renderer might handle file writing directly by sending content to main to save. Or we allow Node to watch for file changes. Simpler is on save button, send content to main, main writes file to disk.
We need to ensure concurrency issues are handled (e.g., user might click multiple actions; main should queue or parallelize appropriately, perhaps restrict one autogen per session atc., which original did by active_task per session).
Given the complexity, our spec will likely be quite lengthy (which is expected). We'll break it down into sections, ensuring each part of original functionality is covered and explaining how to implement with the new stack. We'll now outline the final structure: We will use clear markdown headings as required:
Introduction (maybe just a quick note that this doc is technical prompt for CodeGemini).
Original Functionality Recap – possibly not needed in final since we've detailed above, but maybe a short summary at top.
New System Architecture Overview – describes the Electron/React separation, mention main/renderer responsibilities, and how modules map from old to new.
Electron Main Process:
maybe sub-sections for each major functionality:
Chrome Session Management (launching Chrome with puppeteer, maintaining multiple sessions)
Prompt Automation with Puppeteer (how to implement the logic from autogen/main.py using puppeteer; mention selectors conversion)
Image Generation (Google GenAI) (mention using official API if exists for Node or call a REST endpoint, but google-genai likely doesn’t have Node lib; maybe use REST or keep using Python if needed? Actually google-genai might have an HTTP endpoint – we can call it via fetch. Or skip details; say we'll use fetch with proper auth to some endpoint if available. This is a bit uncertain, but we can say use their REST API or find an npm if exists.)
Video Download Automation (scrolling feed with puppeteer, enabling downloads)
Video Processing (ffmpeg):
Blur (using fluent-ffmpeg filter)
Merge (using fluent-ffmpeg concat)
Watermark Removal Algorithm (with OpenCV in Node, replicating detection and patching. We'll outline using opencv4nodejs to do matchTemplate, etc., as in original).
Telegram Notifications (using node-fetch or an axios to call Telegram API from main, triggered by events)
IPC Communication (how main communicates with renderer for events and commands).
React Renderer (UI):
State Management (Zustand) – how we'll structure stores for sessions, logs, etc.
Workspaces UI – how it looks (cards or list), what actions each card has, how it reflects state (like show if Chrome running, number of prompts in queue, last error maybe).
Automator UI – design of step editor (list of steps with drop-down to select type, multi-select sessions, etc.), preset management UI, run/stop controls, displaying step execution status (maybe highlight current step).
Logs UI –
Session Logs: could be tabs or expandable sections for submitted/failed/download logs per session. Possibly combine them with filtering or se
Process Log (global): maybe a scrollin all events with colored icons.
History UI: maybe a table listing runs (columns: date, sessions involved, steps or tasks, result).
Errors UI: table or list of error events with details.
Content Editors UI – simple text editors for prompts, image_prompts (with JSON awareness if possible), titles (maybe a list with an index and text input per title).
Telegram UI – chat style: we'll specify to show messages in bubble format, list templates in a side or dropdown, etc., plus send and maybe schedule (we might omit schedule if not trivial, or implement a delay send).
Settings UI – multiple tabs (or one page with sections) for directories, generation, ffmpeg, chrome, interface. We'll specify key fields (like inputs for paths, sliders or inputs for numeric fields, toggles for booleans).
Overall Layout – a top bar or side menu for navigation. Possibly mention usage of Tailwind components (like using flex and grid etc.). We must ensure not to put images at headers per instruction (embedding images is optional and we were told not to search specifically for images; we have none relevant to embed because this is mostly code/spec content, not visual reference from web. We'll skip images).
Module Summary – maybe to satisfy "Name all modules and explain each", we can have a list summarizing the new app’s modules:
e.g., main.ts (Electron main), sessionManager.ts, autogen.ts, download.ts, watermark.ts, renderer/components/Workspaces.jsx, etc. But might be too much detail for CodeGemini. Perhaps a simpler description grouping by feature domain is okay. Or a table listing old modules vs new.
Possibly a short Conclusion or note that with this specification, CodeGemini/Codex can implement the entire app.
We'll incorporate examples where helpful:
We gave plenty in original context. We can include maybe a code snippet or pseudo-code for one puppet action to illustrate (like how to use puppeteer to find and fill the prompt).
Ensure to preserve important citations? But since this is internal reverse-engineering, external citations not needed except we have them in the text from search results. Actually, the answer right now has some citations like 【2†L9-L17】 from the README content. needed to keep in final? The instructions said preserve any citations from connected sources. lot from [2], [33], etc. The final likely expects those references to the origias evidence. It might be wise to leave them in because they show we referenced the user-provided a yes, keep the 【†】 citations for statements that came directly from thr code. This is a bit unusual since this is a tech prompt, but the guidelin keep citations. We'll incorporate them where relevant.
But must ensure they are at beginning of paragraphs if they correspond to images or embed (the rule about embed images not to cite at hea have no embed, only text citations, which can appear inline. We should double-check our citations usage:
We quoted README extensively with segments like【2†L9-L17】, which is fine.
It's already integrated above. We should ensure not to cite something incorrectly. All our citations are from user files (the loaded README and code). That should be fine.
Alright, let's compile the final answer with these sections.
Sora Suite – Project Technical Specification (Electron & React Rewrite)
1. Overview and Goals
Sora Suite is a desktop tool for automating AI video content creation. This specification describes a complete rewrite of Sora Suite using Electron, React 18+, Tailwind CSS, Zustand, puppeteer-core, and fluent-ffmpeg. The new application will preserve all core features of the original (multi-profile Chrome workspaces, prompt automation, image generation, video download, watermark removal, Telegram notifications, etc.) while adopting a modern stack and UI/UX. We will also omit deprecated functionality (TikTok/YouTube upload) and reorganize the interface for clarity. Key Goals of the Rewrite:
Electron Main Process (Node.js): Manage Chrome browser automation via puppeteer-core (replacing Playwright), handle filesystem operations (saving prompts, videos, etc.), and perform video processing with fluent-ffmpeg. Maintain multiple Chrome sessions (profiles) in parallel, each with its own DevTools port and user data dir (for Sora logins). Coordinate long-running tasks (prompt submission, downloads, watermark removal) and communicate progress/results to the front-end.
React Frontend (Renderer Process): Provide a modern dark-themed UI (styled with Tailwind CSS) for all control panels: Workspaces, Automator, Logs, Content editors, Telegram, Settings, etc. Use Zustand state management to keep UI in sync with the backend state (list of sessions, running tasks, logs, etc.). The UI should be intuitive: workspace cards for each Chrome profile, a visual automator editor for multi-step pipelines (with the ability to handle errors gracefully), real-time status updates, and chat-like Telegram interface for notifications.
Feature Parity and Enhancements: Replicate all major features of the original:
Multi-session Chrome handling with persistent profiles.
Automated prompt injection into Sora (connect via CDP, fill prompt fields, attach images, click generate, handle queue limits and errors)【6†L135-L143】【6†L146-L153】.
Image generation via Google GenAI API (Imagen) with user-configurable parameters and automatic attachment of generated images to corresponding prompts【2†L73-L82】【15†L4-L12】.
Auto-downloading of generated videos by scrolling the Sora feed and clicking “Download” on each, saving files with sequential or custom titles【18†L128-L137】【18†L139-L146】.
Watermark removal using the moving-logo restoration algorithm (detect watermark in frames, compute median background, seamless clone or inpaint)【2†L95-L103】【20†L68-L77】.
Logging and notifications (session-specific logs for prompts/videos, a global process log with timestamps and statuses【2†L123-L131】, and Telegram bot messages on key events).
A streamlined UI with clear sections (Workspaces, Automation, Logs, Content, Telegram, Settings). Remove the old “Pipeline” page in favor of a more powerful Automator and per-workspace quick actions. Move download/merge limit settings from a global pipeline form into either the Workspace UI or a modal when initiating those actions, for better context.
Throughout this document, we reference the original Sora Suite code and functionality (with snippets of code or config indicated by 【source†line-range】 citations). The implementation should adhere to the described behavior, but use the new technologies and structure described.
2. System Architecture Overview
The new Sora Suite will follow a typical Electron architecture with a Main process (Node.js) and a Renderer process (the React front-end). Communication between them will use IPC channels (ipcMain and ipcRenderer) with structured messages. Main Process Responsibilities:
Launch and manage Chrome instances for each workspace using puppeteer-core (or connect to existing ones via DevTools Protocol).
Perform automation tasks in Chrome: prompt submission and video downloading. These are long-running, asynchronous operations orchestrated by main.
Handle video processing using fluent-ffmpeg (blurring watermarks, merging clips) and possibly OpenCV for advanced watermark removal.
Manage file I/O: reading and writing config files (YAML/JSON), prompt files (prompts.txt, image_prompts.txt), title lists, and log files.
Integrate with external APIs (Google Imagen for image gen, Telegram API for notifications) via HTTP requests or SDKs.
Emit progress events and results to the Renderer so the UI can update (e.g., “Prompt X submitted”, “Download complete for video Y”, “Watermark removed for file Z”).
Renderer Process (React) Responsibilities:
Render the user interface: a sidebar for navigation and content panels for each page.
Use Zustand to maintain application state (e.g., list of workspaces and their statuses, current automator sequence, logs). The state is updated via IPC events from main (for example, when a prompt is submitted, the main process sends an IPC message which updates the state for that session’s log).
Provide interactive controls: buttons and forms that dispatch IPC commands to the main process. For example, when the user clicks “Start Autogen” on a workspace, the frontend sends an IPC call to main like start-autogen with the session ID.
Make use of Tailwind CSS utility classes to style components in a dark theme consistently. The layout should be responsive to the Electron window size (desktop use primarily, but allow resizing).
Implement the Automator editor as an interactive form where each step can be configured, reordered, and executed. Use React state to represent the sequence of steps and their parameters, and send this to main when running.
Display real-time logs and status indicators for each session and for global processes. For example, show a spinner or progress bar when tasks are running, green checkmarks for success, red exclamation for errors, etc., matching the original’s emoji/status scheme【2†L123-L131】.
Provide file editors for content (prompts, image prompts, titles) with proper formatting (possibly including JSON validation for image_prompts.txt). Save changes via IPC (or directly if using a shared filesystem access).
Electron IPC Design: Define channels for key actions and events, for example:
Commands (Renderer -> Main):
workspaces:launchChrome – launch Chrome for a given workspace.
workspaces:stopChrome – close Chrome for a workspace.
autogen:start – start prompt automation on a workspace (with optional flags like images-only).
autogen:stop – cancel prompt automation.
images:generate – trigger image generation for a workspace’s image prompts.
download:start – begin downloading videos for a workspace (possibly with a limit parameter).
download:stop – cancel downloads.
watermark:remove – run watermark removal on a given set of videos (e.g., all in a workspace or all new downloads).
merge:videos – merge videos in a directory (with group size param).
automator:run – execute a defined automation sequence (with steps and sessions specified).
automator:stop – stop the running automation (stop all underlying tasks).
telegram:send – send a Telegram message (with template or custom text).
config:save – update configuration settings.
content:savePrompts / saveImagePrompts / saveTitles – save edits from the content editor.
Events (Main -> Renderer):
workspace:status – informs of a workspace’s Chrome status change (e.g., launched, closed).
autogen:progress – each time a prompt is submitted or an error occurs. Payload might include session ID, prompt text or key, and status ("submitted", "failed: <reason>", etc.)【8†L1-L9】【8†L12-L20】.
autogen:complete – autogen finished for a session (success or partial)【6†L225-L233】.
images:generated – images generation done (with count or any error info).
download:progress – after each video downloaded or when starting/finishing downloads【18†L148-L156】.
download:complete – downloading finished (maybe with number downloaded vs requested).
watermark:progress – progress of watermark removal (e.g., “frame X of Y processed” or per file completion)【20†L79-L88】.
watermark:complete – watermark removal done for a set of files (success or errors count)【20†L93-L100】.
merge:complete – merging finished (with output file path or error).
automator:stepStatus – when an automator step starts, completes, or errors (with step index and status).
automator:finished – entire automation sequence finished (success or stopped on error, and at which step)【35†L23-L31】.
log:entry – a new entry for the global process log (with timestamp, message, level). Could bundle multiple into one message or send frequently.
telegram:sent – confirmation that a Telegram message was sent (or failed), so UI can update history.
The main process will maintain in-memory state for running tasks (e.g., which prompts are left in the queue, which step the automator is on) but will regularly inform the renderer via events so that the UI reflects the current state. Zustand store on the renderer side will merge these updates into its state. For persistence, configuration and user-authored files (prompts, titles, templates) will be saved to disk (likely in a JSON or YAML similar to original app_config.yaml, except we might break it into separate JSON files for ease – e.g., config.json, or reuse YAML if preferred). Now we detail the main subsystems and how to implement them:
3. Electron Main Process Implementation
3.1 Chrome Session Management (Workspaces)
We will implement a Workspace Manager (workspaces.ts in main process) to handle Chrome browser instances for each user-defined profile. Each workspace corresponds to a Chrome user profile directory and DevTools port, much like the original autogen.sessions config in YAML【3†L8-L17】【3†L27-L35】. Workspace Configuration and Launch:
On app startup, load the list of workspaces from config (including id, name, user_data_dir or profile identifier, and desired cdp_port if specified). We can use a JSON config like:
"workspaces": [
  { "id": "default", "name": "Session 1", "profilePath": "/path/to/profile1", "port": 9222 },
  { "id": "session2", "name": "Session 2", "profilePath": "/path/to/profile2", "port": 9223 }
]
This could be derived from original app_config.yaml where chrome.profiles and sessions were defined【3†L8-L17】. Alternatively, allow user to specify a base user-data-dir and profile name; the app will construct the profilePath.
For each workspace, we will manage a puppeteer Browser instance when it’s running. We’ll use puppeteer-core to either launch Chrome or connect to an existing instance:
Binary and Launch Options: Use the Chrome binary path from config (if provided) or puppeteer’s default Chromium. The original app tried to find Chrome across OS or used a configurable path【23†L33-L41】【23†L110-L119】. We’ll do similar:
If config.chrome.binary is set, use that; otherwise, attempt to locate Chrome/Chromium installation (we can use an npm package like chrome-launcher or manually check standard paths as in original portable_config).
For each workspace, call puppeteer.launch() with arguments:
puppeteer.launch({
  executablePath: chromePath,
  userDataDir: profilePath,
  headless: false, // run visible so user can solve captchas
  args: [
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profilePath}`,
    "--disable-features=AutomationControlled",
    "--no-first-run", "--no-default-browser-check",
    "--start-maximized"
  ]
});
This is analogous to how original launched Chrome via subprocess【23†L128-L136】. Puppeteer’s launch will handle starting the process. We pass the remote debugging port explicitly to match our config (ensuring we know how to connect later). We also disable some automation flags to reduce detection (same as original).
If Chrome is already running with the given profile and port (e.g., user launched it manually or from a previous session), we can use puppeteer.connect({browserURL: http://localhost:${port}`})` to attach instead of launch. The app could attempt connect first, and if that fails, then call launch.
Mark the workspace status as "Running" and store the Browser object in an in-memory map (workspaceId -> Browser). Also create a new BrowserContext or use default context for pages (since each userDataDir launch is a separate browser with its own default context).
Immediately navigate to Sora’s drafts page in a new page if needed. Puppeteer’s browser.newPage() then page.goto("https://sora.com/drafts") (using the actual URL from config, original default was sora.chatgpt.com/drafts【6†L97-L100】). We might wait for domcontentloaded or networkidle2. If the user needs to log in or pass Cloudflare, they can do so in this visible window.
Emit an IPC event workspace:status to renderer with status "launched" for that workspace (so UI can indicate Chrome is open). Also send the active page or an identifier if needed (though not strictly necessary for UI, mostly just status).
Auto-Launch & Auto-Close: If auto_launch_chrome is set for a workspace (in original config)【3†L20-L28】, we perform the above on app start. Otherwise, wait for user to trigger launch. Provide a function launchWorkspace(id) accessible via IPC, which does the above. Similarly, implement closeWorkspace(id) that calls browser.close() on that workspace’s Browser instance (which shuts down Chrome) and updates state (clear from map, emit status "closed"). Ensure to gracefully end any running tasks on that workspace first.
Maintain cdp_port usage: If a workspace’s port is in use, launching might fail. Puppeteer will throw an error if it can’t bind. The app can catch that and notify the user (UI error: “Port 9222 already in use, please choose a different port for Session 1”).
Multiple Sessions: We will launch each workspace on a distinct port so they run independently. They can run simultaneously – the user can generate prompts on Session 1 and Session 2 at the same time (the original supported parallel sessions, as each had its own port and queue【2†L41-L47】). Our architecture should allow concurrent operation (just ensure shared resources like ffmpeg usage or heavy CPU tasks might contend, but that’s expected).
DevTools vs Direct Control: We prefer controlling via puppeteer’s API (which uses DevTools under the hood). We won’t need to directly use CDP protocol unless to enable downloads (see downloading section). Puppeteer’s Page and ElementHandle will suffice for DOM manipulation. Session Data: For each workspace, track:
id, name
port
profilePath (user data directory path)
Running status (bool or enumeration: Stopped, Launching, Running)
Perhaps a reference to key Page(s): e.g., main drafts page, or currently active video page if one opened, etc. We can always get active pages from browser.pages(), but storing might help if we want to reuse it for repeated actions (like avoid opening new page for each prompt; better keep one page open for prompts injection).
Active tasks: e.g., if an autogen is running on that session, mark activeTask = "autogen" (like original session.auto_launch_autogen field in config was used to indicate state)【3†L20-L28】. This helps to prevent starting another autogen while one is in progress, and to know what to stop on cancellation. We can manage this via a separate task controller (see below).
3.2 Prompt Injection Automation (Autogen) with Puppeteer
The Autogen Manager (autogen.ts) will handle sending a series of prompts (and optional images) to Sora, using the Chrome instance managed above. This corresponds to the logic in original workers/autogen/main.py. Key steps to implement:
Setup and Prompt Loading: When the user triggers autogen for a session:
Ensure the workspace’s Chrome is launched and a page is open to the Sora drafting interface. We can use the page we navigated to .../drafts on launch. Alternatively, find an existing page with URL matching Sora (like browser.pages() then filter). In original, ensure_page did this search or opened a new one【6†L91-L100】.
Load prompt lines from the session’s prompt file (prompts_<profile>.txt or default prompts.txt). We can read the file from disk (UTF-8). Parse into an array of prompt strings. Also load submitted.log to skip already sent prompts: We will have a log file (or we maintain a JS Set in config memory) of prompt keys that were sent. Original load_submitted() function read the log file and built a set of keys to exclude【15†L37-L46】. We can do similarly: parse each line of submitted log, taking the third column (key)【8†L1-L9】. The key is either provided by user or we can derive one. For simplicity, use the prompt text itself as a unique key (after normalization) if no explicit key. (In original, they sometimes had key in manifest mapping or PromptEntry; they ensure uniqueness by key or use prompt text if no key)【15†L37-L46】.
Filter the prompt list to only those not in submittedKeys. If no new prompts, we can log and exit immediately (“No new prompts – all prompts have been submitted” as original did)【6†L203-L210】.
If image attachment is enabled (attach_to_sora in config is true【3†L50-L58】 and GenAI is configured), also load the image_prompts.txt and manifest.json:
Parse image_prompts.txt which may contain JSON lines or plain lines【17†L1-L10】. Create an array of ImagePromptSpec objects with fields prompts[], count, video_prompt, key.
Read manifest.json (if exists) which has entries of generated images【13†L13-L27】. We will likely generate this manifest when images are created (discussed in image generation section). For now, assume it’s up-to-date.
Create a mapping from a prompt key or text to the image file paths: e.g., if manifest entry has "video_prompt": "A day in the life of a blacksmith." with files ["001-01.jpg"], then map that prompt text to that image file. Or if entry has a key and our text prompts have a corresponding key, use that mapping.
Prepare a list of PromptEntries (like original code’s PromptEntry class) for each prompt to send, including any attached images. For each prompt:
Determine its unique key: original used either an explicit key (like in prompts file if user wrote something like prompt ###key) or a hash of prompt text【15†L37-L46】. We can simply use the prompt text trimmed as key (since user likely ensures uniqueness).
If image manifest has an entry matching this prompt (by key or by exact video_prompt text), attach those image file paths to the PromptEntry.
If consistent_character_design or other style prompts from config should be applied, the original tool might append those to the prompt text or handle differently – for simplicity, we assume prompt text is final.
Also, if any ImagePromptSpec in image_prompts has prompts array and count, our manifest will have possibly multiple images. If multiple images are associated with a single video prompt, we will attach all of them (the Sora UI likely allows multiple image attachments; original logic uploads all and waits for each to appear【6†L129-L139】).
Prepare an internal queue (e.g., an array or deque) of prompt entries to submit.
CDP Connection: We already have a puppeteer Page for Sora. We will use that for injection:
If the page is currently on the drafts list, we may need to open the first draft or ensure a blank draft editor is open. Sora’s interface might have a “New draft” or might allow typing directly on drafts page. In case it requires clicking something to get to an input, mimic original:
The original ensure_page tried to find a page with URL containing “/drafts” and if blank opened that URL【6†L93-L100】. Then find_sora_page(context, hint="sora") likely got the correct page. The original code then did page.bringToFront() and waited for load state. We can do await page.bringToFront() and maybe await page.waitForSelector(textareaSelector).
Select Prompt Input Field: Use the CSS selectors from the original selectors.yaml to find the prompt text box【4†L1-L9】. For instance, textarea.css: "textarea, [contenteditable='true']" is a broad selector for any text input area【4†L1-L4】. We can do:
const textArea = await page.$("textarea, [contenteditable='true']");
If not found, try the alternatives (those with placeholders or data-testid)【4†L3-L9】 via page.$ or even use XPath for contains text. Or we can use a function similar to original resolve_textarea() which tried multiple approaches and also role selectors. Since Playwright supports get_by_role('textbox'), but puppeteer doesn’t directly, we will rely on CSS or XPath:
Attempt page.$("textarea"), if null, page.$("[contenteditable='true']"). If still null, try page.$("textarea[placeholder*='Describe'], [data-testid*='textbox']") (from textarea_alternatives)【4†L5-L9】.
If still not found, we might need to ensure a draft is open. Possibly the user must click "New Draft" button first. If Sora’s UI has a “+ New Draft” button (maybe represented by a plus icon), we might need to click it to get an editor. Original code doesn’t explicitly mention this, but it might be implied by focusing first card or a new draft.
If needed, find a “New” or first draft element. The downloader code found CARD_LINKS = "a[href*='/d/']" for drafts【18†L65-L73】 – we could use similar to open the first draft to get an editor input visible.
Once the input field is found, store a handle to it (textArea ElementHandle).
Find Submit Button: Use selectors to locate the generate/send button. The original logic find_button_in_same_container looked for a <button> with an SVG icon near the textarea【6†L1-L13】【6†L36-L46】. We can simplify with a direct selector if possible:
In selectors.yaml, generate_button.css is "button:has(svg), [role='button']:has(svg)"【4†L10-L12】. Playwright supports :has(), but puppeteer doesn’t support :has in page.$ (as of now). We must find via another method:
Use page.$$("button") to get all buttons, then for each, use element.$("svg") to check if it has an SVG child. Or use page.evaluate to filter buttons containing an <svg>.
Or use XPath: //button[.//svg] could find any button containing svg.
Then refine: original ignored buttons that were in dialog or too far from textarea’s Y position, and filtered out add-media buttons (ones containing "add")【6†L14-L23】【6†L36-L46】. We might not need all those heuristics if we can identify the send button uniquely (perhaps it has a data-testid or aria-label). If not, replicate logic:
Compute bounding box of textarea via element.boundingBox() and get its vertical center.
Among candidate buttons with an SVG, filter by bounding box width/height ~ 32-80px (common size for icon buttons)【6†L20-L27】.
Filter by vertical proximity: accept buttons whose center Y is within ~120px of the textarea’s center Y【6†L28-L35】.
Filter out if button text includes "add" (to avoid an "Add media" plus button)【6†L30-L35】.
Also filter out slider-looking buttons (the original looked inside SVG for <line> or multiple <rect> to avoid picking a volume slider or similar)【6†L36-L46】.
Finally, pick the rightmost of remaining (often the send button is on right)【6†L47-L55】.
This is a lot of detail; we can attempt a simpler approach: look for a button with an SVG and perhaps an aria-label like “Send” or a title. If Sora’s send button has no accessible text, the above method is robust. We'll implement it similarly:
const buttons = await page.$$("button");
const sendButtons = [];
for (let btn of buttons) {
  const hasSvg = await btn.$("svg") !== null;
  if (!hasSvg) continue;
  // bounding box and other checks
  const box = await btn.boundingBox();
  if (!box) continue;
  if (box.width < 32 || box.width > 80 || box.height < 32 || box.height > 80) continue;
  const taCenterY = textAreaCenterY; // precomputed
  const btnCenterY = box.y + box.height/2;
  if (Math.abs(btnCenterY - taCenterY) > 120) continue;
  const btnText = await page.evaluate(el => el.innerText || el.getAttribute('aria-label') || "", btn);
  if (btnText.toLowerCase().includes("add")) continue;
  // check svg content
  const looksLikeSlider = await page.evaluate(el => {
    const svg = el.querySelector('svg');
    if (!svg) return false;
    if (svg.querySelector('line')) return true;
    const rects = svg.querySelectorAll('rect');
    return rects.length >= 3;
  }, btn);
  if (looksLikeSlider) continue;
  sendButtons.push({btn, x: box.x, slider: looksLikeSlider});
}
if (sendButtons.length === 0) throw new Error("Send button not found");
// sort by slider first then by x position
sendButtons.sort((a, b) => (a.slider?1:0) - (b.slider?1:0) || (b.x - a.x));
const sendButton = sendButtons[0].btn;
This mirrors the original logic【6†L36-L46】【6†L47-L55】. We then have sendButton ElementHandle.
If for some reason we fail to find it, as a fallback use the generate_button.css directly by evaluating document.querySelector with :has through page.evaluate:
const sendBtnHandle = await page.evaluateHandle(() => {
  const el = document.querySelector("button:has(svg)");
  return el;
});
Playwright supports :has, but Chrome’s native querySelector may not (currently :has is a CSS4 selector not widely implemented yet in 2025). So fallback might not work in all cases. Best to rely on the filtering logic above or known structure of Sora’s HTML if available.
Media Upload Selector: Find the add-media button and file input if we plan to attach images:
From selectors.yaml, image_upload.trigger lists various selectors for buttons that open media upload【4†L10-L18】. We can attempt to find one of those:
e.g., a button with text 'Upload' or 'Add media', a data-testid containing 'add-media', aria-label containing 'Add media', or a generic plus sign button【4†L11-L18】.
We can combine: const trigger = await page.$("button:has-text('Upload'), button:has-text('Add media'), [data-testid*='add-media'], button[aria-label*='Add media']");
(Note: puppeteer lacks a built-in :has-text() but we can use XPath or evaluate innerText. Alternatively, use page.$x("//button[contains(., 'Upload') or contains(., 'Add media')]").)
Or iterate all buttons again and look for plus sign icon: original included button:has-text('+'), button:has(svg[aria-label*='+']) etc.【4†L14-L18】. Possibly simpler: just before starting attachments, click a button with an SVG that looks like a plus:
We might attempt page.click("button svg[data-icon*='plus']") or so if Sora uses FontAwesome or similar (the original selectors aren't entirely clear without actual HTML).
For robustness, implement a small function to try multiple query selectors from the YAML list until one returns an element.
The image_upload.css is "input[type='file']"【4†L10-L12】, and clear is a selector for any "remove media" buttons to clear previous attachments【4†L15-L18】.
We'll use these in the actual upload function when preparing each prompt submission (discussed below).
Submitting Prompts Loop: We will iterate through the prompt queue and submit each prompt one by one (the original allowed multiple in parallel only via multiple sessions, not concurrent in one session). For each PromptEntry in queue:
Type text into textarea:
Clear any existing text first. Puppeteer’s elementHandle.type can type into an input, but we might prefer to directly set the value like original did to preserve formatting/newlines:
Original used js_inject_text which set el.value = text for <textarea> or el.innerText = text for contenteditable, then dispatched input/change events【6†L71-L81】.
We can do similar via page.evaluate on the textarea element:
await page.evaluate((el, text) => {
  const isTextarea = el.tagName.toLowerCase() === 'textarea';
  const isCE = el.getAttribute('contenteditable') === 'true';
  if (isTextarea) {
    el.value = text;
    el.dispatchEvent(new Event('input', { bubbles:true }));
    el.dispatchEvent(new Event('change', { bubbles:true }));
  } else if (isCE) {
    el.innerText = text;
    el.dispatchEvent(new Event('input', { bubbles:true }));
    el.dispatchEvent(new Event('change', { bubbles:true }));
  }
}, textArea, promptText);
This injects the prompt text instantly. We should focus the element first (though setting value might implicitly focus? safer: await textArea.focus() before evaluate).
Optionally, simulate a small human-like typing after injection to trigger any dynamic UI enabling the send button (the original typed space+backspace, period+backspace to ensure the UI picks up input【6†L82-L90】). We can do:
await textArea.type(" ", {delay: 5});
await page.keyboard.press("Backspace");
await textArea.type(".", {delay: humanDelay}); // humanDelay from config, e.g., 12 ms
await page.keyboard.press("Backspace");
This mimics the original type_prompt function's behavior【6†L82-L90】.
Attach images if any: If the PromptEntry has attachment file paths:
Clear previous attachments (if the Sora UI keeps images from last prompt until removed). The YAML clear selector is used to find a "remove media" button【4†L15-L18】. We can do:
const clearButtons = await page.$$(selectors.image_upload.clear);
for (let cb of clearButtons) {
  try { await cb.click({ timeout: 2000 }); } catch(e) { /* ignore if not present or clickable */ }
}
This attempts to click all remove icons (like little "x" on each attached media thumbnail).
Click the trigger to open file dialog:
const trigger = await page.$(selectors.image_upload.trigger); 
if (trigger) { await trigger.click(); await page.waitForTimeout(200); }
(If not found, maybe the plus button is always visible and the input is hidden, we might skip clicking if not needed.)
Set files on the file input field:
const fileInput = await page.$(selectors.image_upload.css);
if (!fileInput) throw new Error("File input not found for media upload");
await fileInput.uploadFile(...attachmentPaths);
Puppeteer’s uploadFile will attach the files from disk to that input. Since it’s not a visible action, the Sora UI should then show previews.
Wait for the UI to confirm attachments loaded. The YAML suggests wait_for: "img, video" after adding media【4†L16-L18】, and a timeout ~8000ms. We can:
try {
  await page.waitForSelector(selectors.image_upload.wait_for, { timeout: 8000 });
} catch(e) {
  console.warn("Media preview did not appear in time");
}
This will wait until at least an <img> or <video> element appears (the preview of uploaded media).
If multiple images are to be attached, ensure all are set in one uploadFile call (if Sora allows multi-select, likely yes; fileInput.uploadFile can take multiple file paths). Original gather_media function packaged all attachments in one list and one upload attempt【6†L109-L119】【6†L121-L130】.
If file input wasn’t found or fails, we log a warning and continue without media for that prompt (or mark it failed due to "media-upload" error as original did)【6†L131-L139】.
Click Send: Before clicking, ensure the button is enabled (not disabled due to empty input or rate limit):
We can check await page.evaluate(el => !el.disabled && el.getAttribute('data-disabled')!=='true', sendButton) as original is_button_enabled_handle did【6†L49-L56】. If it’s not enabled, wait a bit and re-check until it becomes enabled. Use config retry_interval_ms (e.g. 2500ms) to poll.
Then do await sendButton.click({ timeout: 8000 }). Wrap in try/catch to handle if click times out (which could happen if button never enabled or page not responsive).
Confirmation & Error Handling: After clicking, we need to confirm if the prompt was accepted:
The original logic confirm_start_strict polled until either the textarea cleared (meaning Sora accepted and blanked the input) or the queue of generating videos increased by one, or an error toast appeared【6†L58-L66】【6†L66-L75】.
Implementation:
const beforeQueueCount = await countQueueItems(page);
const startTime = Date.now();
let accepted = false;
while (Date.now() - startTime < startConfirmTimeout) {
  // Check for error toast
  const toast = await page.$(selectors.error_toast.container);
  if (toast) {
    const text = await page.evaluate(el => el.innerText.toLowerCase(), toast);
    if (selectors.error_toast.text_contains.some(substr => text.includes(substr.toLowerCase()))) {
      // an error toast indicating limit or queue full appeared
      accepted = false;
      break;
    }
  }
  // Check if textarea cleared
  const currentVal = await page.evaluate(el => el.value || el.innerText || "", textArea);
  if (currentVal.trim() === "") {
    accepted = true;
    break;
  }
  // Check queue count increased
  const afterQueueCount = await countQueueItems(page);
  if (afterQueueCount > beforeQueueCount) {
    accepted = true;
    break;
  }
  await new Promise(res => setTimeout(res, 200));
}
if (!accepted) { /* handle as failure (likely queue full) */ }
Here countQueueItems(page) will count elements matching queue_generating.css and queuthem【6†L58-L66】. In YAML, queue_generating.csswas a selector for an element indicating generation in progress, andqueue_ready.css` for completed in queue【4†L19-L27】. Possibly Sora shows a list or counter of queued videos; we attempt to detect an increment. We can approximate: after clicking generate, Sora might add a “Generating...” card or change an icon from idle to generating. The provided selectors are likely correct for that.
If the loop exits via error toast detection, we treat it as not accepted (and likely queue-limit error).
If it exits by time (startConfirmTimeout e.g. 9000ms) with no success, also consider it failure.
If accepted remains false, it likely means the Sora queue was full or the request was ignored.
If accepted:
Log success: Append to submitted.log: a line with timestamp, session name, key, prompt text, and “[media: names]” if any attachments【8†L1-L9】. We do this via Node FS.
Mark prompt as submitted (e.g., add its key to submittedKeys set to avoid resending in future).
Optionally, pause if a success pause is configured (original success_pause_every_n etc. to avoid hitting limits)【6†L168-L176】. For exa 2 successes, pause 180s, implement that:
successCount++;
if (successPauseEveryN > 0 && successPauseSeconds > 0 && successCount % successPauseEveryN === 0 && queue.length > 0) {
  await new Promise(res => setTimeout(res, successPauseSeconds * 1000));
}
This matches original behavior of pausing after certain number of prompts【6†L168-L176】.
If not accepted:
Check if we saw a specific error reason (like queue limit). The original submit_prompt_once returned a failure reason like "queue-limit/backoff-180s" if a toast was present【6†L146-L154】.
If the error was a known rate limit/queue full scenario (e.g., toast text included “limit” or “too many” as per selectors.error_toast.text_contains【4†L19-L27】), then we implement a backoff:
Original did: print "[RETRY] queue-limit/backoff-{backoffSeconds}s"【6†L148-L152】, wait backoff_seconds_on_reject (which default 180s)【6†L149-L153】, then mark the prompt for retry.
We maintain a retry list (or simply push this prompt entry to the end of the queue or a separate deque).
Mark in failed.log that it failed (with reason), e.g., timestamp, session, key, prompt, queue-limit/backoff-180s【8†L12-L20】.
If the failure reason is something else (like network error or media upload fail), also log to failed.log accordingly (with reason "media-upload" or a generic).
Use exponential backoff or constant as config says. The YAML had backoff_seconds_on_reject: 180【30†L1-L9】. We'll trust that or user config.
If failure was due to something non-retryable (like maybe content violation, though none specified in original), we could skip or handle differently. The original mainly anticipated queue full issues as retryable.
Continue to next prompt in queue (or if failure was queued for retry, handle after initial queue done).
Retries: After going through initial queue, implement a retry loop similar to original:
If the retryQueue is not empty (prompts that failed due to queue limit), then do another cycle:
let retryRound = 1;
while (retryQueue.length > 0) {
  console.log(`Retrying failed prompts, round #${retryRound}...`);
  const currentRetries = retryQueue.slice();
  retryQueue = [];
  for (let entry of currentRetries) {
    // (similar submission logic as above)
    // If still fails, and reason still queue-limit, push back to retryQueue again.
    // Mark failed log with reason "retry:<reason>" if fails again【8†L15-L20】.
  }
  retryRound++;
  // optional delay between cycles (original waited 20s after a full round)【6†L199-L206】
  await new Promise(res => setTimeout(res, 20000));
}
The loop stops when either all retry prompts eventually succeeded or after a number of cycles (the original code would loop indefinitely until success, but had backoff so eventually it’d either succeed or keep waiting; in worst case, it prints warnings continuously).
For safety, we might decide on a max retries count or time (the original did not set a hard limit, potentially dangerous but user can stop manually).
Each retry attempt also logs to failed.log if it fails again, prefixing reason with "retry:"【8†L15-L20】.
When a prompt finally succeeds on a retry, mark it submitted in log as normal all prompts and retries:
Log a summary: e.g., to process log and/or console, like [STAT] success=X failed=Y elapsed=Zs【6†L225-L233】.
Emit an IPC event `autogen:comstatus (OK if no failures, Partial if some failed but we exhausted retries)【6†L225-L233】.
Write a [NOTIFY] AUTOGEN_FINISH_OK or _PARTIAL event which may trigger a Telegram message if configured【22†L1-L9】.
If the user qumator sequence, signal to automator manager that this step finished (with success or error).
Throughout submission, send progress events to UI:
On each prompt submitted successfully, send autogen:progress with e.g. { sessionId, promptKey, status: "submitted" }. On each failure, send a similar event with status "failed" and reason.
Perhaps also send an event when starting the autogen and when finishing, for UI to dnd global log.
This covers prompt injection in detail. Corner cases:
Login required: If the user is not logged in to Sora, the autopilot will likely not find the textarea or will find a login page. We should detect if the page is on a login URL and pause or notify user. Possibly watch for something like if (page.url().includes("/login")) { emit event "workspace:loginRequired"; }. The user can then login manually in that Chrome window and then we can resume (maybe user re-click start or we auto-detect login completion).
Cloudflare challenge: If Cloudflare's anti-bot is triggered when sending prompt (maybe after many submissions?), Sora might throw up a "Verify you are human". The selectors.yaml had clues in error_toast ("verify you are human" might be in text)【18†L170-L180】. If we detect that, we should pause and notify user to solve in browser (similar to how downloader handles Cloudflare with [NOTIFY] CLOUDFLARE_ALERT【18†L170-L180】). Implementation:
If error_toast_present found "verify you are human" text, or any known Cloudflare patterns (the downloader’s _cloudflare_detected looked for certain phrases in body and presence of challenge forms/frames【18†L170-L180】), then:
Emit NOTIFY: CLOUDFLARE_ALERT event (UI can show a big warning).
Wait in a loop (similar to downloader’s _wait_for_cloudflare【18†L170-L180】) until the page no longer shows the challenge:
if (detectCloudflare(page)) {
  notifyUserCF();
  let cfResolved = false;
  while (!cfResolved) {
    await new Promise(res => setTimeout(res, 1200));
    cfResolved = !(await detectCloudflare(page));
    if (!cfResolved && attempt % 5 == 0) console.log("Still waiting for Cloudflare...");
  }
  console.log("Cloudflare resolved, continuing.");
  await page.waitForTimeout(800);
}
where detectCloudflare(page) checks for text like "checking if site connection is secure" or challenge form presence (mirror logic from _cloudflare_detected【18†L170-L180】).
After it’s cleared, continue with prompt submission or retry the last action.
This ensures the automation doesn’t crash on Cloudflare but waits for user to intervene, exactly as original design did (they printed [!] waiting for Cloudflare... loops)【18†L174-L180】.
Media upload failure: If attaching an image fails (file missing or input not found), we marked that prompt as failed with reason "media-upload". Possibly we skip retries for that because it’s likely a permanent error (the file wasn’t found or similar). But if it’s transient, user can manually fix maybe. We can just log it and not requeue (or requeue but with no media? That could send prompt without image as fallback, but origdn’t explicitly do that except in mark_failed they allowed reason and moved on).
Memory management: If a user submits hundreds of prompts with images, make sure we’re not retaining large ElementHandles or image buffers. Puppeteer automatically w images fully unless needed, we should be fine. We should occasionally consider calling textArea.dispose() or handles didone, but since we reuse same page, not critical.
3.3 Image Generation via Google GenAI (Imagen API)
The new app will incorporate Google’s Imagen API for image generation (the original used google-genai Python library). In the Node context, we may not have an official library, but we can call a REST endpoint or use their client if available via import. For CodeGemini, we outline using a hypothetical HTTP API call. We will implement an ImageGen Manager (genai.ts) responsible for generating imats and storing them in generated_images/ directory, updating manifest.json. Configuration: The user provides:
api_key (we’ll store it in config, possibly allow environment variable override).
Model name (default "imagen-4.0-generate-001")【3†L36-L43】.
Preferences like aspect r., "1:1", "1024x1024"), number_of_images per prompt, etc. (All from google_genai config)【3†L36-L43】. We parse those from config.
Output directory for images (generated_images/) and manifest)【3†L36-L43】.
Rate limiting settings (per minute) and daily quota (we can track usage to avoid exceeding).
Style parameters (like personas, lecolor_palette, etc.) which we might combine into the prompt or send via API if supported. (Original config has fields like seeds list, consistent_character_design toggle, etc. which their library likely accepted【17†L9-L18】.)
Triggering Generation: The user can either:
Click a "Generate Images" button (for all image prompts), possibly on the Content page or a dedicated button in Workspaces or Pipeline.
Or as part of Automator: a step "Session Images" that generates images for certain sessions.
We will implement a ferateImages(sessionId, options)`:
If sessionId is provided, filter the image_prompts to only those relevant to that session’s prompts profile if needed (though by default image_prompts.txt is global, not per session).
Read image_prompts.txt. It may contain multiple lines with JSON or text:
If a line is JSON with "prompts": [...] array and optional "count", treat it as one spec that can yield multiple images. If "prompt" is a single string, treat it as array of length 1.
If a line is plain text, interpret it as { "prot text>"], "count": 1 }.
Each spec can also have "video_prompt" and "key" to link to a video prompt.
For each spec (enumerate them for indexing in manifest):
For each sub-prompt in spec.prompts:
Determine how many images to generate: use spec.count if given, else global number_of_images setting.
Prepare the API request:
Construct the prompt string. Possibly include style presets: e.g., if config.style or lens_type are set and not blank, append them. The original had fields like style, color_palette, which likely were used by adding "in <style> style" or similar. If consistent_character_dmaybe the API requires additional context to keep character same across images (but that might be an advanced feature outside this scope).
Use the `aputhentication, and the model name in the request.
The Google GenAI might have an endpoint like https://imagen.googleapis.com/v1beta/generateImage (for illustration). We send an HTTP POST with JSON:
{
  "prompt": "<image prompt text>",
  "model": "models/imagen-4.0-generate-001",
  "a "1:1",
  "resolution": "1024x1024",
  "num_images": count
  // plus anymeters
}
and include Authorization: Bearer <API_KEY> header or similar (depending on API design; if it's an API key maybe query param).
We need to abide by rate_limit_per_minute: if set (e.g., 0 means none, else number), ensure we do not send requests faster. We can use a simple token bucket or just add delays between calls. Similarly track daily usage if daily_quota > 0, and if reached, stop further generation and notify user (original printed a warning and either enforced stop if quota_enforce true【17†L31-L44】).
The call returns either image bytes or a URL to download them. Likely it returns base64 or binary. Since we probably get binde’s https to fetch and then write files.
Save the returned images:
We will generate a file name for each: original used a tag scheme: <spec_index+1 padded>-<prompt_index+1 padded>.<ext>【15†L4-L12】. E.g., spec 0 prompt 1 -> "001-01.jpg". We can do similar with 3-digit spec and 2-digit prompt index to sort nicely. If count > 1 for a single prompt, they added an index too (they loop prompt_idx and within that loop generate count images but they extended collected list and recorded ame spec).
Alternatively, since we know how many images will come for a spec, we can increment a sequence for files. But to keep manifest sorted, do as they did:
Tag = ${(specIndex+1).toString().padStart(3,'0')}-${(promptIndex+1).toString().padStart(2,'0')} for first image of that prompt variant, then second image (if count>1) maybe as -01a or simply incrIndex? Actually, in code for prompt_idx, prompt in enumerate(prompts, start=1) and then count images, they extend collected and t.record with all files【15†L4-L12】. So for multiple images per prompt variant, they dide tag logic; they'd get multiple images for same tag? But they possibly named them 003-02 (s prompt variant's first image) etc. Actually, reading:
tag = f"{spec_index + 1:03d}-{prompt_idx:02d}"
generated = client.generate(prompt, count, tag)
if generated: collecterated)
So if count>1, client.generate might name them with tag plus an index internally (maybe appending -1, -2). Or client.generate returned a list of file Paths, how did it name them? Possibly google-genai lib saved files like <tag>-1.jpg, <tag>-2.jpg if multiple. We may mimic that:
If generating N images for a prompt, we will produce files like 001-01-1.jpg, 001-01-2.jpg etc. Or simpler 001-01.jpg, 001-02.jpg both assigned to first prompt variant if it's 2 images? That would collide with second prompt variant though.
Actually, in manifest they store all files for a spec under one entry, not caring each prompt variant separahey only differentiate by spec (and by key if key used).
So naming isn't extremely critical beyond uniqueness and groec for order.
We'll implement: base name = ${specIndex+1:03d} (for spec), and then forappend a sequential number across all images of that spec. E.g., spec 1 has 3 images from firariant and 2 from second (5 total), name them 001-01.jpg ... 001-05.jpg. But that loses info which came from which prompt variant. Alternatively, incorporate prompt variant:
Format: ${specIndex+1:03d}-${variantIndex:02d}-${imageIndex:02d}.jpg.
Example: spec 1, variant 1, first of 3 images -> 001-01-01.jpg, second -> 001-01-0iant 2 first image -> 001-02-01.jpg`, etc.
This is explicit and won't collide. We'll use this approach for clarity.
Save images to generated_images/``fs.writeFileSync or streams for binary data. Make sure extension matches the MIME (the config output_mime_type was "image/jpeg" by default【3†L36-L43】, so likely - Update the manifest.json entry for this spec:
If spec.key or spec.video_prompt is provided, include them. In manifest we store:
{
  "spec_index": specIndex,
  "key": spec
  "prompts": spec.prompts, 
  "video_prompt": spec.video_prompt || "",
  "files": ["path/to/file1.jpg", "path/to/file2.jpg", ...],
  "generated_at": Date.now()/1000
}
Remove any old manifest entries with same spec_index or avoid duplicates)【13†L13-L27】.
Sort manifest by spec_index and generation time when saving (original saved ordered by spec_index, then timestamp)【13†L13-L27】.
Write manifest.json to disk.
Keep count of images generated and possibly track usage:
If daily_quota is set, increment usage and if exceeding, set a flag to warn or break out if quota_enforce is true【17†L31-L44】. Possibly send UI a notification if near quoprinted a warning when remaining <= quota_warning_prompts)【17†L31-L44】.
Obey rate_limit_per_minute: we can ensure not more than N images per minute by delaying next API call appropriately. E.g., if limit 60/min, then at most 1 per second average. We can incorporate a simple delay between calls or use a timestamp to schedule next call (like if last call was at time T, ensure next call not before T + (60/limit)*1000 ms). Because images can be heavy, the API call itself might be the bottleneck anyway.
If an API call fails (network error or API error), handle:
Retry a couple times (config max_retries for API, default 3)【17†L15-L23】. Use exponential backoff perhaps (like wait 1s, then 3s, then 7s).
If still fails, log a warning ([WARN] Failed to get images for spec #X)【15†L8-L12】, continue to next spec.
No image files for that spec means manifest not updated for it (or remove old entry if existed).
After processing all specs:
If at least one image was saved, log an [OK] Saved N images -> output_dir message as original did【15†L10-L12】.
If none were saved, log [x] No images generated or similar (original printed different messages based on result: [NOTIFY] IMAGE_AUTOGEN_FINISH_OK or _EMPTY)【6†L177-L186】.
Emit images:generated event to UI with count or success.
nitiated by user clicking "Generate Images", they will then presumably inspect images, maybe remove bad ones manually. The manifest will link them to prompts. The UI could show them in Content page (like a mini gallery perhaps, but not required by prompt – not explicitly requested, but could be nice).
If attach_to_sora is true (which it is by default【3†L36-L43】), these images will autom considered during autogen as descrie autogen function reading manifest attaches them).
Telemetry for quotas: if notifications_enabled and usage is near daily_quota, we might send a Telegram or UI alert. The original would print a warning when remaining prompts <= quota_warning_prompts and if exactly 0 remaining (quota exhausted)【17†L31-L44】, they'd also either enforce (stop generation if quota_enforce=true) or just warn. We'll implement similarly: stop further generation if enforcement on and quota exceeded (with a log "[WARN] Daily quota reached, generation stopped").
Integration with UI & Automator:
If user triggers this manually (say via a "Generate Images" button in the UI), the UI should disable that button until done, show ike "Generating images..."), and list results when done (maybe output to process log or show thumbnails).
If as part of Automator (step type "session_images"), the all call this, and once done (or if empty results), it will continue to next step. The manifest is updated so that subsequent "session_prompts" step can use these images.
Possibly, if the user set attach_to_sora=false in settings, we would skip attaching images in autogen (just ignoring the manifest in that case). But we'll assume it's true (the default config is true)【3†L36-L43】.
3.4 Video Download Automation (Scrolling & Saving)
The Downloader Manager (downloader.ts) will automate retrieving video files from Sora. This corresponds to workers/downloader/download_all.py in original.:
Typically user opens the drafts feed in Chrome. The first video card might be visible. Our puppeteer page should already be on .../drafts (if not, we navigate). The user triggers “Download X videos” for a session via UI or Automator.
Steps to implement:
Attach to Page: Use the same puppeteer Page that the session has for Sora. Ensure it’s at the feed view. The Sora feed likely has a thumbnails; to download, we need to click the first draft, then use the swipe/scroll mechanism to go through.
If currently a draft card is open (say from autogen or user clicking), we can start there. Otherwise, open the first card:
Use a selector to find the first draft link or element in the feed. Original had:
cards = page.locator(CARD_LINKS)
cards.first.wait_for(state="visible", timeout=15000)
cards.first.click()
page.wait_for_url("**/d/**")
In puppeteer: await page.waitForSelector("a[href*='/d/']", { timeout: 15000 }); const firstCard = await page.$("a[href*='/d/']"); await first; await page.waitForNavigation({ url: /\/d\// });
That should open the first draft detail view (URL contains /d/).
Then wait for the video player right panel. There is a selector RIGHT_PANEL = "div.absolute.right-0.top-0" in original【18†L93-L100】 which likely is the container for video controls. We do await page.waitForSelector("div.absolute.right-0.top-0", { timeout: 10000 }); to ensure video loaded.
If OPEN_DRAFTS_FIRST config is false (like user might specify to not automatically go to drafts page before starting, but default was true)【18†L45-L53】, it means maybe the page is already on a draft card or something. We'll assume we open drafts page anyway unless user toggled that.
**Initiate Download of Curre - Find and click the menu (three-dots, “kebab”) button on the right panel:
Use the selector from KEBAB_IN_RIGHT_PANEL which was defined (to exclude the settings gear)【18†L93-L100】:
const kebabButton = await page.$("div.absolute.right-0.top-0 button[aria-haspopup='menu']:not([aria-label='Settings'])");
if (!kebabButton) throw new Error("Kebab menu button not found");
const box = await kebabButton.boundingBox();
if (box) {
  await page.mouse.move(box.x + box.width/2, box.y + box.height/2);
  await page.waitForTimeout(100); // jitter
}
await kebabButton.click();
await page.waitForSelector("[role='menu']", { timeout: 6000 });
This matches original open_kebab_menu function【18†L104-L114】.
In the menu that appears, click the "Download" menu item:
The original code scanned menu items for those containing text "Download", "Скачать", "Save video", etc. (DOWNLOAD_MENU_LABELS)【18†L116-L124】.
Implement:
const menuItems = await page.$$("[role='menu'] [role='menuitem']");
let downloadItem = null;
for (let item of menuItems) {
  const text = (await page.evaluate(el => el.innerText, item)).trim();
  if (!text) continue;
  if (["download", "save video", "export"].some(t => text.toLowerCase().includes(t)) 
      || text.toLowerCase().includes("скачать")) {
    downloadItem = item;
    break;
  }
}
if (!downloadItem) downloadItem = menuItems[0]; // if not found, pick first as fallback
// Setup download behavior:
const downloadPath = path.join(workspace.downloadDir, "/"); // ensure exists
await page._client().send('Page.setDownloadBehavior', { behavior: 'allow', downloadPath });
const [ download ] = await Promise.all([
  page.waitForEvent('download'), // puppeteer fires 'download' event when a download starts
  downloadItem.click()
]);
const path = await download.path();
Note: Using page._client().send('Page.setDownloadBehavior', ...) is a way to auto-download without popup in puppeteer (since Electron's Chrome by default might not prompt for save in headless, but in headed it might).
Alternatively, use browserContext.overrideDownloadBehavior in new puppeteer, but we can use CDP directly as above (the main process has CDP client via _client()).
Then we wait for download event. Puppeteer’s download handling:
In puppeteer v13+, page.waitForEvent('download') returns a Download object which has methods .path() to get the saved file path and .saveAs() to manually save if needed. We used Page.setDownloadBehavior to auto-save to a path, so .path() should give us the saved path.
We need to be careful: page.waitForEvent('download') might not exist in older versions, but by 2025 likely yes (since it's in dev - If not, we can fallback to monitoring the file system for new .mp4 file or intercept network request of video file. But using the built-in is simpler.
The original code then saved the file to a custom name:
They got download.suggested_filename and then either renamed it to a custom title or to next number if no title available【18†L128-L139】.
In puppeteer, download.suggestedFilename() is available on the Download object. Or we handle naming after.
Naming logic:
If we have custom titles (from titles.txt), use next unused title:
Keep an index (cursor). Original next_custom_title() read titles list and a cursor file, got the title at current index, incremented the cursor file【18†L17-L30】.
Implement similarly: maintain a pointer per session. The titles.txt might be global or per session (original had logic to allow separate session title files by naming them titles_<session>.txt in _session_titles_path【28†L1-L9】).
We'll do: if session has its own titles list (UI might allow separate or just one global list), and if the cursor < list.length:
title = titles[cursor], sanitize (remove forbidden filename chars, trim length to 120 as original did【18†L13-L16】),
increment cursor and save it (persist to a .cursor file or config).
If title is empty or cursor out of range, skip custom title (return null).
If a custom title is provided, set targetFileName = <title>.mp4. Otherwise, use numeric naming:
In the session’s download directory, list existing video files (*.mp4, .mov, etc.). Find highest numeric name (if files "1.mp4", "2.mp4" exist, next is 3).
Original next_numbered_filename did that: found all files with numeric names, took max, +1【18†L33-L41】.
Implement:
const files = fs.readdirSync(downloadDir).filter(f => f.match(/\.(mp4|mov|webm)$/i));
const numbers = files.map(f => parseInt(path.pars.filter(n => !isNaN(n));
const nextNum = numbers.length ? Math.max(...numbers) + 1 : 1;
targetFileName = nextNum + ext;
Ensure avoid conflict: if file exists (maybe same number with different ext), loop increment with suffix as original did【18†L133-L139】:
let finalName = targetFileName;
let suffix = 1;
while (fs.existsSync(path.join(downloadDir, finalName))) {
  finalName = `${path.parse(targetFileName).name} (${suffix})${ext}`;
  suffix++;
}
targetFileName = finalName;
Now we have targetFileName (either custom or numeric).
Save downloaded file:
Since Page.setDownloadBehavior saved it automatically to some tempName (maybe the suggested name, or a random name?), the Download object’s path() gives the actual path on disk.
We then rename (move) that file to `targe ```js
const downloadPath = await download.path(); // e.g., /downloads/<suggestedName>.mp4
if (downloadPath) {
fs.renameSync(downloadPath, path.join(downloadDir, targetFileName));
} else {
// if path not available (in headful mode sometimes Chrome doesn't allow path?), we may have to do download.saveAs.
await download.saveAs(path.join(downloadDir, targetFileName));
}
Mark this file as downloaded in logs:
Print to console or log variable: [✓] Downloaded: ${targetFileName}【18†L148-L156】.
Append to a download.log for the session if we maintain one (the original had something like viewing “download.log” in session logs, but it wasn’t a file, they likely just collated outputs).
We can create a session-specific download log file and write each downloaded title or ID.
Also update UI through event: emit download:progress with file name or index.
The click_download_in_menu function in original awaited the download via context manager page.expect_download()【18†L128-L135】, which we mirrored with waitForEvent('download').
After saving, printing success and event, proceed.
Scrolling to Next Video: Use the scroll_to_next_card logic from original:
After downloading current video, we need to move to the next.
The feed is like TikTok style: current video displayed, then by scrolling down we get the next video and so on.
Implementation:
const startUrl = page.url();
const startSrc = await page.evaluate(() => document.querySelector('video')?.currentSrc || "");
// Use CDP to perform smooth scroll:
for (let i = 0; i < 3; i++) {
  await page.mouse.wheel({ deltaY: 300 }); // scroll little by little
  await page.waitForTimeout(160);
}
// additional fallback: if above fails to change video, do one big scroll:
await page.mouse.wheel({ deltaY: 2400 });
await page.waitForTimeout(820);
// After scroll, wait for URL or video src to change:
let changed = false;
try {
  await page.waitForFunction((prevUrl, prevSrc) => {
    return location.href !== prevUrl || (document.querySelector('video')?.currentSrc || "") !== prevSrc;
  }, { timeout: 9000 }, startUrl, startSrc);
  changed = true;
} catch(e) {
  // if not changed, try another scroll attempt as orig   await page.waitForTimeout(700);
  const curUrl = page.url();
  const curSrc = await page.evaluate(() => document.querySelector('video')?.currentSrc || "");
  if (curUrl !== startUrl || curSrc !== startSrc) changed = true;
  else {
    // second attempt if needed (similar to original second swipe)【18†L207-L218】
    await page.mouse.wheel({ deltaY: 900 });
    await page.waitForTimeout(160);
    await page.mouse.wheel({ deltaY: 900 });
    await page.waitForTimeout(160);
    // then wait shorter for change
    try {
      await page.waitForFunction((prevUrl, prevSrc) => {
        return location.href !== prevUrl || (document.querySelector('video')?.currentSrc || "") !== prevSrc;
      }, { timeout: 8000 }, startUrl, startSrc);
      changed = true;
    } catch {}
  }
}
if (changed) {
  // Wait for right panel of new video to load if possible
  try { await page.waitForSelector("div.absolute.right-0.top-0", { timeout: 6500 }); } catch {}
}
return changed;
This replicates the original scroll_to_next_card: they first did a long swipe via multiple small scrolls【18†L169-L181】, then waited for change up to timeout_ms (9s). If no change, they performed another swipe and waited shorter【18†L200-L218】.
If changed == false after that, it means we likely reached the end of feed or couldn’t scroll (maybe because only one video). Then we stop the loop.
Also handle Cloudflare during scrolling:
At the top of scroll function, check detectCloudflare(page) as done in autogen. If found, same process: notify, wait until solved, then after solving set startUrtSrc again (since solving challenge might reload page, original did that)【18†L170-L180】.
The code above sets startUrl each time it's called, but in a loop we will call scroll for each video, so if cloudflare occurs mid-way, one of these calls will detect and handle it. The global wait_for_cloudflare might be better to call in the main loop:
See how orioad_feed_modecalled_wait_for_cloudflare` at top of each loop iteration【18†L231-L239】.
We'll incorporate similarly in our loop below.
Feed Download Loop: The main logic to iterate:
The user may specify desired = N videos to download. If N=0 meaning "all available" (like original max_videos=0 meant download all).
Use a set to track seen video URLs to avoid infinite loops if feed cycles (the original used seen and if current_url already in seen, it broke out with a warning “Card already seen, cannot scroll further”【18†L220-L228】).
Pseudocode:
let downloadedCount = 0;
const seenUrls = new Set();
let currentUrl = page.url();
while (true) {
  // Cloudflare check
  if (awaidflare(page)) {
    emitCloudflareAlert();
    await waitForCloudflareSolve(page);
    // update currentUrl after solve, in case page reloaded
    currentUrl = page.url();
  }
  if (seenUrls.has(currentUrl)) {
    console.log("[!] Card already seen, cannot scroll further — stopping.");
    break;
  }
  seenUrls.add(currentUrl);
  // Down video:
  try {
    await downloadCurrentVideo(); // logic from step 2 (open menu, click download, save file)
    downloadedCount++;
    emit event for UI with downloaded filename.
  } catch (err) {
    conownload failed:", err);
    // If a failure occurs (like no download option found), we try to scroll to next or break?
    // Original on failure after second try logged error and continued to next.
  }
  if (desired > 0 && downloadedCount >= desired) break;
  // Scroll to next video
oved = await scrollToNextVideo();
if (!moved) {
console.log("[!] Could not move to next video — stopping.");
break;
}
// small pause to let video load (original waited 600ms + jitter after scroll)【18†L218-L218】
await page.waitForTimeout(600);
await page.waitForTimeout(Math.random()*1000); // jitter maybe
// loop continues, now page.url() is new video
currentUrl = page.url();
}
console.log([i] Done. Downloaded ${do video(s).);
emit download:complete with count.
```
This implements the logic of download_feed_mode【18†L220-L228】【18†L231-L239】:
It breaks if we circle back to a seen URL (meaning maybe feed ended).
It breaks if reached target count.
It breaks if o change video.
The original also had a condition:
After loop, if fewer new videos than requested, they didn't explicitly log but implicitly user sees equested. We can optionally warn UI if downloadedCount < desired (like "Only X videos founey also flush "Done" message at end【18†L247-L249】 and possibly send a notification event (though not explicit, but process log "Auto-download completed").
Cloudflare handling iand they'd likely not happen repeatedly, but included every loop just in case).
If an individual download fails (like the first attempt times oes menu open again as code above does a fallback for double attempt), and our downloadCurrentVideo might itself handle a retry as original did: it tries click download twice if first time nothing happened【18†L148-L156】. We can incorporate that in downloadCurrentVideo():
If after firload` event not fired in 20s, try to click again after 1.5s delay (as original did)【18†L150-L158】.
If still failing, log error and skip.
Possibly, in failure case (like no download option because video maybe was already downloaded or something?), just skip.
Session Titles Management: Already described in step 2 (naming). We need to persist titles.cursor file (just containing an index). We should do:
On app start, load titles.txt (global or session-specific). If a titles.cursor file exists, read the index (or default 0). Store it in memory.
Each time we use a title, increment and write the cursor file. If we reach beyond list end,turn no title (thus use numeric).
Provide UI to reset this cursor (like original had a button "Reset cursor" in content page that deleted the .cursor file or set to 0【33†L1-L9】).
Logging & Events:
Throughout downloading, push entries to session’s download log (maybe we keep an array of last few downloaded file names to show ind download:progress events for UI to update (like add a line "✓ Downloaded: file.mp4" in session log view).
At end, :complete` with total count and any note if stopped early (UI might show a summary or just rely on the individual progress messages).
If download_all was triggered bession_download step) with a limit, we need to signal back automator when done (likely consider the step successful as long as we didn't hit a critical error, even if fewer videos found is not “error” per se).
Also if desired > 0 limit was set by user (like "last 30 videos"), and we found less (like 10 new videos available), we might want to warn. The original code prints a warning if target videos not reached but no new ones left: they detect if feed loop ended early and printed “[!] Card already seen, cannot scroll further – Could not move to next – stop.” which implies maybe feed had fewer. The UI can interpret that as no more videos in feed.
3.5 Video Post-Processing: Blur & Merge
The app includes two post-processing capabilities:
Blur Watermark (Boxblur): The original allowed applying a blur filter on predefined zones (as a quick alternative to full removal). This was tied to FFmpeg presets (portrait_9x16, etc. with coordina】【3†L80-L88】. Also in UI a page to preview and run blur (maybe in Pipeline or global blur step in Automator).
Merge Clips: Combine multiple video files into one (with a specified group size, e.g., merge every 2 clips into a compilation).
We will implement these using fluent-ffmpeg (which is a Node wrapper for FFmpeg):
Ensure ffmpeg binary is accessible. Config provides ffmpeg.binary (path or just "ffmpeg" for system)【3†L64-L72】. We can set that in fluent-ffmpeg’s setFfmpegPath.
Use the filter and codec settings from config:
post_chain filter string for blur extra pe adding noise and sharpen)【3†L64-L72】.
vcodec (libx264), crf, preset from config【3†L64-L72】.
format (mp4) and whether to copy audio or reencode (copyAudio true means include original audio track without reencoding)【3†L64-L72】.
multi-thread setting (not explic expose).
Blur function (removeWatermark = false): If user chooses blur method (like original had a separate “Blur” pipeline stage and an “Auto Watermark” option that could auto-detect and blur)【3†L80-L88】:
We have coordinates for zones in config presets【3†L80-L88】. active_preset identifies which set to use.
Using fluent-ffmpeg, build a complex filter:
For each zone (x,y,w,h):
Crop that region: crop=w:h:x:y from input.
Apply blur: boxblur=... (like boxblur=1:1 means minimal blur? Actually boxblur=1:1 might be mild blur, they might set bigger values. In config post_chain had boxblur=1:1 which is extremely low blur, but that might have been default or something else).
Then overlay back onto original at same position.
This is done sequentially for each zone using multiple filterchain segments.
Alternatively, use FFmpeg’s delogo filter which takes a rectangle to blur/inpaint. But we have multiple positions and delogo can be used multiple times though only one instance per filter chain, not sure if easily combined. Better to do manual overlay as described.
If their config had auto-watermark detection, skip for blur (we are doing manual fixed zones).
Example filter (for 3 zones from config for portrait_9x16) as we discussed:
[0:v]crop=157:62:30:105,boxblur=10:1[blur0];
[0:v]crop=157:62:515:610,boxblur=10:1[blur1];
[0:v]crop=157:62:30:1110,boxblur=10:1[blur2];
[0:v][blur0]overlay=30:105[tmp1];
[tmp1][blur1]overlay=515:610[tmp2];
[tmp2][blur2]overlay=30:1110,noise=alls=2:allf=t,unsharp=3:3:0.5:3:3:0.0[outv]
This example:
Blurs three specific small zones (the watermark likely appears in those coordinates).
After overlaying them all, applies noise and unsharp from post_chain to entire frame.
We will generate filter dynamically based on config.ffmpeg.presets[active_preset] zones and append config.post_chain to end.
Use fluent-ffmpeg:
ffmpeg(inputPath)
  .videoCodec('libx264').audioCodec(copyAudio ? 'copy' : 'aac')
  .outputOptions('-preset', presetQuality, '-crf', crfValue)
  .complexFilter(filterGraph, /* map outputs */)
  .save(outputPath)
  .on('end', ...) .on('error', ...);
Here filterGraph is an array of filter objects or a string. We might use string for simplicity since we have to carefully craft with pad names (like [tmp1], etc.). Or use array form if simpler:
fluent-ffmpeg allows something like:
.complexFilter([
  { filter: 'crop', options: { w, h, x, y }, inputs: '0:v', outputs: 'crop0' },
r: 'boxblur', options: '10:1', inputs: 'crop0', outputs: 'blur0' },
...
{ filter: 'overlay', options: { x: zoneX, y: zoneY }, inputs: ['prev', blur${i}], outputs: 'prev2' },
...
{ filter: 'noise', options: 'alls=2:allf=t', inputs: 'prevN', outputs: 'outv' },
{ filter: 'unsharp', options: '3:3:0.5:3:3:0.0', inputs: 'outv', outputs: 'outv' }
], 'outv')
But the chain as string might be easier to visualize and ensure correctness. We have to carefully label streams to overlay sequentially. Or simply do multiple `-vf` sequentially by feeding result via `format=rgba,boxblur,format=yuv420p` etc. But multiple overlays need complex filter.
- We will create a general function `blurVideo(inputPath, outputPath, presetName)` that does this. Possibly run for each file in a directory:
* If user triggered "Blur all videos in Downloads", we loop through all mp4 files in that directory,lurVideo on each.
* Or if automator global_blur step, they likely intended to blur all downlove preset.
* If config has `blur_threads`, we might run at most N processes in parallel or just rely on ffmpeg being multithreaded internally (the config had `blur_threads: 2` meaning maybe run two concurrently, original code might spawn two ffmpeg at a time to speed up).
* For simplicity, do sequential or maybe parallel if trivial, but be mindful of CPU.
- Logging: for each file, output a status `[ℹ️] Processing fileX...` and `[✅] fileX blurred` in logs. If any errors, mark them.
- The UI will get a final `blur:complete` event or just rely on logs. In automator context, we mark step success if all files processed (or success even if some fail? But likely success if ffmpeg runs on each, skip missing).
Merge function: The original merge step took a pattern (*.mp4) and a group size (how many videos per merged output)【3†L91-L96】. We implement:
Determine input files list from a directory (likely the blurred videos directory if merging after blur, or from raw if merging raw).
Sort them by name or creation time (if numeric names, numeric order; if custom, maybe use the order in titles.txt or just name order).
Group them: e.g., if group_size=2 and we have 5 videos, create 3 merged files (first two, second two, last one by itself).
For each group, use fluent-ffmpeg to concatenate:
There are two methods:
Use ffmpeg concat demuxer:
Create a text file listing input paths:
file 'video1.mp4'
file 'video2.mp4'
Then run ffmpeg with -f concat -safe 0 -i list.txt -c copy output.mp4 (if we want to just join with same encoding). But if we want to re-encode to ensure uniform codec, can do a normal encode as well.
If copying, the videos must have same resolution, codecs, etc., or it fails. Our case, since they are all from Sora, likely similar (all mp4/h264, probably same resolution, but maybe not guaranteed if user changed output each time).
Safer is re-encode or at least re-scale if needed. But since config has specified vcodec=libx264 and given to blur etc., if merging blurred output, they likely all match that output specification (assuming all blurred outputs had same resolution? Actually if original raw videos had varying dimensions, our blur step wouldn’t scale them to uniform resolution, we blurred in place. For merging, different resolution videos can be concatenated by padding smaller or requiring scale to largest).
Simpler approach: re-encode all into one with a fixed resolution (like if user expects output same as first video).
Maybe we can require the user ensures videos same size for merging (like they recorded them similarly). The original didn't elaborate on resolution matching.
Use filter concat in ffmpeg's filter_complex:
It can take N inputs and produce one output, requiring same resolution & format on all inputs (if not, need to add scale and pad filters before).
If group size is small, we can do directly. For example, to concat 2 videos:
ffmpeg -i vid1.mp4 -i vid2.mp4 -filter_complex "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[outv][outa]" -map "[outv]" -map "[outa]" output.mp4
This re-encodes (unless we specify -c:v libx264 -c:a aac or copy if possible).
For >2, just extend accordingly with n=N.
We can implement using fluent-ffmpeg complex filter concat.
Buttext file method with -c copy to avoid re-encoding if possible (faster, but only works if identical streams).
Considering Sora outputs often have identical formats, we might try copy first:
We'll attempt ffmpeg -f concat via fluent-ffmpeg by using .input(list.txt) with format concat.
fluent-ffmpeg supports .input('filelist.txt').inputFormat('concat').outputOptions('-safe 0').
Then .videoc('copy') to just copy.
If that fails (error thrown in on('error')), we fallback to re-encode method.
Provide a progress to UI or at least final event. This is usually quick if copy, slower if re-encode.
Output naming: If merging in groups, probably user doesn’t have preset names. Possibly just output as merged_1.mp4, merged_2.mp4, etc. Or if each input had a custom join them by an underscore? That could be complicated. The original likely output some generic names or allowed user to specify via UI.
We'll do simple: if pattern='*.mp4' and group=2:
If there are multiple groups, either use a base name plus index (like merged_01.mp4, `me).
Or if within automator, maybe they don't care about name beyond numbering.
Implement mergeVideos(inputDir, pattern, groupSize, outputDir):
Read matching fiy name.
For i from 0 to len-1 step groupSize:
let group = files[i : i+groupSize] (last group may be shorter).
Determine output name: e.g., if first input of group named "videoA.mp4" and oB.mp4", we could name "videoA+videoB.mp4" or just "merge_{index}.mp4".
Safer to just "merged_{batchIndex+1}.mp4".
Try concat with copy. If fails, do with re-encode.
On success of each, increment count.
Logging: show something like 🌫️ Merging files X+Y -> merged_1.mp4 in UI logs or at least fiwhen done.
After all, emit merge:complete (with count of merged files created and maybe the pattern).
3.6 Advanced Watermark Removal (Seamless Clone Algorithm)
This is the most complex part. We will attempt to implement the same algorithm using Node, likely via the opencv4nodejs package (which provides OpenCV functions in Node). This allows us to usatching, median, clone, inpaint. Plan:
Use opencv4nodejs to load videos frame by frame (this can be slow in pure JS, but opencv’s native libs will do decoding).
It might be more efficient to use OpenCV’s C++ API via opencv4nodejs to handle frames than to offload images to JS.
Alternatively, use fluent-ffmpeg to extract frames to images (like to a temporary folder), then process images with OpenCV, then reassemble video. But that’s multi-step and heavy on disk I/O.
Maybe openn directly open video (it has new cv.VideoCapture(videoPath)) to read frames. Yes, opencv4nodejs supports VideoCapture, and also VideoWriter to write output.
We'll basically port workers/watermark_cleaner/restore.py logic:
Prepare template:
(with alpha if present) via opencvcv.imread(templatePath, cv.IMREAD_UNCHANGED)` to get 4-channel image if available.
Create mask from alpha: if image has 4 channels, threshold the alpha channel with mask_threshold to binary mask. If only 3 channels, can create mask by thresholding grayscale (as original does if no alpha)【19†L5-L13】【19†L18-L26】.
Store original template size, grayscale versiofor grayscale (like original TemplatePackage)【19†L5-L13】【19†L18-L26】.
We might also generate scaled versions on the fly later.
For each source video in source_dir:
Use cv.VideoCapture(videoPath) to open the video.
Get total frame count (VideoCapture property CAP_PROP_FRAME_COUNT) and Fecide frames to analyze: default frames=120 or if full_scan enabled then frames to scan = all frames (or skip parameter sets it to full count)【20†L1-L9】.
If frames <= target_frames, use all frames; if more, sample them evenly (take every step = floor(total/tar) frame, plus last).
original _iterate_frames created an index list like range(0, total, step) plus last frame if not included【19†L28-L36】.
For each frame index in that sample:
Seek video to that frame: cap.set(cv.CAP_PROP_POS_x).
Read frame: cap.read() gives an image (cv.Mat).
If fils (maybe out of range), break loop.
Optionally resize frame if downscale is set (e.g., downscale=1080 means if frame height > 1080, scale down so height=1080)【19†L39-L47】. Use cv.resize with INTER_AREA for downscaling.
Convert to grayscale (cv.cvtColor to CV_BGR2GRAY).
For each scale in list of scales (linspace from scale_min to scale_max, leteps)【19†L50-L57】:
Scale the template’s grayscale and mask to that scale: cv.resize(templateGray, 0, fx=scale, fy=scale, interpolation=cv.INTER_AREA) (INTER_AREA for reducing/increasing likely fine since template small).
If scaled template bigger than frame, skip (if watermark can't fit, skip 【19†L57-L63】.
Do template matching: cv.matchTemplate(frameGray, scaledTemplate, cv.TM_CCOEFF_NORMED, mask=scaledMask) if mask available.
Find maxVal, maxLoc from result using cv.minMaxLoc.
Track the best (max) score across scales for this frame. Store bestLoc, bestScale, bestScore.
After trying all scales, >= threshold (e.g., 0.78)【19†L63-L72】:
Compute bounding box on original frame size:
If frame was downscaled by factor frame_scale, then left = bestLoc.x / frame_scale, top = bestLoc.y / frame_scale.
Template original shape * bestScalemark size: width = template.original_width * bestScale / frame_scale, height = template.original_height * bestScale / frame_scale (if we scaled frame, apply inverse scaling).
Round to int and ensure within frame bounds.
Mark this frame index and bbox in a list of detections.
We also store accepted=true for this detection.
(We might also store details for debugging if needed, but not necessary except maybe for zone mask generation if needed).
After scanning frames, build detections map: frame_index -> [bbox] for each accepted detection.
If full_scan was false (partial frames scanned), they still assume watermark present in roughly those positions in intermediate frames too (so detection series might skip some frames; but we treat it as found positions).
If no detections found:
Logrmark not found -> videoName`【20†L32-L39】.
Simply copy video to output (or if user configured, maybe we skip processing).
Actually, original calls _write_video(dest, frames, fps) to just save original frames if none found【20†L32-L3 * We can do fs.copyFileSync(videoPath, outputPath) to keep original if no watermark found.
Continue to next video.
If detections present:
Use logic to reconstruct:
They first do processedFrames = frames.copy() essentially.
Pre-calc frame size (width, height).
Create an OpenCV VideoWriter for output with same resolution and FPS (e.g., new cv.VideoWriter(outputPath, cv.VideoWriter.fourcc('mp4v'), fps, new cv.Size(width, height))).
For each frame index from 0 to last:
If not in detections map, just write the original frame (or from memory).
If in detections:
For each bbox in should be one normally, but code expects possibly multiple)【20†L46-L54】:
~ Skip if bbox w or h < min_size (like <32 px)【20†L49-L58】.
~ Expand bbox by padding: add pad_x = max(padding_px, w*padding_pct), same for pad_y【20†L9-L17】. So region becomes slightly larre context.
Compute region = (x0, y0, w_expanded, h_expanded) clipped to frame bounds.
~ Choose donor frames: _choose_donors picks up to pool_size frames before/after current (within search_span) that do not have overlapping detection in that region (IoU <= max_iou)【20†L19-L30】.
Implement: for offset=1..search_span:
check frame- offset and frame+ offset, ensure within bounds 0..frames-1.
If either frame index is not in detections map or if in map but none of those bboxes overlap (IoU < max_iou) with current region:
Add as dono - If donors length >= pool_size, break.
Actually, original continued collecting until pool_size or ran out of span.
~ Gather donor patches:
For each donor index in donors:
Get that donor frame (if we didn't store all frames in memory, we may need to fetch it now. We could have stored all frames in an array earlier by reading sequentially, which could be heavy memory if video long. But for accuracy, original loaded entire video into frames l ease random access and modifications【20†L1-L9】【20†L46-L54】. If video is short (like 15s, maybe fine; if minutes long, memory heavy but they didn't mention optimizing).
We can consider reading needed donor frames on the fly by seeking VideoCapture for each, but that might slow things drastically. Possibly store frames in an array to allow easy random access (OpenCV would decompress them anyway each time).
We'll opt storing, given typical Sora video might be short (maybe <1 minute).
For donor frame, extract patch = donorFrame[reion.y+region.h, region.x:region.x+region.w] (ROI in OpenCV).
Check patch matches region size (sometimes if region partly out-of-bounds, adjust but we clipped region to frame bounds so should be fully in).
Append to patches list.
~ Compute median patch:
If patches list empty (no donors found) then fallback:
processedFrame = cv.inpaint(originalFrame, maskRegion, radius=inpaint_radius, flags=cv.INPAINT_TELEA or NS) for that region.
(Original does this if no patch computed【20†L79-L88】).
If patches list non-empty:
If only one patch, use it.
If multiple, stack them into a 3D array and compute median along axis=0.
With opencv4nodejs, we can convert each patch to a cv.Mat and use cv.Mat.zeros and manual median calculation (no built-in median).
Or rely on numpy via opencv, but opencv js binding likely doesn’t expose np.median directly.
We can do: convert each patch Mat to a plain JS array or Buffer and compute median manually. That might be slow for large areas. But watermark patch probably small (watermark likely < 10% of frame).
Or use cv.merge and cv.sort cunningly. But easiest: for each pixel coordinate in region, take median of all donor pixel values at that coordinate (multi-channel separately). This is double loop of region size and donors count, could be heavy if region is big. But watermark size maybe ~150x60, donors maybe 4, that’s 150604 ~ 36k operations, trivial.
We'll implement median manually:
For each pixel (i,j) in region:
~ Collect donorsPixels = patches.map(p => p.at(i,j) which gives a [B,G,R] vector).
~ Compute median per channel (since BGR separate).
~ Set resultPatch.at(i,j) = medianColor.
This double loop in JS might be borderline but region not huge. We could also flatten patch data and use typed arrays which might be faster in pure JS.
Alternatively, we can use OpenCV sorts:
For channels individually: stack donor patch single channels into multi-channel matrix where each channel is one donor’s grayscale? Or use cv.merge to combine donors along channel dimension.
Actually, if we had donors as separate Mats, we can push them into an array and do cv.calcMedianBlur except medianBlur is something else (applies median filter on one image).
Possibly better: treat donors as images in an array, use cv.matFromArray to create 3D arrarectly compute median via direct buffer. We'llbe safe.
Once median patch computed (type should be CV_8UC3 likely, cast to uint8).
The median is likely float from averaging near middle values? Actually median picks actual pixel from donors, so it will be uint8 exactly from one donor or average of two if even count (but in practice, if even donors, could average the two middle? The original didn't mention interpolation for median, likely they just took one).
We'll just implement standard median (if even, take lower or upper, doesn’t matter much).
~ Insert patch into processed frame:
Use cv.seamlessClone(medianPatch, originalFrame, mask=full white in that region, center=(region.x+region.w/2, region.y+region.h/2), flags=(MIXED_CLONE if blend=="mixed" else NORMAL_CLONE)) to blend patch in【20†L68-L77】.
Opencv4nodejs likely has seamlessClone via cv.seamlessClone.
If seamlessClone throws (e.g. due to mask issues, size issues), fallback:
Do direct copy: i.e., for all pixels in region, processedFrame pixel = medianPatch pixel (like they did in except block)【20†L68-L77】 or do inpaint instead (the code tries clone, if fails then inpaint)【20†L75-L77】.
If patch was None (no donors) we already did inpaint above for region.
~ That completes fixing that region on that frame. If multiple bboxes (rare, maybe if multiple watermarks on screen), do each.
After processing regions, mark that processed frame ready.
End for each frame.
Now have processed frames list or output was written on the fly.
Write out video:
We can either write frames as we go in the loop (which is memory efficient) or store frames then write all.
The original code built processed frames list and after loop _write_video to file with cv.VideoWriter【20†L40-L48】【20†L91-L100】. That ensures correct frame rate etc.
We'll do similarly: open VideoWriter before loop, and inside loop once a processed frame is ready, call writer.write(processedFrame).
If we didn't open at start because we needed processed frame to know width/height? But we know from original fraon, so open at start using those.
Use fourcc 'mp4v' (which is MPEG-4 Part2, like id【20†L72-L77】 – maybe they picked mp4v for broad compatibility, it’s fine).
Use fps from original video (we got from capture).
Write each processedFrame via writo ensure type matches expectations, maybe need convert color if requiredBGR if not already).
Release writer at end.
Log resul video: [✅] videoName done【20†L90-L100】 or if any error encountered, log ⚠️ Error for videoName: err.
If errors count > 0 for any video, we might consider final status partial.
each video.
After processing all videos, output summary event:
In original, they append a JSON to history: {"event":"watermark_restore","processed":N,"errors":M,...} and then log either "Completed: N files, Xs" or "Completed with errors: N ok, M errors"【20†L93-L100】.
We'll send watermark:complete IPC with { processed, errors }.
The UI then can show a final message in global log. Possibly also the Telegram integration might send a message if configured on finish (original had no direct notify for each video, but after process maybe a message "Watermark removal completed").
Given complexity, we expect this slower. But since it's often last step (maybe user does not mind waiting as much as generation loop).
We should provide intermediate progress if possible:
For each video processed, send event watermark:progress like "Video X of Y done".
Or every 10 frames as original printed status [WMR] Processing videoName: frame i/N【20†L47-L55】. We could send similar progress to UI (maybe not needed if output not live) video we do, which is enough granularity likely.
Memory note: We must consider memory usage:
Storing all frames of a video: If video is 1920x1080, 10 sec at 30fps = 300 frames, each frame ~ 192010803 bytes ~ 6MB, 3006 ~ 1.8GB (ouch). Actually double-check: 19201080=2,073,600 pixels *3 ~ 6,220,800 bytes (~6.2 MB) per frame. 300 frames ~ 1.86 GB indeed. Too high to store all in array for longer videos. But maybe Sora videos are short (like < 10s?) not sure.
The original did read entire video into frames list, which might not scale to long content. Possibly Sora's AI outputs are short clips (maybe a few seconds). If that assumption holds, memory is fine. Or original didn't consider memory, just took the hit assuming typical usage not too big.
We can mitigate by reading and processing on the fly, writing out frames as soon as done:
Use VideoCaperate frame by frame (cap.read in loop).
Keep a sliding window maybe to have donors accessible:
E.g., when at frame i, you need frames up to 12 ahead/behind (search_span) for donotain a small buffer of frames around current as we process sequentially:
But we skip frames out of order to detect watermark first. They scanned sample frames out of order first. We could combine detection and processing in one pass but not straightforward bpossibly any frame.
They opt to detect on sample frames first to know where watermark is for all frames, then second pass to reconstruct.
We could try detect and reconstruct in single pass: e.g., detect on the fly, if watermark found on current frame, fix it after donors processing, but donors come from future frames (maybe not read yet).
That’s why they did two passes: detection (could skip many frames to not spend too much time scanning every single), then second pass covers all fing detection results to know which frames need fix and where donors are.
If we wanted memory optimization: read video and store minimal info:
On detection pass, instead of storing frames fully, we could store just the donor patches needed:
but we don't know which donor frames needed until second pass.
Or store for each detection entry the raw pixel data of region for that frame (which ironically is almost the same memory as storing whole frame for that region, but region small).
However, donors come from outside detection frames too (they pick donors even if watermark not detected on those donors).
It's complex to avoid storing a good chunk of frames data if not entire video.
Given time, we might accept storing frames or reading them again on the fly in second pass:
We can open a new VideoCapture for second pass or rewind first (cap.set frame 0).
Then for each frame in second pass:
If frame index in detections:
If donors for this frame are behind current index (they have smaller index), those are already processed since we going sequentially, but we need original donors (not processed ones) to patch from. We should have saved ormes in memory or at least loaded donors from file again.
Or we can open a separate VideoCapture for original video to extract donorocessing state (since processed frames have watermark removed might have slight differences, we want original background from donors).
E.g., if at frame50 and donors include frame40 and 60:
We have processed 40 by now, but we need original frame40's data for patch. We should have stored original frame40 aside (or read from original video file again).
So likely easier: have an array originalFrames in memory to pull donor data from at any time.
This is what original did by storing frames list (which presumably held original frames).
Then processed frames are separate or done in place on that list (they used separate processed list, but could also do in place).
We can mimic: read all frames into frames list (each a cv.Mat of original).
Then create processedFrames = frames.map(frame=>frame.copy()).
Use processedFrames for writing output eventually.
Use frames (original) for donor patch extraction always.
That uses memory but is simplest to implement without reading file multiple times. We'll accept that for now.
For videos of up to few hundred frames, it's fine. If 1000 frames, that's ~6GB memory which is too high. But again, maybe unrealistic to have that many frames from Sora (maybe they restrict length).
Possibly they trade memory for speed given likely usage scenario.
Given the complexities, let's proceed with the memory-heavy but straightforward method (like original) and caution in documentation that very long videos could be problematic. Summary of Watermark Removal ion:
Use opencv to find watermark positions across frames (sample frames).
Use median of background patches and seamlessClone to remove watermark on each affected frame.
Write out new video.
This yields near-original quality video with watermark gone.
We should ensure to handle any UI config:
The config has lots of parameters, which we did:
threshold used in matchTemplate acceptance【19†L57-L63】.
frames number of frames to scan (we handle).
downscale resizing frames for detection (we handle).
scale_min,max,steps (we handle in loop).
`mask_thr template mask (we handle).
full_scan (we handle via scanning all frames).
padding_px, padding_pct for region expansion (we handle).
min_size (we handle skipping small).
search_span (we handle donors within ±span).
max_iou for excluding donors where watermark overlaps (we handool` (pool_size donors) (we handle).
blend normal vs mixed (if blend_mode == "mixed", use cv.MIXED_CLONE else NORMAL_CLONE)【20†L68-L77】.
inpaint_radius, inpaint_method telea vs ns (if method ns, use cv.INPAINT_NS, else INPAINT_TELEA).
Telegram: Possibly send a message on completion if configured, but not explicitly stated. Maybe not needed, but user can set up template for "watermark done".
3.7 Telegram Notification Integration (Bot API calls)
For Telegram, we'll implement a simple module using node-fetch or axios to call the Bot API:
Bot token and chat_id from config (the UI will save these).
Templates: We will store templates in config (list of { name, text }). Possibly allow placeholders in text (like {session}, {count}) that we fill in before sending if needed. The original allowed templates and scheduling, but not much code aside from storing and sending static text.
Provide a function sendTelegramMessage(templateNameOrText):
If input is one of saved template names, find its text. If the template text contains placeholders like {steps} or {ok}, we can replace them if we have relevant data. But original likely not that dynamic – their examples looked more static or usedlues if needed.
If user invoked via UI sending a quick message, just send that text.
Use token and chat_id to craft URL: https://api.telegram.org/bot<token>/sendMessage with JSON body { chat_id: config.chat_id, text: message }.
Or use node-fetch:
const res =h(`https://api.telegram.org/bot${token}/sendMessage`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ chat_id: chatId, text: text })
});
const data = await res.json();
if (data.ok) ... else handle error.
We can optionally support Markdown or HTML formatting if user desires (could allow parse_mode).
After sending, log result:
If success, record message in a local telegramHistory (with truncated text or whole text and timestamp). We may keep last 50 messages in memory (like original had a deque of size 200)【21†L45-L53】.
If error, record as failed in history (with error code perhaps).
Emit telegram:sent event with success boolean, short text (like "Telegram ✓ TemplateName" or "✗ ...").
The UI will display the telegramHistory as chat bubbles or a list. We’ll maintain that in Zustand or as part of config if we want persistence of last messages (the original did not persist history to disk, only kept runtime in deque).
Also, integrate with notifications:
The original app automatically sent certain templates on events if set. For example, in their mapping of NOTIFY events, they might have triggered a Telegram send for some events, or at least logged to history which the user could see in Telegram UI or repeated via template.
If user wants auto-status messages:
E.g., if telegram.enabled and templates define one named "AutogenFinish", we coulo NOTIFY AUTOGEN_FINISH events. But original didn't explicitly map templates to events except the user can schedule or manually send.
There is an notifications_enabled in genai frnings (which they might have considered sending via Telegram).
Possibly out of scope to fully automate event-based messages, unless user uses the Automator to add a step that sends a Telegram (like they can create an automator step of type "global_probe" or "send_telegram" etc., but d not mention a step for Telegram).
We'll mainly implement manual sending and allow user to send quick messages or test messages from the UI.
If desired, it's easy to call sendTelegramMessage inside main when receiving a NOTIFY event. For example, on [NOTIFY] AUTOGEN_FINISH_OK, we can automatically call sendTelegramMessage("Autogen completed successfully") if telegram.auto_notify config alloince not specified, we won by default (to avoid surprise meThe UI's Telegram page will use IPC to call telegram:send with either a template name or raw text. The main then calls sendTelegramMessage and returns a success/fail event.
Historeate a structure like:
telegramHistory:: 1, text: "Autogen finished (Session 1)", status: "success", date: "2025-11-22T12:00:00Z" },
  ...
]
Possibly store it in memory or in a file telegram_history.json if we want persistence.
But likely not necessary to persist across app restarts.
The user can also define schedule for a template (like "send template X every day at 9:00"). The original mention "schedule" in UI but not elaborated. If needed, we can use Node setInterval or something for simple scheduling (but if app not running, won't send; likely user triggers manually or uses a separate scheduler outside).
We'll mention scheduling as possible extension or at least parse any schedule field (maybe to be safe, we'll not implement actual scheduling unless user interacts to send).
With the above, we have covered each area. Finally, to address the output structure:
We need to clearly delineate modules in our written spec, usage of each, and how they interact:
Summaries of main modules:
main.js – Initialize app, handle app events (like ready, window creation), instantiate needed managers (workspace manager, etc.), and set up IPC handlers.
workspaces.ts – Manage Chrome processes (launch/close, track status).
autogen.ts – Functions to run prompt injection (connect to page, send prompts).
genai.ts – Functions to call GenAI API and handle manifest.
downloader.ts – Functions for video download (open page, loop through feed).
ffmpeg.ts or part of ts` – For blur and merge processing with FFmpeg.
watermark.ts – Functions for watermark removal (with OpenCV).
telegram.ts – Functions to send Telegram messages.
Possibly a automator.ts – Or automator can be orchestrated in main, but likely better to separate logic:
define AutomatorStep types, and a function runAutomation(sequence) that goes through steps, calling the above modules for each step as needed.
e.g., if step.type == "session_prompts", call autogen for those sessions (maybe sequentially, or concurrently? The original executed session tasks sequentially within a step – though allowed multiple sessions in one step, they ended up sequential anyway by code design)【35†L70-L78】.
The new automator design might even allow parallel tasks if we wanted (like run autogen on Session1 and Session2 simultaneously as part of one step – original structured it sequentially for simplicity). We can replicate original (they actually coded an async parallel but then did sequential run_next)【35†L70-L78】, so maybe keep sequential to avoid complexity.
Steps like global_blur, global_merge, global_watermark – run once as they are global, not per session (just call those functions).
So the automator manager will orchestrate, sending UI updates for step status, and handle errors (stop chain on error with message).
We should stress that new architecture allows multiple tasks concurrently if they are on different sessions (since each session has its own Chrome and tasks). Within one session, tasks are serialized (can't do autogen and download simultaneously on one session logically because they both need control of page).
But user could be downloading on Session1 while autogen runs on Session2 – our design supports that because each session page is separate and our code doesn’t globally block (just ensure to not block Node event loop unnecessarily).
We will be carefuvy loops (like median patch calculation in JS) might block Node event loop. Possibly better to do that in a worker thread if huge. But given likely small patch sizes, it’s okay. If not, we could spawn a worker thread for watermark procesode’s worker_threads to not freeze main. But given complexity, maybe no typical use. We'll mention potential heavy CPU tasks like watermark might block main – we could advise to run that in a separate thread or process to keep UI responsive. But if UI can handle freezeeconds, maybe fine. Now we have to format this as a cohesive technical prompt with headings as required and incorporate code exudo-code where helpful, plus not forgetting to preserve citations. I will ensure to keep references to original lines from user files where we used them, to validate that our description matches original behavior. We likely won't include extensive code blocks, but some pseudo-code (like we included in sections with triple backticks) clarity. Finally ensure we not put any embed images references incorrectly, we've not used embed in analysis except code. For citations, our analysis inserted them inline. That should satisfy "preserve all citations in format". view and structure final content to follow a clear Markdown outline, fulfilling the "Detailed headings, examples, data structures" requirement. Let's structure the final spec: We might use top-level headings for each major category:
1: Introduction (we did)
2: System Architecture (we did in combined with introduction)
Actually we have "Connected Source Citatioriginal design references". We have many already in text.
We might break out a section for "UI Design and Modules" separate from main. Let's do:
4. Renderer Process Implementation (with subheads for each page or component group).
We touched on UI in analysis bmore on main logic. We should explicitly describe UI pages now:
Workspaces Page UI
Automator Page UI
Logs/History/Errors UI
Content Editor UI
Telegram UI
Settings UI
(We did mention them in analysis under "React Renderer (UI)" but let's ensure all subpoints are covered.)
We should address user’s point 3: "Превести ... context new UI and architecture", which we did intermix, but maybe we need to clearly enumerate mapping:
Workspaces → separate profile cards with logs and buttons (we covered in UI section likely).
Automator → visual step editor with try/catch (we mentioned in UI and that automator logic stops on error, possibly plan to allow skip error toggles).
Settings → tabbed structure (we have that).
Content → prompt/image_prompts/titles editor with profile switching (we have content editors UI).
Telegram → chat-like UI (we said we will do chat bubble style).
Remove TikTok/YouTube (we won't implement those pages).
Move limits (doe) to Workspaces or modal (we said we will prompt when clicking download for number, and merge group can be in settings or asked).
Pipeline tab removed (we replaced by Automator and Workspaces).
Add pages: History, Errors, Documentation (we mentioned logs history and errors pages; documentation might just be referencing a markdown viewer for help).
They specifically said "Documentation (MD viewer)" – yes, we can implement a page that loads a local README.md and displays in UI (maybe using a React markdown component).
We'll mention that in UI (Settings group maybnav item).
We should highlight each difference from old:
Workspaces unify launching tasks per profile (instead of pipeline’s combined interface).
Automator replaced old pipeline plus adds multi-session sequences with better control.
Logs are split: session logs vs global, new History and Errors pages for record-keeping.
We did mention these in passing, but let's ensure to explicitly mention "History page to list past automation runs and results", "Errors page to filter error logs", "Documentation page to display README using a Markdown viewer".
Alright, let's incorporate the UI details from earlier analysis into a Renderer section.
4. React Renderer Implementation (User Interface)
The front-end of the new Sora Suite is built with React 18+ and styled with Tailwind CSS for a modern dark-themed look. The UI is organized into logical pages, accessible via a sidebar navigation (with icons and labels). We leverage Zustand for global state so that multiple components can reflect the application state (workspaces, tasks, logs, etc.) in real-time as it changes. Global Layout & Navigation: The app window is divided between a left sidebar (navigation menu) and a main content area for pages. The sidebar groups related pages under collapsible headings (similar to original UI)【2†L19-L27】. For example:
Workspaces (session management)
Automation (Automator editor)
Logs (Session Logs, Process Log, History, Errors)
Content (Prompts/Images/Titles editors)
Telegram (Bot integration)
Settings (Paths, FFmpeg, Chrome, GenAI, Interface, etc.)
Help (Documentation viewer)
Each menu item opens the corresponding page in the content area. The right-side context panel from the original is removed; instead, each page contains context-relevant info. A small status bar or activity panel is used to show global statuses (like running tasks or a Cloudflare alert) so that it's visible regardless of page. For instance, if Cloudflare verification is needed, a banner is shown across the app (and possibly a 🔒 icon in status bar) until resolved【18†L170-L180】. We now describe each major UI page/component and how it works with the backend:
4.1 Workspaces Page (Chrome Session Cards)
The Workspaces page displays each Chrome profile (session) as an interactive card. Each workspace card shows:
Session Name (e.g., "Session 1") and an optional small status indicator (green dot for Chrome launched, gray for stopped).
Chrome Profile Info: possibly the profile directory or a user-provided note (original had a notes field per session for reminders)【2†L39-L47】.
Launch/Close Button: A toggle to start or stop the Chrome instance for that session (🚀 Launch or 🛑 Close). This triggers workspaces:launchChrome or workspaces:closeChrome IPC. The button is disabled if Chrome is already running or stopping. When launched, the UI updates the status dot to green and possibly shows "Chrome running (DevTools port XXXX)".
Quick Action Buttons: Buttons to start specific tasks on that session:
⚡ Autogen Prompts: runs prompt automation (calls autogen:start for that session). If this session has image prompts ready, it will include them. While running, this button might turn into a stop button (⛔) to cancel autogen if needed.
🖼️ Gen Images: generates images for this session’s prompt list (calls images:generate with profile key). This is shown if GenAI is enabled. Once clicked, it may show a small progress bar on the card or in a modal listing image generation progress (especially if multiple images).
⬇️ Download Videos: initiates downloading of new videos for that session. If the user has set a default number (in Settings or previously via prompt), it uses that, else opens a small modal prompt: "Download how many latest videos? [ 30 ]" (with 0 meaning all) for input. After confirmation, sends download:start with that number. While downloading, the card shows a progress count (e.g., "Downloading... 5/30") and possibly a cancel button (which calls download:stop to abort).
🧼 Remove Watermark: runs the advanced watermark removal on this session’s downloaded videos (calls watermark:remove for that session directory). This button might be shown next to or instead of a "Blur" button (depending on user choice: we might include both options – the original UI had a separate "Watermark" page for restoration and a blur step in pipeline). In the new UI, we expose the full restoration here. When clicked, the card or a modal will show "Removing watermark from videos..." with a spinner. On completion, output videos are in the restored directory (the Settings page defines output paths).
(Optional) 🌫️ Blur Watermark: If the user prefers quick blur, we can include a "Blur" button (cloud emoji) that applies the FFmpeg blur filter preset to this session’s videos (calls blur:videos for that session). This might also be accessible via the Automator or Settings rather than each card. But for completeness we can include it here as well. It's less used if full removal is available, but original had both.
Recent Activity Summary: Each card can show a short log excerpt for that session – e.g., the last prompt that was submitted or last video downloaded. The original Workspaces page had "last events" on the card【2†L41-L47】. We implement by listening to session-specific events (like on submitted.log append or download complete) and updating a text line on the card. For example: "Last prompt: 'A funny windy scene...' ✓ Submitted 12:00:05" or "Downloaded video: CatVideo.mp4 at 12:05".
Notes Field: If the user has any notes for the session (from original config or UI input), display them in a smaller font or an info icon that toggles note view. The user can edit the note (we send config:updateSessionNote IPC to save).
The Workspaces page state is backed by Zustand store workspaces array:
// example structure
workspaces: [
  { id: 'default', name: 'Session 1', chromeStatus: 'running', cdpPort: 9222, 
    lastPrompt: "…", lastDownload: "…", notes: "Using main account", 
    tasks: { autogenRunning: true, downloadRunning: false } }
  // ... other sessions
]
The store is updated via IPC listeners:
When main sends workspace:status (e.g., {id:'default', status:'launched'}), the store finds that workspace and sets chromeStatus = 'running'.
When autogen:progress or download:progress events come in for a session, the store updates the lastPrompt or lastDownload fields accordingly (and maybe logs arrays).
The React component for each card subscribes to its workspace data and re-renders changes (status labels, etc.).
Interactions:
Clicking "Launch Chrome" triggers state change immediately to "launching..." (optimistic UI), then if main confirms launch (or error) via event, update accordingly. If error (Chrome not found etc.), show a toast error and reset button state.
Clicking any task button will disable that button (and possibly others if tasks conflict) while running. For example, if Autogen is running on Session 1, we disable the Gen Images and Download buttons for that session to avoid simultaneous actions on same page (the original UI likely did similar by only allowing one pipeline per session at a time).
The UI can still allow launching tasks on other sessions concurrently, since each has independent Chrome (the main backend handles parallel sessions).
Cloudflare Alerts: If any session triggers a [NOTIFY] CLOUDFLARE_ALERT【18†L170-L178】, we overlay a red banner on the app (maybe at top, or on that workspace card) saying "Cloudflare verification required – please check the Chrome window for Session X." Possibly include a "Bring to front" button that uses browserWindow.webContents to focus Chrome (or we rely on user manually going to Chrome).
Right-click context: We might allow right-click on a workspace card to access advanced actions (like "Copy DevTools URL" or "Open user data folder").
This page replaces the need to go to a separate Pipeline page to start tasks – everything per session is at your fingertips, which is more intuitive.
4.2 Automator Page (Multi-Step Automation Editor)
The Automator page allows building and executing complex sequences of steps (the replacement for the old Pipeline and extended with multi-session support)【2†L99-L107】. It features:
Step List Editor: A list (vertical) of steps, executed top-down. Each step is represented as a card/row with:
A Step Type dropdown: options include:
Session Prompts – send prompts (autogen) on selected session(s).
Session Images – generate images on selected session(s) (i.e., runs GenAI for those sessions' prompt files).
Session Mix – combined images + prompts on sessions (first generate images, then autogen with attaching – essentially a shorthand for Session Images + Session Prompts in one step).
Session Download – download videos on selected session(s). A numeric field for "Max videos" is shown when this type is selected (default 0 for all).
Session Watermark – remove watermark on videos for selected session(s) (calls our advanced watermark cleaner for each).
Session Chrome – launch Chrome for selected sessions (ensures those sessions are running before subsequent steps).
Global Blur – blur watermark on all videos in the global downloads directory (not session-specific, intended to run once).
Global Merge – merge videos in global blurred (or raw) directory. Shows a "Group size" field to specify how many per merged output.
Global Watermark – remove watermark on all videos in a given directory (e.g., global raw downloads directory).
Global Probe – (optional) just detect watermark positions or verify presence (could flip template if needed) – mostly a utility to test settings【33†L13-L21】.
Telegram Message – send a Telegram template or custom message (e.g., notify completion to user).
Session Selector: For step types that involve sessions (those beginning with "Session ..."), an input to choose one or multiple sessions to apply. This could be a multi-select dropdown listing all sessions by name. For example, for "Session Prompts", user can tick Session 1 and Session 2, meaning run autogen on both concurrently.
Parameters: Depending on step:
Session Download: an input for number of videos (limit).
Session Watermark: maybe an option to choose output folder (though by default uses config output).
Global Merge: an input for group size, maybe an input or pattern for source files (default *.mp4 in blurred directory).
Telegram Message: a dropdown of templates or an input for custom text.
We also include a checkbox "Continue on error" (try/catch concept) for each step: if checked, the automator will not stop if this step fails, it will proceed to next. If unchecked (default), any error aborts the sequence. This addresses the "try/catch" requirement: the user can mark non-critical steps to not halt the chain on failure. Internally, we'll implement by catching errors and if continueOnError is true, treat it as a warning and keep going.
Reordering Controls: Up/down arrows or drag-and-drop handle to reorder steps. The internal state is updated (Zustand store or component state) and UI re-renders accordingly.
Delete Step: A trash icon to remove the step.
Presets Management: A toolbar above the list with:
"Load Preset" dropdown (listing saved presets by name).
"Apply Preset" button (replaces current steps with the preset’s steps)【33†L23-L35】.
"Append Preset" (appends preset steps after current).
"Save as Preset" (opens a modal asking for name, saves current sequence under that name, storing in config automator.presets)【33†L23-L35】.
"Delete Preset" (removes a saved preset by name).
These presets are stored in config (with unique ids and name, see original config structure for automator presets)【33†L23-L35】.
Run Controls: At the bottom or top, a "Run Automator" button (⚡ icon or ▶️). Also a "Stop" button that appears when running (⏹ icon).
When "Run" is clicked:
The current sequence of steps is locked (inputs disabled) and an execution starts.
The front-end sends an IPC automator:run with a detailed representation of steps: e.g.,
[
  { "type": "session_prompts", "sessions": ["default","session2"], "continueOnError": false },
  { "type": "session_download", "sessions": ["default"], "limit": 30, "continueOnError": false },
  { "type": "global_merge", "group": 2, "continueOnError": true }
]
The main process will translate this into actual calls (launch autogen for default & session2 in parallel or sequence).
The UI immediately shows a progress indicator for the sequence. Each step in the list could show an status icon:
The active step could highlight or show a spinner.
Completed steps get a green check (✅) if success or orange warning (⚠️) if succeeded with non-halting errors, or red cross (❌) if it failed and stopped sequence.
If any step fails and continueOnError was false, the sequence stops at that point:
The UI marks that step with ❌ and perhaps highlights it.
Later steps remain in not-run state (greyed out or not checked).
The "Run" button re-enables (since sequence ended).
If all steps succeed, UI marks each with ✅ and perhaps shows a message "Automation completed" at bottom.
The UI receives progress events via IPC:
main will send automator:stepStatus for each step: e.g., {"index": 1, "status": "running"} when a step starts, and {"index": 1, "status": "success"} or "error" when done. We use that to update icons.
If a step involves multiple sub-actions (like sending prompts on 2 sessions), the UI may show sub-progress (maybe in a nested list or as part of the status text: "Session 1 done, Session 2 in progress..."). But to keep it simple, we consider the step as one block (the main can internally parallelize but reports step done when both sessions done).
For long global steps (like watermark removal that might take minutes), we might get intermediate events (like watermark:progress which we can tie to a specific step by context) – but simpler: automator can send periodic stepStatus: "running... X% done" updates via some text property if we want. Or just rely on global log and session logs for details, while the step card just shows spinner until done.
The Stop button: If user clicks Stop while automation is running, send automator:stop. The main will attempt to cancel ongoing tasks:
E.g., if currently in middle of autogen and download in parallel, main should stop those gracefully (we have autogen:stop to abort injection loop, and download:stop to abort feed loop).
Also it should not start any new steps.
The UI then marks current step (or entire sequence) as aborted (maybe use a special icon or treat as error). Possibly treat as user-cancelled = not a "failure" per se but incomplete.
Workspaces that had tasks might remain in partially done state, which user can see in logs.
When sequence finishes or is stopped, unlock the editor (allow editing steps or running again).
Usage Example: The user could create a sequence:
Launch Chrome (Session 1 and Session 2).
Session Images (Session 1 & 2) – generate images for both sessions in parallel.
Session Prompts (Session 1 & 2) – then send prompts on both (with images attached).
Session Download (Session 1 & 2, limit 10 each) – fetch results from both.
Session Watermark (Session 1 & 2) – clean watermarks on downloaded videos for both sessions.
Global Merge (group 2) – merge all resulting videos into compilations of 2.
Telegram Message – send a notification "✅ All tasks done!" to my chat.
The automator would execute step 1 (open 2 Chromes), step 2 (start image gen on both concurrently – our main might do that sequentially or concurrently; we can do concurrently since they are independent and it speeds up, but need to handle API quotas accordingly), etc.
If somewhere an error occurs (say Session 2 autogen hits a rate limit and fails completely), and continueOnError is false for that step, the automator stops and user sees which step failed ("Session Prompts: Session 2 error (rate limit)"). They can then potentially fix (maybe wait or reduce prompts) and rerun from that step or toggle continueOnError and run again.
State & Storage: Automator steps are kept in Zustand store automatorSteps (an array of step objects as above) and automatorPresets (array of preset objects).
We load any saved presets from config at startup (original saved in app_config.yaml under automator.presets)【33†L23-L35】 and populate store.
As user edits steps, store updates in real-time (so if they navigate away and back, the steps remain – we can also preserve them in config or memory). Possibly keep unsaved sequence in memory unless user explicitly saves as preset.
The "Run" action will also preserve the sequence in memory until next changes. If needed, we can autosave the last sequence to config to allow resume after app restart (not critical though).
Execution progress (which step index is active etc.) can be part of store as well (like automatorStatus: { running: true, currentIndex: 3, error: null }). UI subscribes to update step highlights.
The Automator UI thus provides a powerful visual script builder, making it easy for the user to automate repetitive tasks across multiple profiles, with error handling choices. It directly maps to the functionality described in the original design for Automator【2†L
(Continuing from above)
4.3 Logs and History Pages (Session Logs, Process Log, History, Errors)
Monitoring and reviewing logs is crucial. The new UI separates session-specific logs from the global process log, and adds dedicated views for historical runs and errors:
Session Logs (🗒️) – On this page, the user can select a workspace from a dropdown (or tabs per session). For the chosen session, display three log panels side by side or in tabs:
Submitted Log: recent entries from submitted.log for that session (each line with timestamp, prompt key, prompt text, and any media info)【8†L1-L9】. Successful prompt submissions appear here. We highlight the prompt text and maybe truncate if long.
Failed Log: recent entries from failed.log for that session (timestamp, prompt key, text, and failure reason)【8†L12-L20】. E.g., "2025-11-22 12:05:30 – prompt X – queue-limit/backoff-180s" indicating that prompt hit the queue limit and is in backoff【8†L12-L20】. These entries are shown in red or with a ❌ icon. If a failure was later retried and succeeded, the UI could mark it (e.g., strike-through or a note "retried successfully"), though that detail might not be explicitly logged except as retry:... reason in failed.log【8†L15-L20】.
Download Log: a log of video downloads for that session. The original app did not have a separate log file for downloads, but the Session Logs UI was described to show lines from "download.log"【2†L123-L131】. We implement this by capturing [✓] Downloaded: filename messages from our downloader events and storing them in memory or writing to a download.log for the session. The UI displays entries like "2025-11-22 12:10:00 – ✅ 3.mp4 downloaded" or custom titles if used. Warnings like cloudflare alerts or "No new videos" appear here or in process log.
Each log panel updates live as new events occur (using Zustand subscription or direct IPC to component). The user can scroll back to see earlier entries (we might cap at last N lines for performance, with an option to open full file externally if needed).
Process Log (📜) – This page shows the unified global log of significant events, similar to the original "Journal of processes"【2†L123-L131】. Entries are timestamped and prefixed with status emojis:
ℹ️ Info messages for routine events (e.g., "Chrome launched on port 9222", "Autogen started for Session 1")【22†L1-L9】.
🔄 Running status for tasks in progress (e.g., "Session 1: Sending prompts..." or "Watermark removal in progress") – we might animate this icon (spinning) to indicate ongoing.
✅ Success messages when tasks complete (e.g., "Session 1: Prompts insertion – successful", "Auto-download finished")【22†L1-L9】.
⚠️ Warnings for recoverable issues (e.g., "Session 2: Prompt X temporarily refused (queue full, retrying)" or "Cloudflare verification needed") – these highlight potential problems but not full stops.
❌ Errors for failures (e.g., "Session 2: Autogen aborted – too many failures", "Automation stopped at step 3 due to error")【22†L1-L9】.
Each entry is one line (possibly truncated if very long message, with tooltip for full text). We style each line with color corresponding to level (info grey, success green, warn orange, error red). The log auto-scrolls as new entries come in, but the user can also pause auto-scroll to inspect previous lines (a toggle).
We include controls to filter the log by level or component (e.g., show only errors, or only messages related to "Session 1"). A search box allows keyword filtering. We also have a Clear Log button that clears the in-memory log (original had a clear with confirmation)【2†L131-L138】 and an Export button to save the log to a file.
This global log corresponds to the original combined process journal with emoji statuses【2†L123-L131】.
History (📅) – A page to review past automation runs and other major actions. We implement this by reading the history.jsonl that the app writes (each line a JSON event)【20†L93-L100】. We parse entries like {"event":"scenario_finish","ok":true,"steps":["autogen","download","watermark"],"ts":"2025-11-22T12:30:00Z"} or {"event":"watermark_restore","processed":5,"errors":1,"seconds":32.5,"ts":...}【20†L93-L100】.
We display a table or list where each entry is one run or process:
For an Automator sequence: show date/time, the name (maybe user can name sequences or we list steps), and result (✅ if all ok, ❌ if aborted or errors). For example, "2025-11-22 12:00 – Automation: Full Pipeline – ✅ Success".
For significant processes (watermark removal, merges) run outside automator, we list them as well: e.g., "2025-11-22 12:45 – Watermark removal on 5 files – 4 ✅, 1 ❌ (some errors)"【20†L93-L100】.
Possibly for manual tasks like a single autogen run, we could include "Session 1 autogen – completed with 2 fails" if we logged such summary to history.
The user can click a history entry to see more details:
We can show a modal or expandable panel with the detailed log of that run (for automations, perhaps the step-by-step outcomes; we could reconstruct from process log events if stored).
For example, clicking an automation history could show each step and whether it succeeded, and any error message at the failure point.
This helps users recall what was done previously and identify patterns (like repeated failures).
We implement loading of history.jsonl entries on page open (since it might be large but we can limit to last N or allow scroll loading older).
Provide an option to clear history (or automatically prune old entries beyond retention settings if any).
Errors (❗) – A specialized view to aggregate all error events from logs. This page scans through either the process log or history to find any errors or warnings:
We list each error with timestamp, source, and message. E.g., "12:05:30 – Session 2 Autogen: queue limit reached (stopped)" or "12:47:10 – Watermark: OpenCV error 'template not found'" etc.
This gives the user a quick checklist of issues to address. We can sort by time (latest first) or by category.
Each error entry might have an expand option to show the surrounding context (e.g., 2 lines before and after from process log for context).
The user can filter by error type (if we categorize, e.g., "Rate Limit", "Network", "Cloudflare", "Other").
This page basically greps the log for "❌" and "⚠️" entries and presents them in a concise list. It's easier for the user than scrolling through the full log.
These logging pages ensure the user can monitor the system in real-time and retrospectively, fulfilling the functionality of the original UI's logs section【2†L123-L131】 and the requested new History/Errors pages. All logs are updated live via IPC events (we push new log lines to Zustand state arrays for each relevant log). The UI emphasizes clarity with formatting and filtering, so the user can quickly catch problems or verify that tasks ran successfully.
4.4 Content Editors Page (Prompts, Images, Titles)
The Content page (📝 Content) provides built-in editors for the text-based content that drives Sora Suite: prompt lists, image generation prompts, and video title lists. This corresponds to the original "Content editors" section of the UI【2†L139-L147】. It is divided into three sub-sections, accessible via tabs or a secondary menu:
Prompts Editor: Allows editing of the Sora prompt list(s). If the user uses one global prompt file (like prompts.txt for general profile), we show that by default. If the user has multiple prompt profiles (the original app could maintain prompts_<profile>.txt per session and had an active profile concept)【26†L5-L13】, we provide a dropdown to select which profile’s prompts to edit.
The editor is a multiline text area where each line is one prompt (supporting multi-line prompts via literal newline characters if needed – we might allow that via a special input method or instruct user to use \n if content requires, but likely each prompt fits on one line).
We display line numbers for reference. Short prompts can be written plainly, long prompts can span lines if needed (maybe use JSON mode for multiline? But the original stored multi-line by actual newline in file).
There is a "Save" button (or auto-save on blur). When saved, the main process writes to prompts.txt (or profile-specific file).
We also show a "Profile" label or selector. If user switches profile, the text area loads that profile’s prompt file.
Possibly include a history of sent prompts for that profile: e.g., below or alongside the editor, a small list "Previously submitted prompts:" showing prompts that are in submitted.log for this profile (the original "history of sent lines for each profile is kept separately"【2†L139-L147】). We can implement a toggle button "Show usage history" that opens a sidebar listing all prompts that have been submitted (with perhaps a count if some were repeated, and timestamp of last submission). This is read from submitted.log (filter by profile). The user can use this to avoid duplicates or recall what they've already done.
If the user wants to clear the history, they'd clear logs or make a new profile.
This addresses original mention of keeping prompt history per profile.
Image Prompts Editor: Edits the image_prompts.txt file which contains input for image generation. This file supports more complex structure (each entry can be a plain prompt or a JSON object with fields prompt(s), count, video_prompt, key, etc.)【17†L1-L10】.
We integrate a simple JSON-aware editor:
If the file content starts with { or [, we assume JSON lines. We could show a code editor (monospaced, maybe using a small embedded code mirror for JSON syntax highlight).
In practice, the format is one JSON per line or a raw prompt line. We can simplify by treating it as plain text but instructing the user they can enter JSON objects for advanced usage.
Possibly provide a template or helper UI: e.g., a small form to add a new image prompt entry:
Fields: "Prompt text (or multiple variants)", "Images count", "Optional video prompt to attach to", "Optional key".
On submit, it appends a properly formatted JSON line to the text area. This helps non-technical users add entries without writing JSON manually.
We ensure that when saving, the text is valid JSON for those lines that are supposed to be JSON. We might run a quick validation:
For each line that starts with { or [", try JSON.parse. If any errors, highlight line in red or show an alert "JSON format error on line X".
The original might not have had a robust UI for this beyond letting user use correct syntax; we can improve a bit.
After editing and saving, the main writes to image_prompts.txt. We also reset the manifest (or rather, on next image gen run, it will regenerate).
We also show a note about format: e.g., "Tip: you can write plain prompt lines or JSON objects with fields prompt/prompts, count, video_prompt, key." to guide the user.
There's no dedicated history for image prompts (since usage is short: you generate images and then likely modify prompt or reuse).
The user can always open the generated_images/manifest.json manually to see which image corresponds to which prompt, but we might present a small table:
For convenience, below the editor, list current entries from manifest.json: e.g., for each spec_index or key, show the prompt and how many images generated. Even possibly show thumbnails of the latest images (this is advanced: we could load images from disk and display small thumbnails next to their prompt text). That would mirror original which didn't have such an image gallery UI, but it would be user-friendly.
If not implementing thumbnails, a simple text list: "Spec 1 (key: cat-sun) – 2 images generated on 2025-11-22"【15†L8-L12】. This gives the user feedback after generation. The user can decide to adjust the prompt or count and regenerate.
Titles Editor: Edits titles.txt, the list of custom titles for naming downloaded videos. We present this as either:
A numbered list: each line has an index and a title. For example:
1. Cat Wins The Race
2. Doorbell Prank
3. Windy City Surprise
4.  (empty)
5. ...
The user can click on a title to edit it in place. If there are empty lines (meaning skip or to be filled), we preserve them.
Or a plain multiline text area with one title per line (including blanks). Given simplicity, a textarea might suffice.
Show the current cursor position (the index of the next title to be used for naming downloads). For example, if 3 videos have been named already, show "Next title index: 4". The original had a separate .cursor file to track this【28†L1-L9】. We can read that and display "Next title to use: Title 4 (Windy City Surprise)" or if out of titles, say "No titles left (will use numeric names)".
Provide a "Reset Cursor" button (original had this in UI)【33†L1-L9】. Clicking it sets the cursor to 0 (meaning start from first title again) and perhaps highlights that user should ensure not to duplicate names with already used videos.
As user edits the titles and saves, we write to titles.txt. If they inserted or removed lines, we adjust accordingly. The cursor file is not changed unless they click reset (or if they manually want to set it somewhere, we could allow editing the number).
If the user wants separate titles list per session (not common, but the code did generate session-specific titles file names with a slug)【28†L1-L9】, we might offer an option or separate lists per session. However, to keep UI simpler, assume one global titles list used for all downloads (which is the default usage).
On the Titles editor, we might list any leftover (unused) titles count and encourage adding more if nearing end. We also warn if any title is blank (the downloader code will skip blank and not use it)【18†L17-L25】.
This editor ensures the user can manage the naming of their downloaded videos easily, rather than editing files externally.
All content editors have Save buttons to write changes (or we auto-save on blur of field). We'll show a small "Saved ✔️" indicator when saved. The content page thus consolidates what was originally separate text files into one UI, making it easy for the user to manage the core textual content that drives the automation【2†L139-L147】. It also provides context like history of usage (for prompts) and results (for image prompts) to inform the user's edits.
4.5 Telegram Page (Bot Integration)
The Telegram page (📨 Telegram) allows the user to configure and use the Telegram bot for notifications and quick messages, as described in the original interface【2†L147-L154】. It is structured like a mini chat client for the bot:
Configuration Header: At the top, fields to enter Bot Token and Chat ID (the user’s target chat or channel). These are the credentials needed to connect. We include a "Connect" or "Enable" toggle:
If the user enters a token and chat ID and clicks "Enable", we save them to config (telegram.enabled=true along with the values)【21†L7-L15】. The main process might attempt a test connection (like fetching getMe or sending a test message "Bot connected ✔️").
If successful, we indicate "Bot connected" (and maybe show the bot's name returned by API). If failure (invalid token or network issue), show an error message and keep it disabled.
These fields remain visible so the user can update them if needed (with a Save button).
We also allow setting a default Chat ID if they want to send to a group or different chat.
Templates Management: Similar to Automator presets, there's a section to manage message templates:
A list of saved templates appears, each with a Name and Message Text (the message body). For example, Name: "Run Complete", Text: "✅ Automation finished successfully at {time}."
The user can add a new template (a form pops up for name and text), edit an existing one (inline or via modal), or delete one.
These templates are stored in config (telegram.templates as an array of objects)【21†L39-L47】. We update the config on add/edit/delete.
The template text can include placeholders like {session} or {count} – we will not automatically replace those unless we implement a simple macro (for now, it's static text as saved).
One template can be marked as "last used" (last_template in config)【21†L41-L49】, which we highlight or pre-select for quick sending.
Message Compose and Send: The main panel looks like a chat interface:
At the bottom, an input box where the user can type a message or select a template:
We provide a dropdown of template names (populated from saved templates). When the user selects one, its text loads into the input field (they can still modify it before sending).
They can also ignore templates and just type a custom message free-form.
A "Send" button (▶️ or 📤 icon). Clicking it sends the content of the input to the bot via telegram:send IPC. If a template was selected, we might note which one for history.
We also include a "Quick Delay" option if desired (like original had quick_delay_minutes)【21†L1-L8】 – e.g., a spinner to send the message after X minutes. If the user sets X > 0 before clicking Send, we schedule the send accordingly (but this requires the app to remain open; it's a minor feature, we can include a simple delay).
If scheduled, the message appears in the history with a clock icon until sent.
When the main process confirms the message was sent (or fails) via telegram:sent event, we update the chat history. If success, show the message with a check mark; if error, show with a red X and maybe the error reason (like "Forbidden: bot not in target chat").
The input field then clears (or retains text depending on user preference).
Message History Display: Above the input, we show the last N messages that were sent through the bot:
Each message is shown as a chat bubble on the right (since all are outgoing from the user’s perspective). We might use a speech balloon UI with the message text and a small status icon:
A green check (✓) or double-check mark if delivered successfully, a red warning (⚠️) if not.
Also include the time sent.
We don't show bot responses (we expect this bot mostly to send out, not receive messages).
This history is stored in memory (or partly in config if needed). We maintain it in Zustand telegramHistory (with entries like {text, status, timestamp}). The main process doesn't store full history by default (the original stored last 200 in a deque in memory)【21†L45-L53】, so we'll do similarly.
The user can scroll this area to see earlier messages (we keep maybe last 50 for display).
A "Refresh" button can fetch recent messages from memory if needed (not really since we push live).
A "Clear History" button could clear the in-memory list (this doesn't affect anything on Telegram side, just UI).
The user can also re-send a previous message easily: either we allow clicking a history item to copy its text into the input for editing and sending again, or have a "Resend" icon on each (the original interface allowed re-sending last notifications)【21†L41-L49】. We'll implement: clicking a history entry loads it into the compose box (and possibly selects its template if it matches one).
Usage Scenario: The user configures the bot and perhaps creates templates like:
"Start": "⚡ Automation {name} started."
"Finish": "✅ Automation finished. {successCount} prompts succeeded, {failCount} failed."
During usage, they might manually send a "Start" message when they run something (or even automate sending by adding a Telegram step in Automator for finish).
The Telegram page lets them quickly send updates or any custom message (like "Uploading results now..." etc.) to their phone without leaving the app.
If something goes wrong (like invalid chat_id), an error will be shown in red in the history and the user can correct the ID.
This Telegram UI replicates the capabilities of the original (configuring bot, sending test or templated messages, viewing history) in a more interactive way【2†L147-L154】. It ensures the user can get notified about Sora Suite's operations remotely via their phone.
4.6 Settings Page (Configuration Tabs)
The Settings page (⚙️ Settings) provides a structured interface to view and modify all configurable options of Sora Suite, grouped into thematic tabs for clarity【2†L154-L163】:
Paths (Directories): Configure where the app stores files. Fields:
Project Root Directory: base folder for the Sora Suite project (contains downloads, blurred, merged, etc.). Changing this will move or re-reference all subdirectories. (We will likely disable editing this if not needed, or allow and warn user to manually move existing files.)
Downloads Directory: location where raw downloaded videos are saved【3†L1-L9】. Default ./downloads under project root. Provide a "Browse" button to select a folder. If changed, update config and let the user choose whether to move existing files.
Blurred Directory: folder for videos with watermark blurred (the output of blur step)【3†L64-L72】.
Merged Directory: folder for merged compilation videos【3†L64-L72】.
Restored Directory: folder for videos with watermark fully removed (output of restoration)【3†L48-L56】.
History File: path to history.jsonl (usually in project root).
Generated Images Directory: path where GenAI outputs images【3†L36-L43】.
These paths are initially set to defaults (within project root). Changing any updates the config and we ensure the app uses the new location (we may require restarting some services or recreating directories). We validate the path exists or attempt to create it (with error feedback if not possible).
Image Generation (GenAI): Settings for the Google Imagen integration【2†L73-L82】:
API Key: input for the GenAI API key (we store it securely, not exposing in logs).
Model ID: dropdown or text (default "models/imagen-4.0-generate-001"). If Google releases multiple models, they can select (like 2B vs 4B parameters models).
Aspect Ratio: dropdown with options ("1:1 square", "16:9 landscape", "9:16 portrait", etc.) which correspond to allowed aspect ratio strings (config aspect_ratio)【3†L36-L43】.
Image Resolution: dropdown or slider for size (e.g., "1024 px (1K)", "768 px", "512 px"). This sets image_size (like "1K" for 1024)【3†L36-L43】.
Images per Prompt: number input for number_of_images to generate per prompt (default 1)【3†L36-L43】.
Output Format: fixed as JPEG (image/jpeg in config) – we mention it's using JPEG, maybe allow user to choose PNG (but config default is JPEG).
Style Options: optional fields to refine style:
Seed(s): text for specific random seeds (config seeds list)【17†L9-L18】.
Consistent Character: checkbox for consistent character design across images (config boolean)【17†L9-L18】.
Lens Type: text or dropdown if known options (like "DSLR", "macro", etc., per config lens_type)【17†L15-L23】.
Color Palette: text (like "vibrant", "pastel").
Art Style: text or dropdown (if config style presets were provided) – user can enter e.g. "oil painting" or "cyberpunk".
Reference Prompt: a text that might be appended or used as a guiding reference (config reference_hint).
Rate Limit: number for rate_limit_per_minute (if user wants to throttle API calls to avoid hitting quotas).
Max Retries: number for max_retries on API failures (default 3)【17†L15-L23】.
Daily Quota: number of prompts per day (0 if unlimited)【17†L31-L39】.
Quota Warning Threshold: number of prompts remaining at which to warn (e.g., 5)【17†L31-L39】.
Enforce Quota: checkbox to actually stop generating when quota exceeded (vs just warn)【17†L37-L44】.
Enable Notifications: checkbox (original notifications_enabled) to send a warning via UI/Telegram when nearing quota【17†L37-L44】.
These options correspond to google_genai config fields and are saved accordingly. We might also include a "Test Image Generation" button that triggers a quick generate with a sample prompt to verify the key and settings (especially since there's no direct "connect" test for this as with Telegram, but we can try a trivial request).
Automation (Autogen): Settings for the prompt automation engine:
Typing Delay (ms): slider or number (config human_typing_delay_ms, default ~12) – controls the tiny keystroke simulation delay【6†L82-L90】.
Start Confirmation Timeout (ms): number (config start_confirmation_timeout_ms, default ~8000) – how long to wait for prompt acceptance before treating as failure【6†L66-L75】.
Retry Interval (ms): number (how often to re-click if send button was disabled, config queue_retry.retry_interval_ms ~2500)【6†L144-L152】.
Backoff on Reject (s): number (default 180) for how long to wait when queue limit hit (config queue_retry.backoff_seconds_on_reject)【6†L148-L156】.
Success Pause Every N: number (success_pause_every_n, default 0/off) – if >0, after this many successful prompts, pause for ... seconds.
Success Pause Duration (s): number (success_pause_seconds) – how long to pause after N successes【6†L168-L176】.
DOM Timeout (ms): number (dom_timeout_ms for finding elements, default 12000)【6†L244-L251】.
Auto-Accept Media Agreement: checkbox (config auto_accept_media_agreement, default true) – if enabled, the automator will automatically tick any "I agree to upload media" dialog that appears on first image upload【6†L87-L95】.
Debug Mode: checkbox (config debug in autogen, default false) – if on, the autogen script can log extra info to console (we would produce more verbose logs).
These are advanced controls. Average users likely don't need to tweak them, but we expose them under an "Advanced Autogen Settings" collapsible section.
FFmpeg & Watermark: Settings for video processing:
FFmpeg Binary: file path input (if user needs to specify a custom ffmpeg executable; otherwise use system default). We can attempt to detect installed ffmpeg and show status "Found ffmpeg vX.Y" or "ffmpeg not found" if none. The user can browse to an ffmpeg binary if not in PATH.
Encoding Preset: dropdown (ultrafast, fast, medium, etc.) corresponding to config ffmpeg.preset (medium default)【3†L64-L72】.
CRF (Quality): number (0–51, default 18) for H.264 quality (lower is better quality/bigger file)【3†L64-L72】.
Copy Audio: checkbox (config ffmpeg.copy_audio, default true)【3†L64-L72】 – if true, use audio stream copy to preserve original quality; if false, re-encode audio (we'd then allow setting audio bitrate maybe).
Blur Preset: dropdown to select which zone preset to use for quick blur (config ffmpeg.active_preset, e.g., portrait_9x16 or landscape_16x9)【3†L69-L77】. We list presets defined in config (with their names). Possibly an option "Custom" which if selected, allows editing coordinates:
If user selects "Custom", we show a mini-form to input up to 3 zone coordinates (x,y,w,h). Or better, instruct to edit a config file if needed, as this might be too technical – perhaps skip detailed editing UI. Most users will stick to provided presets.
The chosen preset will be used by default in the blur function (we already allow switching here).
Watermark Removal: parameters for the advanced algorithm:
These are quite advanced; we might not expose all unless user needs to tweak:
Template File: path to the watermark image (default watermark.png in project root). If user wants to use a different watermark image (maybe if the watermark changes for different sources), they can browse to one. They should ideally use the one we shipped or extracted from a frame. We can provide a button "Extract from Video" that takes a frame from a selected video for them – out of scope perhaps.
Match Threshold: number (0–1, default 0.78) for template matching acceptance【19†L57-L64】.
Frames to Scan: number (default 120) – how many frames to sample for detection (0 means full scan)【19†L28-L36】.
Downscale for Detect: number (vertical resolution to downscale frames to for speed, default 1080)【19†L39-L47】.
Scale Range (min–max & steps): e.g., min 0.85, max 1.2, steps 9 (fields to adjust the range of watermark scaling to try)【19†L50-L57】.
Mask Alpha Threshold: number 0–255 (default 8) for converting watermark alpha to binary mask【19†L18-L26】.
Full Scan: checkbox (default false) – if true, scan every frame instead of sampling【20†L1-L9】.
Expand Region Padding: fields for px (default 12) and percent (default 0.18) to enlarge the detected watermark region for patching【20†L9-L17】.
Minimum Watermark Size: number (default 32 px) – detections smaller than this are ignored as possibly noise【20†L49-L58】.
Donor Search Span: number (default 12 frames) – how far ahead/behind to look for clean frames【20†L19-L27】.
Max Overlap (IoU): number (default 0.25) – maximum overlap allowed to consider a donor frame "clean" in that region【20†L25-L30】.
Donor Pool Size: number (default 4) – how many donor frames to use at most for median patch【20†L19-L27】.
Clone Mode: dropdown (Normal vs Mixed) – which seamlessClone mode to use (normal preserves lighting, mixed blends textures)【20†L68-L77】.
Inpaint Radius: number (default 6) – radius for Telea inpainting fallback【20†L79-L88】.
Inpaint Method: dropdown (Telea vs Navier-Stokes) – algorithm for fallback inpainting【20†L79-L88】.
These are truly expert knobs; by default the user need not change them. We might tuck them under an "Advanced Watermark Settings" section collapsed by default. Only if the user finds the default removal isn't perfect might they tweak threshold or full_scan, etc.
We include a note: "These parameters fine-tune watermark detection & removal. Default values work for the standard Sora watermark." to caution.
We also show a "Preview Watermark Detection" button – which takes one video frame and highlights where the watermark is found using current settings (like a dry-run detection). This would call workers/watermark_detector.detect_watermark on one video and overlay a red box on output image to show user. This is an advanced helper, but if time allows we mention it.
Possibly an "Reset to Defaults" button for this section to restore recommended values if user messed them up.
Chrome & Profiles: Settings related to Chrome usage:
Chrome Executable Path: file path input (if blank, the app uses the first found Chrome/Chromium). We might display the one detected. If user wants to use a different browser (Edge, etc.), they could provide its path (the original code looked for Edge and Chromium too)【23†L33-L41】. We'll document that "any Chromium-based browser path can be used".
Default User Data Directory: input (like for default profile name/path on this system) – original config had a list of OS-specific default paths and allowed active_profile selection【3†L80-L88】. We likely have taken one by default. We might not expose this if not needed.
DevTools Port Start: number (default 9222) – the base port for the first session's remote debugging. We automatically assign subsequent ports incrementally. If user needs a different port (maybe 9222 in use), they can change it here. Changes apply next time Chrome is launched.
Workspaces (Profiles): A section to manage sessions:
We list each session (id and profile path). Possibly allow editing name or profile dir. But altering an existing session's profile dir could be risky while running. Perhaps we only allow adding or removing sessions when Chrome instances are not running.
Add Workspace: a button that opens a dialog: "Session Name:" and "Chrome Profile Directory (leave blank for default)". If blank, we create a fresh user-data-dir under our project (like <project_root>/profiles/SessionName). If a path provided, use that.
We then update config.autogen.sessions array (with a new id, default cdp_port next free).
The UI then updates Workspaces page with the new session card.
Remove Workspace: a button next to each session in list (except maybe not allow removing 'default'). Removing would require user confirmation (and perhaps offering to delete its user data dir).
Removing updates config to drop it and UI will no longer show it. If Chrome for that session is running, we'd ask user to close it first.
This covers profile management which original didn't have a GUI for, but is a nice addition.
Auto-Launch on Start: toggles per session or global setting to automatically start Chrome for certain sessions when app opens (like original auto_launch_chrome flag)【3†L20-L28】. We can have a checkbox on each session entry "Launch at app startup". If enabled, config is saved and main process will launch those (we already implement that in code).
Auto-Start Autogen: possibly a global or per-session setting (auto_launch_autogen in config, could be 'idle' or times)【3†L20-L28】. This is a bit dangerous to do automatically on app open. We might omit it, or include a global "Resume last autogen on start" if needed. The original had auto_launch_autogen: idle meaning do nothing by default (other values maybe 'on' to start immediately – not documented but maybe planned).
Provide a button "Clear Chrome Cache" – which if clicked, closes Chrome if running and deletes the user-data-dirs for sessions (or specific ones). This might be used if user wants to flush cookies or start fresh. We warn user this will log them out of Sora etc.
YouTube/TikTok: In our rewrite, we are removing TikTok/YouTube auto-upload functionality entirely as requested. Therefore, we do not include those sections in the Settings UI. (Original had tabs for YouTube and TikTok with scheduling, but we skip them)【3†L100-L100】. If any config for them remains, we ignore it. The UI simply does not have those pages or settings, aligning with "Remove all TikTok and YouTube functionality" requirement.
Interface: Preferences for the app UI and behavior:
Show Activity Panel: checkbox (config ui.show_activity, default true) – toggles the visibility of the real-time process log panel on maybe the right or bottom. If user finds n hide it (like original allowed hiding context panel)【3†L87-L95】.
Compact Log View: dropdown (config ui.activity_density: 'compact' vs 'comfortable') – controls how dense log lines appear (original had this)【3†L87-L95】.
Theme Accent Color: dropdown (config ui.accent_kind, options like 'info' (blue), 'success' (green), etc.) – the original allowed choosing an accent highlight color for UI elements【3†L87-L95】.
Custom Commands: an interface to manage any user-defined quick commands.
The original had ui.custom_commands list and a "Commands" palette in UI【3†L95-L96】.
We implement a sub-list where user can add a command: give it a name and a shell command or script path. These commands will appear under a top-bar menu "Commands" or accessible via a hotkey palette (like Ctrl+K).
For example, a user might add { name: "Open Downloads Folder", command: "xdg-open ./downloads" } on Linux or the equivalent for Windows. Clicking that in the Commands menu will send an IPC to main to execute it (with caution).
We allow add/edit/delete commands in this settings section.
Auto-Update: If the app supports auto-updates, an option to toggle auto-check on startup and a "Chnal maintenance section mentioned auto_update in passing【2†L165-L172】. If we include it: we would call our updater script or check version from GitHub, etc. But this might be out-of-scope; we can simply provide a "Check for updates" which opens our documentation or release page.
Maintenance:
Auto-Cleanup on Start: checkbox (config maintenance.auto_cleanup_on_start, default false) – if enabled, on app launch we automatically delete old files beyond retention days.
Retain Downloads for: number of days (config maintenance.retention_days.downloads, e.g. 7)【3†L97-L100】. Similarly blurred and merged retention days. The user sets these, and if auto-clean on start is on, files older than that are removed.
A "Clean Now" button for each category (downloads/blurred/merged) to manually purge files older than the set days immediately (with confirmation).
Possibly a "Reset Application" button (with heavy confirmation) that clears all user data (log files, config to default, etc.) if needed for troubleshooting.
Documentation: Instead of mixing into maintenance, we give Documentation its own page (see below) or a section with a "Open Documentation" button (which could open the doc viewer or external link).
About: a small about section could show version number, author, and a link to GitHub or website for the project. This might be at bottom of settings or in Help page.
Help/Documentation: The UI includes a "Documentation" page (📖 Documentation) accessible via Help or directly in Settings navigation. This page loads an embedded Markdown or HTML of the user manual. We can ship the README.md (which is quite detailed as we saw, albeit in Russian – perhaps we have it in English as well) and use a React Markdown renderer to display it in-app, styled appropriately.
The user can scroll through headings (we might add a sidebar table of contents for quick nav).
If multi-lingual docs, possibly allow switching language (though out-of-scope unless we provide both).
We ensure any URLs in the doc open in external browser (to avoid navigation in our app window).
This fulfills the idea of a built-in MD viewer for documentation【2†L165-L172】.
Additionally, a "Report Issue" or "Contact Support" link might be provided here or in About.
All settings changes are applied either immediately or on restart depending on the setting:
Simple UI and behavior toggles apply right away (we programmatically adjust UI for show/hide panels or accent color).
Path changes and heavy config might need restart (we inform user if required).
We always save to config file so next launch uses them.
The structured Settings UI ensures the user can customize the app without editing YAML manually, matching and expanding on the original config capabilities【2†L154-L163】. We've reorganized to make it intuitive (for example, splitting Chrome and UI settings). Each group corresponds to a tab which mirrors the categories listed in the original settings navigation.
5. Modules and Components Summary
To realize the above functionality, the application is organized into cohesive modules on both the main and renderer sides: Main Process Modules:
main.js (Electron Main): Initializes the app, creates the BrowserWindow, and sets up IPC channels. On app ready, it loads config (from disk JSON/YAML), inits managers (Chrome, tasks, Telegram), and if configured, launches any auto-start tasks (e.g., auto-launch Chrome sessions). It also handles alosed, etc.) and global error handling (e.g., catch unhandled promise rejections in tasks and log them).
workspaceManager.js: Manages Chrome sessions. Contains functions like ace(id), closeWorkspace(id), and tracks running Browser instances and their DevTools connections. On launch, it uses puppeteer-core to start Chrome with specified user-data-dir and port【23†L128-L136】, and ensures a page is at Sora draens for Chrome process exits (to update status and possibly attempt reconnect).
autogenManager.js: Implements the prompt automation logic (as detailed in section 3.2). It exports an async function runAutogen(sessionId) that carries out connecting to the session’s page, injecting prompts from the session’s prompts file, and handling the loop of submission with retries【6†L135-L143】【6†L146-L154】. It uses the selectors fnverted to equivalent in puppeteer) to find text area, send button, etc., and orchestrates the Playwright-equivalent interactions with puppeteer. It emits IPC progress events for each prompt (success or failure) and a completion event【6†L225-L233】. It respects config timings (typing delay, timeout) and limits (backoff, pause) as set in settings.
genaiManager.js: Handles image generation via Google’s API. Provides generateImages(sessionId or profileKey) method which reads image_prompts.txt, calls the GenAI API for each prompt spec (honoring API key, model, count)【15†L4-L12】, saves image files to disk and updates manifest.json【13†L13-L27】. It uses node-fetch/axios for HTTP calls and monitors rate limits and quotas (perhaps using a token bucket or simple time checks)【17†L31-L39】. It emits events like images:progress (if we decide to update UI per prompt spec) and images:generated when done. It also logs warnings if nearing quota or if generation fails for a prompt (e.g., API error).
downloadManager.js: Contains functions to automate downloading videos from Sora (section 3.4). Key function downloadAll(sessionId, maxVideos) that attaches to the session’s page, opens the first draft card【18†L65-L73】, then enters the scroll-download loop, clicking the menu and download for each card【18†L128-L137】【18†L139-L146】. It sets up Page.setDownloadBehavior to auto-save files, and uses the naming logic (with titles list) to rename each file as it arrives【18†L128-L137】【18†L137-L146】. It keeps track of how many downloaded and stops when reaching maxVideos or feed end【18†L220-L228】. It sends download:progress events for each file (with file name) and download:finish at end. It also detects Cloudflare issues and sends NOTIFY:CLOUDFLARE_ALERT and waits as described【18†L170-L178】.
ffmpegManager.js: Implements video post-processing features:
blurVideos(sessionId or inputDir): uses fluent-ffmpeg to blur watermark zones on all videos in the input directory (or just those for a given session). It builds filter graphs per config preset zones【3†L69-L77】 and encodes each video to output (in blurred_dir). Emits events per file processed and final event.
mergeVideos(inputDir, pattern, groupSize): uses fluent-ffmpeg (concat demuxer or filter) to merge videos from inputDir matching pattern (e.g., "*.mp4"). Produces one or more merged files in merged_dir, with names like "merge_1.mp4". Sends progress for each merged file and a completion event.
These functions run synchronously (one file at a time) to avoid overloading CPU (though we could parallelize if ffmpeg.blur_threads is set >1, splitting tasks).
watermarkManager.js: Implements the advanced watermark removal (section 3.6). It uses opencv4nodejs:
A function r(sessionId or inputDir) that iterates through videos, and for each runs detection then restoration. It internally has helper detectWatermark(frameSet) and processFrames(frames, detections) as described, or combined per video. It loads `watermark. thresholds, etc., uses VideoCapture to read frames, etc. This module likely is the heaviest and might even run in a separhread to not block the main event loop (we could spawn a Worker with this code if openCV processing is slow, and communicate progress via parent thread events).
It emits events like watermark:progress (for each frame or each video) and watermark:complete with count/errors【20†L93-L100】. If run as part of Automator, automator will interpret those events for step status updates.
telegramManager.js: Integrates with Telegram API:
It stores the botToken and chatId from config. Provides sendMethat posts to https://api.telegram.org/bot<token>/sendMessagewith given chat_id and text. On success or failure, it triggers atelegram:sent` event (with success boolean and maybe short error message). It might also push the message to an internal history array for reference (though we keess and let renderer track history, we could maintain a capped list).
It also could handle templating: if we allow placeholders (like {time}), we can replace them here (e.g., {time} => new Dag()).
Listens for any telegram:send IPC calls from UI, and executes accordingly, then replies with result event.
Possibly a helper to test (like calling getMe and returning bot name).
automatorManager.js: Coordinates the execution of automation sequences as in section 3.7. It exports runSequence(steps) that:
Iterates over the steps array. For each step, depending on type, calls the appropriate manager function:
For session steps, if multiple sessions, it can either run them sequentially or in parallel. In our design, to mirror original (which effectively did sessions one after another via run_next loop)【35†L70-L78】, we'll do sequentially by defaulel if safe – but parallel autogen on two sessions is actually fine since they use separate Chromes, we could do concurrently to speed up. The original code attempted concurrency (they launched tasks async and waited for both)【35†L70-L78】, but effectively still sequential in implementing UI because they needed to manage outputs. We can improve by actually running concurrently when multiple sessions are listed, since our architecture supports it).
Manage errors: if a sub-task fails and continueOnError is false, we abort sequence (stop sta steps, and propagate an error to UI).
If continueOnError is true, we log the error (and maybe mark step as partial success) and continue to next step.
It sends events automator:stepStatus:
Before starting step i, event {index:i, status:"running"}.
On step success, event {index:i, status:"success"}.
On step failure (with continueOnError=false causing abort), event {index:i, status:"error", error:"<message>"}.
If continueOnError true and a failure happened, maybe status "warning" to denote it failed but sequence continues.
After all steps done or aborted, send automator:finished event outcome (ok or error).
It uses the managers above to actually perform each step:
E.g., for "session_prompts" step with sessions [A,B], call autogenutogen(A) and runAutogen(B) possibly concurrently (Promise.all), and wait for both to finish. Gather any errors.
For "global_blur", call ffmpegManager.blurVideos(globalDownloadsDir) and wait.
For "telegram_message" step, call telegramManager.sendMessage(templateText) (or if template name given, find text from config).
r functions themselves emit events (like autogen progress) that UI already handles in logs; auto not forward all those, focusing on step-level statuses to UI.
Ensures that if user sends stop command (automator:stop), it will break out of the loop and attempt to cancel any currently running tasks:
For currently running step, if it's, say, autogen on sessions, call autogenManager.cancel(session) for each (we implement a cancel mechanism, e.g., setting a flag that runAutogen checks each prompt iteration and exits if set, and perhaps closing Chrome page or pressing stop).
If it's a long watermark processing, perhaps we can't easily stop mid-video unless we also have a cancel flag the code checks per frame. We will implement such checks in loops.
The automatoen sends automator:finished with an indication it was aborted by user.
This module ties everything together to implement the high-level automation the user defines in the UI.
Renderer Components:
Sidebar.jsx: The navigation menu with sections for Workspaces, Automation, Logs, Content, Telegram, Settings, etc. Clicking an item triggers view switching (we use React Router or our own state to swap pages).
WorkspaceCard.jsx: Renders a single workspace’s info and buttons (used in Worksp- WorkspacesPage.jsx: Maps through workspaces state array, creates a WorkspaceCard for each. Also possibly a button to add new workspace (if implementing that in UI).
AutomatorPage.jsx: Renders the list of step components and controls (Add Step, Preset actions, Run/Stop). It manages the automatorSteps state slice (via Zustand).
It uses child component `StepEditor.h step, which includes the type dropdown, session selector (if applicable), fields for limit/group, continueOnError checkbox, and a r.
PresetSelector.jsx might handle the preset dropdown and apply/append/save logimatorPresets state and actions).
On run, it disables inputs and listens for events via Zectly via an IPC callback updating automatorStatus state (like currentIndex and any error).
It highlights the current running step (maybe by adding a spinner icon) and marks completed ones with check orcribed.
LogsPage.jsx: Contains sub-tabs for "Session Logs" and "Process Log".
For Session Logs, a dropdown to pick session (or one column per session if we choose multi-column view). When a session is selected, it subscribes to that session's logs slices in Zustand (we can store logs in state or simply read from log files on demand; better maintain in memory for live update).
Shows three textareas or scrollable divs for Submitted, Failed, Download logs for that session, as described.
For Process Log, a component that subscribes to global log list (Zustand processLog array). It renders each log line with emoji and colored text. It has filter controls (which updatstate to hide certain lines; filtering can be done client-side since logs not huge).
A clear button (calls an action to cleay) and maybe an export (which can trigger main to write history.jsonl or so to a chosen path, log to history already, maybe export not needed as user can find history.jsonl).
The plines are appended in real-time via IPC events (we push to state on every main log event).
HistoryPage.jsx: Reads the parsed history.jsonl lines (or uses a Zustand historyEvents loaded at startup). Renders a list or table of runs as described. Could allow filtering by event type (scenario vs process).
Possibly includes a button to clear ich would remove the file or truncate it).
If we want to allow clicking an entry for details, implement that either in this component (with expanded rows) or a modal an entry click, e.g., HistoryDetailModal that shows relevant info from that run (like steps and their outcome if it was an automator run).
ErrorsPage.jsx: s to either processLog or history state to pick out errors/warnings.
We could pre-compute an errorsList in state that collects all ❌ and ⚠️ events. This list updates whenever log adds such an event.
It lists them with tsage and maybe link to context (if we provide a "Go to Log" button that opens the Process Log page scrolled to that time – not trivial unless we store index).
It has filter options and a clear (clearing errors could simply acknowledge them but not remove from actual logs).
Co: Contains sub-tabs for Prompts, Image Prompts, Titles:
PromptsTab uses a multi-line text field. We bind its value to Zustand state promptsContent[currentProfile] (we can keep unsaved edits here). On Save, we send to main (which writes file and also updates the loaded prompts for autogen).
Possibly we maintain a dictionary in state: profileKey -> text content. On initial load, main process can supply all prompt files content via r we read directly from disk in renderer (but better via main for security).
The profile selector dropdown is populated from config autogen.sessions (which has prompt_profile per session)【3†L8-L17】. But in our redesign, maybe profile names are just session names. We can treat each session as having its own prompt file if desired, or just one global. We'll assume at least a __general__ profile for default. If user selects a different session, we load that session's prompt file (if none separate, might load same global or an empty template for new).
We also include a "History" sidebar toggled by a button, which lists submitted prompts (Zustand can provide that from logs) for the selected profile. This is read-only list for reference. (Original said "history of sent lines per profile is kept separately"【2†L139-L147】, which suggests UI reference rather than user editing anything).
ImagePromptsTab with a text area or code editor. We might integrate a small code-highlighting library for JSON (like react-simple-code-editor + prism for JSON) to make braces and quotes colored, easing JSON editing. Or at least monospaced font.
We maintain imagePromptsContent state string. On Save, validate each line as described. If valid, send to main to overwrite image_prompts.txt.
Possibly a helper "Add Prompt" formN editing directly (makes it user-friendly). But given time, maybe skip and rely on user to follow format for advanced usage.
We can show a summary of the current manifest: e.g., after generation, to the right of the text area we could list each spec index with how many images generated. But that might complicate live updates if user hasn't generated yet. Instead, maybe have a "View Genes" button which opens a simple gallding generated_images/ folder (optionally).
That could be part of a separate "Gallery" page or under History, but it’s an idea. Not mandatory, but if easy, show thumbnails of images for the last generation at least in a modal (the user can open the folder manually too).
TitlesTab with a text area listing each title line. We maintain titlesContent in state. On Save, write to titles.txt. Also update titlesCount (length of list) and maybe adjust cursor if needed (we do not auto-reset cursor if user adds lines; cursor continues from where it was).
We display the current cursor below: e.g., "Next title to use: #4" and a "Reset" button next to it. If clicked, we send titles:resetCursor to mts the cursor file to 0 and updates state).
If the user deletes or inserts lines before the cursor index, the mapping of index to title changes. Probably simplesedit the list drastically, we might reset cursor to 0 or ensure it doesn't point past end. We can implement: after save, if cursor index > new length, set cursor = new length (meaning no titles left, will use numeric).
Also, highlight blank titles or duplicates so user knows to fill them or that there might be issues.
Each tab has its own Save button (or one global Save All, but separate is clearer).
Possibly an "Import/Export" for these text files if user wants to backup or edit in external editor. But they can also just open the file from disk; not critical to duplicate that.
Thee thus centralizes content editing as the original did【2†L139-L147】, but with slight enhancements (history view, validation).
TelegramPage.jsx: Implements the UI described in 4.5:
Fields for token/chatId (with Save and test).
Template list with Add/Edit/Delete (modals for editing).
Message history area (div with bubbles for each message).
Compose input and Send button.
It uses Zelegram.templates, telegramHistory` and config for token/chat.
On send, it calls an action that invokes an IPC to main. The main then responds with telegram:sent which we handle to update history entry status (like mark the last message as delivered or failed).
The history list in state might contain entries with a temp "pending" status until we get result; or we just wait to add it on success. But better show it immediately with spinner or single check that turns double-check on confirmation. Simpler: add immediately with status "pending..." (blue clock icon), then update to ✓ or ❌.
Template editing: we can reuse some component like `TemplateEfor new/edit, similar to presets editing code.
The page also could allow switching the chatId if user wants to test sending to themselves vs a group (maybe just editing chatId field directly).
It's connected to config via Zustand such that changes persist (we ensure to call main to save config for templates and token changes).
This page ensures the user can manage and use Telegram easily.
SettingsPage.jsx: Contains tabs for each category described in 4.6. We can implement each as a sub-component:
PathsSettings.jsx, GenAISettings.jsx, AutogenSettings.jsx, FFmpegSettings.jsx, ChromeSettings.jsx, InterfaceSettings.jsx, MaintenanceSettings.jsx (which may include Documentation or not).
These sub-components eacled form inputs bound to config state (Zustand or context specifically for config editing). We might have a separate store slice for unsaved config changes or directly bind to config state if we keep the config in Zustand.
For simplicity, we might treat config in main as source of truth and fetch it via IPC on load into a local state in React (like a copy of config). The user edits the copy, and when hitting "Apply" or leaving the page, we send the diff to main to update config file and apply changes.
Alternatively, use IPC for each change immediately: e.g., on toggling a checkbox, call ipc.send('config:set', {key, value}). That could be too granular. Possibly accumulate or just do immediate to keep config always updated (like enabling a feature).
Some settings (like UI toggles) we apply immediately in front-end as well for effect.
Each sub-section likely has its own Save/Apply button or a global one at bottom of settings page to apply all. The original mentioned "settings auto-save but can apply manually if needed"【2†L161-L163】. We can auto-save on each change to reduce friction (with maybe a small "Saved" indicator).
Complex changes (like adding a workspace) we do via dedicated UI flows as described.
The Settings page is largely straightforward forms reflecting config structure.
DocumentationPage.jsx: If included as separate route, loads the Markdown. We can use a library like react-markdown with allowed tags and our own styling (via Tailwind classes for headings, etc.). We fetch the Markdown content either from an embedded file (we can import README.md as text via Webpack loader) or from a public URL. To keep offline capability, bundling the documentation is fine.
We implement scroll and possibly a table of contents if needed (maybe generate one by scanning headings).
If documentation is long (which in original it is quite detailed and maybe only in Russian; for our context maybe we have an English version to embed), we ensure the page is scrollable.
The user can also click external links in it which we open via shell.openExternal.
Finally, an About dialog (not necessarily a full page, could be a modal from Help menu) that shows version, license, etc., could be included. Possibly the Documentation page top can serve as "About this app (vX.Y)". All these UI components use Tailwind CSS classes to ensure a coherent dark theme (e.g., background dark-gray, text light-gray, form inputs with dark background and bright text, etc.). Icons used (like emoji or font icons) will visually convey statuses as described (we might use an icon library for consistency but emoji can suffice as in original). Module Mapping to Original Structure:
The Chrome session handling in new app corresponds to original PyQt handling plus portable_config.py. We now manage via puppeteer and settings in ChromeSettings UI cover what was in portable config (binary path, profiles)【23†L- The Prompt automation module corresponds to original workers/autogen/main.py (we have effectively ported its logic to JS)【6†L135-L143】【6†L146-L154】.
The Image generation module corresponds to the usage of google-genai in original (we replaced it with direct API calls but from user perspective it's same feature)【2†L73-L82】.
The Downloader module maps to workers/downloader/download_all.py (we ported its flow)【18†L220-L228】.
The Watermark cleaner (OpenCV) corresponds to workers/watermark_cleaner/watermark_detector.py and restore.py (we combined their functionality in one manager)【20†L46-L54】【20†L68-L77】.
The Automator logic corresponds to the original Automator (they built sequences and executed sequentially with stop on error)【35†L23-L31】【35†L57-L68】, which we have with enhancements (parallel session support, UI try/catch).
The Telegram integration corresponds exactly to original (they had templates, scheduling, and history in UI)【21†L41-L49】, which we have matched with an interactive chat-like UI.
The Logs/History we matched to original logs page and extended with separate History & Errors pages as requested.
The Content editors correspond to original content editing sections (prompts per profile, image prompts JSON, titles table)【2†L139-L147】, we delivered similar but with validation and history hints.
The Settings correlates to original settings groups (we covered directories, genAI, autogen, ffmpeg, chrome, interface, maintenance)【2†L154-L163】 and removed those for features we dropped (YouTube/TikTok).
The Help (Documentation) addresses the request for an MD viewer for documentation and possibly the "Maintenance & documentation" original section where they could view full event log and help info【2†L165-L172】.
