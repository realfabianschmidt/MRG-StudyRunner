# DeepFace model assets

Study Runner releases intentionally contain no model weight in this folder.
The DeepFace emotion model inherits the upstream VGG-Face terms and is not
covered by Study Runner's proprietary license. Review `THIRD_PARTY_NOTICES.md`
before use.

When those terms fit the study, provision the pinned asset into the ignored
runtime cache from the repository root with:

```bash
python release_tools/fetch_deepface_model_assets.py --accept-vgg-face-non-commercial-research-terms
```

The command downloads from the official DeepFace model release and verifies the
expected SHA-256. The dashboard repair action does not download model weights.
