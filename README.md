# Cuddler for ComfyUI

> **In development.** This plugin is not on the Comfy Registry yet. Version 1.0 adds the
> Cuddler Video, Image, Speech and Music nodes; until then it only provides the settings page.

[中文说明](README.zh-CN.md)

Generate video, images, speech and music inside ComfyUI workflows with your
[Cuddler Platform](https://platform.cuddler.ai) API key. Every model in Cuddler's public
catalog is available from four nodes, prices are shown on the node before you run, and every
run has a spending limit that Cuddler enforces.

## What you need

- ComfyUI 0.14.0 or newer.
- A Cuddler Platform API key. Create one at <https://platform.cuddler.ai/console/keys>; add funds
  at <https://platform.cuddler.ai/console/billing>.

## Install

After the 1.0 release: search for **Cuddler** in ComfyUI-Manager, or run `comfy node install cuddler`,
then restart ComfyUI. The plugin needs no extra Python packages.

## Add your API key

Open **Settings → Cuddler**, paste the key and press **Save**. The page shows the last four
characters of the key and your available balance.

On a server, set the `CUDDLER_API_KEY` environment variable instead. It takes precedence over a
key saved on the settings page.

The settings page and price labels answer only pages opened at `localhost`, an IP address or a
local name such as `my-pc.local`, so other websites cannot change your key. If you reach ComfyUI
through a domain name or a tunnel, add that name to the `CUDDLER_ALLOWED_HOSTS` environment
variable (comma separated; `.example.com` covers every name under it) and restart ComfyUI.

## Costs

- Each node shows its price before you run. Prices come from Cuddler's price estimate, which is free.
- Each node has a spending limit per run (default $2). Cuddler refuses to start a job that could
  cost more than the limit, and never charges more than it.
- Running a node again with the same inputs and the same seed reuses the earlier result for
  7 days instead of paying again. Change the seed to get a new one.
- If you stop a run while a video, speech or music job is still queued, the job is cancelled and
  nothing is charged. Image jobs and jobs that have already started cannot be cancelled: they
  finish and are charged, and running the node again collects the result.

## Privacy and security

- The key is stored in `ComfyUI/user/__cuddler/credentials.json`. ComfyUI never serves files
  from `__` folders over HTTP, and on macOS and Linux the file is readable only by your account.
- The key is never written to workflows, to image or video metadata, to ComfyUI's settings or to logs.
- `ComfyUI/user/__cuddler/state.sqlite3` keeps run fingerprints (hashes), idempotency keys, job and
  upload ids and each run's spending limit for 8 days after their last update, so a crash or a lost
  connection never creates a second job. It holds no prompts or media.
- Every job sent to Cuddler carries the metadata `client: comfyui-cuddler/<version>`, so Cuddler
  can count jobs made from ComfyUI. Nothing else about you or your machine is sent.
- ComfyUI has no login. Anyone who can open your ComfyUI page can spend with your key, so do not
  expose ComfyUI to a network you do not trust. The per-run limit and your account's daily limit
  cap what can be spent.
- If ComfyUI runs on another machine, a key typed on the settings page travels over that connection.
  Prefer `CUDDLER_API_KEY` there.

## Development

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -t .
npm test
```

Before a release, refresh the bundled model list with `python scripts/export_catalog_snapshot.py`.

## License

MIT, see [LICENSE](LICENSE).
