# Shine It Asset Agent — V7

AI asset-generation pipeline for **single-item ASMR cleaning games**.
Brainstorm steps → generate at 2K → approve → auto background-remove + split → learn.

V7's headline feature: **a Streamlit web UI** with a freeform session model, full EN/VN bilingual support, Nano-Banana 2 @ 2K, and a cross-session pattern learner.

```
┌──────────────────────────────────────────────────────────────┐
│  🎬 Start Level   →  Gemini brainstorms a cleaning chain     │
│  🛠 Generate      →  Per-step prompts + auto-ref + 2K images │
│  ✅ Approve       →  rembg/BiRefNet + tight crop + split     │
│  📋 Review        →  Verdicts for the current session        │
│  📚 History       →  Browse archived sessions                │
│  🧠 Memory        →  Curate the agent's brain                │
│  📊 Insights      →  Cost + approval-rate analytics          │
└──────────────────────────────────────────────────────────────┘
```

---

## Quick start (any machine)

### Prerequisites
- **macOS / Linux / WSL**, Python **3.11+** (3.13 tested)
- A **Google AI Studio API key** (`GOOGLE_KEY`) — get one at <https://aistudio.google.com/app/apikey>
- *(optional)* A **Fal.ai API key** (`FAL_KEY`) if you want the Fal Nano-Banana backends
- *(optional)* A **Google service-account JSON** (`gemini_service_account.json`) if you want the Vertex `google_flash` backend

### One-shot setup

```bash
# 1. Clone the V7 branch
git clone -b V7-include-UI https://github.com/deekay71/asmr-asset-generation-agent.git shine_it
cd shine_it

# 2. (recommended) virtualenv
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. Dependencies
pip install -r pipeline/requirements.txt

# 4. Credentials
cp .env.example .env
# then edit .env:
#   GOOGLE_KEY=AIza...        ← required for default 2K backend
#   FAL_KEY=fal_...           ← optional, only for fal_* backends

# 5. (optional) Vertex service account for google_flash backend
cp ~/Downloads/gemini_service_account.json .

# 6. Launch the UI
streamlit run pipeline/ui/Home.py
```

Open **http://localhost:8501** in your browser. That's it.

The CLI (`python pipeline/shine_it_pipeline.py …`) still works unchanged for power users and CI.

### Verify it works
1. Click **🎬 Start Level** in the sidebar
2. Type `AC`, set State steps `3`, Tools `2`, click **🧠 Brainstorm steps**
3. You should see 3 state cards (super-dirty → intermediate → pristine) and 2 tool cards in ~10s
4. Click **🚀 Create session** → sidebar takes you to **🛠 Generate**
5. Click **▶️ Generate** on step 1 → an image appears in ~30s tagged with `🖼 2048×2048 · model gemini-3.1-flash-image-preview`

---

## What V7 adds (vs V6)

| Feature | What it does |
|---|---|
| **Streamlit UI** (`pipeline/ui/`) | Replaces the static HTML review + CLI-only workflow. Multipage app: Start Level, Generate, Review, History, Memory, Insights, How It Works. |
| **Freeform session model** | Generation no longer requires a `level_NN/items_config.json` GDD. Sessions live at `runs/<ts>_<slug>/`, each with N user-defined steps and their generations + verdicts. |
| **🎬 Start Level brainstorm** | Type an item name → Gemini Flash text proposes a cleaning chain. Enforces *exact* state count, gradual dirty→clean, step 2 must still have problems, conditioned on past approved sessions for style match. |
| **`google_nb2_2k` backend** | Nano-Banana 2 (`gemini-3.1-flash-image-preview`) at **2048×2048** via Google AI Studio API key. New default. |
| **`google_nb_pro_2k`** | Nano-Banana Pro (`gemini-3-pro-image-preview`) at 2K for hero shots. |
| **Auto bg-removal on Approve** | FAL BiRefNet (cloud) → rembg (local) fallback → hard-binary alpha → tight crop → `scipy.ndimage.label` component split into per-blob PNGs. Lands in `step_NN/approved/`. |
| **Style anchor** (session-level) | Upload one image → auto-prepended to refs for every generation in the session. Pins visual style across the chain. |
| **Auto previous-step ref** | For state-kind steps, the prior step's approved image is auto-attached as a reference — keeps chain consistency without manual ref management. |
| **Cross-session learner** | "🔬 Analyze patterns" on Memory tab. Walks every approved + regen + deny verdict across sessions; Gemini distils into best_practices / forbid candidates in `step_patterns.json`. |
| **Editable step IDs + reorder** | Click into a step header to rename (folder on disk + every ref/generation path renames atomically). Up/down arrows reorder. |
| **Prefill from past steps** | Each step has a dropdown listing every step across all sessions (✅ marks approved). One click copies all template fields. |
| **Per-generation telemetry** | Each generation stamped with backend, model_id, actual decoded size, ts. Insights dashboard groups by all of these. |
| **Full EN/🇻🇳 VN i18n** | Every UI string translated + the prompts sent to the model. Brainstorm and learner output in Vietnamese when the toggle is on. |
| **Improve-with-comment regen** | Each preview has two regen modes: 🔁 Regenerate (same prompt, new seed) and ✨ Improve with comment (appends a correction to the prompt + uses prior gen as a ref). |

