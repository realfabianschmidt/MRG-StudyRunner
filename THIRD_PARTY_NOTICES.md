# Third-party notices

Study Runner itself is proprietary and all rights are reserved as described in
`LICENSE`. The following components are not relicensed by that notice.

## App-LabRecorder / XDFWriter

The source tree includes the Lab Streaming Layer App-LabRecorder XDFWriter
sources pinned by `software/recording_worker/native/UPSTREAM_LOCK.json`.
They are licensed under the MIT License. The adapted
`native/src/xdfwriter_patched.cpp` and `.h` files remain derived works under
that same license. The complete upstream notice is kept at
`software/recording_worker/native/vendor/App-LabRecorder/LICENSE`.

App-LabRecorder's `xdfwriter/conversions.h` notes portions derived from
Christian Pfligersdorffer's portable archive implementation under the
Boost Software License 1.0. The complete Boost license text is kept at
`software/recording_worker/native/vendor/BOOST_LICENSE_1_0.txt`.

## Iconoir

`software/study_runner/web/vendor/iconoir/iconoir.css` contains Iconoir 7.11.1
icons by Luca Burgio and contributors. Iconoir is licensed under the MIT
License. The complete upstream notice is kept at
`software/study_runner/web/vendor/iconoir/LICENSE`.

## Geist

`software/study_runner/web/vendor/geist/` contains three weights each of Geist
Sans and Geist Mono 1.7.2 by Vercel, in collaboration with basement.studio. Geist is licensed under
the SIL Open Font License, Version 1.1. The complete upstream notice is kept at
`software/study_runner/web/vendor/geist/LICENSE`.

Geist Sans is the body face and Geist Mono carries tabular values. Headings are
drawn in Materiability, which is first-party and covered by `LICENSE` rather than
by this file; Geist also stands behind it in the stack as the fallback for a
checkout whose font files have not been fetched. See
`software/study_runner/web/fonts/README.md`.

## DeepFace and optional VGG-Face-derived model weights

DeepFace is installed from PyPI on supported local-worker platforms and remains
subject to its own MIT License and to the licenses of the models it wraps.
Study Runner source archives intentionally do **not** contain
`facial_expression_model_weights.h5`.

An operator may separately provision the DeepFace v1.0 emotion weights from:

`https://github.com/serengil/deepface_models/releases/download/v1.0/facial_expression_model_weights.h5`

Expected SHA-256:
`e8e8851d3fa05c001b1c27fd8841dfe08d7f82bb786a53ad8776725b7a1e824c`.

The DeepFace model repository states that the emotion model inherits the
VGG-Face license. Oxford states that the VGG-Face models may be used for
non-commercial research with attribution. Before provisioning or using the
weights, the operator must review and accept the current upstream terms:

- `https://github.com/serengil/deepface_models`
- `https://www.robots.ox.ac.uk/~vgg/software/vgg_face/`

Study Runner does not grant any right to these weights and does not assert that
their terms are suitable for a particular study or production use. For a use
outside those terms, configure the plugin's `remote_worker` mode with a model
for which the operator has appropriate rights.

## Installed Python packages

The source installers obtain Python dependencies from their package indexes.
Those packages are not part of Study Runner's proprietary license and retain
their own licenses and notices. Operators distributing a combined environment
remain responsible for reviewing those dependency licenses.
