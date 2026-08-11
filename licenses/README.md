# Third-party licenses

Study Runner itself is proprietary; see `../LICENSE`. This folder collects
the full license text of every third-party component vendored into the
source tree, in one place, so none of them has to be hunted down individually.
See `../THIRD_PARTY_NOTICES.md` for what each component is, exactly which
files it covers, and any additional attribution terms.

These are **copies for easy reference**. The authoritative copy of each
license stays next to the vendored code it covers, since that is what most
of these licenses themselves require:

| Copy here | Original location | Covers |
|---|---|---|
| `App-LabRecorder-XDFWriter-LICENSE.txt` | `software/recording_worker/native/vendor/App-LabRecorder/LICENSE` | The vendored LabRecorder/XDFWriter sources and the adapted `xdfwriter_patched.cpp`/`.h` files (MIT) |
| `BOOST_LICENSE_1_0.txt` | `software/recording_worker/native/vendor/BOOST_LICENSE_1_0.txt` | Portions of `xdfwriter/conversions.h` derived from a Boost-licensed portable archive implementation (Boost Software License 1.0) |
| `Geist-LICENSE.txt` | `software/study_runner/frontend/vendor/geist/LICENSE` | The vendored Geist Sans/Mono font files (SIL Open Font License 1.1) |
| `Iconoir-LICENSE.txt` | `software/study_runner/frontend/vendor/iconoir/LICENSE` | The vendored Iconoir icon set (MIT) |

Not third-party, kept out of this folder on purpose: the Materiability
heading font (first-party, covered by `../LICENSE`) and
`software/study_runner/plugins/brainbit/HelloEEG_HelloMYO_01.3.toe` (a
project-original TouchDesigner reference project, also covered by
`../LICENSE` — see `THIRD_PARTY_NOTICES.md`).

Installed Python packages (DeepFace, notion-client, python-osc, pyneurosdk2,
etc.) keep their own upstream licenses and are not vendored into this repo,
so there is no local copy to collect here — see the "Installed Python
packages" section of `../THIRD_PARTY_NOTICES.md`.
