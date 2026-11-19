# PT Slide Viewer

A minimal, free, self-hosted viewer for microscopy proficiency-testing rounds.
Participants open one web page, pick a round, switch between the images, and
zoom or drag around each one. There's nothing else on the page.

```
pt-viewer/
├── add_round.py      ← run this on your computer to add each new round
├── README.md
└── docs/             ← this folder IS the website (GitHub Pages publishes it)
    ├── index.html
    ├── manifest.json        (list of rounds, written by add_round.py)
    ├── openseadragon/       (zoom/pan library, bundled so the page has no outside dependencies)
    └── rounds/              (image tiles, written by add_round.py)
```

## Why there's a conversion step

Web browsers can't open .czi files, and a 300–500 MB file is too big to download
just to look at it. `add_round.py` converts each .czi into a "deep zoom" pyramid:
thousands of small JPEG tiles at every zoom level. The viewer only downloads the
tiles that are on screen, so a huge slide opens instantly, even on a phone.

---

## 1. One-time setup

### a) Put the website on GitHub (about 10 minutes)

1. Create a free account at <https://github.com/signup>.
2. Install **GitHub Desktop** from <https://desktop.github.com> and sign in with that account.
3. Unzip `pt-viewer.zip` somewhere permanent, such as your Documents folder.
4. In GitHub Desktop, go to **File → Add local repository…** and choose the `pt-viewer` folder.
   It will say this isn't a repository yet. Click **create a repository**, then **Create repository**.
5. Click **Publish repository**. **Untick "Keep this code private"** (free GitHub Pages
   needs a public repository), then click **Publish repository**. The upload takes a minute or two.
6. In GitHub Desktop, choose **Repository → View on GitHub**. On the GitHub website, open
   **Settings → Pages**. Under *Build and deployment*, set **Source: Deploy from a branch**,
   **Branch: main**, and **folder: /docs**, then click **Save**.
7. After a minute or two, the Pages settings show your address, something like
   `https://yourname.github.io/pt-viewer/`. That's the link you give participants.

### b) Install Python (for converting .czi files)

1. Install Python 3.9 or newer from <https://www.python.org/downloads/>
   (on Windows, tick **"Add python.exe to PATH"** during install).
2. In GitHub Desktop, choose **Repository → Open in Command Prompt** (Mac: *Open in Terminal*) and run:

   ```
   pip install pylibCZIrw pillow numpy
   ```

3. Optional: set the title shown at the top of the page:

   ```
   python add_round.py --title "Semen Analysis PT Program"
   ```

## 2. Every 6 months: add the new round

1. In GitHub Desktop, choose **Repository → Open in Command Prompt** and run:

   ```
   python add_round.py --round "2027 Round 1" --czi "D:\PT\slide_A.czi" --label "Specimen A" --czi "D:\PT\slide_B.czi" --label "Specimen B"
   ```

2. Back in GitHub Desktop, type a summary (e.g. "2027 Round 1"), click **Commit to main**, then **Push origin**.
3. The page updates within a few minutes. You may need to refresh your browser.

About the command:
- `--round` is the name shown in the Round drop-down. The newest round appears first and opens by default.
- Put a `--label` after each `--czi` to set the tab name. If you leave the labels out, the file names are used.
- If a .czi contains several separately scanned areas (scenes), each area gets its own tab
  ("Specimen A - area 1", "Specimen A - area 2"). To keep them on one canvas instead, add `--combine-scenes`.
- A 300 MB slide takes under a minute and produces about 70 MB of tiles, so a round is about 150 MB.
- Running the same `--round` name again replaces that round.
- The .czi files themselves are never uploaded. Only the converted tiles are.

Other commands:

```
python add_round.py --list                      # show all rounds
python add_round.py --remove "2025 Round 1"     # delete a round (then commit and push)
```

**Space limit:** a GitHub Pages site may be no larger than 1 GB, which is about 5–6 rounds.
When you get close, `--remove` the oldest round before adding a new one.

### Preview before publishing (optional)

```
cd docs
python -m http.server 8000
```

Then open <http://localhost:8000> in your browser. Opening `index.html` by
double-clicking won't work because browsers block it. Use the command above.

## If you outgrow GitHub: Cloudflare R2

Cloudflare R2's free tier has 10 GB of storage with no bandwidth charges, which is enough for decades
of rounds. Create a bucket, turn on its public *R2.dev subdomain*, set up `rclone` with an R2 API token,
then run `rclone sync docs r2:<bucket> --progress --transfers 32`. Participants then use
`https://pub-xxxx.r2.dev/index.html`.

---

## Notes on the images

- **Brightfield (RGB) scans** are converted as-is, with white filling any area outside the scanned region.
- **Fluorescence / multichannel** images are merged into a colour composite using the channel colours
  saved in the file, with automatic contrast (0.1–99.9 percentile).
- **Brightness:** colours are taken as stored in the file. ZEN often applies a display gamma (often
  0.45), so slides can look a little lighter in ZEN. To match that, add `--gamma 0.7` (lighter) or experiment.
- **Z-stacks:** only one focal plane is shown (the first by default; choose another with `--z 3`).
- **Multiple scenes** in one file are shown together on one canvas, in their stage positions.
- If the .czi records a pixel size, a **scale bar** appears in the bottom-left corner. To remove it,
  delete the `<div id="scalebar">` line in `index.html`.
- You can link straight to an image: `…/index.html#2027-round-1/2` opens Image 2 of that round.

## Privacy

Anyone who has the link can view the images. Nothing is password-protected, and nothing
is collected from viewers. Because the GitHub repository is public, the image tiles can also be
browsed there. If images must stay hidden until a challenge opens,
don't publish the round until that date.