V6 still here too: `pipeline/prompt_agent/`, `step_patterns.json`, `pipeline/shine_it_pipeline.py` CLI, and the GDD-driven phase chain all work unchanged.

---

## Repo layout

```
shine_it/
├── README.md                            ← you are here
├── .env                                 ← GOOGLE_KEY, FAL_KEY (gitignored)
├── gemini_service_account.json          ← optional, Vertex auth (gitignored)
├── docs/
│   ├── WORKFLOW.md
│   ├── BEST_PRACTICES.md
│   ├── SETUP.md
│   ├── TOOL_RULES.md
│   └── V6_CHANGELOG.md
├── pipeline/
│   ├── ui/                              ← V7 Streamlit app
│   │   ├── Home.py                      ← entry point
│   │   ├── pages/
│   │   │   ├── 0_Start_Level.py
│   │   │   ├── 1_Generate.py
│   │   │   ├── 2_Review.py
│   │   │   ├── 3_History.py
│   │   │   ├── 4_Memory.py
│   │   │   ├── 5_Insights.py
│   │   │   └── 6_How_It_Works.py
│   │   ├── services/                    ← backend logic
│   │   │   ├── sessions.py              ← session CRUD + style anchor + prev-ref
│   │   │   ├── generator.py             ← build_prompt + I2I dispatch
│   │   │   ├── brainstorm.py            ← Start Level Gemini brainstorm
│   │   │   ├── learner.py               ← cross-session pattern analyzer
│   │   │   ├── postprocess.py           ← bg removal + tight crop + split
│   │   │   ├── memory_io.py             ← step_patterns.json CRUD
│   │   │   ├── insights.py              ← analytics aggregations
│   │   │   ├── i18n.py                  ← EN/VN strings + language toggle
│   │   │   └── projects.py              ← path helpers
│   │   └── static/
│   │       ├── how_it_works.en.md
│   │       └── how_it_works.vi.md
│   ├── shine_it_pipeline.py             ← V6 CLI orchestrator (unchanged)
│   ├── i2i_backend.py                   ← + GoogleAIStudioBackend
│   ├── prompt_agent/                    ← V6 prompt assembly + learn
│   ├── fal_helper.py
│   ├── step_patterns.json               ← agent's persistent memory
│   └── requirements.txt
├── runs/                                ← user sessions (gitignored, per-machine)
│   └── 20260526_140212_aircon/
│       ├── session.json                 ← steps + verdicts + processed paths
│       ├── style_anchor.png             ← session-pinned style ref
│       ├── step_01/
│       │   ├── refs/                    ← user-uploaded refs
│       │   ├── gen_001.png              ← raw generation
│       │   ├── gen_001.prompt.txt
│       │   └── approved/                ← post-Approve outputs
│       │       ├── gen_001.png          ← bg-removed + cropped (merged)
│       │       ├── gen_001_part_01.png  ← split components, if >1
│       │       └── gen_001_part_02.png
│       └── step_02/…
├── projects/                            ← V6 GDD-driven levels (still usable)
│   ├── level_05_plushie/
│   ├── level_06_aircon/
│   ├── level_07_keyboard/
│   └── level_08_fireplace/
└── references/                          ← style refs, GDD PDFs, tool orientation
```

---

## Backends at a glance

| Name | Model | Auth | Resolution | Cost/call | Best for |
|---|---|---|---|---|---|
| **`google_nb2_2k`** *(default)* | `gemini-3.1-flash-image-preview` | `GOOGLE_KEY` | 2048×2048 | ~$0.04 | Default — Nano Banana 2 |
| `google_nb_pro_2k` | `gemini-3-pro-image-preview` | `GOOGLE_KEY` | 2048×2048 | ~$0.14 | Hero shots |
| `google_flash` | `gemini-2.5-flash-image` | Vertex SA | 1024×1024 | ~$0.04 | Legacy 1K |
| `fal_nb_pro` | `fal-ai/nano-banana-pro/edit` | `FAL_KEY` | up to 2K | ~$0.06 | Fal alternative |
| `fal_nb2` | `fal-ai/nano-banana-2/edit` | `FAL_KEY` | 1024×1024 | ~$0.03 | Cheap Fal |

Switch backends per-session via the **Backend** dropdown at the top of the Generate tab. Choice is sticky to each session.

---

## Deploying on a new PC — step-by-step

### A. Local dev (recommended)

Already covered in **Quick start** above. Five commands and a browser open.

### B. Headless server / VM / cloud instance

1. **SSH in.** Install Python 3.11+ and git.
   ```bash
   sudo apt update && sudo apt install -y python3.11 python3.11-venv git
   ```
