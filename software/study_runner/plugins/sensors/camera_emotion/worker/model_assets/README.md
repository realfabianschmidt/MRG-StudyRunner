# DeepFace model assets

**This folder is the drop-off point for the emotion model.** It is empty on
purpose, and emotion analysis does not work until you put the model file here
or fetch it with the script below.

Study Runner never downloads this file on its own. The DeepFace emotion weight
is derived from VGG-Face and carries **non-commercial research terms** that are
not covered by Study Runner's MIT license, so the decision to obtain it has to
be yours, made once, deliberately. Read `THIRD_PARTY_NOTICES.md` in the
repository root before you start.

## The one file you need

| | |
|---|---|
| Name | `facial_expression_model_weights.h5` |
| Source | <https://github.com/serengil/deepface_models/releases/download/v1.0/facial_expression_model_weights.h5> |
| SHA-256 | `e8e8851d3fa05c001b1c27fd8841dfe08d7f82bb786a53ad8776725b7a1e824c` |

There is no choice of model. Study Runner accepts exactly this file: anything
with a different checksum is rejected and the worker starts without emotion
analysis rather than using a model nobody verified.

## Way 1: let the script fetch it (needs internet)

Open a terminal in the repository root — the folder containing `README.md` and
`software/` — and run one line:

```bash
python release_tools/fetch_deepface_model_assets.py --accept-vgg-face-non-commercial-research-terms
```

The long flag is not decoration: without it the script refuses to run and
prints the license pages instead. It is how you record that you checked the
terms.

The script downloads the file, verifies the checksum, and puts it where the
worker looks. Running it a second time does nothing if the file is already
there and valid. Then restart Study Runner.

## Way 2: put the file here yourself (works without internet)

Use this when the study computer has no internet, when a download is blocked,
or when your institution supplies the file.

1. Obtain `facial_expression_model_weights.h5` from the source above — on any
   machine, for example your own laptop.
2. Verify the checksum before you trust the file:

   ```powershell
   Get-FileHash facial_expression_model_weights.h5 -Algorithm SHA256
   ```

   ```bash
   shasum -a 256 facial_expression_model_weights.h5
   ```

   It must match the SHA-256 above, character for character. If it does not,
   throw the file away — do not "try it anyway", the worker will reject it.
3. Copy the file into **this folder**, next to this README. Keep the name
   exactly as it is.
4. Restart Study Runner.

On the next start the worker checks the file again and copies it into its
runtime cache, printing `Seeded DeepFace weights from bundled asset`. You do
not have to copy anything by hand a second time, and `.gitignore` keeps the
file out of the repository, so it will not end up in a commit or a release.

## Checking that it worked

In the admin dashboard, the camera plugin's status changes from `failed` to
`connected` and the message row stops asking for the model.

From a terminal, inside `software/`:

```bash
python server.py --emotion-worker-self-test --json
```

`"model_ready": true` means analysis is working. If it is `false`, the
`model_error` and `suggested_action` fields say what is missing, and
`model_asset_path` names the exact file the worker was looking for.

## If you may not use this model

The terms are non-commercial research only. If your study does not fit them,
do not provision the file. Two options remain:

- Set the plugin's worker mode to `remote_worker` and point it at a service
  running a model you are licensed to use.
- Run the study without emotion analysis. Camera capture, the LSL bridge and
  XDF recording all keep working; only the derived emotion values are absent.

The dashboard actions **Repair emotion runtime** and **Install emotion
dependencies** repair Python packages only. Neither one downloads a model, by
design.