2. **Clone + venv + install** as in Quick start.
3. **Populate `.env`** with `GOOGLE_KEY` (and `FAL_KEY` if used).
4. **Run Streamlit bound to all interfaces:**
   ```bash
   streamlit run pipeline/ui/Home.py \
     --server.address 0.0.0.0 \
     --server.port 8501 \
     --server.headless true
   ```
5. **Visit `http://<server-ip>:8501`** from your browser.
6. **Persistent run:** wrap it in `tmux` / `screen` / `systemd`:
   ```ini
   # /etc/systemd/system/shine-it.service
   [Unit]
   Description=Shine It UI
   After=network.target

   [Service]
   User=ubuntu
   WorkingDirectory=/home/ubuntu/shine_it
   ExecStart=/home/ubuntu/shine_it/.venv/bin/streamlit run pipeline/ui/Home.py --server.address 0.0.0.0 --server.port 8501 --server.headless true
   Restart=on-failure

   [Install]
   WantedBy=multi-user.target
   ```
   ```bash
   sudo systemctl enable --now shine-it
   sudo systemctl status shine-it
   ```

### C. Behind a reverse proxy (HTTPS / domain)

Put `nginx` or `caddy` in front:

```nginx
server {
    listen 443 ssl;
    server_name shine.example.com;

    ssl_certificate     /etc/letsencrypt/live/shine.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/shine.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 600s;
    }
}
```

Streamlit handles WebSockets — the `Upgrade` header is required.

### D. Docker

```dockerfile
# Dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY pipeline/requirements.txt ./pipeline/requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r pipeline/requirements.txt
COPY . .
EXPOSE 8501
ENV PYTHONUNBUFFERED=1
CMD ["streamlit", "run", "pipeline/ui/Home.py", \
     "--server.address", "0.0.0.0", "--server.port", "8501", \
     "--server.headless", "true"]
```

```bash
docker build -t shine-it .
docker run -p 8501:8501 \
  -e GOOGLE_KEY=$GOOGLE_KEY \
  -e FAL_KEY=$FAL_KEY \
  -v $(pwd)/runs:/app/runs \
  -v $(pwd)/pipeline/step_patterns.json:/app/pipeline/step_patterns.json \
  shine-it
```

Mount `runs/` so sessions survive container restarts. Same for `step_patterns.json` so the agent's learned memory persists.

### E. Streamlit Cloud (one-click hosted)

1. Push this repo (the `V7-include-UI` branch) to a private GitHub repo.
2. Connect at <https://share.streamlit.io/>.
3. Set **Main file path** to `pipeline/ui/Home.py`.
4. Add secrets in the Streamlit Cloud UI:
   ```toml
   GOOGLE_KEY = "AIza..."
   FAL_KEY = "fal_..."           # optional
   ```
5. Deploy. Streamlit Cloud auto-installs from `pipeline/requirements.txt`.

⚠️ **Storage warning:** Streamlit Cloud's filesystem is ephemeral — `runs/` and `step_patterns.json` reset on every redeploy. For shared deployments, mount external storage (S3, GCS) or use a self-hosted option above.

---

## Required env vars

| Var | Where | Required? | Used by |
|---|---|---|---|
| `GOOGLE_KEY` | `.env` or shell | **Yes** | Default 2K backends + Start Level brainstorm + cross-session learner |
| `FAL_KEY` | `.env` or shell | No | `fal_nb2`, `fal_nb_pro` backends + BiRefNet bg removal on Approve |
| `GOOGLE_APPLICATION_CREDENTIALS` *or* `gemini_service_account.json` in repo root | env var or file | No | `google_flash` Vertex backend |

If `FAL_KEY` is absent, Approve still works — postprocess falls back to local `rembg`.

---

## Troubleshooting

- **"NoneType is not iterable" from Google API** — content/safety filter refused. The traceback expander now shows `finish_reason=SAFETY` + the model's text refusal. Re-phrase prompt; avoid heavy negative directives.
- **Generated images still 1024×1024** — your session was created with a 1K backend. Open Generate → Session expander → switch backend to `google_nb2_2k`. Persists to disk.
- **Streamlit shows duplicate-key error after delete** — fixed in V7's `add_step` (uses max-index+1). Pre-V7 sessions auto-repaired on load.
- **Background still visible on split parts** — was an issue with rembg's soft alpha; V7 binarises (alpha 0 or 255) + zeroes RGB on transparent pixels. Re-Approve to regenerate.

---

## Read in this order

1. **`README.md`** ← you are here
2. **In-app** — the **6. How It Works** tab (renders `pipeline/ui/static/how_it_works.<lang>.md`)
3. **`docs/V6_CHANGELOG.md`** — the V5→V6 prompt-agent foundation
4. **`docs/WORKFLOW.md`** — original phase-by-phase walkthrough
5. **`docs/BEST_PRACTICES.md`** — visual rules

---

## License

See `LICENSE`. The default attribution is preserved in commits.
